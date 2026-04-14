// Thermal Printer Console — frontend glue

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

const state = {
  imageFile: null,
  lastPreview: null,
};

// ---------- tabs ----------

$$(".tab").forEach((t) => {
  t.addEventListener("click", () => {
    $$(".tab").forEach((x) => x.classList.toggle("active", x === t));
    const pane = t.dataset.tab;
    $$(".tabpane").forEach((p) =>
      p.classList.toggle("active", p.dataset.pane === pane)
    );
    if (pane !== "image") hidePreviewImage();
    if (pane === "compose") refreshComposePreview();
  });
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

async function postJSON(url, data) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data || {}),
  });
  const j = await r.json().catch(() => ({ ok: false, error: "bad JSON" }));
  if (!j.ok) throw new Error(j.error || "unknown error");
  return j;
}

async function postForm(url, formData) {
  const r = await fetch(url, { method: "POST", body: formData });
  const j = await r.json().catch(() => ({ ok: false, error: "bad JSON" }));
  if (!j.ok) throw new Error(j.error || "unknown error");
  return j;
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

$$("button[data-widget]").forEach((b) => {
  b.addEventListener("click", () => {
    const kind = b.dataset.widget;
    guard(async () => {
      if (kind === "weather") {
        await postJSON("/api/print/weather", { location: $("#w-loc").value });
      } else if (kind === "eight_ball") {
        await postJSON("/api/print/eight_ball", { question: $("#w-8q").value });
      } else if (kind === "dice") {
        await postJSON("/api/print/dice", {
          count: Number($("#w-dice-count").value),
          sides: Number($("#w-dice-sides").value),
        });
      } else if (kind === "ascii") {
        await postJSON("/api/print/ascii", { name: $("#w-ascii").value });
      } else {
        await postJSON(`/api/print/${kind}`, {});
      }
    }, "printing…");
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
    const r = await fetch("/api/hw/codepages").then((x) => x.json());
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
    const r = await fetch("/api/hw/led/protocols").then((x) => x.json());
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
    const r = await fetch("/api/hw/cheatsheet").then((x) => x.json());
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

// Lazy-load hardware/console content when those tabs are opened.
$$(".tab").forEach((t) => {
  t.addEventListener("click", () => {
    if (t.dataset.tab === "hardware") { loadCodePages(); loadLedProtocols(); }
    if (t.dataset.tab === "console") loadCheatSheet();
  });
});

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
