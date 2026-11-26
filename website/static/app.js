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
let loadToken = 0;        // a newer open or new shop ignores an older reply
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

// What the merchant reads for each error code (LLD-0002 §6). The server's own message is kept
// for the browser console: some codes carry developer detail that merchants should not see.
const ERROR_COPY = {
  corpus_unavailable: "The policy corpus did not answer, so this reply has no policy sentence. That also happens when its search model is down. Anything already saved is still saved. Here is where the application stands.",
  onb_unavailable: "ONB, which saves the application, did not answer. It may be waking up after sitting idle, or its connection failed. This message was not saved.",
  chat_unavailable: "The copilot did not answer. It sleeps on the free plan after about 15 minutes with no messages, or it may be down. This message was not sent.",
  history_unavailable: "The saved conversation could not be opened. Try again in a moment.",
  conversation_busy: "This conversation is busy, perhaps in another tab. Try again in a moment.",
  turn_interrupted: "The last message was interrupted before it finished. Your application was refreshed. Check the current step.",
  not_found: "That application isn't available. It may belong to another Google account.",
  message_id_reused: "That send was already used for a different message. Try again.",
  rate_limited: "Too many messages in one minute. Wait a minute and try again.",
  network: "This page could not reach the website. Check your connection and try again.",
};
// For these codes the server's message is already written for merchants.
const SHOW_SERVER_MESSAGE = new Set(["unsupported_type", "file_too_large", "empty_message", "no_file", "bad_document"]);
const RETRYABLE = new Set(["onb_unavailable", "chat_unavailable", "history_unavailable", "conversation_busy", "network"]);
const WAKE_MODAL = new Set(["chat_unavailable", "onb_unavailable", "corpus_unavailable", "history_unavailable", "network"]);
const WAKE_WORD = { asleep: "Asleep", waking: "Waking", up: "Awake", down: "Did not start" };
let wakeTimer = 0;
let wakeRetry = null;
let wakeReason = "";

class ApiError extends Error {
  constructor(status, code, serverMessage) {
    super(SHOW_SERVER_MESSAGE.has(code) && serverMessage ? serverMessage
      : ERROR_COPY[code] || "Something went wrong. Try again.");
    this.status = status;
    this.code = code;
    this.serverMessage = serverMessage;
  }
}

async function api(method, path, body, isForm) {
  const opts = { method, headers: { "X-IPE-Client": "web" } };
  if (body && !isForm) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  if (body && isForm) opts.body = body;
  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    throw new ApiError(0, "network", String(e));
  }
  const data = await res.json().catch(() => null);
  if (res.status === 401) {
    // The website has already signed this merchant out and stored a message for the sign-in page.
    location.replace("/");
    throw new ApiError(401, "sign_in_required", data && data.error && data.error.message);
  }
  if (!res.ok || !data) {
    const err = (data && data.error) || {};
    if (err.message) console.warn(`[iPe] ${res.status} ${err.code}: ${err.message}`);
    throw new ApiError(res.status, err.code || (data ? "bad_request" : "chat_unavailable"), err.message);
  }
  return data;
}

// One banner under the conversation, with an optional action button ("Try again").
function showError(msg, action) {
  const box = $("error");
  box.replaceChildren();
  box.hidden = !msg;
  if (!msg) return;
  box.append(el("span", "", msg));
  if (action) {
    const b = el("button", "link alert-action", action.label);
    b.type = "button";
    b.onclick = () => { showError(""); action.run(); };
    box.append(b);
  }
}

function wakeNames(services) {
  const list = services.map((service) => service.name);
  if (list.length < 2) return list.join("");
  return `${list.slice(0, -1).join(", ")} and ${list[list.length - 1]}`;
}

function wakeLine(services) {
  const waking = services.filter((service) => service.state === "waking");
  if (waking.length === 1) return `Still starting ${waking[0].name}.`;
  if (waking.length > 1) return `Still starting ${wakeNames(waking)}.`;
  if (services.some((service) => service.state === "down")) return "One service answered, and it did not start. Checking again.";
  return "Checking each service.";
}

function paintWake(body) {
  const wake = $("wake");
  for (const service of body.services || []) {
    const row = wake.querySelector(`[data-id="${service.id}"]`);
    if (!row) continue;
    row.dataset.state = service.state;
    row.querySelector("small").textContent = WAKE_WORD[service.state] || service.state;
  }
  if (body.ready) {
    $("wake-lead").textContent = "All three are up.";
    $("wake-note").textContent = `${wakeReason} They are answering again. Send the message once more, or sign out to open the start page.`;
    $("wake-retry").hidden = !wakeRetry;
    wake.setAttribute("aria-busy", "false");
    return true;
  }
  $("wake-lead").textContent = wakeLine(body.services || []);
  return false;
}

async function wakeTick() {
  try {
    const wake = $("wake");
    await holdHealth([wake.dataset.copilot, wake.dataset.onb, wake.dataset.corpus]);
    const response = await fetch("/ready", { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(String(response.status));
    if (paintWake(await response.json())) return;
  } catch (_error) {
    const services = [...$("wake").querySelectorAll("[data-id]")].map((row) => ({
      name: row.querySelector("b").textContent,
      state: row.dataset.state,
    }));
    $("wake-lead").textContent = services.some((service) => service.state !== "asleep")
      ? wakeLine(services)
      : "Still trying to reach the services.";
  }
  wakeTimer = window.setTimeout(wakeTick, 3000);
}

function openWake(message, retry) {
  window.clearTimeout(wakeTimer);
  wakeRetry = retry || null;
  wakeReason = message;
  $("wake-note").textContent = message;
  $("wake-lead").textContent = "Checking Copilot, ONB, and the policy corpus.";
  $("wake-retry").hidden = true;
  $("wake").setAttribute("aria-busy", "true");
  $("wake").querySelectorAll("[data-id]").forEach((row) => {
    row.dataset.state = "waking";
    row.querySelector("small").textContent = "Waking";
  });
  $("wake-modal").hidden = false;
  wakeTick();
}

$("wake-retry").onclick = () => {
  const retry = wakeRetry;
  window.clearTimeout(wakeTimer);
  wakeRetry = null;
  $("wake-modal").hidden = true;
  if (retry) retry();
};

function showFailure(e, retry) {
  if (e.code === "sign_in_required") return;           // the page is already going to sign-in
  if (WAKE_MODAL.has(e.code)) {
    showError("");
    openWake(e.message, retry);
    return;
  }
  showError(e.message, retry && RETRYABLE.has(e.code) ? { label: "Try again", run: retry } : null);
}

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

// ---------- keep the newest message in view ----------
// The visible area shrinks when the phone keyboard opens, the browser bar moves, buttons or a
// banner appear, or the window is resized. If the merchant was reading the end of the
// conversation, keep them there; if they had scrolled up to reread, leave them where they are.
let stickToEnd = true;
function nearEnd() { const t = $("thread"); return t.scrollHeight - t.scrollTop - t.clientHeight < 80; }
function scrollToEnd(force) {
  if (force) stickToEnd = true;
  if (!stickToEnd) return;
  requestAnimationFrame(() => { const t = $("thread"); t.scrollTop = t.scrollHeight; });
}
$("thread").addEventListener("scroll", () => { stickToEnd = nearEnd(); }, { passive: true });
new ResizeObserver(() => scrollToEnd()).observe($("thread"));
if (window.visualViewport) visualViewport.addEventListener("resize", () => scrollToEnd());
$("text").addEventListener("focus", () => { scrollToEnd(); setTimeout(() => scrollToEnd(), 350); });

function parkTyping() {
  const typing = $("typing");
  if (typing.parentElement === $("thread")) $("thread").after(typing);
}

function render(c) {
  conv = c;
  parkTyping();
  $("typing").hidden = true;
  $("shimmer").hidden = true;
  const thread = $("thread");
  thread.hidden = false;
  thread.replaceChildren();
  c.messages.forEach(drawMessage);
  drawSide(c);
  drawPrompt(c.prompt);
  thread.classList.remove("settle");
  void thread.offsetWidth;
  thread.classList.add("settle");
  scrollToEnd(true);
  highlightShop();
}

// A saved application: bone shapes in the window until its messages arrive.
function showLoading(id) {
  parkTyping();
  $("typing").hidden = true;
  $("thread").replaceChildren();
  $("thread").hidden = true;
  $("shimmer").hidden = false;
  $("pills").replaceChildren();
  $("composer").hidden = true;
  const known = id && shops.find((s) => s.id === id);
  $("shopName").textContent = known ? (known.shop_name || "New shop") : "…";
  $("headTitle").textContent = known ? (known.shop_name || "New application") : "Opening application";
  if (known && STATUS[known.status]) {
    const status = $("status");
    status.textContent = STATUS[known.status];
    status.className = "badge " + known.status;
  }
  $("reviewRow").hidden = true;
  document.querySelectorAll("#steps li").forEach((li) => { li.className = ""; });
  document.querySelectorAll("#shops a").forEach((a) => a.setAttribute("aria-current", id && a.dataset.id === id ? "page" : "false"));
}

function clearLoading() {
  $("shimmer").hidden = true;
  $("thread").hidden = false;
}

// ---------- waiting for a reply ----------
// The dots sit where the next message will appear, inside the window that is already on screen.
function setBusy(on) {
  busy = on;
  const typing = $("typing");
  typing.hidden = !on;
  if (on) {
    $("shimmer").hidden = true;
    $("thread").hidden = false;
    $("thread").append(typing);
    $("pills").replaceChildren();
    $("composer").hidden = true;
    scrollToEnd(true);
  } else {
    parkTyping();
  }
}

// ---------- sending ----------
// Each message carries a client_message_id. "Try again" sends the same id, so the copilot returns
// the stored reply instead of running the turn twice.
async function send({ text, action }, id) {
  if (busy || !conv) return;
  const messageId = id || newId();
  showError("");
  drawMessage({ role: "merchant", text: action === "submit" ? "Submit application" : text, attachments: [] });
  scrollToEnd(true);
  setBusy(true);
  try {
    const d = await api("POST", `/api/conversations/${conv.id}/messages`, { text, action: action || null, client_message_id: messageId });
    if (text && $("text").value === text) $("text").value = "";       // a retry went through
    setBusy(false); render(d.conversation); refreshShops();
  } catch (e) {
    setBusy(false);
    await afterFailure(e);
    if (text && !action && !$("text").value) $("text").value = text;   // don't lose what they typed
    showFailure(e, () => send({ text, action }, messageId));
  }
}

// After a failed turn, show the conversation as the server has it. corpus_unavailable and
// turn_interrupted can happen after ONB already saved the answer, so the screen must be reloaded.
async function afterFailure(e) {
  if (["corpus_unavailable", "turn_interrupted"].includes(e.code)) {
    try { const d = await api("GET", `/api/conversations/${conv.id}`); render(d.conversation); return; } catch (_) { /* fall through */ }
  }
  render(conv);
}

function pickFile(documentId) {
  uploadFor = documentId;
  $("file").accept = ACCEPT.join(",");
  $("file").click();
}

async function upload(file, documentId, id) {
  if (busy || !conv) return;
  const messageId = id || newId();
  showError("");
  drawMessage({ role: "merchant", text: "", attachments: [{ document: documentId, filename: file.name }] });
  scrollToEnd(true);
  setBusy(true);
  const fd = new FormData();
  fd.append("document", documentId);
  fd.append("client_message_id", messageId);
  fd.append("file", file);
  try {
    const d = await api("POST", `/api/conversations/${conv.id}/files`, fd, true);
    setBusy(false); render(d.conversation); refreshShops();
  } catch (e) {
    setBusy(false);
    await afterFailure(e);
    showFailure(e, () => upload(file, documentId, messageId));
  }
}

$("file").onchange = () => {
  const f = $("file").files[0];
  $("file").value = "";
  if (!f || !uploadFor || busy) return;
  if (!ACCEPT.some((ext) => f.name.toLowerCase().endsWith(ext))) { showError("Please choose a PDF, JPG or PNG file."); return; }
  if (f.size > 10 * 1024 * 1024) { showError("Files can be up to 10 MB."); return; }
  upload(f, uploadFor);
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

async function openConversation(id, push, attempt = 0, fallback = true) {
  if (busy) return false;
  showError("");
  const token = ++loadToken;
  const previous = conv;
  showLoading(id);
  try {
    const d = await api("GET", `/api/conversations/${id}`);
    if (token !== loadToken) return false;
    if (push) history.pushState({}, "", "/c/" + id);
    render(d.conversation);
    $("side").classList.remove("open");
    return true;
  } catch (e) {
    if (token !== loadToken) return false;
    if (e.code === "conversation_busy" && attempt < 2) {
      await new Promise((r) => setTimeout(r, 1500));
      if (token !== loadToken) return false;
      return openConversation(id, push, attempt + 1, fallback);
    }
    if (e.code === "not_found" && fallback) {
      // An old link, or another account's application: open this merchant's latest instead.
      const other = shops.find((s) => s.id !== id);
      if (other) { if (await openConversation(other.id, false, 0, false)) history.replaceState({}, "", "/c/" + other.id); }
      else if (!conv) { history.replaceState({}, "", "/"); await newShop(); }
      showFailure(e, () => openConversation(id, push));
      return false;
    }
    if (previous) render(previous);
    else clearLoading();
    showFailure(e, () => openConversation(id, push));
    return false;
  }
}

// Switch the window to a blank new shop first, then show the typing dots there.
// The dots must not play over the shop the merchant is leaving.
function beginNewShop() {
  showError("");
  parkTyping();
  $("shimmer").hidden = true;
  $("thread").hidden = false;
  $("thread").replaceChildren();
  $("shopName").textContent = "New shop";
  $("headTitle").textContent = "New application";
  const status = $("status");
  status.textContent = STATUS.DRAFT;
  status.className = "badge";
  $("reviewRow").hidden = true;
  document.querySelectorAll("#steps li").forEach((li) => { li.className = ""; });
  document.querySelectorAll("#shops a").forEach((a) => a.setAttribute("aria-current", "false"));
  setBusy(true);
}

async function newShop() {
  if (busy) return;
  const token = ++loadToken;
  const previous = conv;
  beginNewShop();
  try {
    const d = await api("POST", "/api/conversations");
    if (token !== loadToken) return;
    setBusy(false);
    history.pushState({}, "", "/c/" + d.conversation.id);
    render(d.conversation);
    refreshShops();
  } catch (e) {
    if (token !== loadToken) return;
    setBusy(false);
    if (previous) render(previous);
    else clearLoading();
    showFailure(e, newShop);
  }
}
$("newShop").onclick = newShop;
$("toggleSide").onclick = () => {
  const open = $("side").classList.toggle("open");
  $("toggleSide").setAttribute("aria-expanded", String(open));
};
const CID_PATH = /^\/c\/(c_[a-z0-9]{12}|app_[0-9a-f]{12})$/;
window.onpopstate = () => { const m = location.pathname.match(CID_PATH); if (m) openConversation(m[1], false); };

// ---------- start: a refresh shows the same conversation ----------
// The list comes from the copilot's stored history, newest first. A /c/<id> link opens that
// conversation; otherwise the most recent one opens; a merchant with none starts a new shop.
async function init() {
  showLoading(null);
  try {
    shops = (await api("GET", "/api/conversations")).conversations;
    drawShops();
    const m = location.pathname.match(CID_PATH);
    const id = m ? m[1] : shops.length ? shops[0].id : null;
    if (id) { await openConversation(id, !m); } else { await newShop(); }
  } catch (e) { clearLoading(); showFailure(e, init); }
}
init();
