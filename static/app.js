// Thermal Printer Console — frontend glue

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

const state = {
  imageFile: null,
  activePane: "compose",
  previewSeq: 0,
  previewOwner: null,
  previewKind: null,
  previewTrigger: null,
};

// ---------- tabs ----------

function activateTab(t, { focus = false } = {}) {
  const pane = t.dataset.tab;
  state.activePane = pane;
  $$(".tab").forEach((x) => {
    const on = x === t;
    x.classList.toggle("active", on);
    x.setAttribute("aria-selected", on ? "true" : "false");
    x.tabIndex = on ? 0 : -1;
  });
  $$(".tabpane").forEach((p) => {
    const on = p.dataset.pane === pane;
    p.classList.toggle("active", on);
    p.setAttribute("aria-hidden", on ? "false" : "true");
  });
  if (focus) t.focus();
  configurePreviewForPane(pane);
  if (pane === "hardware") { loadCodePages(); loadLedProtocols(); }
  if (pane === "console") loadCheatSheet();
  if (pane === "admin") refreshAdmin();
}

$$(".tab").forEach((t) => t.addEventListener("click", () => activateTab(t)));

// The compact help markers work for keyboard and screen-reader users too.
$$(".tip").forEach((tip) => {
  tip.tabIndex = 0;
  tip.setAttribute("role", "note");
  tip.setAttribute("aria-label", tip.dataset.tip || "more information");
});

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

// Auth rides on the admin session cookie set by the TOTP login — no
// tokens in the page. A 401 means the session expired; reloading gets
// the login form back (the server gates /admin itself).
async function apiFetch(url, init = {}) {
  const r = await fetch(url, init);
  if (r.status === 401) {
    location.reload();
    return new Promise(() => {}); // never resolves — the reload wins
  }
  const j = await r.json().catch(() => ({ ok: false, error: "bad server response" }));
  if (!j.ok) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
}

async function postJSON(url, data, options = {}) {
  return apiFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data || {}),
    signal: options.signal,
  });
}

async function postForm(url, formData) {
  return apiFetch(url, { method: "POST", body: formData });
}

async function getJSON(url) {
  return apiFetch(url, { method: "GET" });
}

function setButtonBusy(btn, busy) {
  if (!btn) return;
  btn.disabled = busy;
  btn.classList.toggle("is-busy", busy);
  if (busy) btn.setAttribute("aria-busy", "true");
  else btn.removeAttribute("aria-busy");
}

async function guard(fn, okMsg = "done", btn = null) {
  // Prints are synchronous server-side (a briefing can take 15s+), so
  // disable the trigger while in flight — otherwise the natural move
  // (click again) queues a duplicate behind the USB lock.
  setButtonBusy(btn, true);
  try {
    await fn();
    toast(okMsg, "ok");
  } catch (e) {
    toast(e.message, "err");
  } finally {
    setButtonBusy(btn, false);
  }
}

// ---------- preview ----------

const PREVIEW_TITLES = {
  compose: "compose preview",
  image: "image preview",
  codes: "code preview",
  widgets: "widget preview",
  labs: "lab preview",
};

function setPreviewStatus(label, kind = "idle") {
  const status = $("#preview-state");
  status.textContent = label;
  status.dataset.state = kind;
  const panel = $("#preview-panel");
  const busy = kind === "loading";
  panel.setAttribute("aria-busy", busy ? "true" : "false");
  $("#paper-frame").setAttribute("aria-busy", busy ? "true" : "false");
}

function beginPreview(owner, title = PREVIEW_TITLES[owner]) {
  if (state.activePane !== owner) return null;
  const token = ++state.previewSeq;
  state.previewOwner = owner;
  const panel = $("#preview-panel");
  panel.hidden = false;
  $("#preview-title").textContent = title;
  setPreviewStatus("rendering", "loading");
  return token;
}

function previewIsCurrent(owner, token) {
  return state.activePane === owner && state.previewOwner === owner && state.previewSeq === token;
}

function finishPreview(owner, token, label = "ready", kind = "ready") {
  if (!previewIsCurrent(owner, token)) return false;
  setPreviewStatus(label, kind);
  return true;
}

function clearPreviewImage() {
  const wrap = $("#preview-image-wrap");
  wrap.hidden = true;
  wrap.replaceChildren();
}

function showPreviewText(text) {
  $("#preview-out").textContent = text;
  clearPreviewImage();
}
function showPreviewImage(url) {
  const wrap = $("#preview-image-wrap");
  // Replace children: one <img> per segment, separated by tear-lines.
  wrap.replaceChildren();
  const urls = Array.isArray(url) ? url : [url];
  urls.forEach((u, i) => {
    const img = document.createElement("img");
    img.src = u;
    img.alt = urls.length > 1 ? `receipt preview, part ${i + 1} of ${urls.length}` : "receipt preview";
    img.decoding = "async";
    img.className = "preview-result";
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

function showPreviewPlaceholder(message) {
  showPreviewText(message);
}

function configurePreviewForPane(pane) {
  // Invalidate any response still in flight from the previous pane.
  state.previewSeq += 1;
  state.previewOwner = null;
  state.previewKind = null;
  state.previewTrigger = null;
  const panel = $("#preview-panel");
  if (!PREVIEW_TITLES[pane]) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  $("#preview-title").textContent = PREVIEW_TITLES[pane];
  setPreviewStatus("waiting", "idle");
  if (pane === "compose") refreshComposePreview();
  if (pane === "image") {
    if (state.imageFile) refreshImagePreview();
    else showPreviewPlaceholder("select an image");
  }
  if (pane === "codes") showPreviewPlaceholder("select QR or barcode");
  if (pane === "widgets") showPreviewPlaceholder("select a widget");
  if (pane === "labs") showPreviewPlaceholder("select a lab");
}

async function refreshComposePreview() {
  const owner = "compose";
  const token = beginPreview(owner, "compose preview");
  if (token === null) return;
  const body = $("#compose-body").value;
  const rich = $("#compose-rich").checked;
  try {
    if (rich) {
      const { segments } = await postJSON("/api/admin/preview/rich", { body });
      if (!previewIsCurrent(owner, token)) return;
      showPreviewImage(segments);
    } else {
      const { preview } = await postJSON("/api/admin/preview", { body });
      if (!previewIsCurrent(owner, token)) return;
      showPreviewText(preview);
    }
    finishPreview(owner, token);
  } catch (e) {
    if (!previewIsCurrent(owner, token)) return;
    showPreviewText("preview failed\n\n" + e.message);
    finishPreview(owner, token, "failed", "error");
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
$("#compose-print").addEventListener("click", (e) => {
  guard(async () => {
    await postJSON("/api/admin/print/text", {
      body: $("#compose-body").value,
      cut: $("#compose-cut").checked,
      rich: $("#compose-rich").checked,
    });
  }, "printed", e.currentTarget);
});
refreshComposePreview();

// ---------- image ----------

const drop = $("#drop");
const fileInput = $("#image-file");

drop.addEventListener("click", () => fileInput.click());
drop.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    fileInput.click();
  }
});
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
  const owner = "image";
  const token = beginPreview(owner, "image preview");
  if (token === null) return;
  const fd = buildImageForm();
  try {
    const { data_url } = await postForm("/api/admin/image/preview", fd);
    if (!previewIsCurrent(owner, token)) return;
    showPreviewImage(data_url);
    finishPreview(owner, token);
  } catch (e) {
    if (!previewIsCurrent(owner, token)) return;
    showPreviewText("preview failed\n\n" + e.message);
    finishPreview(owner, token, "failed", "error");
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

$("#image-print").addEventListener("click", (e) => {
  if (!state.imageFile) return;
  guard(async () => {
    await postForm("/api/admin/print/image", buildImageForm());
  }, "printed", e.currentTarget);
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

function widgetPayload(kind, source) {
  if (kind === "weather") {
    return { location: $("#w-loc").value, days: Number($("#w-days").value) || 1 };
  }
  if (kind === "dice") {
    return {
      count: Number($("#w-dice-count").value),
      sides: Number($("#w-dice-sides").value),
      mode: source.dataset.mode || "standard",
    };
  }
  if (kind === "ascii") return { name: $("#w-ascii").value };
  if (kind === "briefing") return { location: $("#w-brief-loc").value };
  if (kind === "hn") return { count: Number($("#w-hn-count").value) };
  if (kind === "onthisday") return { count: Number($("#w-otd-count").value) };
  if (kind === "calendar") {
    return {
      year: Number($("#w-cal-year").value) || null,
      month: Number($("#w-cal-month").value) || null,
    };
  }
  if (kind === "countdown") {
    return { label: $("#w-cd-label").value, date: $("#w-cd-date").value };
  }
  if (kind === "habits") {
    return {
      habits: $("#w-habits").value.split("\n").map((s) => s.trim()).filter(Boolean),
    };
  }
  return {};
}

function previewName(button, fallback) {
  const heading = button.closest(".wid, details")?.querySelector("h3");
  const name = heading?.childNodes[0]?.textContent?.trim();
  return name || fallback;
}

// Honest status labels: random and network-backed widgets can change on
// print, and "now" re-stamps the clock — only the deterministic
// cards print exactly what the preview shows ("ready").
const PREVIEW_FRESHNESS = {
  dice: "sample",
  advice: "sample",
  weather: "sample",
  briefing: "sample",
  hn: "sample",
  onthisday: "sample",
  now: "snapshot",
};

async function previewWidget(button) {
  const kind = button.dataset.previewWidget;
  const token = beginPreview("widgets", `${previewName(button, kind)} preview`);
  if (token === null) return;
  state.previewKind = kind;
  state.previewTrigger = button;
  setButtonBusy(button, true);
  try {
    const { data_url } = await postJSON(
      `/api/admin/preview/widget/${kind}`,
      widgetPayload(kind, button),
    );
    if (!previewIsCurrent("widgets", token)) return;
    showPreviewImage(data_url);
    finishPreview("widgets", token, PREVIEW_FRESHNESS[kind] || "ready");
  } catch (e) {
    if (!previewIsCurrent("widgets", token)) return;
    showPreviewText("preview failed\n\n" + e.message);
    finishPreview("widgets", token, "failed", "error");
  } finally {
    setButtonBusy(button, false);
  }
}

$$("button[data-preview-widget]").forEach((button) => {
  button.addEventListener("click", () => previewWidget(button));
});

$$("button[data-widget]").forEach((b) => {
  b.addEventListener("click", () => {
    const kind = b.dataset.widget;
    guard(async () => {
      await postJSON(`/api/admin/print/${kind}`, widgetPayload(kind, b));
    }, kind === "briefing" ? "briefing printed" : "printed", b);
  });
});

// ---------- labs ----------

function labPayload(kind) {
  if (kind === "todo") {
    return { title: $("#todo-title").value, items: $("#todo-items").value.split("\n") };
  }
  if (kind === "label") {
    return { text: $("#label-text").value, big: $("#label-big").checked };
  }
  if (kind === "receipt") {
    const items = $$(".ritem").map((row) => ({
      name: $(".r-name", row).value,
      qty: Number($(".r-qty", row).value) || 1,
      price: Number($(".r-price", row).value) || 0,
    })).filter((item) => item.name.trim());
    return {
      store: $("#r-store").value,
      items,
      tax_rate: Number($("#r-tax").value) || 0,
      note: $("#r-note").value,
    };
  }
  return {};
}

async function previewLab(button) {
  const kind = button.dataset.previewLab;
  const token = beginPreview("labs", `${previewName(button, kind)} preview`);
  if (token === null) return;
  state.previewKind = kind;
  state.previewTrigger = button;
  setButtonBusy(button, true);
  try {
    const { data_url } = await postJSON(`/api/admin/preview/lab/${kind}`, labPayload(kind));
    if (!previewIsCurrent("labs", token)) return;
    showPreviewImage(data_url);
    finishPreview("labs", token);
  } catch (e) {
    if (!previewIsCurrent("labs", token)) return;
    showPreviewText("preview failed\n\n" + e.message);
    finishPreview("labs", token, "failed", "error");
  } finally {
    setButtonBusy(button, false);
  }
}

$$("button[data-preview-lab]").forEach((button) => {
  button.addEventListener("click", () => previewLab(button));
});

$$("button[data-lab]").forEach((b) => {
  b.addEventListener("click", () => {
    const kind = b.dataset.lab;
    guard(async () => {
      await postJSON(`/api/admin/print/${kind}`, labPayload(kind));
    }, "printed", b);
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

$("#r-add").addEventListener("click", (e) => {
  $("#r-items").appendChild(makeReceiptRow());
  scheduleToolPreview("labs", e.currentTarget);
});
$("#r-items").addEventListener("click", (e) => {
  if (e.target.classList.contains("r-del")) {
    const rows = $$(".ritem");
    if (rows.length > 1) {
      e.target.closest(".ritem").remove();
      // The clicked delete button is already detached, so it would fail the
      // containment check — hand over the list container instead.
      scheduleToolPreview("labs", $("#r-items"));
    }
  }
});

// Once the user asks for an offline preview, keep that one receipt in sync
// as its fields change. Network-backed widgets stay manual to avoid turning
// every keystroke into an external API request on the Pi.
const AUTO_PREVIEW_WIDGETS = new Set(["calendar", "countdown", "habits", "ascii"]);
let toolPreviewT;
function scheduleToolPreview(owner, origin) {
  if (state.previewOwner !== owner || !state.previewTrigger) return;
  if (owner === "widgets" && !AUTO_PREVIEW_WIDGETS.has(state.previewKind)) return;
  // Only edits inside the previewed card count — typing in a neighboring
  // widget shouldn't re-render this one.
  if (origin && !state.previewTrigger.closest(".wid, details")?.contains(origin)) return;
  clearTimeout(toolPreviewT);
  toolPreviewT = setTimeout(() => {
    if (owner === "widgets") previewWidget(state.previewTrigger);
    if (owner === "labs") previewLab(state.previewTrigger);
  }, 420);
}

["input", "change"].forEach((eventName) => {
  $("#pane-widgets").addEventListener(eventName, (e) => scheduleToolPreview("widgets", e.target));
  $("#pane-labs").addEventListener(eventName, (e) => scheduleToolPreview("labs", e.target));
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
$("#qr-print").addEventListener("click", (e) => {
  guard(async () => {
    await postJSON("/api/admin/print/qr", {
      data: qrCtrl.data.value,
      ec: qrCtrl.ec.value,
      size: Number(qrCtrl.size.value),
    });
  }, "printed", e.currentTarget);
});

let qrT;
function debouncedQr() {
  clearTimeout(qrT);
  qrT = setTimeout(refreshQrPreview, 200);
}
async function refreshQrPreview() {
  const owner = "codes";
  const token = beginPreview(owner, "QR code preview");
  if (token === null) return;
  if (!qrCtrl.data.value.trim()) {
    showPreviewText("enter QR data");
    finishPreview(owner, token, "waiting", "idle");
    return;
  }
  try {
    const { data_url } = await postJSON("/api/admin/code/qr/preview", {
      data: qrCtrl.data.value,
      ec: qrCtrl.ec.value,
      size: Number(qrCtrl.size.value),
      box_size: 10,
    });
    if (!previewIsCurrent(owner, token)) return;
    showPreviewImage(data_url);
    finishPreview(owner, token);
  } catch (e) {
    if (!previewIsCurrent(owner, token)) return;
    showPreviewText("preview failed\n\n" + e.message);
    finishPreview(owner, token, "failed", "error");
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
$("#bc-print").addEventListener("click", (e) => {
  guard(async () => {
    await postJSON("/api/admin/print/barcode", buildBcBody());
  }, "barcode printed", e.currentTarget);
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
  const owner = "codes";
  const token = beginPreview(owner, "barcode preview");
  if (token === null) return;
  if (!bcCtrl.data.value.trim()) {
    showPreviewText("enter barcode data");
    finishPreview(owner, token, "waiting", "idle");
    return;
  }
  try {
    const { data_url } = await postJSON("/api/admin/code/barcode/preview", buildBcBody());
    if (!previewIsCurrent(owner, token)) return;
    showPreviewImage(data_url);
    finishPreview(owner, token);
  } catch (e) {
    if (!previewIsCurrent(owner, token)) return;
    showPreviewText("preview failed\n\n" + e.message);
    finishPreview(owner, token, "failed", "error");
  }
}

// ---------- hardware ----------

$$("button[data-hw]").forEach((b) => {
  b.addEventListener("click", () => {
    const kind = b.dataset.hw;
    guard(async () => {
      if (kind === "cash_drawer") {
        await postJSON("/api/admin/hw/cash_drawer", { pin: Number(b.dataset.pin) });
      } else if (kind === "beep") {
        await postJSON("/api/admin/hw/beep", {
          count: Number($("#hw-beep-count").value),
          duration_units: Number($("#hw-beep-dur").value),
        });
      } else if (kind === "feed") {
        await postJSON("/api/admin/hw/feed", { lines: Number($("#hw-feed").value) });
      } else if (kind === "cut") {
        await postJSON("/api/admin/hw/cut", {
          partial: b.dataset.partial === "true",
        });
      } else if (kind === "density") {
        await postJSON("/api/admin/hw/density", { level: Number($("#hw-dens").value) });
      } else if (kind === "codepage") {
        await postJSON("/api/admin/hw/codepage", { n: Number($("#hw-cp").value) });
      } else if (kind === "reset") {
        await postJSON("/api/admin/hw/reset", {});
      } else if (kind === "self_test") {
        await postJSON("/api/admin/hw/self_test", {});
      } else if (kind === "status") {
        const j = await postJSON("/api/admin/hw/status", {});
        renderStatus(j.statuses);
        return; // custom toast
      }
    }, kind === "cut" ? "cut" :
       kind === "feed" ? "fed" :
       kind === "cash_drawer" ? "drawer kicked" :
       kind === "beep" ? "beeped" :
       kind === "self_test" ? "self-test sent" :
       kind === "reset" ? "reset sent" :
       "applied", b);
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
        if (set.length) out += "  /  " + set.map((key) => key.replaceAll("_", " ")).join(", ");
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
    const r = await getJSON("/api/admin/hw/codepages");
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
    const r = await getJSON("/api/admin/hw/led/protocols");
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
    const j = await postJSON("/api/admin/hw/led/preview", {
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

$("#led-send")?.addEventListener("click", (e) => {
  const { r, g, b } = hexColorToRgb($("#led-color").value);
  guard(async () => {
    const j = await postJSON("/api/admin/hw/led", {
      protocol: $("#led-protocol").value,
      r, g, b,
      blink: $("#led-blink").checked,
    });
    $("#led-bytes").textContent = j.bytes;
  }, "sent", e.currentTarget);
});

// ---------- console (raw ESC/POS) ----------

$("#console-send").addEventListener("click", (e) => {
  guard(async () => {
    const r = await postJSON("/api/admin/hw/raw", { bytes: $("#console-input").value });
    $("#console-sent").textContent = `sent ${r.sent} bytes`;
  }, "bytes sent", e.currentTarget);
});

let cheatLoaded = false;
async function loadCheatSheet() {
  if (cheatLoaded) return;
  cheatLoaded = true;
  try {
    const r = await getJSON("/api/admin/hw/cheatsheet");
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

const adminGET = (url) => apiFetch(url, { method: "GET" });
const adminPOST = (url) => apiFetch(url, { method: "POST" });

$("#signout").addEventListener("click", async () => {
  await adminPOST("/api/admin/auth/logout").catch(() => {});
  location.reload();
});

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
  b.addEventListener("click", () => guard(onClick, "done"));
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
    actions.appendChild(adminButton("approve", "approve", async () => {
      await adminPOST(`/api/admin/users/${u.id}/approve`);
      await refreshAdmin();
    }));
  }
  if (opts.canBlock) {
    actions.appendChild(adminButton("block", "block", async () => {
      await adminPOST(`/api/admin/users/${u.id}/revoke`);
      await refreshAdmin();
    }));
  }
  if (opts.canUnblock) {
    actions.appendChild(adminButton("unblock", "approve", async () => {
      await adminPOST(`/api/admin/users/${u.id}/approve`);
      await refreshAdmin();
    }));
  }

  // No self-service reset on the friends page — this link is the only
  // recovery path for a friend who forgot their password. The server
  // returns just the path; we bolt on location.origin here so the link
  // matches whatever host the console is open on (tunnel or localhost).
  const resetBtn = document.createElement("button");
  resetBtn.className = "ghost tiny admin-btn admin-btn-reset";
  resetBtn.textContent = "reset link";
  resetBtn.addEventListener("click", async () => {
    try {
      const j = await postJSON(`/api/admin/users/${u.id}/reset_link`, {});
      const url = location.origin + j.path;
      try {
        await navigator.clipboard.writeText(url);
        toast(`reset link copied. expires in ${j.expires_minutes} min`, "ok");
      } catch {
        // Clipboard can be denied; prompt() pre-selects its value, so
        // the link is still one keystroke away.
        prompt(`reset link for ${u.username}. expires in ${j.expires_minutes} min`, url);
      }
    } catch (e) {
      toast(e.message, "err");
    }
  });
  actions.appendChild(resetBtn);

  actions.appendChild(adminButton("delete", "delete", async () => {
    if (!confirm(`delete ${u.username} and all of their prints?`)) return;
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
    if (!users.length) return list.appendChild(emptyState("none pending"));
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
    if (!users.length) return list.appendChild(emptyState("none approved"));
    users.forEach((u) => list.appendChild(userRow(u, { canBlock: true })));
  } catch (e) {
    list.appendChild(emptyState("error: " + e.message));
  }
}

async function loadBlocked() {
  const list = $("#admin-blocked-list");
  list.replaceChildren();
  try {
    const { users } = await adminGET("/api/admin/users?status=blocked");
    if (!users.length) return list.appendChild(emptyState("none blocked"));
    users.forEach((u) => list.appendChild(userRow(u, { canUnblock: true })));
  } catch (e) {
    list.appendChild(emptyState("error: " + e.message));
  }
}

async function loadMessages() {
  const list = $("#admin-msgs-list");
  list.replaceChildren();
  try {
    const { messages } = await adminGET("/api/admin/messages?limit=20");
    if (!messages.length) return list.appendChild(emptyState("no prints yet"));
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
      if (m.deliver_at) {
        when.textContent += " · delivery " + new Date(m.deliver_at).toLocaleString();
      }
      if (m.status && m.status !== "printed") {
        const badge = document.createElement("span");
        badge.className = "admin-msg-status " + m.status;
        badge.textContent = m.status === "failed" ? "didn't print" : m.status;
        when.append(" · ", badge);
      }
      head.append(who, when);

      if (m.status === "failed") {
        const retry = document.createElement("button");
        retry.className = "ghost tiny admin-btn admin-btn-approve";
        retry.textContent = "retry";
        retry.addEventListener("click", () => guard(async () => {
          await adminPOST(`/api/admin/messages/${m.id}/retry`);
          await loadMessages();
        }, "printed", retry));
        head.appendChild(retry);
      }

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
  await Promise.all([loadPending(), loadAllowed(), loadBlocked(), loadMessages()]);
}

$("#admin-refresh-pending").addEventListener("click", () => guard(loadPending, "refreshed"));
$("#admin-refresh-allowed").addEventListener("click", () => guard(loadAllowed, "refreshed"));
$("#admin-refresh-blocked").addEventListener("click", () => guard(loadBlocked, "refreshed"));
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
