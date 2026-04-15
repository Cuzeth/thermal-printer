/* Friends page — passkey ceremony + send-message form. */

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

// ---------- base64url helpers ----------

function b64urlToBuf(b64url) {
  const pad = "=".repeat((4 - (b64url.length % 4)) % 4);
  const b64 = (b64url + pad).replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(b64);
  const buf = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
  return buf.buffer;
}

function bufToB64url(buf) {
  const bytes = new Uint8Array(buf);
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// Convert WebAuthn options coming from the server (with base64url strings)
// into the ArrayBuffer-ish shape navigator.credentials.create expects.
function decodeRegisterOptions(opts) {
  const o = { ...opts };
  o.challenge = b64urlToBuf(o.challenge);
  o.user = { ...o.user, id: b64urlToBuf(o.user.id) };
  if (o.excludeCredentials) {
    o.excludeCredentials = o.excludeCredentials.map((c) => ({
      ...c,
      id: b64urlToBuf(c.id),
    }));
  }
  return o;
}

function decodeAuthOptions(opts) {
  const o = { ...opts };
  o.challenge = b64urlToBuf(o.challenge);
  if (o.allowCredentials) {
    o.allowCredentials = o.allowCredentials.map((c) => ({
      ...c,
      id: b64urlToBuf(c.id),
    }));
  }
  return o;
}

// PublicKeyCredential → JSON-friendly shape the server can verify.
function encodeRegisterCredential(cred) {
  const r = cred.response;
  return {
    id: cred.id,
    rawId: bufToB64url(cred.rawId),
    type: cred.type,
    authenticatorAttachment: cred.authenticatorAttachment ?? null,
    clientExtensionResults: cred.getClientExtensionResults?.() ?? {},
    response: {
      attestationObject: bufToB64url(r.attestationObject),
      clientDataJSON: bufToB64url(r.clientDataJSON),
      transports: typeof r.getTransports === "function" ? r.getTransports() : [],
    },
  };
}

function encodeAuthCredential(cred) {
  const r = cred.response;
  return {
    id: cred.id,
    rawId: bufToB64url(cred.rawId),
    type: cred.type,
    authenticatorAttachment: cred.authenticatorAttachment ?? null,
    clientExtensionResults: cred.getClientExtensionResults?.() ?? {},
    response: {
      authenticatorData: bufToB64url(r.authenticatorData),
      clientDataJSON: bufToB64url(r.clientDataJSON),
      signature: bufToB64url(r.signature),
      userHandle: r.userHandle ? bufToB64url(r.userHandle) : null,
    },
  };
}

// ---------- state machine ----------

function show(state) {
  STATES.forEach((s) => {
    const el = document.querySelector(`[data-state="${s}"]`);
    if (el) el.hidden = s !== state;
  });
}

let me = null;

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
  if (user.status === "allowed") return show("allowed");
  show("guest");
}

async function refreshMe() {
  const j = await getJSON("/api/m/me");
  applyMe(j.user);
}

// ---------- registration ----------

async function doRegister(username) {
  const begin = await postJSON("/api/m/auth/register/begin", { username });
  const opts = decodeRegisterOptions(begin.options);

  let cred;
  try {
    cred = await navigator.credentials.create({ publicKey: opts });
  } catch (e) {
    throw new Error("passkey creation cancelled or failed");
  }
  if (!cred) throw new Error("no credential returned");

  const finish = await postJSON("/api/m/auth/register/finish", encodeRegisterCredential(cred));
  applyMe(finish.user);
  toast("passkey saved — you're in the queue", "ok");
}

// ---------- login ----------

async function doLogin(username) {
  const begin = await postJSON("/api/m/auth/login/begin", { username });
  const opts = decodeAuthOptions(begin.options);

  let cred;
  try {
    cred = await navigator.credentials.get({ publicKey: opts });
  } catch (e) {
    throw new Error("passkey login cancelled or failed");
  }
  if (!cred) throw new Error("no credential returned");

  const finish = await postJSON("/api/m/auth/login/finish", encodeAuthCredential(cred));
  applyMe(finish.user);
  toast("signed in", "ok");
}

// ---------- send message ----------

const recents = [];
function pushRecent(body) {
  recents.unshift({ body, at: new Date() });
  if (recents.length > 5) recents.pop();
  const list = $("#recents-list");
  const items = recents.map((r) => {
    const li = document.createElement("li");
    li.textContent = r.body;
    return li;
  });
  list.replaceChildren(...items);
  $("#recents").hidden = recents.length === 0;
}

async function sendMessage() {
  const body = $("#msg-body").value.trim();
  if (!body) return;
  await postJSON("/api/m/print", { body });
  pushRecent(body);
  $("#msg-body").value = "";
  $("#msg-count").textContent = "0 / 800";
  // little flash to telegraph "it printed"
  const flash = document.createElement("div");
  flash.className = "printed-flash";
  document.body.appendChild(flash);
  setTimeout(() => flash.remove(), 600);
  toast("printed", "ok");
}

// ---------- wiring ----------

$("#go-register").addEventListener("click", () => show("register"));
$("#go-login").addEventListener("click", () => show("login"));
document.querySelectorAll("[data-back]").forEach((b) =>
  b.addEventListener("click", () => show("guest"))
);

$("#register-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const u = $("#reg-username").value.trim();
  const btn = e.submitter || e.target.querySelector("button[type=submit]");
  btn.disabled = true;
  try {
    await doRegister(u);
  } catch (err) {
    toast(err.message, "err");
  } finally {
    btn.disabled = false;
  }
});

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const u = $("#login-username").value.trim();
  const btn = e.submitter || e.target.querySelector("button[type=submit]");
  btn.disabled = true;
  try {
    await doLogin(u);
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
});

$("#recheck").addEventListener("click", () => {
  refreshMe().catch((e) => toast(e.message, "err"));
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

// Replace the loading card with a fallback message if WebAuthn is unavailable.
function buildUnsupportedFallback() {
  const card = document.querySelector('[data-state="loading"]');
  if (!card) return;
  const h = document.createElement("h1");
  h.textContent = "browser missing passkey support";
  const p = document.createElement("p");
  p.className = "dim";
  p.textContent = "use a recent version of Chrome, Safari, Firefox, or Edge.";
  card.replaceChildren(h, p);
}

if (!window.PublicKeyCredential) {
  buildUnsupportedFallback();
} else {
  refreshMe().catch((e) => {
    toast("couldn't reach the server: " + e.message, "err");
    show("guest");
  });
}
