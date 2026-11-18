// iPe merchant onboarding: the page logic.
// The page only shows what the chat service sends back (conversation + prompt).
// It never decides anything about the application itself.

const STATUS = { DRAFT: "Draft", IN_REVIEW: "In review", RECOMMEND_GO_LIVE: "Recommend go-live", REFUSED: "Refused" };
const ROLE = { MERCHANT_RISK_LEAD: "Merchant Risk Lead", HEAD_OF_PAYMENTS: "Head of Payments" };
const ITEM = { business_registration: "Business registration", owner_id: "Owner's government ID", bank_proof: "Bank proof",
               shopfront_photo: "Shopfront photo", dpa_addendum: "Data-processing addendum" };
const OUTCOME = { IN_REVIEW: "Stays in review", RECOMMEND_GO_LIVE: "Go-live may be recommended", REFUSED: "Application refused" };
const ACCEPT = [".pdf", ".jpg", ".jpeg", ".png"];

const $ = (id) => document.getElementById(id);
let conv = null;          // the conversation currently on screen
let busy = false;
let uploadFor = null;     // which document the file picker is for

// ---------- small helpers ----------
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
function inr(n) { return "₹" + Number(n).toLocaleString("en-IN"); }
function newId() { return (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random()).slice(0, 36); }

async function api(method, path, body, isForm) {
  const opts = { method, headers: { "X-IPE-Client": "web" } };
  if (body && !isForm) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  if (body && isForm) opts.body = body;
  const res = await fetch(path, opts);
  if (res.status === 401) { location.href = "/"; throw new Error("Please sign in again."); }
  const data = await res.json().catch(() => ({ error: { message: "The server sent an unreadable reply." } }));
  if (!res.ok) throw new Error((data.error && data.error.message) || res.statusText);
  return data;
}

function showError(msg) { $("error").textContent = msg; $("error").hidden = !msg; }

// ---------- drawing the conversation ----------
function addCites(node, text) {
  const rx = /\(?(ONB-POL-\d{3}(?:\s*§\s*\d)?)\)?/g;
  let last = 0, m;
  while ((m = rx.exec(text))) {
    node.append(text.slice(last, m.index), el("span", "cite", m[1].replace(/\s*§\s*/, " §")));
    last = rx.lastIndex;
  }
  node.append(text.slice(last));
}

function summaryCard(card) {
  const c = el("div", "card");
  c.append(el("h3", "", "Your application"));
  const t = el("table");
  card.rows.forEach((r) => { const tr = el("tr"); tr.append(el("td", "", r.label), el("td", "", r.value || "—")); t.append(tr); });
  c.append(t);
  return c;
}

function resultCard(r) {
  const c = el("div", "card");
  const head = el("div", "result-head");
  head.append(el("span", "badge " + r.outcome, OUTCOME[r.outcome] || r.outcome));
  if (r.review_id) head.append(el("strong", "", r.review_id));
  c.append(head);
  const t = el("table");
  const row = (k, v) => { const tr = el("tr"); tr.append(el("td", "", k), el("td", "", v)); t.append(tr); };
  row("Reviewed by", r.assignee ? ROLE[r.assignee] : "No review (refused)");
  row("Monthly volume", inr(r.volume_inr));
  row("Still missing", r.missing_items.length ? r.missing_items.map((i) => ITEM[i] || i).join(", ") : "Nothing");
  row("Policy", (r.citations || []).join(", "));
  c.append(t);
  return c;
}

function drawMessage(m) {
  const thread = $("thread");
  if (m.card && m.card.type === "result") thread.append(resultCard(m.card));
  const b = el("div", "msg " + m.role);
  // a summary is shown as its card, so the bubble keeps only the question
  const text = m.card && m.card.type === "summary" ? "Here is your application. Please check it, then submit or change an answer." : m.text;
  if (m.role === "assistant") addCites(b, text); else b.append(text);
  (m.attachments || []).forEach((a) => {
    if (m.text) b.append(document.createElement("br"));
    b.append(el("span", "file", (ITEM[a.document] || a.document) + ": " + a.filename));
  });
  if (text || (m.attachments || []).length) thread.append(b);
  if (m.card && m.card.type === "summary") thread.append(summaryCard(m.card));
}

function drawSide(c) {
  $("shopName").textContent = c.shop_name || "New shop";
  $("headTitle").textContent = c.shop_name || "New application";
  const s = $("status");
  s.textContent = STATUS[c.status]; s.className = "badge " + c.status;
  $("reviewRow").hidden = !c.review_id;
  $("reviewId").textContent = c.review_id || "";
  document.querySelectorAll("#steps li").forEach((li) => { li.className = c.steps[li.dataset.step]; });
}

function drawPrompt(p) {
  const box = $("pills");
  box.replaceChildren();
  (p.pills || []).forEach((pill) => {
    const cls = pill.action === "submit" ? "pill primary" : pill.action === "upload" ? "pill upload" : "pill";
    const b = el("button", cls, pill.label);
    b.type = "button";
    b.onclick = () => {
      if (pill.action === "upload") pickFile(pill.document);
      else if (pill.action === "submit") send({ text: "", action: "submit" });
      else send({ text: pill.value || pill.label });
    };
    box.append(b);
  });
  $("composer").hidden = !p.input;
  $("text").placeholder = p.kind === "closed" ? "Ask about the result" : p.kind === "summary" ? "Submit, or say which answer to change" : "Type your answer";
}

function render(c) {
  conv = c;
  $("thread").replaceChildren();
  c.messages.forEach(drawMessage);
  drawSide(c);
  drawPrompt(c.prompt);
  $("thread").scrollTop = $("thread").scrollHeight;
  highlightShop();
}

// ---------- waiting for a reply ----------
function setBusy(on) {
  busy = on;
  $("typing").hidden = !on;
  if (on) { $("pills").replaceChildren(); $("composer").hidden = true; }
}

// ---------- sending ----------
async function send({ text, action }) {
  if (busy || !conv) return;
  showError("");
  drawMessage({ role: "merchant", text: action === "submit" ? "Submit application" : text, attachments: [] });
  $("thread").scrollTop = $("thread").scrollHeight;
  setBusy(true);
  try {
    const d = await api("POST", `/api/conversations/${conv.id}/messages`, { text, action: action || null, client_message_id: newId() });
    setBusy(false); render(d.conversation); refreshShops();
  } catch (e) {
    setBusy(false); render(conv); showError(e.message);
  }
}

function pickFile(documentId) {
  uploadFor = documentId;
  $("file").accept = ACCEPT.join(",");
  $("file").click();
}

$("file").onchange = async () => {
  const f = $("file").files[0];
  $("file").value = "";
  if (!f || !uploadFor || busy) return;
  if (!ACCEPT.some((ext) => f.name.toLowerCase().endsWith(ext))) { showError("Please choose a PDF, JPG or PNG file."); return; }
  if (f.size > 10 * 1024 * 1024) { showError("Files can be up to 10 MB."); return; }
  showError("");
  drawMessage({ role: "merchant", text: "", attachments: [{ document: uploadFor, filename: f.name }] });
  setBusy(true);
  const fd = new FormData();
  fd.append("document", uploadFor);
  fd.append("file", f);
  try {
    const d = await api("POST", `/api/conversations/${conv.id}/files`, fd, true);
    setBusy(false); render(d.conversation); refreshShops();
  } catch (e) {
    setBusy(false); render(conv); showError(e.message);
  }
};

$("composer").onsubmit = (e) => {
  e.preventDefault();
  const t = $("text").value.trim();
  if (!t) return;
  $("text").value = "";
  send({ text: t });
};

// ---------- shops list (one conversation per shop) ----------
let shops = [];
function highlightShop() {
  document.querySelectorAll("#shops a").forEach((a) => a.setAttribute("aria-current", conv && a.dataset.id === conv.id ? "page" : "false"));
}
function drawShops() {
  $("shops").replaceChildren(...shops.map((s) => {
    const li = el("li"); const a = el("a");
    a.href = "/c/" + s.id; a.dataset.id = s.id;
    a.append(el("span", "", s.shop_name || "New shop"), el("small", "", STATUS[s.status]));
    a.onclick = (e) => { e.preventDefault(); openConversation(s.id, true); };
    li.append(a); return li;
  }));
  highlightShop();
}
async function refreshShops() {
  try { shops = (await api("GET", "/api/conversations")).conversations; drawShops(); } catch (e) { /* the list can wait */ }
}

async function openConversation(id, push) {
  showError("");
  try {
    const d = await api("GET", `/api/conversations/${id}`);
    if (push) history.pushState({}, "", "/c/" + id);
    render(d.conversation);
    $("side").classList.remove("open");
  } catch (e) { showError(e.message); }
}

async function newShop() {
  if (busy) return;
  setBusy(true);
  try {
    const d = await api("POST", "/api/conversations");
    setBusy(false);
    history.pushState({}, "", "/c/" + d.conversation.id);
    render(d.conversation);
    refreshShops();
  } catch (e) { setBusy(false); showError(e.message); }
}
$("newShop").onclick = newShop;
$("toggleSide").onclick = () => {
  const open = $("side").classList.toggle("open");
  $("toggleSide").setAttribute("aria-expanded", String(open));
};
const CID_PATH = /^\/c\/(c_[a-z0-9]{12}|app_[0-9a-f]{12})$/;
window.onpopstate = () => { const m = location.pathname.match(CID_PATH); if (m) openConversation(m[1], false); };

// ---------- start: a refresh shows the same conversation ----------
(async function init() {
  try {
    shops = (await api("GET", "/api/conversations")).conversations;
    drawShops();
    const m = location.pathname.match(CID_PATH);
    const id = m ? m[1] : shops.length ? shops[0].id : null;
    if (id) { await openConversation(id, !m); } else { await newShop(); }
  } catch (e) { showError(e.message); }
})();

