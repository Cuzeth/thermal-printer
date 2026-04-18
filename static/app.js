// Thermal Printer Console — frontend glue

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

const state = {
  imageFile: null,
  lastPreview: null,
};

// ---------- tabs ----------

function activateTab(t, { focus = false } = {}) {
  const pane = t.dataset.tab;
  $$(".tab").forEach((x) => {
    const on = x === t;
    x.classList.toggle("active", on);
    x.setAttribute("aria-selected", on ? "true" : "false");
    x.tabIndex = on ? 0 : -1;
  });
  $$(".tabpane").forEach((p) => p.classList.toggle("active", p.dataset.pane === pane));
  if (focus) t.focus();
  if (pane !== "image") hidePreviewImage();
  if (pane === "compose") refreshComposePreview();
  if (pane === "hardware") { loadCodePages(); loadLedProtocols(); }
  if (pane === "console") loadCheatSheet();
  if (pane === "admin") refreshAdmin();
}

$$(".tab").forEach((t) => t.addEventListener("click", () => activateTab(t)));

// Arrow-key navigation per WAI-ARIA tabs pattern.
$(".tabs").addEventListener("keydown", (e) => {
  const tabs = $$(".tab");
  const i = tabs.indexOf(document.activeElement);
  if (i < 0) return;
  let next = i;
  if (e.key === "ArrowRight") next = (i + 1) % tabs.length;
  else if (e.key === "ArrowLeft") next = (i - 1 + tabs.length) % tabs.length;
  else if (e.key === "Home") next = 0;
  else if (e.key === "End") next = tabs.length - 1;
  else return;
  e.preventDefault();
  activateTab(tabs[next], { focus: true });
});

// ---------- toast ----------

function toast(msg, kind = "ok") {
  const el = $("#toast");
  el.textContent = msg;
  el.className = `toast ${kind}`;
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (el.hidden = true), 2600);
}

// ---------- api helper ----------

// The owner token doubles as the admin token. It's inlined in the page body
// by Flask and added to every request the main console makes.
const OWNER_TOKEN = document.body.dataset.adminToken || "";
const AUTH_HEADER = { Authorization: `Bearer ${OWNER_TOKEN}` };

async function apiFetch(url, init = {}) {
  const headers = { ...AUTH_HEADER, ...(init.headers || {}) };
  const r = await fetch(url, { ...init, headers });
  const j = await r.json().catch(() => ({ ok: false, error: "bad JSON" }));
  if (!j.ok) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
}

async function postJSON(url, data) {
  return apiFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data || {}),
  });
}

async function postForm(url, formData) {
  return apiFetch(url, { method: "POST", body: formData });
}

async function getJSON(url) {
  return apiFetch(url, { method: "GET" });
}

async function guard(fn, okMsg = "sent to printer") {
  try {
    await fn();
    toast(okMsg, "ok");
  } catch (e) {
    toast(e.message, "err");
  }
}

// ---------- preview ----------

function showPreviewText(text) {
  $("#preview-out").textContent = text;
  hidePreviewImage();
}
function showPreviewImage(url) {
  const wrap = $("#preview-image-wrap");
  // Replace children: one <img> per segment, separated by tear-lines.
  while (wrap.firstChild) wrap.removeChild(wrap.firstChild);
  const urls = Array.isArray(url) ? url : [url];
  urls.forEach((u, i) => {
    const img = document.createElement("img");
    img.src = u;
    img.alt = "preview";
    wrap.appendChild(img);
    if (i < urls.length - 1) {
      const sep = document.createElement("div");
      sep.className = "segcut";
      wrap.appendChild(sep);
    }
  });
  wrap.hidden = false;
  $("#preview-out").textContent = "";
}
function hidePreviewImage() {
  const wrap = $("#preview-image-wrap");
  wrap.hidden = true;
  while (wrap.firstChild) wrap.removeChild(wrap.firstChild);
}

async function refreshComposePreview() {
  const body = $("#compose-body").value;
  const rich = $("#compose-rich").checked;
  try {
    if (rich) {
      const { segments } = await postJSON("/api/preview/rich", { body });
      showPreviewImage(segments);
    } else {
      const { preview } = await postJSON("/api/preview", { body });
      showPreviewText(preview);
    }
  } catch (e) {
    showPreviewText("(preview failed: " + e.message + ")");
  }
}

// ---------- compose ----------

let composeT;
$("#compose-body").addEventListener("input", () => {
  clearTimeout(composeT);
  composeT = setTimeout(refreshComposePreview, 180);
});
$("#compose-preview").addEventListener("click", refreshComposePreview);
$("#compose-rich").addEventListener("change", refreshComposePreview);
$("#compose-print").addEventListener("click", () => {
  guard(async () => {
    await postJSON("/api/print/text", {
      body: $("#compose-body").value,
      cut: $("#compose-cut").checked,
      rich: $("#compose-rich").checked,
    });
  }, "printing…");
});
refreshComposePreview();

// ---------- image ----------

const drop = $("#drop");
const fileInput = $("#image-file");

drop.addEventListener("click", () => fileInput.click());
["dragenter", "dragover"].forEach((evt) =>
  drop.addEventListener(evt, (e) => {
    e.preventDefault();
    drop.classList.add("drag");
  })
);
["dragleave", "drop"].forEach((evt) =>
  drop.addEventListener(evt, (e) => {
    e.preventDefault();
    drop.classList.remove("drag");
  })
);
drop.addEventListener("drop", (e) => {
  const f = e.dataTransfer.files && e.dataTransfer.files[0];
  if (f) onImageChosen(f);
});
fileInput.addEventListener("change", (e) => {
  const f = e.target.files[0];
  if (f) onImageChosen(f);
});

function onImageChosen(f) {
  state.imageFile = f;
  $("#image-print").disabled = false;
  $(".drop-inner strong").textContent = f.name;
  refreshImagePreview();
}

["contrast", "brightness", "threshold"].forEach((k) => {
  const s = $(`#img-${k}`);
  const v = $(`#img-${k}-v`);
  s.addEventListener("input", () => {
    v.textContent = s.value;
    debouncedPreview();
  });
});
$("#img-mode").addEventListener("change", refreshImagePreview);
$("#img-width").addEventListener("change", refreshImagePreview);
$("#img-invert").addEventListener("change", refreshImagePreview);
$("#image-refresh").addEventListener("click", refreshImagePreview);

let imgT;
function debouncedPreview() {
  clearTimeout(imgT);
  imgT = setTimeout(refreshImagePreview, 150);
}

async function refreshImagePreview() {
  if (!state.imageFile) return;
  const fd = buildImageForm();
  try {
    const { data_url } = await postForm("/api/image/preview", fd);
    showPreviewImage(data_url);
  } catch (e) {
    toast(e.message, "err");
  }
}

function buildImageForm() {
  const fd = new FormData();
  fd.append("file", state.imageFile);
  fd.append("mode", $("#img-mode").value);
  fd.append("width", $("#img-width").value);
  fd.append("contrast", $("#img-contrast").value);
  fd.append("brightness", $("#img-brightness").value);
  fd.append("threshold", $("#img-threshold").value);
  fd.append("invert", $("#img-invert").checked ? "true" : "false");
  fd.append("caption", $("#img-caption").value);
  return fd;
}

$("#image-print").addEventListener("click", () => {
  if (!state.imageFile) return;
  guard(async () => {
    await postForm("/api/print/image", buildImageForm());
  }, "printing…");
});

// ---------- widgets ----------

// Default countdown to 30 days out so the date field isn't empty on load.
(() => {
  const d = $("#w-cd-date");
  if (d && !d.value) {
    const t = new Date();
    t.setDate(t.getDate() + 30);
    d.value = t.toISOString().slice(0, 10);
  }
})();

// Live number-of-items sliders.
["hn", "otd"].forEach((k) => {
  const s = $(`#w-${k}-count`);
  const v = $(`#w-${k}-v`);
  if (s && v) s.addEventListener("input", () => (v.textContent = s.value));
});

$$("button[data-widget]").forEach((b) => {
  b.addEventListener("click", () => {
    const kind = b.dataset.widget;
    guard(async () => {
      if (kind === "weather") {
        await postJSON("/api/print/weather", {
          location: $("#w-loc").value,
          days: Number(b.dataset.days || 1),
        });
      } else if (kind === "dice") {
        await postJSON("/api/print/dice", {
          count: Number($("#w-dice-count").value),
          sides: Number($("#w-dice-sides").value),
          mode: b.dataset.mode || "standard",
        });
      } else if (kind === "ascii") {
        await postJSON("/api/print/ascii", { name: $("#w-ascii").value });
      } else if (kind === "briefing") {
        await postJSON("/api/print/briefing", {
          location: $("#w-brief-loc").value,
        });
      } else if (kind === "hn") {
        await postJSON("/api/print/hn", {
          count: Number($("#w-hn-count").value),
        });
      } else if (kind === "onthisday") {
        await postJSON("/api/print/onthisday", {
          count: Number($("#w-otd-count").value),
        });
      } else if (kind === "calendar") {
        await postJSON("/api/print/calendar", {
          year: Number($("#w-cal-year").value) || null,
          month: Number($("#w-cal-month").value) || null,
        });
      } else if (kind === "countdown") {
        await postJSON("/api/print/countdown", {
          label: $("#w-cd-label").value,
          date: $("#w-cd-date").value,
        });
      } else if (kind === "habits") {
        const habits = $("#w-habits").value.split("\n")
          .map((s) => s.trim()).filter(Boolean);
        await postJSON("/api/print/habits", { habits });
      } else {
        await postJSON(`/api/print/${kind}`, {});
      }
    }, kind === "briefing" ? "printing briefing…" : "printing…");
  });
});

// ---------- labs ----------

$$("button[data-lab]").forEach((b) => {
  b.addEventListener("click", () => {
    const kind = b.dataset.lab;
    guard(async () => {
      if (kind === "todo") {
        const items = $("#todo-items").value.split("\n");
        await postJSON("/api/print/todo", {
          title: $("#todo-title").value,
          items,
        });
      } else if (kind === "label") {
        await postJSON("/api/print/label", {
          text: $("#label-text").value,
          big: $("#label-big").checked,
        });
      } else if (kind === "receipt") {
        const items = $$(".ritem").map((row) => ({
          name: $(".r-name", row).value,
          qty: Number($(".r-qty", row).value) || 1,
          price: Number($(".r-price", row).value) || 0,
        })).filter((i) => i.name.trim());
        await postJSON("/api/print/receipt", {
          store: $("#r-store").value,
          items,
          tax_rate: Number($("#r-tax").value) || 0,
          note: $("#r-note").value,
        });
      }
    }, "printing…");
  });
});

function makeReceiptRow() {
  const row = document.createElement("div");
  row.className = "ritem";

  const name = document.createElement("input");
  name.className = "r-name";
  name.placeholder = "item";

  const qty = document.createElement("input");
  qty.className = "r-qty";
  qty.type = "number";
  qty.value = "1";
  qty.min = "1";

  const price = document.createElement("input");
  price.className = "r-price";
  price.type = "number";
  price.value = "0";
  price.step = "0.01";
  price.min = "0";

  const del = document.createElement("button");
  del.className = "ghost r-del";
  del.textContent = "\u00d7";

  row.append(name, qty, price, del);
  return row;
}

$("#r-add").addEventListener("click", () => {
  $("#r-items").appendChild(makeReceiptRow());
});
$("#r-items").addEventListener("click", (e) => {
  if (e.target.classList.contains("r-del")) {
    const rows = $$(".ritem");
    if (rows.length > 1) e.target.closest(".ritem").remove();
  }
});

// ---------- codes (QR + barcodes) ----------

const qrCtrl = {
  size: $("#qr-size"),
  sizeV: $("#qr-size-v"),
  data: $("#qr-data"),
  ec: $("#qr-ec"),
};
qrCtrl.size.addEventListener("input", () => {
  qrCtrl.sizeV.textContent = qrCtrl.size.value;
  debouncedQr();
});
qrCtrl.data.addEventListener("input", debouncedQr);
qrCtrl.ec.addEventListener("change", debouncedQr);
$("#qr-refresh").addEventListener("click", refreshQrPreview);
$("#qr-print").addEventListener("click", () => {
  guard(async () => {
    await postJSON("/api/print/qr", {
      data: qrCtrl.data.value,
      ec: qrCtrl.ec.value,
      size: Number(qrCtrl.size.value),
    });
  }, "printing QR…");
});

let qrT;
function debouncedQr() {
  clearTimeout(qrT);
  qrT = setTimeout(refreshQrPreview, 200);
}
async function refreshQrPreview() {
  if (!qrCtrl.data.value.trim()) return;
  try {
    const { data_url } = await postJSON("/api/code/qr/preview", {
      data: qrCtrl.data.value,
      ec: qrCtrl.ec.value,
      size: Number(qrCtrl.size.value),
      box_size: 10,
    });
    showPreviewImage(data_url);
  } catch (e) {
    toast(e.message, "err");
  }
}

const bcCtrl = {
  kind: $("#bc-kind"),
  data: $("#bc-data"),
  width: $("#bc-width"),
  widthV: $("#bc-width-v"),
  height: $("#bc-height"),
  heightV: $("#bc-height-v"),
  hri: $("#bc-hri"),
  font: $("#bc-font"),
};
["width", "height"].forEach((k) => {
  bcCtrl[k].addEventListener("input", () => {
    bcCtrl[k + "V"].textContent = bcCtrl[k].value;
    debouncedBc();
  });
});
[bcCtrl.kind, bcCtrl.data, bcCtrl.hri, bcCtrl.font].forEach((el) =>
  el.addEventListener("input", debouncedBc)
);
$("#bc-refresh").addEventListener("click", refreshBcPreview);
$("#bc-print").addEventListener("click", () => {
  guard(async () => {
    await postJSON("/api/print/barcode", buildBcBody());
  }, "printing barcode…");
});

let bcT;
function debouncedBc() {
  clearTimeout(bcT);
  bcT = setTimeout(refreshBcPreview, 200);
}
function buildBcBody() {
  return {
    kind: bcCtrl.kind.value,
    data: bcCtrl.data.value,
    width: Number(bcCtrl.width.value),
    height: Number(bcCtrl.height.value),
    hri: bcCtrl.hri.value,
    font: bcCtrl.font.value,
  };
}
async function refreshBcPreview() {
  if (!bcCtrl.data.value.trim()) return;
  try {
    const { data_url } = await postJSON("/api/code/barcode/preview", buildBcBody());
    showPreviewImage(data_url);
  } catch (e) {
    toast(e.message, "err");
  }
}

// ---------- hardware ----------

$$("button[data-hw]").forEach((b) => {
  b.addEventListener("click", () => {
    const kind = b.dataset.hw;
    guard(async () => {
      if (kind === "cash_drawer") {
        await postJSON("/api/hw/cash_drawer", { pin: Number(b.dataset.pin) });
      } else if (kind === "beep") {
        await postJSON("/api/hw/beep", {
          count: Number($("#hw-beep-count").value),
          duration_units: Number($("#hw-beep-dur").value),
        });
      } else if (kind === "feed") {
        await postJSON("/api/hw/feed", { lines: Number($("#hw-feed").value) });
      } else if (kind === "cut") {
        await postJSON("/api/hw/cut", {
          partial: b.dataset.partial === "true",
        });
      } else if (kind === "density") {
        await postJSON("/api/hw/density", { level: Number($("#hw-dens").value) });
      } else if (kind === "codepage") {
        await postJSON("/api/hw/codepage", { n: Number($("#hw-cp").value) });
      } else if (kind === "reset") {
        await postJSON("/api/hw/reset", {});
      } else if (kind === "self_test") {
        await postJSON("/api/hw/self_test", {});
      } else if (kind === "status") {
        const j = await postJSON("/api/hw/status", {});
        renderStatus(j.statuses);
        return; // custom toast
      }
    }, kind === "cut" ? "cutting…" :
       kind === "feed" ? "feeding…" :
       kind === "cash_drawer" ? "kicking drawer…" :
       kind === "beep" ? "beep!" :
       kind === "self_test" ? "running self-test…" :
       kind === "reset" ? "reset sent" :
       "applied");
  });
});

// slider labels
$("#hw-feed").addEventListener("input", (e) => {
  $("#hw-feed-v").textContent = e.target.value;
});
$("#hw-dens").addEventListener("input", (e) => {
  $("#hw-dens-v").textContent = e.target.value;
});

function renderStatus(rows) {
  const el = $("#hw-status-out");
  const lines = rows.map((r) => {
    let out = `[${r.mode}] ${r.label}: `;
    if (r.raw === null) {
      out += "no response";
    } else {
      out += `0x${r.raw.toString(16).padStart(2, "0")}`;
      if (r.flags) {
        const set = Object.entries(r.flags).filter(([, v]) => v).map(([k]) => k);
        if (set.length) out += "  ⚠ " + set.join(", ");
      }
    }
    return out;
  });
  el.textContent = lines.join("\n");
  toast("status read", "ok");
}

// load code-pages on first hardware tab view (once)
let cpLoaded = false;
async function loadCodePages() {
  if (cpLoaded) return;
  cpLoaded = true;
  try {
    const r = await getJSON("/api/hw/codepages");
    const sel = $("#hw-cp");
    while (sel.firstChild) sel.removeChild(sel.firstChild);
    r.pages.forEach((pg) => {
      const opt = document.createElement("option");
      opt.value = String(pg.n);
      opt.textContent = `${pg.n}: ${pg.label}`;
      sel.appendChild(opt);
    });
  } catch (e) { /* non-fatal */ }
}

// ---------- LED / RGB ----------

let ledLoaded = false;
async function loadLedProtocols() {
  if (ledLoaded) return;
  ledLoaded = true;
  try {
    const r = await getJSON("/api/hw/led/protocols");
    const sel = $("#led-protocol");
    while (sel.firstChild) sel.removeChild(sel.firstChild);
    r.protocols.forEach((p) => {
      const opt = document.createElement("option");
      opt.value = p.key;
      opt.textContent = p.name;
      opt.dataset.note = p.note;
      sel.appendChild(opt);
    });
    updateLedNote();
    refreshLedPreview();
  } catch (e) { /* non-fatal */ }
}

function hexColorToRgb(hex) {
  const m = String(hex || "").match(/^#?([0-9a-f]{6})$/i);
  if (!m) return { r: 0, g: 0, b: 0 };
  const n = parseInt(m[1], 16);
  return { r: (n >> 16) & 0xff, g: (n >> 8) & 0xff, b: n & 0xff };
}

function updateLedNote() {
  const sel = $("#led-protocol");
  const opt = sel.options[sel.selectedIndex];
  $("#led-protocol-note").textContent = opt ? opt.dataset.note || "" : "";
}

async function refreshLedPreview() {
  if (!$("#led-protocol").value) return;
  const { r, g, b } = hexColorToRgb($("#led-color").value);
  try {
    const j = await postJSON("/api/hw/led/preview", {
      protocol: $("#led-protocol").value,
      r, g, b,
    });
    $("#led-bytes").textContent = j.bytes;
  } catch (e) {
    $("#led-bytes").textContent = "(preview failed)";
  }
}

$("#led-color")?.addEventListener("input", refreshLedPreview);
$("#led-protocol")?.addEventListener("change", () => {
  updateLedNote();
  refreshLedPreview();
});

$$(".led-preset").forEach((b) => {
  b.addEventListener("click", () => {
    $("#led-color").value = b.dataset.color;
    refreshLedPreview();
  });
});

$("#led-send")?.addEventListener("click", () => {
  const { r, g, b } = hexColorToRgb($("#led-color").value);
  guard(async () => {
    const j = await postJSON("/api/hw/led", {
      protocol: $("#led-protocol").value,
      r, g, b,
      blink: $("#led-blink").checked,
    });
    $("#led-bytes").textContent = j.bytes;
  }, "LED bytes sent");
});

// ---------- console (raw ESC/POS) ----------

$("#console-send").addEventListener("click", () => {
  guard(async () => {
    const r = await postJSON("/api/hw/raw", { bytes: $("#console-input").value });
    $("#console-sent").textContent = `sent ${r.sent} bytes`;
  }, "bytes sent");
});

let cheatLoaded = false;
async function loadCheatSheet() {
  if (cheatLoaded) return;
  cheatLoaded = true;
  try {
    const r = await getJSON("/api/hw/cheatsheet");
    const tbody = $("#cheat-body");
    while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
    r.entries.forEach((e) => {
      const tr = document.createElement("tr");
      const nameC = document.createElement("td");
      nameC.textContent = e.name;
      nameC.className = "mono";
      const hexC = document.createElement("td");
      hexC.textContent = e.hex;
      hexC.className = "mono dim";
      const descC = document.createElement("td");
      descC.textContent = e.desc;
      const actC = document.createElement("td");
      const btn = document.createElement("button");
      btn.className = "ghost";
      btn.textContent = "insert";
      btn.addEventListener("click", () => {
        const ta = $("#console-input");
        const cur = ta.value.trimEnd();
        ta.value = cur ? cur + "\n" + e.hex : e.hex;
        ta.focus();
      });
      actC.appendChild(btn);
      tr.append(nameC, hexC, descC, actC);
      tbody.appendChild(tr);
    });
  } catch (e) { /* non-fatal */ }
}

// ---------- admin tab ----------

// Admin + owner share the same bearer today — apiFetch attaches it for us.
const adminGET = (url) => apiFetch(url, { method: "GET" });
const adminPOST = (url) => apiFetch(url, { method: "POST" });

function fmtWhen(iso) {
  if (!iso) return "";
  // SQLite returns "YYYY-MM-DD HH:MM:SS" in UTC. Show local short form.
  const d = new Date(iso.replace(" ", "T") + "Z");
  if (isNaN(d)) return iso;
  return d.toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

function adminButton(label, kind, onClick) {
  const b = document.createElement("button");
  b.className = "ghost tiny admin-btn admin-btn-" + kind;
  b.textContent = label;
  b.addEventListener("click", () => guard(onClick, label.toLowerCase() + " ok"));
  return b;
}

function userRow(u, opts) {
  const row = document.createElement("div");
  row.className = "wid admin-row";

  const name = document.createElement("div");
  name.className = "admin-row-name";
  name.textContent = u.username;

  const meta = document.createElement("div");
  meta.className = "admin-row-meta";
  const since = u.status === "allowed" ? `approved ${fmtWhen(u.approved_at)}` : `joined ${fmtWhen(u.created_at)}`;
  meta.textContent = since;

  const actions = document.createElement("div");
  actions.className = "row admin-row-actions";

  if (opts.canApprove) {
    actions.appendChild(adminButton("Approve", "approve", async () => {
      await adminPOST(`/api/admin/users/${u.id}/approve`);
      await refreshAdmin();
    }));
  }
  if (opts.canBlock) {
    actions.appendChild(adminButton("Block", "block", async () => {
      await adminPOST(`/api/admin/users/${u.id}/revoke`);
      await refreshAdmin();
    }));
  }
  actions.appendChild(adminButton("Delete", "delete", async () => {
    if (!confirm(`Delete ${u.username}? Their account and messages will be removed.`)) return;
    await adminPOST(`/api/admin/users/${u.id}/delete`);
    await refreshAdmin();
  }));

  row.append(name, meta, actions);
  return row;
}

function emptyState(msg) {
  const el = document.createElement("div");
  el.className = "admin-empty dim";
  el.textContent = msg;
  return el;
}

async function loadPending() {
  const list = $("#admin-pending-list");
  list.replaceChildren();
  try {
    const { users } = await adminGET("/api/admin/users?status=pending");
    if (!users.length) return list.appendChild(emptyState("no pending requests"));
    users.forEach((u) => list.appendChild(userRow(u, { canApprove: true })));
  } catch (e) {
    list.appendChild(emptyState("error: " + e.message));
  }
}

async function loadAllowed() {
  const list = $("#admin-allowed-list");
  list.replaceChildren();
  try {
    const { users } = await adminGET("/api/admin/users?status=allowed");
    if (!users.length) return list.appendChild(emptyState("no approved friends yet"));
    users.forEach((u) => list.appendChild(userRow(u, { canBlock: true })));
  } catch (e) {
    list.appendChild(emptyState("error: " + e.message));
  }
}

async function loadMessages() {
  const list = $("#admin-msgs-list");
  list.replaceChildren();
  try {
    const { messages } = await adminGET("/api/admin/messages?limit=20");
    if (!messages.length) return list.appendChild(emptyState("no messages yet"));
    messages.forEach((m) => {
      const card = document.createElement("div");
      card.className = "wid admin-msg";

      const head = document.createElement("div");
      head.className = "admin-msg-head";
      const who = document.createElement("span");
      who.className = "admin-msg-who";
      who.textContent = m.username;
      const when = document.createElement("span");
      when.className = "admin-msg-when dim";
      when.textContent = fmtWhen(m.printed_at);
      head.append(who, when);

      const body = document.createElement("pre");
      body.className = "admin-msg-body";
      body.textContent = m.body;

      card.append(head, body);
      list.appendChild(card);
    });
  } catch (e) {
    list.appendChild(emptyState("error: " + e.message));
  }
}

async function refreshAdmin() {
  await Promise.all([loadPending(), loadAllowed(), loadMessages()]);
}

$("#admin-refresh-pending").addEventListener("click", () => guard(loadPending, "refreshed"));
$("#admin-refresh-allowed").addEventListener("click", () => guard(loadAllowed, "refreshed"));
$("#admin-refresh-msgs").addEventListener("click", () => guard(loadMessages, "refreshed"));

// ---------- keyboard ----------

document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
    const pane = $(".tab.active").dataset.tab;
    if (pane === "compose") $("#compose-print").click();
    else if (pane === "image") $("#image-print").click();
    else if (pane === "codes") $("#qr-print").click();
    else if (pane === "console") $("#console-send").click();
  }
});
