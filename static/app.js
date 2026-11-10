"use strict";
/* Personal AI Agent UI. Talks to the authenticated JSON API with a bearer token kept in localStorage.
 * Voice logic lives in voice.js (VoiceController). */
const $ = (id) => document.getElementById(id);

const prefs = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* storage unavailable */ } },
  del(k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } }
};
let token = prefs.get("agent_token", "");
let currentId = null;
let timer = null;
let tick = 0;
let activeTab = "chat";
let submitting = false;
let lastSubmit = { text: "", at: 0 };
let voiceRequestId = null;
const pendingSpeak = new Set();      // task ids submitted from this page, to be read aloud when finished
const spokenTasks = new Set();
const spokenApprovals = new Set();

async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || "GET",
    headers: Object.assign({ "Authorization": "Bearer " + token }, opts.body ? { "Content-Type": "application/json" } : {}),
    body: opts.body ? JSON.stringify(opts.body) : undefined
  });
  let data = {};
  try { data = await res.json(); } catch (e) { /* non-JSON body */ }
  if (res.status === 401) { showLogin("Token rejected."); throw new Error("unauthorized"); }
  if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
  return data;
}

function el(tag, props = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v);
  }
  for (const kid of kids) n.append(kid);
  return n;
}
const newId = () => (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : ("r" + Date.now() + Math.random().toString(16).slice(2));
const LABEL = { pending: "Pending", running: "Running", waiting_approval: "Needs your approval", completed_verified: "Done · verified", completed_unverified: "Done · not verified", failed: "Failed", cancelled: "Cancelled" };
const FINAL = ["completed_verified", "completed_unverified", "failed", "cancelled"];
const ACTIVE = ["pending", "running", "waiting_approval"];
const badge = (s, text) => el("span", { class: "badge s-" + s }, text || LABEL[s] || s);

/* ------------------------------------------------------------------ login */
function showLogin(msg) {
  $("app").classList.add("hidden"); $("login").classList.remove("hidden");
  $("loginErr").textContent = msg || ""; $("conn").textContent = "offline";
  clearInterval(timer);
}
async function showApp() {
  $("login").classList.add("hidden"); $("app").classList.remove("hidden");
  try { await api("/api/status"); $("conn").textContent = "online"; } catch (e) { return; }
  refreshApprovals(); startPolling();
}
$("loginBtn").onclick = async () => {
  token = $("tok").value.trim(); if (!token) return;
  try { await api("/api/status"); prefs.set("agent_token", token); showApp(); }
  catch (e) { if (e.message !== "unauthorized") $("loginErr").textContent = e.message; }
};
$("logout").onclick = () => { prefs.del("agent_token"); token = ""; voice.stopSpeaking(); voice.stop(); showLogin(""); };

/* ------------------------------------------------------------------ tabs */
const TABS = ["chat", "history", "memory", "phone", "about"];
function openTab(name) {
  activeTab = name;
  document.querySelectorAll("nav button").forEach((x) => x.classList.toggle("active", x.dataset.tab === name));
  TABS.forEach((t) => $("tab-" + t).classList.toggle("hidden", t !== name));
  if (name === "history") loadHistory();
  if (name === "memory") loadMemory();
  if (name === "phone") loadPhone();
  if (name === "about") loadAbout();
}
document.querySelectorAll("nav button").forEach((b) => { b.onclick = () => openTab(b.dataset.tab); });

/* ------------------------------------------------------------------ voice */
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const VOICE_LABEL = {
  idle: "Tap the microphone and speak", listening: "Listening…", processing: "Working on it…",
  speaking: "Speaking…", error: "Voice problem. See the message below."
};
const voice = new VoiceController({
  SpeechRecognition: SR,
  speechSynthesis: window.speechSynthesis,
  Utterance: window.SpeechSynthesisUtterance,
  lang: prefs.get("lang", "en-US"),
  onState: renderVoiceState,
  onInterim: (t) => setHeard(t, true),
  onFinal: handleFinal,
  onError: (code, message) => showVoiceMsg(message, true),
  onSpeakEnd: () => {}
});

function showVoiceMsg(msg, isError) {
  const n = $("voiceMsg"); n.textContent = msg || ""; n.className = isError ? "err" : "mute";
}
function setHeard(text, interim) {
  $("heard").textContent = text ? (interim ? "Hearing: " : "Heard: ") + text : "";
}
function renderVoiceState(state) {
  const mic = $("mic");
  mic.dataset.state = state;
  mic.setAttribute("aria-label", VOICE_LABEL[state]);
  mic.classList.toggle("live", state === "listening");
  $("voiceState").textContent = VOICE_LABEL[state];
  $("stopSpeak").disabled = state !== "speaking";
  $("retry").classList.toggle("hidden", state !== "error");
  if (state === "listening") showVoiceMsg("");
}

function handleFinal(text) {
  setHeard(text, false);
  $("req").value = text;
  voiceRequestId = newId();                       // reused if the same transcript is submitted twice
  if ($("autoSend").checked) {
    submit(text, true);
  } else {
    voice.done();
    showVoiceMsg("Check the text, then press Send.");
  }
}

async function submit(text, viaVoice) {
  text = (text || "").trim();
  if (!text) { voice.done(); return; }
  const now = Date.now();
  if (submitting || (text === lastSubmit.text && now - lastSubmit.at < 3000)) return;   // duplicate guard
  submitting = true; lastSubmit = { text, at: now };
  $("sendErr").textContent = "";
  try {
    const rid = (viaVoice && voiceRequestId) || newId();
    voiceRequestId = null;
    const r = await api("/api/tasks", { method: "POST", body: { request: text, client_request_id: rid } });
    $("req").value = "";
    currentId = r.id; pendingSpeak.add(r.id);
    renderCurrent();
  } catch (e) {
    $("sendErr").textContent = e.message;
    voice.done();
  } finally {
    submitting = false;
  }
}

function spokenTextFor(t) {
  if (t.status === "failed") return "The task failed. " + (t.error || "");
  if (t.status === "cancelled") return "The task was cancelled.";
  return t.result || "The task finished without a text answer.";
}
function speakIfWanted(text) {
  if ($("speakReplies").checked && voice.canSpeak) { if (!voice.speak(text)) voice.done(); }
  else voice.done();
}
function afterTaskUpdate(d) {
  const t = d.task;
  if (FINAL.includes(t.status) && pendingSpeak.has(t.id) && !spokenTasks.has(t.id)) {
    spokenTasks.add(t.id); pendingSpeak.delete(t.id);
    speakIfWanted(spokenTextFor(t));
  }
  for (const a of d.approvals || []) {
    if (!spokenApprovals.has(a.id) && pendingSpeak.has(t.id)) {
      spokenApprovals.add(a.id);
      speakIfWanted("I need your approval. " + a.description);
    }
  }
}

let lastMicTap = 0;
$("mic").onclick = () => {
  const now = Date.now();
  if (now - lastMicTap < 600) return;            // an accidental double-tap must not start and then cancel
  lastMicTap = now;
  if (voice.state === "listening") voice.stop(); else voice.start();
};
$("retry").onclick = () => { voice.start(); };
$("stopSpeak").onclick = () => { voice.stopSpeaking(); };
$("cancelBtn").onclick = async () => {
  voice.stop(); voice.stopSpeaking();
  if (currentId) { try { await api("/api/tasks/" + currentId + "/cancel", { method: "POST" }); } catch (e) { /* not active */ } }
  voice.done(); renderCurrent();
};
$("send").onclick = () => submit($("req").value, false);
$("lang").value = prefs.get("lang", "en-US");
$("lang").onchange = () => {
  voice.setLang($("lang").value); prefs.set("lang", $("lang").value);
  if (voice.hasVoiceFor($("lang").value) === false) showVoiceMsg("No installed voice for this language on this device. Replies may be read in another language or not at all. The text is always shown.", false);
};
$("autoSend").checked = prefs.get("autoSend", "1") === "1";
$("autoSend").onchange = () => prefs.set("autoSend", $("autoSend").checked ? "1" : "0");
$("speakReplies").checked = prefs.get("speak", "1") === "1";
$("speakReplies").onchange = () => { prefs.set("speak", $("speakReplies").checked ? "1" : "0"); if (!$("speakReplies").checked) voice.stopSpeaking(); };

if (!voice.canListen) {
  $("mic").classList.add("hidden");
  $("voiceState").textContent = "Text mode";
  showVoiceMsg(VoiceController.messageFor("unsupported"), false);
}
if (!voice.canSpeak) { $("speakRow").classList.add("hidden"); $("stopSpeak").classList.add("hidden"); }
renderVoiceState("idle");
if (!voice.canListen) $("voiceState").textContent = "Text mode";

/* ------------------------------------------------------------------ tasks */
async function renderCurrent() {
  if (!currentId) return;
  let d; try { d = await api("/api/tasks/" + currentId); } catch (e) { return; }
  const t = d.task, box = $("current"); box.replaceChildren();
  const card = el("div", { class: "card" });
  card.append(el("div", { class: "row" }, el("b", { class: "grow" }, "Task #" + t.id), badge(t.status)));
  card.append(el("div", { class: "mute" }, t.request));
  if (t.plan) card.append(el("p", { class: "mute" }, "Plan: " + t.plan));
  if (t.result) card.append(el("p", { class: "result" }, t.result));
  if (t.error) card.append(el("p", { class: "err" }, t.error));
  if (t.verification) card.append(el("p", { class: "mute" }, "Check: " + t.verification));
  const steps = el("details", {}, el("summary", { class: "mute" }, d.steps.length + " steps"));
  d.steps.forEach((s) => steps.append(el("div", { class: "step" + (s.kind === "error" ? " error" : "") }, s.summary)));
  card.append(steps); box.append(card);
  afterTaskUpdate(d);
}

async function refreshApprovals() {
  let d; try { d = await api("/api/approvals"); } catch (e) { return; }
  const box = $("approvals"); box.replaceChildren();
  d.approvals.forEach((a) => {
    const decide = async (decision) => { await api("/api/approvals/" + a.id, { method: "POST", body: { decision } }); refreshApprovals(); renderCurrent(); };
    box.append(el("div", { class: "card", style: "border-color:var(--warn)" },
      el("b", {}, "Approval needed (task #" + a.task_id + ")"),
      el("p", { class: "result" }, a.description),
      el("div", { class: "row" },
        el("button", { class: "primary grow", onclick: () => decide("approve") }, "Approve"),
        el("button", { class: "grow danger", onclick: () => decide("deny") }, "Deny"))));
  });
}
function startPolling() {
  clearInterval(timer);
  timer = setInterval(() => {
    tick++;
    renderCurrent(); refreshApprovals();
    if (activeTab === "phone" && tick % 3 === 0) loadPhone();
  }, 2000);
}

async function loadHistory() {
  const d = await api("/api/tasks"), box = $("hist"); box.replaceChildren();
  if (!d.tasks.length) box.append(el("p", { class: "mute" }, "No tasks yet."));
  d.tasks.forEach((t) => box.append(el("div", { class: "card item", onclick: () => { currentId = t.id; openTab("chat"); renderCurrent(); } },
    el("div", { class: "row" }, el("b", { class: "grow" }, "#" + t.id), badge(t.status)),
    el("div", { class: "mute" }, t.request.slice(0, 140)), el("div", { class: "mute" }, t.created_at))));
}
$("clearHist").onclick = async () => { if (confirm("Delete all finished tasks?")) { await api("/api/tasks", { method: "DELETE" }); loadHistory(); } };

/* ------------------------------------------------------------------ memory */
async function loadMemory() {
  const d = await api("/api/memory"), box = $("mem"); box.replaceChildren();
  if (!d.memory.length) box.append(el("p", { class: "mute" }, "Nothing saved."));
  d.memory.forEach((m) => box.append(el("div", { class: "card row" }, el("span", { class: "badge" }, m.kind), el("span", { class: "grow" }, m.text),
    el("button", { class: "danger", onclick: async () => { await api("/api/memory/" + m.id, { method: "DELETE" }); loadMemory(); } }, "✕"))));
}
$("memAdd").onclick = async () => {
  const t = $("memText").value.trim(); if (!t) return;
  try { await api("/api/memory", { method: "POST", body: { kind: "note", text: t } }); $("memText").value = ""; loadMemory(); }
  catch (e) { alert(e.message); }
};
$("memClear").onclick = async () => { if (confirm("Delete ALL saved memory?")) { await api("/api/memory", { method: "DELETE" }); loadMemory(); } };

/* ------------------------------------------------------------------ phone (Android companion) */
const DEV_BADGE = { active: "completed_verified", revoked: "failed", expired: "completed_unverified" };
const CMD_BADGE = { approved: "pending", running: "running", completed: "completed_verified", failed: "failed", cancelled: "cancelled", expired: "completed_unverified" };
async function loadPhone() {
  let d; try { d = await api("/api/devices"); } catch (e) { return; }
  const box = $("devices"); box.replaceChildren();
  if (!d.devices.length) box.append(el("p", { class: "mute" }, "No phone is paired yet."));
  d.devices.forEach((dev) => {
    const s = dev.state || {};
    const card = el("div", { class: "card" },
      el("div", { class: "row" }, el("b", { class: "grow" }, dev.name), badge(DEV_BADGE[dev.status], dev.status)),
      el("div", { class: "mute" }, "Last contact: " + (dev.last_seen || "never")),
      el("div", { class: "mute" }, "Notifications allowed: " + (s.notifications_enabled === undefined ? "unknown" : s.notifications_enabled) +
        " · reminders on phone: " + (s.pending_reminders === undefined ? "unknown" : s.pending_reminders)),
      el("div", { class: "mute" }, "Credential valid until: " + dev.expires_at));
    if (dev.status === "active") card.append(el("button", { class: "danger", onclick: async () => {
      if (confirm("Disconnect this phone? It will stop receiving commands.")) { await api("/api/devices/" + dev.id + "/revoke", { method: "POST" }); loadPhone(); }
    } }, "Disconnect this phone"));
    box.append(card);
  });
  const cm = $("commands"); cm.replaceChildren();
  d.commands.forEach((c) => {
    const row = el("div", { class: "card" }, el("div", { class: "row" }, el("b", { class: "grow" }, "#" + c.id + " " + c.action), badge(CMD_BADGE[c.status], c.status)));
    if (c.result) row.append(el("div", { class: "mute" }, c.result));
    if (c.status === "approved") row.append(el("button", { class: "danger", onclick: async () => { try { await api("/api/commands/" + c.id + "/cancel", { method: "POST" }); } catch (e) { alert(e.message); } loadPhone(); } }, "Cancel"));
    cm.append(row);
  });
}
$("pairBtn").onclick = async () => {
  try {
    const r = await api("/api/devices/pairing", { method: "POST" });
    $("pairOut").replaceChildren(
      el("div", { class: "card" },
        el("b", {}, "Pairing code"), el("div", { class: "code" }, r.code),
        el("p", { class: "mute" }, "Valid for " + r.valid_minutes + " minutes, works once. In the Android app enter this code and this server address:"),
        el("div", { class: "result" }, location.origin)));
  } catch (e) { $("pairOut").textContent = e.message; }
};

/* ------------------------------------------------------------------ about / self-check */
async function loadAbout() {
  const d = await api("/api/status"), box = $("about"); box.replaceChildren();
  const ICON = { error: "⛔ ", warn: "⚠️ ", info: "ℹ️ " };
  d.warnings.forEach((w) => box.append(el("div", { class: "card", style: w.level === "error" ? "border-color:var(--bad)" : w.level === "warn" ? "border-color:var(--warn)" : "" }, ICON[w.level] + w.message)));
  const out = el("p", { class: "result" });
  const runCheck = async (what, label) => {
    out.textContent = label + "…";
    try { const r = await api("/api/selfcheck", { method: "POST", body: { what } }); out.textContent = (r.ok ? "✅ " : "❌ ") + r.message; }
    catch (e) { out.textContent = "❌ " + e.message; }
  };
  box.append(el("div", { class: "card" }, el("b", {}, "Self-check"),
    el("p", { class: "mute" }, "The API test makes one tiny real model call (a few tokens of cost)."),
    el("div", { class: "row" },
      el("button", { class: "grow", onclick: () => runCheck("api", "Testing API") }, "Test API key"),
      el("button", { class: "grow", onclick: () => runCheck("search", "Testing search") }, "Test web search")), out));
  box.append(el("div", { class: "card" }, el("b", {}, "Model: "), d.model, el("br"), el("b", {}, "API key: "), d.api_key_configured ? "configured" : "NOT configured (tasks cannot run)"));
  box.append(el("div", { class: "card" }, el("b", {}, "Working tools"), el("p", { class: "mute" }, d.tools.join(", "))));
  const ni = el("div", { class: "card" }, el("b", {}, "Not implemented yet"));
  d.not_implemented.forEach((x) => ni.append(el("div", { class: "mute" }, "• " + x))); box.append(ni);
}

if (token) showApp(); else showLogin("");
