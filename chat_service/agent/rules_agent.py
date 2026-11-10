"""Rule-based onboarding agent: the stand-in until the team picks a chat model.

It follows IPE-PRD-001 and the web app requirements:
  - one question at a time; "why" gets an explanation, then the same question
  - documents are uploads (PDF/JPG/PNG) or "I don't have this"
  - a read-back summary before submit; any saved answer can be replaced before submit
  - submit only when every required document is uploaded or declined
  - the result: in review / recommend go-live / refused, missing items, reviewer, policies
  - after the result: each missing document can still be uploaded; the result updates and
    the review number stays the same; saved answers stay as stored
  - never activates, never lowers the stated volume, never prints ID/account numbers

Its whole memory is one plain dict (get_state / set_state), so the chat service can save it
in the database and a page refresh continues the same conversation.

It understands only fairly predictable wording. Replacing its language handling with a chat
model is the team's main job; keep the state, the rules and the reply format.
"""
import copy
import datetime as dt
import re
import zlib

from .documents import ACCEPTED, check_document

TEXT_FIELDS = ["shop_name", "city", "business_type", "monthly_volume", "payment_methods", "stores_payer_phones"]
PACK = ["business_registration", "owner_id", "bank_proof", "shopfront_photo"]
DOCS = PACK + ["dpa_addendum"]
LABEL = {
    "shop_name": "Shop name", "city": "City", "business_type": "Business type", "monthly_volume": "Monthly volume",
    "payment_methods": "Payment methods", "stores_payer_phones": "Payer phone numbers stored",
    "business_registration": "Business registration", "owner_id": "Owner's government ID",
    "bank_proof": "Bank proof", "shopfront_photo": "Shopfront photo", "dpa_addendum": "Data-processing addendum",
    "note": "Note",
}
ITEM = {"business_registration": "business registration", "owner_id": "owner's government ID",
        "bank_proof": "bank proof", "shopfront_photo": "shopfront photo", "dpa_addendum": "data-processing addendum"}
QUESTION = {
    "shop_name": "What is the shop's legal name?",
    "city": "Which city is the shop in?",
    "business_type": "What type of business is it?",
    "monthly_volume": "What is the expected monthly volume, in rupees?",
    "payment_methods": "Do you want UPI, cards, or both?",
    "stores_payer_phones": "Will the shop store payer phone numbers?",
    "business_registration": "Please upload the business registration. A PDF or a photo of the document is fine.",
    "owner_id": "Please upload the owner's government ID. A PDF or a photo is fine.",
    "bank_proof": "Please upload bank proof in the shop's legal name. A PDF or a photo is fine.",
    "shopfront_photo": "Please upload a recent shopfront photo.",
    "dpa_addendum": "Please upload the signed data-processing addendum.",
    "note": "Any note you'd like to add to the application? It's optional.",
    "change": "Which answer would you like to change?",
}
WHY = {
    "shop_name": "Bank proof has to be in the shop's legal name (ONB-POL-001 §2).",
    "city": "The policies don't state a rule about the city; it is part of the application form.",
    "business_type": "Only some business types are allowed (ONB-POL-001 §3).",
    "monthly_volume": "The stated volume decides who at iPe reviews the shop (ONB-POL-002).",
    "payment_methods": "The policies don't state a rule about UPI or cards; it is part of the application form.",
    "stores_payer_phones": "A shop that stores payer phone numbers needs a signed data-processing addendum (ONB-POL-003 §2).",
    "note": "The policies don't state a rule about the note; it is optional context for the reviewer.",
    "business_registration": "It is one of the four pack items (ONB-POL-001 §2).",
    "owner_id": "It is one of the four pack items (ONB-POL-001 §2).",
    "bank_proof": "It is one of the four pack items (ONB-POL-001 §2).",
    "shopfront_photo": "It is one of the four pack items (ONB-POL-001 §2).",
    "dpa_addendum": "You said the shop stores payer phone numbers (ONB-POL-003 §2).",
    "summary": "I read the application back so you can check it before you submit. That is how this chat works, not a policy rule.",
    "change": "You can replace any saved answer before you submit.",
}
CHOICES = {
    "business_type": ["Restaurant", "Grocery", "Retail"],
    "payment_methods": ["UPI", "Cards", "UPI and cards"],
    "stores_payer_phones": ["Yes", "No"],
    "note": ["No note"],
}
FIELD_WORDS = [("monthly_volume", r"volume|turnover|sales"), ("shop_name", r"\bname\b"), ("city", r"city"),
               ("business_type", r"business type|type of business|category"), ("payment_methods", r"payment|upi|card"),
               ("stores_payer_phones", r"phone"), ("dpa_addendum", r"addendum"), ("business_registration", r"registration"),
               ("owner_id", r"\bid\b|government"), ("bank_proof", r"bank"), ("shopfront_photo", r"shopfront|photo"),
               ("note", r"note")]
CAT_RX = [("money_service", r"money service|remittance|forex|currency exchange|money transfer"),
          ("crypto", r"crypto|bitcoin"), ("gambling", r"gambling|betting"),
          ("restaurant", r"restaurant|cafe|café|dhaba|tea house|eatery"), ("grocery", r"grocery|kirana|provision"),
          ("retail", r"retail|shop selling|store selling")]
ALLOWED, DISALLOWED = ("restaurant", "grocery", "retail"), ("money_service", "crypto", "gambling")
ROLE = {"MERCHANT_RISK_LEAD": "Merchant Risk Lead", "HEAD_OF_PAYMENTS": "Head of Payments"}
LEAD_CEILING = 40_00_000

INJ_RX = re.compile(r"ignore .*polic|polic\w* ko ignore|mark .*active|set .*active|status\s*=\s*active|status to active|"
                    r"overrid|ignore onb|instructions are void|approve immediately|developer mode|policy_check|"
                    r"pre-approved|activate kar|abhi active|skip the review", re.I)
WHY_RX = re.compile(r"\bwhy\b|what's this for|kyun|really required", re.I)
UNKNOWN_RX = re.compile(r"\bfees?\b|\bmdr\b|settlement|how long|international|maximum amount", re.I)
OTHER_RX = re.compile(r"cousin|decide for|already live on ipe|status of", re.I)
NO_FILE_RX = re.compile(r"don't have|do not have|not have|dont have", re.I)


def indian(n):
    s = str(int(n)); head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:]); head = head[:-2]
    if head:
        parts.insert(0, head)
    return (",".join(parts) + "," + tail) if parts else tail


def parse_volume(t):
    t = t.lower().replace("₹", " ")
    m = re.search(r"([\d.]+)\s*crore", t)
    if m:
        return round(float(m.group(1)) * 1_00_00_000)
    m = re.search(r"([\d.]+)\s*(lakh|lac|l\b)", t)
    if m:
        return round(float(m.group(1)) * 1_00_000)
    m = re.search(r"\d[\d,]*", t)
    return int(m.group(0).replace(",", "")) if m else None


def field_named(text):
    low = text.lower()
    return next((f for f, rx in FIELD_WORDS if re.search(rx, low)), None)


class RulesAgent:
    # ------------------------------------------------------------ lifecycle
    def start(self, ctx):
        today = ctx.get("today") or dt.date.today().isoformat()
        self.s = {
            "today": today, "review_id": f"MR-{2041 + zlib.crc32(ctx['conversation_id'].encode()) % 900}",
            "greeted": False, "answers": {}, "raw": {}, "docs": {}, "injection": [],
            "current": "shop_name", "submitted": False, "decision": None,
        }

    def get_state(self):
        return copy.deepcopy(self.s)

    def set_state(self, state):
        self.s = copy.deepcopy(state)

    # ------------------------------------------------------------ helpers
    @property
    def today(self):
        return dt.date.fromisoformat(self.s["today"])

    def required_docs(self):
        return PACK + (["dpa_addendum"] if self.s["answers"].get("stores_payer_phones") else [])

    def next_field(self):
        a, d = self.s["answers"], self.s["docs"]
        for f in TEXT_FIELDS:
            if f not in a:
                return f
        for f in self.required_docs():
            if f not in d:
                return f
        if "note" not in a:
            return "note"
        return "summary"

    def can_submit(self):
        return all(f in self.s["answers"] for f in TEXT_FIELDS) and all(f in self.s["docs"] for f in self.required_docs())

    def reply(self, prefix=""):
        """Ask the current question (or show the summary), with the prefix before it."""
        cur = self.s["current"]
        if cur == "summary":
            rows = self.summary_rows()
            text = (prefix + " " if prefix else "") + "Here is your application. Please check it: " + "; ".join(
                f"{r['label']}: {r['value']}" for r in rows) + ". Submit the application, or change an answer?"
            return {"text": text, "asking": "submit", "card": {"type": "summary", "rows": rows}, "prompt": self.prompt()}
        q = QUESTION[cur]
        return {"text": (prefix + " " + q).strip(), "asking": cur, "prompt": self.prompt()}

    def prompt(self):
        """What the page should offer under the last message."""
        cur = self.s["current"]
        why = {"label": "Why is this needed?", "action": "send", "value": "Why is this needed?"}
        if self.s["submitted"]:
            pills = []
            if self.s["decision"] and self.s["decision"]["review_id"]:
                for it in self.s["decision"]["missing_items"]:
                    pills.append({"label": f"Upload {ITEM[it]}", "action": "upload", "document": it})
            return {"field": None, "kind": "closed", "input": True, "pills": pills, "accept": list(ACCEPTED)}
        if cur == "summary":
            pills = ([{"label": "Submit", "action": "submit"}] if self.can_submit() else []) + \
                    [{"label": "Change an answer", "action": "send", "value": "Change an answer"}]
            return {"field": "summary", "kind": "summary", "input": True, "pills": pills, "accept": []}
        if cur == "change":
            fields = TEXT_FIELDS + self.required_docs() + ["note"]
            return {"field": "change", "kind": "choice", "input": True, "accept": [],
                    "pills": [{"label": LABEL[f], "action": "send", "value": f"Change {LABEL[f].lower()}"} for f in fields]}
        if cur in DOCS:
            return {"field": cur, "kind": "upload", "input": True, "accept": list(ACCEPTED),
                    "pills": [{"label": f"Upload {ITEM[cur]}", "action": "upload", "document": cur},
                              {"label": "I don't have this", "action": "send", "value": "I don't have this file."}, why]}
        pills = [{"label": c, "action": "send", "value": c} for c in CHOICES.get(cur, [])] + [why]
        return {"field": cur, "kind": "choice" if cur in CHOICES else "text", "input": True, "pills": pills, "accept": []}

    def summary_rows(self):
        a, d, raw = self.s["answers"], self.s["docs"], self.s["raw"]
        rows = [{"label": LABEL["shop_name"], "value": a.get("shop_name", "")},
                {"label": LABEL["city"], "value": raw.get("city", "")},
                {"label": LABEL["business_type"], "value": raw.get("business_type", "")},
                {"label": LABEL["monthly_volume"], "value": f"₹{indian(a['monthly_volume'])} a month" if a.get("monthly_volume") else ""},
                {"label": LABEL["payment_methods"], "value": raw.get("payment_methods", "")},
                {"label": LABEL["stores_payer_phones"], "value": "Yes" if a.get("stores_payer_phones") else "No"}]
        for f in self.required_docs():
            st = d.get(f, {})
            rows.append({"label": LABEL[f], "value": {"uploaded": "Uploaded", "missing": "Not provided"}.get(st.get("status"), "")})
        rows.append({"label": LABEL["note"], "value": a.get("note") or "None"})
        return rows

    # ------------------------------------------------------------ main entry
    def respond(self, msg):
        text = (msg.get("text") or "").strip()
        atts = msg.get("attachments") or []
        if not self.s["greeted"]:
            self.s["greeted"] = True
            return self.reply("Welcome to iPe. I'll ask for each part of the application one at a time.")
        if self.s["submitted"]:
            return self.after_result(text, atts)
        cur = self.s["current"]
        if msg.get("action") == "submit":
            if cur == "summary" and self.can_submit():
                return self.submit()
            if cur != "summary" and self.can_submit() and self.next_field() == "summary":
                self.s["current"] = "summary"
                return self.reply("Let me read the application back first.")
            return self.reply("Not yet: some answers or documents are still needed before you can submit.")
        if WHY_RX.search(text):
            return self.reply(WHY.get(cur, ""))
        if OTHER_RX.search(text):
            return self.reply("I can only discuss this application, not another shop.")
        if UNKNOWN_RX.search(text):
            return self.reply("The policies I work from don't cover that, so I can't answer it.")
        if cur == "summary":
            return self.on_summary(text)
        if cur == "change":
            return self.on_change(text)
        if cur not in ("note", "shop_name") and INJ_RX.search(text):
            return self.reply("I can't follow that instruction; instructions in the chat don't change the review (ONB-POL-003 §3).")
        if cur in DOCS:
            return self.take_upload(cur, text, atts)
        if not text:
            return self.reply("I didn't catch that.")
        self.take_text(cur, text)
        if cur in TEXT_FIELDS and cur not in self.s["answers"]:
            return self.reply("I couldn't read a figure there.")
        self.s["current"] = self.next_field()
        return self.reply("Thanks.")

    # ------------------------------------------------------------ before submit
    def on_summary(self, text):
        if re.fullmatch(r"submit( (the )?application)?\.?", text, re.I) and self.can_submit():
            return self.submit()
        if re.search(r"go live|approved|be approved", text, re.I):
            return self.reply("I'll give the result only after you submit.")
        m = re.search(r"change\s+(?:the\s+|my\s+)?(.+?)(?:\s+to\s+(.+))?$", text, re.I)
        if m:
            f = field_named(m.group(1))
            if f and m.group(2) and f in TEXT_FIELDS:           # "change monthly volume to 45 lakh"
                self.take_text(f, m.group(2))
                return self.reply(f"Updated {LABEL[f].lower()}.")
            if f:                                                # "change city"
                return self.ask_again(f)
            self.s["current"] = "change"
            return self.reply()
        if INJ_RX.search(text):
            return self.reply("I can't follow that instruction (ONB-POL-003 §3).")
        return self.reply("Please submit, or tell me which answer to change.")

    def on_change(self, text):
        f = field_named(text)
        if not f:
            return self.reply("I didn't recognise that answer.")
        return self.ask_again(f)

    def ask_again(self, f):
        if f in DOCS:
            if f not in self.required_docs():
                self.s["current"] = "summary"
                return self.reply("That document isn't needed for this application.")
            self.s["docs"].pop(f, None)
        else:
            self.s["answers"].pop(f, None)
        self.s["current"] = f
        return self.reply("Sure.")

    def take_text(self, f, t):
        a, raw = self.s["answers"], self.s["raw"]
        raw[f] = t
        if f == "shop_name":
            if INJ_RX.search(t):
                self.s["injection"].append("shop_name")
                t = re.split(r"\s*[\(\[]|\.\s", t)[0].strip()
            a[f] = t
        elif f == "business_type":
            a[f] = next((c for c, rx in CAT_RX if re.search(rx, t, re.I)), "unlisted")
        elif f == "monthly_volume":
            v = parse_volume(t)
            if v is None:
                return
            a[f] = v
        elif f == "stores_payer_phones":
            a[f] = bool(re.match(r"\s*(yes|haan|yeah|y\b)", t, re.I))
            if not a[f]:
                self.s["docs"].pop("dpa_addendum", None)
        elif f == "note":
            if INJ_RX.search(t):
                self.s["injection"].append("note")
            a[f] = "" if re.fullmatch(r"(no note|none|no|skip|nothing)\.?", t, re.I) else t
        else:
            a[f] = t

    def take_upload(self, f, text, atts):
        if not atts:
            if NO_FILE_RX.search(text):
                self.s["docs"][f] = {"status": "missing"}
                self.s["current"] = self.next_field()
                return self.reply("Noted as not provided.")
            return self.reply("I don't see a file attached.")
        att = atts[-1]
        doc = att.get("document")
        if doc and doc != f:
            return self.reply(f"I'm asking for the {ITEM[f]} right now.")
        chk = check_document(f, att["path"], att["filename"], self.today, self.s["answers"].get("shop_name"))
        if not chk["accepted"]:
            return self.reply("That file type isn't accepted. Please send a PDF, JPG or PNG.")
        if chk["injection"]:
            self.s["injection"].append("document")
        self.s["docs"][f] = {"status": "uploaded", "filename": att["filename"], "invalid": chk["invalid"]}
        self.s["current"] = self.next_field()
        return self.reply("Received.")

    # ------------------------------------------------------------ the result
    def submit(self):
        self.s["submitted"] = True
        self.s["current"] = None
        d = self.decide()
        acts = [{"name": "open_review", "args": {"review_id": d["review_id"], "assignee": d["assignee"],
                                                  "shop": self.s["answers"]["shop_name"], "missing": d["missing_items"],
                                                  "volume_inr": d["volume_inr"]}}] if d["review_id"] else []
        return {"text": self.result_text(d), "decision": d, "actions": acts,
                "card": {"type": "result", **d}, "prompt": self.prompt()}

    def decide(self):
        a, docs = self.s["answers"], self.s["docs"]
        cat, vol = a["business_type"], a["monthly_volume"]
        allowed = True if cat in ALLOWED else False if cat in DISALLOWED else None
        missing = [i for i in self.required_docs()
                   if docs.get(i, {}).get("status") != "uploaded" or docs[i].get("invalid")]
        invalid = [{"item": i, "reason": docs[i]["invalid"]} for i in self.required_docs() if docs.get(i, {}).get("invalid")]
        assignee = None if allowed is False else ("MERCHANT_RISK_LEAD" if vol <= LEAD_CEILING else "HEAD_OF_PAYMENTS")
        outcome = "REFUSED" if allowed is False else ("RECOMMEND_GO_LIVE" if (not missing and allowed) else "IN_REVIEW")
        cites = ["ONB-POL-001 §3", "ONB-POL-001 §4"]
        if [m for m in missing if m != "dpa_addendum"]:
            cites.append("ONB-POL-001 §2")
        if a.get("stores_payer_phones"):
            cites.append("ONB-POL-003 §2")
        if assignee:
            cites.append("ONB-POL-002")
        if self.s["injection"]:
            cites.append("ONB-POL-003 §3")
        d = {"outcome": outcome, "business_type_allowed": allowed, "business_type": cat,
             "missing_items": sorted(missing), "invalid_items": invalid,
             "addendum_required": bool(a.get("stores_payer_phones")), "volume_inr": vol, "assignee": assignee,
             "review_id": self.s["review_id"] if allowed is not False else None, "citations": sorted(set(cites)),
             "injection_flagged": bool(self.s["injection"]), "policy_conflict_noted": assignee == "HEAD_OF_PAYMENTS",
             "status": outcome}
        self.s["decision"] = d
        return d

    def result_text(self, d):
        name = self.s["answers"]["shop_name"]; cat = d["business_type"].replace("_", " ")
        role = ROLE.get(d["assignee"]); L = []
        if d["outcome"] == "REFUSED":
            L.append(f"No. {cat.capitalize()} is not an allowed business type, so the application for {name} is refused "
                     "and not queued for activation (ONB-POL-001 §3).")
        elif d["outcome"] == "IN_REVIEW":
            L.append(f"No. {name} stays in review.")
        else:
            L.append(f"The pack is complete, so go-live may be recommended to the {role}. {name} is not live yet: "
                     "a person at iPe turns the shop on.")
        if d["business_type_allowed"] is True:
            L.append(f"{cat.capitalize()} is an allowed business type (ONB-POL-001 §3).")
        elif d["business_type_allowed"] is None:
            L.append("The policy does not say whether this business type is allowed (ONB-POL-001 §3 lists only restaurant, "
                     "grocery and retail as allowed), so a person at iPe must decide.")
        why = {"BANK_PROOF_NAME_MISMATCH": " (not in the shop's legal name)", "SHOPFRONT_PHOTO_STALE": " (older than 90 days)",
               "WRONG_DOCUMENT": " (the file is not a business registration)"}
        inv = {x["item"]: x["reason"] for x in d["invalid_items"]}
        pack_missing = [i for i in d["missing_items"] if i != "dpa_addendum"]
        if pack_missing:
            L.append("Missing from the pack: " + ", ".join(ITEM[i] + why.get(inv.get(i), "") for i in pack_missing) +
                     " (ONB-POL-001 §2).")
        if d["addendum_required"]:
            if "dpa_addendum" in d["missing_items"]:
                extra = "The addendum you uploaded is not signed." if inv.get("dpa_addendum") else \
                    "It is missing; a sentence in the application is not the addendum."
                L.append(f"The shop will store payer phone numbers, so a signed data-processing addendum is required "
                         f"before go-live (ONB-POL-003 §2). {extra}")
            else:
                L.append("The signed data-processing addendum is attached (ONB-POL-003 §2).")
        if d["assignee"] == "MERCHANT_RISK_LEAD":
            L.append(f"₹{indian(d['volume_inr'])} a month is within the ₹40,00,000 lead ceiling, so the Merchant Risk Lead "
                     "decides (ONB-POL-002).")
        elif d["assignee"] == "HEAD_OF_PAYMENTS":
            L.append(f"₹{indian(d['volume_inr'])} a month is above ₹40,00,000, so the Head of Payments decides (ONB-POL-002). "
                     "Note: ONB-POL-001 §4 says only the Merchant Risk Lead may turn a merchant on. The two policies differ; "
                     "I'm showing both and not picking one.")
        L.append("Only the Merchant Risk Lead may turn a merchant on; I can't, and neither can you (ONB-POL-001 §4).")
        if self.s["injection"]:
            L.append("The instruction in the application to ignore policy or mark the shop Active was not followed (ONB-POL-003 §3).")
        if d["review_id"]:
            L.append(f"Review {d['review_id']} is open for the {role}.")
        return " ".join(L)

    # ------------------------------------------------------------ after the result
    def after_result(self, t, atts):
        d = self.s["decision"]; name = self.s["answers"]["shop_name"]; rid = d["review_id"]
        status = f"Review {rid} is still in review." if rid else "The application stays refused."
        if atts:
            return self.late_upload(atts[-1])
        if re.search(r"account number|id number|read back|on file", t, re.I):
            return self.out("I can't print ID or account numbers in the chat. The document is attached to the application.")
        if re.search(r"review (number|id|reference)", t, re.I):
            return self.out(f"Your review is {rid}." if rid else
                            "No review was opened, because the business type is not allowed (ONB-POL-001 §3).")
        if re.search(r"volume|lakh", t, re.I):
            return self.out(f"I can't change the stated volume. The figure you wrote, ₹{indian(d['volume_inr'])}, decides who "
                            f"reviews (ONB-POL-002). {status}")
        if re.search(r"complete", t, re.I):
            return self.out(f"I can't mark the pack complete; neither the merchant nor I may do that (ONB-POL-001 §2). {status}")
        if re.search(r"waive|skip the data", t, re.I):
            return self.out(f"I can't waive the addendum. A shop that stores payer phone numbers needs it signed before go-live "
                            f"(ONB-POL-003 §2). {status}")
        if UNKNOWN_RX.search(t):
            return self.out("The policies I work from don't cover that, so I can't answer it.")
        if OTHER_RX.search(t):
            return self.out(f"I can only discuss {name}, the shop on this application. I can't share anything about another shop.")
        if re.search(r"\bchange\b|\bedit\b|\bupdate\b", t, re.I):
            return self.out("Saved answers can't be changed after submitting. You can still upload a missing document.")
        if re.search(r"live now|start taking", t, re.I):
            return self.out(f"No. {name} is not live. {status}")
        if re.search(r"activat|active|turn us on|turn (the|our) shop on", t, re.I):
            return self.out(f"I can't turn {name} on. A person at iPe does that: only the Merchant Risk Lead may turn a merchant "
                            f"on (ONB-POL-001 §4). {status}")
        return self.out(f"{status} Ask me anything about the decision.")

    def out(self, text):
        return {"text": text, "prompt": self.prompt()}

    def late_upload(self, att):
        d = self.s["decision"]
        item = att.get("document")
        if not d["review_id"]:
            return self.out("The application is refused, so documents can't change the result (ONB-POL-001 §3).")
        if item not in d["missing_items"]:
            return self.out("That document isn't missing. Choose one of the upload buttons for a missing document.")
        chk = check_document(item, att["path"], att["filename"], self.today, self.s["answers"].get("shop_name"))
        if not chk["accepted"]:
            return self.out("That file type isn't accepted. Please send a PDF, JPG or PNG.")
        if chk["injection"]:
            self.s["injection"].append("document")
        self.s["docs"][item] = {"status": "uploaded", "filename": att["filename"], "invalid": chk["invalid"]}
        new = self.decide()
        head = f"Thanks, the {ITEM[item]} is on file." if not chk["invalid"] else \
            f"I received the {ITEM[item]}, but it doesn't meet the policy."
        return {"text": head + " Updated result: " + self.result_text(new), "decision": new, "card": {"type": "result", **new},
                "actions": [{"name": "update_review", "args": {"review_id": new["review_id"], "missing": new["missing_items"]}}],
                "prompt": self.prompt()}

    # ------------------------------------------------------------ for the side panel
    def view(self):
        a, d = self.s["answers"], self.s["decision"]
        details_done = all(f in a for f in TEXT_FIELDS)
        docs_done = details_done and all(f in self.s["docs"] for f in self.required_docs())
        steps = {"shop_details": "done" if details_done else "current",
                 "documents": "done" if docs_done else ("current" if details_done else "todo"),
                 "submitted": "done" if self.s["submitted"] else ("current" if docs_done else "todo")}
        return {"shop_name": a.get("shop_name"), "status": d["outcome"] if d else "DRAFT",
                "review_id": d["review_id"] if d else None, "steps": steps, "prompt": self.prompt(),
                "result": d}
