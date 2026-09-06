/* Photo booth: keep crops local, send only small square frames to the Pi. */

let photoFrames = [];
let photoActive = 0;
let photoSavedId = null;
let photoBusy = false;
let photoRevision = 0;
let photoReadyRevision = -1;
let photoTimer = null;
let photoAbort = null;

function photoControls() {
  const hasPhotos = photoFrames.length > 0 || photoSavedId !== null;
  $("#photo-controls").disabled = photoBusy;
  $("#photo-files").disabled = photoFrames.length === 4;
  $("#photo-send").disabled = photoBusy || !hasPhotos || photoReadyRevision !== photoRevision;
  $("#photo-send").textContent = photoBusy ? "working" : $("#delivery-mode").value === "later"
    ? "save capsule" : photoSavedId !== null ? "reprint strip" : "print strip";
  $("#photo-reset").disabled = photoBusy || !hasPhotos;
  $("#photo-options").hidden = photoSavedId !== null;
  $("#photo-count").textContent = `${photoFrames.length} / 4`;
}

function photoPlaceholder(message, error = false) {
  const p = document.createElement("p");
  p.className = error ? "preview-error" : "preview-placeholder";
  p.textContent = message;
  $("#photo-paper").replaceChildren(p);
}

function photoProgress(label, state = "idle") {
  const el = $("#photo-progress");
  el.textContent = label;
  el.dataset.state = state;
  el.hidden = !label;
  $("#photo-paper").setAttribute("aria-busy", String(state === "loading"));
}

function drawPhotoCrop(frame, canvas) {
  const image = frame.image;
  const side = Math.min(image.width, image.height) / frame.zoom;
  const x = (image.width - side) * frame.x / 100;
  const y = (image.height - side) * frame.y / 100;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(image, x, y, side, side, 0, 0, canvas.width, canvas.height);
}

function renderPhotoFrames() {
  $("#photo-frames").replaceChildren(...photoFrames.map((frame, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "photo-frame";
    button.setAttribute("aria-pressed", String(index === photoActive));
    button.setAttribute("aria-label", `crop frame ${index + 1}`);
    const thumbnail = document.createElement("canvas");
    thumbnail.width = thumbnail.height = 96;
    thumbnail.setAttribute("aria-hidden", "true");
    drawPhotoCrop(frame, thumbnail);
    const label = document.createElement("span");
    label.textContent = String(index + 1).padStart(2, "0");
    button.append(thumbnail, label);
    button.addEventListener("click", () => {
      photoActive = index;
      renderPhotoFrames();
      // Replacing the selected thumbnail should not lose keyboard focus.
      $("#photo-frames").children[index]?.focus();
    });
    return button;
  }));
  $("#photo-crop").hidden = !photoFrames.length;
  if (photoFrames.length) {
    const frame = photoFrames[photoActive];
    $("#photo-frame-label").textContent = `frame ${photoActive + 1} / ${photoFrames.length}`;
    ["zoom", "x", "y"].forEach((key) => { $("#photo-" + key).value = frame[key]; });
    $("#photo-earlier").disabled = photoActive === 0;
    drawPhotoCrop(frame, $("#photo-canvas"));
  }
  photoControls();
}

async function readPhoto(file) {
  if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
    throw new Error("use JPEG, PNG or WebP; export HEIC photos as JPEG first");
  }
  if (file.size > 20 * 1024 * 1024) throw new Error("choose photos smaller than 20 MB");
  const url = URL.createObjectURL(file);
  const image = new Image();
  try {
    await new Promise((resolve, reject) => {
      image.onload = resolve;
      image.onerror = () => reject(new Error("photo could not be opened; export it as JPEG and try again"));
      image.src = url;
    });
    if (image.naturalWidth * image.naturalHeight > 30_000_000) {
      throw new Error("photo is too large; resize it below 30 million pixels");
    }
    // Retain only a modest editing copy, not four fully decoded phone images.
    const scale = Math.min(1, 1600 / Math.max(image.naturalWidth, image.naturalHeight));
    const small = document.createElement("canvas");
    small.width = Math.max(1, Math.round(image.naturalWidth * scale));
    small.height = Math.max(1, Math.round(image.naturalHeight * scale));
    const ctx = small.getContext("2d");
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, small.width, small.height);
    ctx.drawImage(image, 0, 0, small.width, small.height);
    return { image: small, zoom: 1, x: 50, y: 50 };
  } finally {
    URL.revokeObjectURL(url);
    image.src = "";
  }
}

async function photoFormData() {
  const form = new FormData();
  form.append("anonymous", String($("#photo-anon").checked));
  if (photoSavedId !== null) {
    form.append("saved_id", photoSavedId);
    return form;
  }
  form.append("treatment", $("#photo-treatment").value);
  form.append("caption", $("#photo-caption").value);
  // Canvas work happens before any await, making one immutable snapshot even
  // if the friend adjusts another slider while PNG encoding is finishing.
  const frames = photoFrames.map((frame) => {
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 576;
    drawPhotoCrop(frame, canvas);
    return new Promise((resolve, reject) => canvas.toBlob(
      (blob) => blob ? resolve(blob) : reject(new Error("photo could not be prepared")), "image/png",
    ));
  });
  const blobs = await Promise.all(frames);
  blobs.forEach((blob, index) => form.append("photos", blob, `frame-${index + 1}.png`));
  return form;
}

async function postPhoto(url, form, signal) {
  const response = await fetch(url, {
    method: "POST", credentials: "same-origin", body: form, signal,
  });
  const result = await response.json().catch(() => ({ error: "bad server response" }));
  if (!response.ok || !result.ok) throw new Error(result.error || `HTTP ${response.status}`);
  return result;
}

function schedulePhotoPreview() {
  clearTimeout(photoTimer);
  photoAbort?.abort();
  photoRevision += 1;
  photoReadyRevision = -1;
  photoControls();
  if (!photoFrames.length && photoSavedId === null) {
    photoPlaceholder("add photos to build your strip");
    photoProgress("");
    return;
  }
  // Remove old pixels as soon as a control changes: stale output must never
  // look like the receipt that an enabled print button is about to send.
  photoPlaceholder("updating your strip");
  photoProgress("updating", "pending");
  photoTimer = setTimeout(updatePhotoPreview, 350);
}

async function updatePhotoPreview() {
  const revision = photoRevision;
  const controller = new AbortController();
  photoAbort = controller;
  photoProgress("rendering", "loading");
  try {
    const form = await photoFormData();
    if (revision !== photoRevision) return;
    const result = await postPhoto("/api/photo/preview", form, controller.signal);
    if (revision !== photoRevision) return;
    const images = result.segments.map((src, index) => {
      const image = document.createElement("img");
      image.src = src;
      image.className = "preview-img";
      image.alt = ["receipt name header", "photo strip and caption", "receipt timestamp footer"][index];
      return image;
    });
    $("#photo-paper").replaceChildren(...images);
    photoReadyRevision = revision;
    photoProgress("ready", "ready");
  } catch (error) {
    if (error.name === "AbortError" || revision !== photoRevision) return;
    photoPlaceholder(error.message, true);
    photoProgress("failed", "error");
  } finally {
    photoControls();
  }
}

function resetPhotos() {
  photoFrames = [];
  photoActive = 0;
  photoSavedId = null;
  $("#photo-files").value = "";
  $("#photo-caption").value = "";
  $("#photo-treatment").value = "soft";
  $("#photo-anon").checked = false;
  $("#photo-saved").hidden = true;
  renderPhotoFrames();
  schedulePhotoPreview();
}

function restorePhoto(message) {
  if (photoBusy) return toast("wait for this photo strip to finish", "err");
  resetPhotos();
  photoSavedId = message.id;
  $("#photo-anon").checked = !!message.anonymous;
  $("#photo-saved").textContent = `${message.body}. Saved pixels and caption. Add new photos to make a different strip.`;
  $("#photo-saved").hidden = false;
  setMode("photo");
  renderPhotoFrames();
  schedulePhotoPreview();
  const behavior = matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth";
  $("#photo-panel").scrollIntoView({ behavior, block: "start" });
}

$("#photo-files").addEventListener("change", async (event) => {
  const files = Array.from(event.target.files);
  event.target.value = "";
  if (!files.length) return;
  if (files.length + photoFrames.length > 4) return toast("a strip holds up to 4 photos", "err");
  photoBusy = true;
  photoControls();
  photoProgress("opening photos", "loading");
  try {
    const frames = [];
    // Decode one original at a time to bound peak memory on a phone.
    for (const file of files) frames.push(await readPhoto(file));
    photoSavedId = null;
    $("#photo-saved").hidden = true;
    photoActive = photoFrames.length;
    photoFrames.push(...frames);
  } catch (error) {
    toast(error.message, "err");
  } finally {
    photoBusy = false;
    renderPhotoFrames();
    schedulePhotoPreview();
  }
});

["zoom", "x", "y"].forEach((key) => {
  $("#photo-" + key).addEventListener("input", (event) => {
    const frame = photoFrames[photoActive];
    if (!frame) return;
    frame[key] = Number(event.target.value);
    drawPhotoCrop(frame, $("#photo-canvas"));
    const thumbnail = $("#photo-frames").children[photoActive]?.querySelector("canvas");
    if (thumbnail) drawPhotoCrop(frame, thumbnail);
    schedulePhotoPreview();
  });
});
$("#photo-earlier").addEventListener("click", () => {
  if (!photoActive) return;
  [photoFrames[photoActive - 1], photoFrames[photoActive]] = [photoFrames[photoActive], photoFrames[photoActive - 1]];
  photoActive -= 1;
  renderPhotoFrames();
  schedulePhotoPreview();
});
$("#photo-remove").addEventListener("click", () => {
  photoFrames.splice(photoActive, 1);
  photoActive = Math.max(0, Math.min(photoActive, photoFrames.length - 1));
  renderPhotoFrames();
  schedulePhotoPreview();
});
$("#photo-treatment").addEventListener("change", schedulePhotoPreview);
$("#photo-caption").addEventListener("input", schedulePhotoPreview);
$("#photo-anon").addEventListener("change", schedulePhotoPreview);
$("#photo-reset").addEventListener("click", resetPhotos);
$("#photo-panel").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (photoBusy || photoReadyRevision !== photoRevision) return;
  photoBusy = true;
  photoControls();
  try {
    const deliverAt = deliveryAt();
    const form = await photoFormData();
    if (deliverAt) form.append("deliver_at", deliverAt);
    const result = await postPhoto("/api/print/photo", form);
    resetPhotos();
    celebrateQueued(result);
  } catch (error) {
    toast(error.message, "err");
  } finally {
    photoBusy = false;
    photoControls();
  }
});
