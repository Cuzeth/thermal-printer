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
  const j = await getJSON("/m/api/me");
  applyMe(j.user);
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
  await postJSON("/m/api/print", { body });
  pushRecent(body);
  $("#msg-body").value = "";
  $("#msg-count").textContent = "0 / 800";
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
  const username = $("#reg-username").value.trim();
  const password = $("#reg-password").value;
  const confirm = $("#reg-password-2").value;
  if (password !== confirm) {
    return toast("passwords don't match", "err");
  }
  const btn = e.submitter || e.target.querySelector("button[type=submit]");
  btn.disabled = true;
  try {
    const j = await postJSON("/m/api/auth/register", { username, password });
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
    const j = await postJSON("/m/api/auth/login", { username, password });
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
    await postJSON("/m/api/auth/logout", {});
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
