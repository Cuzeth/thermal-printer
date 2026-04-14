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

// ---------- keyboard ----------

document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
    const pane = $(".tab.active").dataset.tab;
    if (pane === "compose") $("#compose-print").click();
    else if (pane === "image") $("#image-print").click();
  }
});
