/* Friends page — username + password login + send-message form. */

const $ = (sel) => document.querySelector(sel);

const STATES = ["loading", "guest", "register", "login", "reset", "pending", "blocked", "allowed"];

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

async function postJSON(url, data, options = {}) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(data || {}),
    signal: options.signal,
  });
  const j = await r.json().catch(() => ({ ok: false, error: "bad server response" }));
  if (!r.ok || !j.ok) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
}

async function getJSON(url) {
  const r = await fetch(url, { credentials: "same-origin" });
  return await r.json();
}

// ---------- state machine ----------

function show(state) {
  // The header narrates the state machine from this hook. It must not be
  // data-state: the cards are looked up by that attribute just below.
  document.body.dataset.page = state;
  STATES.forEach((s) => {
    const el = document.querySelector(`[data-state="${s}"]`);
    if (el) el.hidden = s !== state;
  });
  if (state === "register") requestAnimationFrame(() => $("#reg-username")?.focus());
  if (state === "login") requestAnimationFrame(() => $("#login-username")?.focus());
  if (state === "reset") requestAnimationFrame(() => $("#reset-password")?.focus());
}

// An admin-minted forgot-password link lands here as /#reset=<token>. The
// token rides in the URL fragment on purpose: fragments never leave the
// browser, so the secret can't end up in tunnel or server access logs.
function resetTokenFromHash() {
  const m = location.hash.match(/^#reset=([A-Za-z0-9_\-]+)$/);
  return m ? m[1] : null;
}

let me = null;

// While the friend sits on the PENDING screen, quietly re-check every 30s
// so approval flips the page without them hammering "Check again".
let pendingTimer = null;
function setPendingPolling(on) {
  clearInterval(pendingTimer);
  pendingTimer = on
    ? setInterval(() => refreshMe().catch(() => {}), 30_000)
    : null;
}

// Soft "printer looks offline" banner, informational only — the queue
// still accepts prints while it's showing. Best-effort: any failure
// leaves the banner hidden rather than toasting an error at the friend.
async function refreshPrinterBanner() {
  const banner = $("#printer-banner");
  if (!banner) return;
  const j = await getJSON("/api/printer");
  banner.hidden = j.printer?.ok !== false;
}

// While on the ALLOWED screen, recheck every 60s so the banner clears
// itself if the printer comes back without the friend reloading.
let printerTimer = null;
function setPrinterPolling(on) {
  clearInterval(printerTimer);
  printerTimer = on
    ? setInterval(() => refreshPrinterBanner().catch(() => {}), 60_000)
    : null;
}

// Name-style options mirrored from auth.db.VALID_NAME_STYLES. The `preview`
// field is CSS-side flavor text — it's not the thermal-printer rendering,
// just a visual hint so the friend knows what they're picking.
const NAME_STYLES = [
  { key: "plain",  label: "plain",      preview: "your name" },
  { key: "big",    label: "big",        preview: "YOUR NAME" },
  { key: "caps",   label: "caps",       preview: "YOUR NAME" },
  { key: "serif",  label: "serif",      preview: "your name" },
  { key: "script", label: "script",     preview: "your name" },
  { key: "gothic", label: "papyrus",    preview: "your name" },
  { key: "mono",   label: "typewriter", preview: "your name" },
];

function applyMe(user) {
  me = user;
  clearTimeout(historyTimer);
  setPendingPolling(!!user && user.status === "pending");
  setPrinterPolling(!!user && user.status === "allowed");
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
    refreshPrinterBanner().catch(() => {});
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
    btn.setAttribute("aria-pressed", s.key === current ? "true" : "false");
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
    const j = await postJSON("/api/settings", { name_style: style });
    applyMe(j.user);
    toast(style + " selected", "ok");
    // Refresh the preview so they see the new header font immediately.
    schedulePreview();
    schedulePhotoPreview();
  } catch (err) {
    toast(err.message, "err");
  }
}

async function refreshMe() {
  const j = await getJSON("/api/me");
  applyMe(j.user);
}

// ---------- live preview ----------
//
// Debounce so we don't POST on every keystroke. A sequence counter guards
// against slow responses overwriting fresh ones when the user keeps typing.

let previewTimer = null;
let previewSeq = 0;
let previewAbort = null;
const PREVIEW_DEBOUNCE_MS = 250;

function schedulePreview() {
  clearTimeout(previewTimer);
  setPreviewProgress("typing", "pending");
  previewTimer = setTimeout(updatePreview, PREVIEW_DEBOUNCE_MS);
}

function setPreviewProgress(label, state = "idle") {
  const el = $("#preview-progress");
  if (!el) return;
  el.textContent = label;
  el.dataset.state = state;
  el.hidden = !label;
  $("#preview-paper")?.setAttribute("aria-busy", state === "loading" ? "true" : "false");
}

function setPreviewPlaceholder(msg, kind = "placeholder") {
  const p = document.createElement("p");
  p.className = `preview-${kind}`;
  p.textContent = msg;
  $("#preview-paper").replaceChildren(p);
  setPreviewProgress(kind === "error" ? "failed" : "", kind === "error" ? "error" : "idle");
}

async function updatePreview() {
  const body = $("#msg-body").value;
  const anonymous = !!$("#msg-anon")?.checked;
  const seq = ++previewSeq;
  if (!body.trim()) {
    previewAbort?.abort();
    setPreviewPlaceholder("preview appears here");
    return;
  }
  previewAbort?.abort();
  previewAbort = new AbortController();
  setPreviewProgress("rendering", "loading");
  try {
    const j = await postJSON("/api/preview", { body, anonymous }, { signal: previewAbort.signal });
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
      img.alt = segments.length > 1 ? `receipt preview, part ${i + 1} of ${segments.length}` : "receipt preview";
      img.className = "preview-img";
      img.decoding = "async";
      nodes.push(img);
      if (i < segments.length - 1) {
        const cut = document.createElement("div");
        cut.className = "preview-cut";
        nodes.push(cut);
      }
    });
    $("#preview-paper").replaceChildren(...nodes);
    setPreviewProgress("ready", "ready");
  } catch (err) {
    if (err.name === "AbortError") return;
    if (seq !== previewSeq) return;
    setPreviewPlaceholder("preview failed: " + err.message, "error");
  }
}

// ---------- history (server-backed, persists across sessions) ----------
//
// SQLite returns "YYYY-MM-DD HH:MM:SS" in UTC; show the local short form.
function fmtWhen(iso) {
  if (!iso) return "";
  const d = new Date(iso.endsWith("Z") ? iso : iso.replace(" ", "T") + "Z");
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

  // 'printed' is the boring default — only surface the exceptions.
  if (msg.status === "failed") {
    const badge = document.createElement("span");
    badge.className = "history-status failed";
    badge.textContent = "didn't print";
    when.appendChild(badge);
  } else if (msg.status === "queued") {
    const badge = document.createElement("span");
    badge.className = "history-status queued";
    badge.textContent = "queued";
    when.appendChild(badge);
  } else if (["scheduled", "cancelled"].includes(msg.status)) {
    const badge = document.createElement("span");
    badge.className = "history-status " + msg.status;
    badge.textContent = msg.status === "scheduled" ? "waiting" : "cancelled";
    when.appendChild(badge);
  }

  const body = document.createElement("pre");
  const isDoodle = msg.body === "drawing" || msg.body === "(doodle)";
  body.className = "history-body" + (isDoodle ? " history-doodle" : "");
  body.textContent = isDoodle
    ? (msg.has_drawing ? "drawing · tap to reuse" : "drawing · preview unavailable")
    : msg.body;

  li.append(when, body);
  if (msg.deliver_at) {
    const delivery = document.createElement("div");
    delivery.className = "history-delivery";
    const past = new Date(msg.deliver_at) <= new Date();
    delivery.textContent = `${msg.status === "scheduled" && past ? "due; waiting for queue" : "delivery"}: ${fmtDelivery(msg.deliver_at)}`;
    if (msg.requested_for && msg.requested_for !== msg.deliver_at) {
      delivery.textContent += ` · requested ${fmtDelivery(msg.requested_for)}`;
    }
    li.append(delivery);
  }
  if (msg.status === "scheduled") {
    // This row has a native cancel button and no restore handler or button
    // role. Enter/Space on cancel can never bubble into a reprint action.
    li.classList.add("history-capsule");
    body.textContent = isDoodle ? "drawing" : msg.body;
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "ghost tiny history-cancel";
    cancel.textContent = "cancel capsule";
    cancel.setAttribute("aria-label", `cancel capsule for ${fmtDelivery(msg.deliver_at)}`);
    cancel.addEventListener("click", async () => {
      cancel.disabled = true;
      cancel.textContent = "cancelling";
      try {
        await postJSON(`/api/history/${msg.id}/cancel`, {});
        toast("capsule cancelled");
      } catch (error) {
        toast(error.message, "err");
      } finally {
        await loadHistory();
        $("#history-refresh").focus();
      }
    });
    li.append(cancel);
    return li;
  }
  if (msg.kind === "photo") {
    body.textContent = msg.body + " · tap to reprint";
    li.tabIndex = 0;
    li.setAttribute("role", "button");
    li.setAttribute("aria-label", `reopen the photo strip from ${fmtWhen(msg.printed_at)}`);
    li.addEventListener("click", () => restorePhoto(msg));
    li.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        li.click();
      }
    });
    return li;
  }
  if (isDoodle) {
    if (msg.has_drawing) {
      li.tabIndex = 0;
      li.setAttribute("role", "button");
      li.setAttribute("aria-label", `reuse the drawing from ${fmtWhen(msg.printed_at)}`);
      li.addEventListener("click", () => restoreDrawing(msg));
      li.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          li.click();
        }
      });
    } else {
      // Rows created before drawing storage was added have no pixels to
      // restore. Keep the history entry honest and non-interactive.
      li.classList.add("history-doodle-row");
    }
    return li;
  }
  li.tabIndex = 0;
  li.setAttribute("role", "button");
  li.setAttribute("aria-label", `edit the receipt from ${fmtWhen(msg.printed_at)}`);
  // Click-to-restore: drops the body back into the composer so the friend
  // can tweak + reprint without retyping. No auto-submit.
  li.addEventListener("click", () => {
    setMode("write");
    const ta = $("#msg-body");
    ta.value = msg.body;
    $("#msg-count").textContent = `${msg.body.length} / 800`;
    ta.focus();
    schedulePreview();
    ta.scrollIntoView({ behavior: "smooth", block: "center" });
  });
  li.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      li.click();
    }
  });
  return li;
}

const HISTORY_EMPTY_DEFAULT = "no prints yet";
let historyTimer = null;

async function loadHistory() {
  clearTimeout(historyTimer);
  const list = $("#history-list");
  const empty = $("#history-empty");
  try {
    const j = await getJSON("/api/history?limit=50");
    if (!j.ok) throw new Error(j.error || "history failed");
    const items = (j.messages || []).map(historyItem);
    list.replaceChildren(...items);
    const count = $("#history-count");
    if (count) count.textContent = String(items.length);
    // Restore the default copy — a previous failed load may have replaced it.
    empty.textContent = HISTORY_EMPTY_DEFAULT;
    empty.hidden = items.length > 0;
    if ((j.messages || []).some((msg) => ["scheduled", "queued"].includes(msg.status))) {
      pollHistory();
    }
  } catch (err) {
    // Non-fatal: show a lightweight error row but leave the form usable.
    list.replaceChildren();
    empty.hidden = false;
    empty.textContent = "history failed: " + err.message;
  }
}

function pollHistory() {
  historyTimer = setTimeout(() => {
    if (me?.status !== "allowed") return;
    // Preserve keyboard focus while someone is choosing an archive action.
    if (document.activeElement?.closest(".history-item")) return pollHistory();
    loadHistory();
  }, 15_000);
}

// ---------- delivery choice (shared by all three composer modes) ----------

function fmtDelivery(iso) {
  return new Date(iso).toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric", hour: "numeric",
    minute: "2-digit", timeZoneName: "short",
  });
}

function localInputValue(date) {
  const pad = (part) => String(part).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function deliveryAt() {
  if ($("#delivery-mode").value === "now") return null;
  const value = $("#delivery-date").value;
  const date = new Date(value);
  if (!value || isNaN(date) || localInputValue(date) !== value) {
    throw new Error("choose a valid local date and time");
  }
  if (date <= new Date()) throw new Error("delivery must be in the future");
  if (date.getTime() > Date.now() + 365 * 86_400_000) throw new Error("choose a date within 365 days");
  return date.toISOString();
}

function updateDeliveryControls() {
  const later = $("#delivery-mode").value === "later";
  $("#delivery-date-label").hidden = !later;
  const date = $("#delivery-date");
  date.min = localInputValue(new Date(Date.now() + 60_000));
  date.max = localInputValue(new Date(Date.now() + 365 * 86_400_000));
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  let note = "send to the printer queue.";
  if (later) {
    note = `your time: ${zone}. Up to 365 days ahead; 10 capsules can wait at once.`;
    if (date.value) {
      try { note = `delivery: ${fmtDelivery(deliveryAt())} (${zone}). Cancel from your prints before it enters the queue.`; }
      catch (error) { note = error.message + ` (${zone})`; }
    }
  }
  $("#delivery-hint").textContent = note;
  $("#msg-form button[type=submit]").textContent = later ? "save capsule" : "print it";
  $("#doodle-send").textContent = later ? "save capsule" : "print it";
  if (typeof photoControls === "function") photoControls();
}

$("#delivery-mode").addEventListener("change", updateDeliveryControls);
$("#delivery-date").addEventListener("input", updateDeliveryControls);
updateDeliveryControls();

// ---------- send message ----------

// Shared success choreography for friend prints: flash the screen,
// toast an honest "queued" message using the server's `ahead` count, and
// refresh history so the new row (with its canonical server timestamp)
// shows up.
function celebrateQueued(j) {
  const card = document.querySelector('[data-state="allowed"]');
  card?.classList.remove("print-confirmed");
  requestAnimationFrame(() => card?.classList.add("print-confirmed"));
  setTimeout(() => card?.classList.remove("print-confirmed"), 700);
  const ahead = Number(j.ahead) || 0;
  toast(j.scheduled ? `${j.quiet_held ? "held for quiet hours" : "capsule saved"}: ${fmtDelivery(j.deliver_at)}`
    : ahead > 0 ? `queued: ${ahead} ahead` : "printing", "ok");
  $("#delivery-mode").value = "now";
  $("#delivery-date").value = "";
  updateDeliveryControls();
  loadHistory().catch((err) => console.warn("history refresh failed:", err));
  refreshPrinterBanner().catch(() => {});
}

async function sendMessage() {
  const body = $("#msg-body").value.trim();
  if (!body) return;
  const anonymous = !!$("#msg-anon")?.checked;
  const j = await postJSON("/api/print", { body, anonymous, deliver_at: deliveryAt() });
  $("#msg-body").value = "";
  $("#msg-count").textContent = "0 / 800";
  // Reset anon back to the default so it doesn't silently stick.
  if ($("#msg-anon")) $("#msg-anon").checked = false;
  setPreviewPlaceholder("preview appears here");
  celebrateQueued(j);
}

// ---------- doodle canvas ----------

let doodleCtx = null;
let doodleBrushSize = 8;
let doodleActions = [];
let doodleHistoryIndex = 0;

function doodleFillWhite() {
  const canvas = $("#doodle-canvas");
  doodleCtx.fillStyle = "#fff";
  doodleCtx.fillRect(0, 0, canvas.width, canvas.height);
}

function drawDoodleStroke(stroke) {
  const points = stroke.points;
  if (!points.length) return;
  doodleCtx.fillStyle = "#000";
  doodleCtx.strokeStyle = "#000";
  doodleCtx.lineWidth = stroke.size;
  doodleCtx.lineCap = "round";
  doodleCtx.lineJoin = "round";
  if (points.length === 1) {
    doodleCtx.beginPath();
    doodleCtx.arc(points[0][0], points[0][1], stroke.size / 2, 0, Math.PI * 2);
    doodleCtx.fill();
    return;
  }
  doodleCtx.beginPath();
  doodleCtx.moveTo(points[0][0], points[0][1]);
  for (let i = 1; i < points.length; i += 1) {
    doodleCtx.lineTo(points[i][0], points[i][1]);
  }
  doodleCtx.stroke();
}

function drawDoodleImage(image) {
  const canvas = $("#doodle-canvas");
  const scale = Math.min(canvas.width / image.width, canvas.height / image.height);
  const width = image.width * scale;
  const height = image.height * scale;
  doodleCtx.drawImage(
    image,
    (canvas.width - width) / 2,
    (canvas.height - height) / 2,
    width,
    height,
  );
}

function renderDoodleHistory() {
  doodleFillWhite();
  for (const action of doodleActions.slice(0, doodleHistoryIndex)) {
    if (action.kind === "clear") doodleFillWhite();
    else if (action.kind === "image") drawDoodleImage(action.image);
    else drawDoodleStroke(action);
  }
}

function updateDoodleHistoryControls() {
  $("#doodle-undo").disabled = doodleHistoryIndex === 0;
  $("#doodle-redo").disabled = doodleHistoryIndex === doodleActions.length;
}

function commitDoodleAction(action) {
  // A fresh stroke after undo starts a new timeline, just like a text editor.
  doodleActions = doodleActions.slice(0, doodleHistoryIndex);
  doodleActions.push(action);
  doodleHistoryIndex = doodleActions.length;
  updateDoodleHistoryControls();
}

function undoDoodle() {
  if (doodleHistoryIndex === 0) return;
  doodleHistoryIndex -= 1;
  renderDoodleHistory();
  updateDoodleHistoryControls();
}

function redoDoodle() {
  if (doodleHistoryIndex === doodleActions.length) return;
  doodleHistoryIndex += 1;
  renderDoodleHistory();
  updateDoodleHistoryControls();
}

function resetDoodle() {
  doodleActions = [];
  doodleHistoryIndex = 0;
  doodleFillWhite();
  updateDoodleHistoryControls();
}

function doodleHasInk() {
  let hasInk = false;
  for (const action of doodleActions.slice(0, doodleHistoryIndex)) {
    hasInk = action.kind === "clear" ? false : true;
  }
  return hasInk;
}

function loadDrawingImage(src) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("saved drawing could not be opened"));
    image.src = src;
  });
}

async function restoreDrawing(msg) {
  try {
    const j = await getJSON(`/api/history/${encodeURIComponent(msg.id)}/drawing`);
    if (!j.ok) throw new Error(j.error || "drawing failed to load");
    const image = await loadDrawingImage(j.image);
    setMode("draw");
    resetDoodle();
    drawDoodleImage(image);
    commitDoodleAction({ kind: "image", image });
    $("#doodle-canvas").scrollIntoView({ behavior: "smooth", block: "center" });
    toast("drawing restored", "ok");
  } catch (err) {
    toast(err.message, "err");
  }
}

function initDoodleCanvas() {
  const canvas = $("#doodle-canvas");
  if (!canvas || doodleCtx) return;
  doodleCtx = canvas.getContext("2d");
  resetDoodle();

  let activeStroke = null;
  let activePointerId = null;

  // CSS size can differ from the 576x576 backing store — scale client
  // coords into canvas space so strokes land under the pointer.
  function canvasPoint(e) {
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    return [(e.clientX - rect.left) * scaleX, (e.clientY - rect.top) * scaleY];
  }

  canvas.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    if (activeStroke) return;
    e.preventDefault();
    canvas.setPointerCapture(e.pointerId);
    activePointerId = e.pointerId;
    activeStroke = {
      kind: "stroke",
      size: doodleBrushSize,
      points: [canvasPoint(e)],
    };
    // A tap should make a dot, not disappear because there was no move.
    drawDoodleStroke(activeStroke);
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!activeStroke || e.pointerId !== activePointerId) return;
    const events = e.getCoalescedEvents ? e.getCoalescedEvents() : [e];
    for (const event of events) {
      const previous = activeStroke.points[activeStroke.points.length - 1];
      const point = canvasPoint(event);
      doodleCtx.beginPath();
      doodleCtx.moveTo(previous[0], previous[1]);
      doodleCtx.lineTo(point[0], point[1]);
      doodleCtx.strokeStyle = "#000";
      doodleCtx.lineWidth = activeStroke.size;
      doodleCtx.lineCap = "round";
      doodleCtx.lineJoin = "round";
      doodleCtx.stroke();
      activeStroke.points.push(point);
    }
  });
  const finishStroke = (e) => {
    if (!activeStroke || e.pointerId !== activePointerId) return;
    commitDoodleAction(activeStroke);
    activeStroke = null;
    activePointerId = null;
  };
  canvas.addEventListener("pointerup", finishStroke);
  canvas.addEventListener("pointercancel", finishStroke);
}

async function sendDoodle() {
  const canvas = $("#doodle-canvas");
  const anonymous = !!$("#doodle-anon")?.checked;
  const image = canvas.toDataURL("image/png");
  const j = await postJSON("/api/print/doodle", { image, anonymous, deliver_at: deliveryAt() });
  resetDoodle();
  if ($("#doodle-anon")) $("#doodle-anon").checked = false;
  celebrateQueued(j);
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
    const j = await postJSON("/api/auth/register", { username, password });
    applyMe(j.user);
    toast("account created", "ok");
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
    const j = await postJSON("/api/auth/login", { username, password });
    applyMe(j.user);
    toast("signed in", "ok");
  } catch (err) {
    toast(err.message, "err");
  } finally {
    btn.disabled = false;
  }
});

$("#reset-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const password = $("#reset-password").value;
  const confirm = $("#reset-password-2").value;
  if (password !== confirm) {
    return toast("passwords don't match", "err");
  }
  const btn = e.submitter || e.target.querySelector("button[type=submit]");
  btn.disabled = true;
  try {
    const j = await postJSON("/api/auth/reset", { token: resetTokenFromHash() || "", password });
    // Scrub the burnt token from the URL so a reload or a shared tab
    // doesn't reopen the form with a link that can never work again.
    history.replaceState(null, "", location.pathname);
    applyMe(j.user);
    toast("password saved", "ok");
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

// ---------- composer mode switch ----------

function setMode(mode) {
  const panels = { write: "#msg-form", draw: "#doodle-panel", photo: "#photo-panel" };
  Object.entries(panels).forEach(([key, selector]) => {
    const selected = key === mode;
    $(selector).hidden = !selected;
    const tab = $("#mode-" + key);
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });
  if (mode === "draw") initDoodleCanvas();
}

$("#mode-write").addEventListener("click", () => setMode("write"));
$("#mode-draw").addEventListener("click", () => setMode("draw"));
$("#mode-photo").addEventListener("click", () => setMode("photo"));
$("#mode-switch").addEventListener("keydown", (e) => {
  const modes = ["write", "draw", "photo"];
  const current = modes.findIndex((mode) => $("#mode-" + mode) === e.target);
  if (current < 0 || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
  e.preventDefault();
  const next = e.key === "Home" ? 0 : e.key === "End" ? modes.length - 1
    : (current + (e.key === "ArrowRight" ? 1 : -1) + modes.length) % modes.length;
  setMode(modes[next]);
  $("#mode-" + modes[next]).focus();
});
setMode("write");

document.querySelectorAll(".brush-size").forEach((button) => {
  button.addEventListener("click", () => {
    doodleBrushSize = Number(button.dataset.size);
    document.querySelectorAll(".brush-size").forEach((candidate) => {
      const selected = candidate === button;
      candidate.classList.toggle("active", selected);
      candidate.setAttribute("aria-pressed", selected ? "true" : "false");
    });
  });
});

$("#doodle-undo").addEventListener("click", undoDoodle);
$("#doodle-redo").addEventListener("click", redoDoodle);

$("#doodle-clear").addEventListener("click", () => {
  if (!doodleCtx || !doodleHasInk()) return;
  doodleFillWhite();
  commitDoodleAction({ kind: "clear" });
});

document.addEventListener("keydown", (e) => {
  if ($("#doodle-panel").hidden || !(e.metaKey || e.ctrlKey)) return;
  const key = e.key.toLowerCase();
  if (key === "z") {
    e.preventDefault();
    if (e.shiftKey) redoDoodle();
    else undoDoodle();
  } else if (key === "y") {
    e.preventDefault();
    redoDoodle();
  }
});

$("#doodle-send").addEventListener("click", async () => {
  const btn = $("#doodle-send");
  btn.disabled = true;
  try {
    await sendDoodle();
  } catch (err) {
    toast(err.message, "err");
  } finally {
    btn.disabled = false;
  }
});

$("#history-refresh").addEventListener("click", async () => {
  try {
    await loadHistory();
    toast("prints refreshed", "ok");
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
    await postJSON("/api/auth/logout", {});
    me = null;
    applyMe(null);
  } catch (e) {
    toast(e.message, "err");
  }
});

// A reset link takes priority over whatever session this browser holds —
// the friend clicked it because they can't get in, so send them straight
// to the new-password form instead of the usual signed-in/guest routing.
if (resetTokenFromHash()) {
  show("reset");
} else {
  refreshMe().catch((e) => {
    toast("server unavailable: " + e.message, "err");
    show("guest");
  });
}
