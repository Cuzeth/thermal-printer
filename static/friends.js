/* Friends page — username + password login + send-message form. */

const $ = (sel) => document.querySelector(sel);

const STATES = ["loading", "guest", "register", "login", "pending", "blocked", "allowed"];

// ---------- toast ----------

const toastEl = $("#toast");
let toastTimer = null;
function toast(msg, kind = "ok") {
  toastEl.textContent = msg;
  toastEl.className = "toast " + kind;
  toastEl.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toastEl.hidden = true;
  }, 2800);
}

async function postJSON(url, data) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(data || {}),
  });
  const j = await r.json().catch(() => ({ ok: false, error: "bad JSON" }));
  if (!r.ok || !j.ok) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
}

async function getJSON(url) {
  const r = await fetch(url, { credentials: "same-origin" });
  return await r.json();
}

// ---------- state machine ----------

function show(state) {
  STATES.forEach((s) => {
    const el = document.querySelector(`[data-state="${s}"]`);
    if (el) el.hidden = s !== state;
  });
}

let me = null;

// Name-style options mirrored from auth.db.VALID_NAME_STYLES. The `preview`
// field is CSS-side flavor text — it's not the thermal-printer rendering,
// just a visual hint so the friend knows what they're picking.
const NAME_STYLES = [
  { key: "plain",  label: "plain",      preview: "from you" },
  { key: "big",    label: "big",        preview: "FROM YOU" },
  { key: "caps",   label: "caps",       preview: "FROM YOU" },
  { key: "serif",  label: "serif",      preview: "from you" },
  { key: "script", label: "script",     preview: "from you" },
  { key: "gothic", label: "papyrus",    preview: "from you" },
  { key: "mono",   label: "typewriter", preview: "from you" },
];

function applyMe(user) {
  me = user;
  const who = $("#who");
  if (user) {
    who.hidden = false;
    who.querySelector(".who-name").textContent = user.username;
  } else {
    who.hidden = true;
  }
  if (!user) return show("guest");
  if (user.status === "pending") {
    $("#pending-name").textContent = user.username;
    return show("pending");
  }
  if (user.status === "blocked") return show("blocked");
  if (user.status === "allowed") {
    show("allowed");
    renderStylePicker(user.name_style || "plain");
    // Pull the persistent history now that we know who you are. Best-effort —
    // the form still works even if this fetch blows up.
    loadHistory().catch((err) => console.warn("history load failed:", err));
    return;
  }
  show("guest");
}

function renderStylePicker(current) {
  const grid = $("#name-style-grid");
  if (!grid) return;
  $("#settings-current").textContent = current;
  grid.replaceChildren(...NAME_STYLES.map((s) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "style-chip style-" + s.key + (s.key === current ? " active" : "");
    btn.dataset.style = s.key;
    const label = document.createElement("span");
    label.className = "style-chip-label";
    label.textContent = s.label;
    const preview = document.createElement("span");
    preview.className = "style-chip-preview";
    preview.textContent = s.preview;
    btn.append(label, preview);
    btn.addEventListener("click", () => saveNameStyle(s.key));
    return btn;
  }));
}

async function saveNameStyle(style) {
  try {
    const j = await postJSON("/api/m/settings", { name_style: style });
    applyMe(j.user);
    toast("style: " + style, "ok");
    // Refresh the preview so they see the new header font immediately.
    schedulePreview();
  } catch (err) {
    toast(err.message, "err");
  }
}

async function refreshMe() {
  const j = await getJSON("/api/m/me");
  applyMe(j.user);
}

// ---------- live preview ----------
//
// Debounce so we don't POST on every keystroke. A sequence counter guards
// against slow responses overwriting fresh ones when the user keeps typing.

let previewTimer = null;
let previewSeq = 0;
const PREVIEW_DEBOUNCE_MS = 250;

function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(updatePreview, PREVIEW_DEBOUNCE_MS);
}

function setPreviewPlaceholder(msg, kind = "placeholder") {
  const p = document.createElement("p");
  p.className = `preview-${kind}`;
  p.textContent = msg;
  $("#preview-paper").replaceChildren(p);
}

async function updatePreview() {
  const body = $("#msg-body").value;
  const anonymous = !!$("#msg-anon")?.checked;
  const seq = ++previewSeq;
  if (!body.trim()) {
    setPreviewPlaceholder("start typing to see how it'll print…");
    return;
  }
  try {
    const j = await postJSON("/api/m/preview", { body, anonymous });
    if (seq !== previewSeq) return; // stale — user kept typing
    const segments = j.segments || [];
    if (segments.length === 0) {
      setPreviewPlaceholder("empty");
      return;
    }
    const nodes = [];
    segments.forEach((url, i) => {
      const img = document.createElement("img");
      img.src = url;
      img.alt = "preview";
      img.className = "preview-img";
      nodes.push(img);
      if (i < segments.length - 1) {
        const cut = document.createElement("div");
        cut.className = "preview-cut";
        nodes.push(cut);
      }
    });
    $("#preview-paper").replaceChildren(...nodes);
  } catch (err) {
    if (seq !== previewSeq) return;
    setPreviewPlaceholder("preview failed: " + err.message, "error");
  }
}

// ---------- history (server-backed, persists across sessions) ----------
//
// SQLite returns "YYYY-MM-DD HH:MM:SS" in UTC; show the local short form.
function fmtWhen(iso) {
  if (!iso) return "";
  const d = new Date(iso.replace(" ", "T") + "Z");
  if (isNaN(d)) return iso;
  return d.toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

function historyItem(msg) {
  const li = document.createElement("li");
  li.className = "history-item";
  li.dataset.id = msg.id;

  const when = document.createElement("div");
  when.className = "history-when dim";
  when.textContent = fmtWhen(msg.printed_at);

  const body = document.createElement("pre");
  body.className = "history-body";
  body.textContent = msg.body;

  li.append(when, body);
  // Click-to-restore: drops the body back into the composer so the friend
  // can tweak + reprint without retyping. No auto-submit.
  li.addEventListener("click", () => {
    const ta = $("#msg-body");
    ta.value = msg.body;
    $("#msg-count").textContent = `${msg.body.length} / 800`;
    ta.focus();
    schedulePreview();
    ta.scrollIntoView({ behavior: "smooth", block: "center" });
  });
  return li;
}

async function loadHistory() {
  const list = $("#history-list");
  const empty = $("#history-empty");
  try {
    const j = await getJSON("/api/m/history?limit=50");
    if (!j.ok) throw new Error(j.error || "couldn't load history");
    const items = (j.messages || []).map(historyItem);
    list.replaceChildren(...items);
    empty.hidden = items.length > 0;
  } catch (err) {
    // Non-fatal: show a lightweight error row but leave the form usable.
    list.replaceChildren();
    empty.hidden = false;
    empty.textContent = "couldn't load history: " + err.message;
  }
}

// ---------- send message ----------

async function sendMessage() {
  const body = $("#msg-body").value.trim();
  if (!body) return;
  const anonymous = !!$("#msg-anon")?.checked;
  await postJSON("/api/m/print", { body, anonymous });
  $("#msg-body").value = "";
  $("#msg-count").textContent = "0 / 800";
  // Reset anon back to the default so it doesn't silently stick.
  if ($("#msg-anon")) $("#msg-anon").checked = false;
  setPreviewPlaceholder("start typing to see how it'll print…");
  const flash = document.createElement("div");
  flash.className = "printed-flash";
  document.body.appendChild(flash);
  setTimeout(() => flash.remove(), 600);
  toast("printed", "ok");
  // Pull the fresh row (+ its canonical server timestamp) into the list.
  loadHistory().catch((err) => console.warn("history refresh failed:", err));
}

// ---------- wiring ----------

$("#go-register").addEventListener("click", () => show("register"));
$("#go-login").addEventListener("click", () => show("login"));
document.querySelectorAll("[data-back]").forEach((b) =>
  b.addEventListener("click", () => show("guest"))
);

$("#register-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = $("#reg-username").value.trim();
  const password = $("#reg-password").value;
  const confirm = $("#reg-password-2").value;
  if (password !== confirm) {
    return toast("passwords don't match", "err");
  }
  const btn = e.submitter || e.target.querySelector("button[type=submit]");
  btn.disabled = true;
  try {
    const j = await postJSON("/api/m/auth/register", { username, password });
    applyMe(j.user);
    toast("account created — waiting for approval", "ok");
  } catch (err) {
    toast(err.message, "err");
  } finally {
    btn.disabled = false;
  }
});

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = $("#login-username").value.trim();
  const password = $("#login-password").value;
  const btn = e.submitter || e.target.querySelector("button[type=submit]");
  btn.disabled = true;
  try {
    const j = await postJSON("/api/m/auth/login", { username, password });
    applyMe(j.user);
    toast("signed in", "ok");
  } catch (err) {
    toast(err.message, "err");
  } finally {
    btn.disabled = false;
  }
});

$("#msg-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = e.submitter || e.target.querySelector("button[type=submit]");
  btn.disabled = true;
  try {
    await sendMessage();
  } catch (err) {
    toast(err.message, "err");
  } finally {
    btn.disabled = false;
  }
});

$("#msg-body").addEventListener("input", (e) => {
  $("#msg-count").textContent = `${e.target.value.length} / 800`;
  schedulePreview();
});

$("#msg-anon")?.addEventListener("change", schedulePreview);

$("#history-refresh").addEventListener("click", async () => {
  try {
    await loadHistory();
    toast("history refreshed", "ok");
  } catch (err) {
    toast(err.message, "err");
  }
});

$("#recheck").addEventListener("click", async () => {
  try {
    await refreshMe();
  } catch (err) {
    toast(err.message, "err");
  }
});

$("#logout").addEventListener("click", async () => {
  try {
    await postJSON("/api/m/auth/logout", {});
    me = null;
    applyMe(null);
  } catch (e) {
    toast(e.message, "err");
  }
});

refreshMe().catch((e) => {
  toast("couldn't reach the server: " + e.message, "err");
  show("guest");
});
