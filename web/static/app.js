// VideoCraft Studio — web client.
// Flow: pick file → choose settings → upload (XHR for progress) → poll job → show before/after + download.
(() => {
  "use strict";

  // ---------- Helpers ----------
  const $ = (id) => document.getElementById(id);
  const IMAGE_EXT = [".png", ".jpg", ".jpeg", ".jfif", ".webp", ".bmp", ".tif", ".tiff", ".avif", ".heic", ".heif"];
  const RES_LABELS = { source: "كما هي", "1080p": "1080p (Full HD)", "2k": "2K", "4k": "4K" };
  const PRESETS = {
    balanced: { denoise: 40, sharpness: 40, brightness: 0, contrast: 10, saturation: 10, color_style: "natural" },
    clean:    { denoise: 85, sharpness: 55, brightness: 0, contrast: 8,  saturation: 6,  color_style: "natural" },
    vivid:    { denoise: 35, sharpness: 50, brightness: 4, contrast: 18, saturation: 30, color_style: "bright" },
    light:    { denoise: 15, sharpness: 20, brightness: 0, contrast: 4,  saturation: 4,  color_style: "none" },
  };
  const SLIDERS = ["denoise", "sharpness", "brightness", "contrast", "saturation", "quality"];
  const STORE_KEY = "videocraft-job";
  const ltr = (text) => `\u2066${text}\u2069`; // keep "1600×1200" readable inside Arabic text

  const ext = (name) => (name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
  const formatSize = (bytes) => {
    const units = ["B", "KB", "MB", "GB"];
    let i = 0;
    while (bytes >= 1024 && i < units.length - 1) { bytes /= 1024; i++; }
    return `${bytes.toFixed(i < 2 ? 0 : 1)} ${units[i]}`;
  };
  const store = {
    get() { try { return JSON.parse(sessionStorage.getItem(STORE_KEY)); } catch { return null; } },
    set(v) { try { sessionStorage.setItem(STORE_KEY, JSON.stringify(v)); } catch { /* private mode */ } },
    clear() { try { sessionStorage.removeItem(STORE_KEY); } catch { /* ignore */ } },
  };

  // ---------- State ----------
  let config = { max_upload_mb: 200, max_video_seconds: 180, video_resolutions: ["source", "1080p"], image_resolutions: ["source", "1080p", "2k", "4k"] };
  let file = null;
  let kind = "video";
  let sourceUrl = null;
  let jobId = null;
  let pollTimer = null;
  let upload = null;
  let imageSize = null; // natural size of the picked image, for the upscale preview
  let aiAbort = null; // AbortController while the in-browser AI is running
  let aiInfo = ""; // e.g. "Real-CUGAN ×4 • webgpu", shown with the result
  let localResultUrl = null; // blob: URL of a result made on this device (AI mode)
  let resultNote = ""; // extra line under the result, e.g. why AI was skipped
  const AI_SUPPORTED = Boolean(window.VCAI && VCAI.supported());
  const AI_MAX_OUTPUT_MP = 36; // same cap as the server, keeps browser memory sane

  // ---------- Views ----------
  const views = ["pick", "setup", "work", "done", "failed"];
  function show(view) {
    views.forEach((v) => { $(v).hidden = v !== view; });
    const heading = $(view).querySelector("h1, h2");
    if (heading && view !== "pick") { heading.tabIndex = -1; heading.focus({ preventScroll: false }); }
  }

  // ---------- Config ----------
  fetch("/api/config")
    .then((r) => (r.ok ? r.json() : Promise.reject()))
    .then((c) => {
      config = c;
      $("limits").textContent = `فيديو حتى ${Math.round(c.max_video_seconds / 60)} دقائق أو صورة • حتى ${c.max_upload_mb} ميغابايت`;
      if (!c.ffmpeg) showPickError("خدمة المعالجة غير متاحة الآن. حاول لاحقًا.");
    })
    .catch(() => { $("limits").textContent = "فيديو أو صورة"; });

  // ---------- 1. Pick ----------
  const drop = $("drop");
  $("file").addEventListener("change", (e) => e.target.files[0] && choose(e.target.files[0]));
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("is-over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, () => drop.classList.remove("is-over")));
  drop.addEventListener("drop", (e) => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) choose(f); });

  function showPickError(msg) { const el = $("pick-error"); el.textContent = msg; el.hidden = !msg; }

  function choose(f) {
    showPickError("");
    const isImage = f.type.startsWith("image/") || IMAGE_EXT.includes(ext(f.name));
    const isVideo = f.type.startsWith("video/") || (config.accept || []).includes(ext(f.name));
    if (!isImage && !isVideo) return showPickError("نوع الملف غير مدعوم. اختر فيديو أو صورة.");
    if (f.size > config.max_upload_mb * 1024 * 1024) return showPickError(`الملف أكبر من ${config.max_upload_mb} ميغابايت.`);

    file = f;
    kind = isImage ? "image" : "video";
    if (sourceUrl) URL.revokeObjectURL(sourceUrl);
    sourceUrl = URL.createObjectURL(f);

    // Thumbnail
    const thumb = $("thumb");
    thumb.replaceChildren();
    const media = document.createElement(kind === "image" ? "img" : "video");
    media.src = sourceUrl;
    imageSize = null;
    if (kind === "image") {
      media.alt = "";
      media.onload = () => { imageSize = { w: media.naturalWidth, h: media.naturalHeight }; updateUpscaleHint(); };
    } else { media.muted = true; media.preload = "metadata"; }
    media.onerror = () => media.remove();
    thumb.append(media);

    // Duration check for video (when the browser can read it)
    if (kind === "video") {
      const probe = document.createElement("video");
      probe.preload = "metadata";
      probe.onloadedmetadata = () => {
        if (probe.duration > config.max_video_seconds) {
          show("pick");
          showPickError(`الفيديو أطول من ${Math.round(config.max_video_seconds / 60)} دقائق. قصّه أولًا أو استخدم نسخة ويندوز.`);
        }
      };
      probe.src = sourceUrl;
    }

    $("setup-title").textContent = f.name;
    $("file-size").textContent = `${kind === "image" ? "صورة" : "فيديو"} • ${formatSize(f.size)}`;

    document.querySelectorAll("[data-only]").forEach((el) => { el.hidden = el.dataset.only !== kind; });
    const res = $("resolution");
    const allowed = kind === "image" ? config.image_resolutions : config.video_resolutions;
    res.replaceChildren(...allowed.map((v) => new Option(RES_LABELS[v] || v, v)));
    res.value = "source";
    syncAiControls();

    show("setup");
  }

  $("change-file").addEventListener("click", resetToPick);

  // ---------- 2. Settings ----------
  const form = $("settings");

  function syncOutput(input) {
    const out = form.querySelector(`output[for="${input.id}"]`);
    if (out) out.textContent = input.value;
  }
  function applyPreset(name) {
    const p = PRESETS[name];
    Object.entries(p).forEach(([k, v]) => { const el = form.elements[k]; if (el) el.value = v; });
    SLIDERS.forEach((k) => syncOutput(form.elements[k]));
  }
  form.elements.quality.value = 20;
  applyPreset("balanced");

  form.addEventListener("input", (e) => {
    if (e.target.type === "range") {
      syncOutput(e.target);
      if (e.target.name !== "quality") form.querySelectorAll('input[name="preset"]').forEach((r) => { r.checked = false; });
    }
    if (e.target.name === "preset") applyPreset(e.target.value);
  });

  // Mirrors web/jobs.py upscale_size(): ×N or "fit 4K", capped by the server's megapixel limit, never smaller.
  function upscaleSize(w, h, choice) {
    let factor;
    if (["2", "3", "4"].includes(choice)) factor = Number(choice);
    else if (choice === "4k") {
      const [bw, bh] = h > w ? [2160, 3840] : w === h ? [2160, 2160] : [3840, 2160];
      factor = Math.min(bw / w, bh / h);
    } else return null;
    let ow = Math.floor(w * factor), oh = Math.floor(h * factor);
    const cap = (config.max_output_megapixels || 36) * 1e6;
    if (ow * oh > cap) { const k = Math.sqrt(cap / (ow * oh)); ow = Math.floor(ow * k); oh = Math.floor(oh * k); }
    ow = Math.max(2, ow - (ow % 2)); oh = Math.max(2, oh - (oh % 2));
    return ow <= w && oh <= h ? null : [ow, oh];
  }

  function updateUpscaleHint() {
    const hint = $("upscale-size");
    if (!imageSize) { hint.textContent = ""; return; }
    const { w, h } = imageSize;
    const choice = form.elements.upscale.value;
    if (useAI()) {
      const n = Number(choice);
      const problem = aiSizeProblem();
      hint.textContent = problem || `النتيجة: ${ltr(`${w}×${h} → ${w * n}×${h * n}`)}`;
      return;
    }
    const out = upscaleSize(w, h, choice);
    hint.textContent = out
      ? `النتيجة: ${ltr(`${w}×${h} → ${out[0]}×${out[1]}`)}`
      : choice === "none" ? `المقاس: ${ltr(`${w}×${h}`)} (بدون تغيير)` : `المقاس ${ltr(`${w}×${h}`)} كبير أصلًا، ما يحتاج تكبير`;
  }
  form.elements.upscale.addEventListener("change", updateUpscaleHint);

  // ---------- AI (runs in the browser on the visitor's GPU) ----------
  const aiToggle = form.elements.ai;
  if (!AI_SUPPORTED) {
    aiToggle.checked = false;
    aiToggle.disabled = true;
    $("ai-unsupported").hidden = false;
  }
  const useAI = () => kind === "image" && AI_SUPPORTED && aiToggle.checked;

  // The AI models only do ×2 and ×4, so the other upscale choices are disabled while AI is on.
  function syncAiControls() {
    const on = useAI();
    $("ai-options").hidden = !on;
    const select = form.elements.upscale;
    [...select.options].forEach((o) => { o.disabled = on && !["2", "4"].includes(o.value); });
    if (on && !["2", "4"].includes(select.value)) select.value = "2";
    // The AI model already removes noise and restores edges; extra denoise/sharpen would only hurt.
    ["denoise", "sharpness"].forEach((k) => { form.elements[k].disabled = on; });
    updateUpscaleHint();
    warmUp();
  }
  aiToggle.addEventListener("change", syncAiControls);
  form.elements.ai_model.addEventListener("change", warmUp);
  form.elements.upscale.addEventListener("change", warmUp);

  // Download the model and compile GPU shaders while the visitor is still choosing settings.
  let warmToken = 0;
  function warmUp() {
    const status = $("ai-status");
    if (!useAI()) { status.textContent = ""; return; }
    const token = ++warmToken;
    const model = form.elements.ai_model.value;
    const scale = Number(form.elements.upscale.value);
    VCAI.prepare(model, scale, (text) => { if (token === warmToken) status.textContent = `⏳ ${text}`; })
      .then(() => { if (token === warmToken) status.textContent = "✅ النموذج جاهز على كرت الشاشة"; })
      .catch(() => { if (token === warmToken) status.textContent = ""; }); // the real run reports errors
  }

  // Returns an Arabic message if the image is too big for an AI upscale, else "".
  function aiSizeProblem() {
    if (!useAI() || !imageSize) return "";
    const scale = Number(form.elements.upscale.value);
    const outMP = (imageSize.w * scale) * (imageSize.h * scale) / 1e6;
    if (outMP <= AI_MAX_OUTPUT_MP) return "";
    return scale === 4 && imageSize.w * imageSize.h * 4 / 1e6 <= AI_MAX_OUTPUT_MP
      ? "الصورة كبيرة على ×4 بالذكاء الاصطناعي. اختر ×2."
      : "الصورة كبيرة على التكبير بالذكاء الاصطناعي. استخدم التكبير العادي.";
  }

  function collectOptions() {
    const o = {};
    ["resolution", "upscale", "ai_model", "fps", "color_style", "image_format", ...SLIDERS].forEach((k) => { o[k] = form.elements[k].value; });
    ["interpolate", "stabilize", "deinterlace", "audio_normalize", "audio_clean"].forEach((k) => { o[k] = form.elements[k].checked; });
    return o;
  }

  form.addEventListener("submit", (e) => { e.preventDefault(); start(); });

  // ---------- 3. Upload + poll ----------
  // With AI the bar is split: AI on this device 0–50%, upload + server 50–100%.
  let phase = { base: 0, span: 1 };
  function setProgress(fraction, text, indeterminate = false) {
    fraction = phase.base + fraction * phase.span;
    const bar = $("bar");
    bar.classList.toggle("is-indeterminate", indeterminate);
    const pct = Math.round(Math.max(0, Math.min(1, fraction)) * 100);
    $("bar-fill").style.width = indeterminate ? "" : `${pct}%`;
    bar.setAttribute("aria-valuenow", String(pct));
    if (indeterminate) bar.removeAttribute("aria-valuenow");
    if (text) $("work-status").textContent = text;
  }

  async function start() {
    if (!file) return;
    const problem = aiSizeProblem();
    if (problem) { $("upscale-size").textContent = problem; form.elements.upscale.focus(); return; }

    $("start").disabled = true;
    show("work");
    aiInfo = "";
    resultNote = "";
    const options = collectOptions();

    if (!useAI()) {
      phase = { base: 0, span: 1 };
      return send(file, file.name, options);
    }

    // 1) AI upscale in the browser
    phase = { base: 0, span: 0.9 };
    setProgress(0, "تجهيز الذكاء الاصطناعي…", true);
    aiAbort = new AbortController();
    let result;
    try {
      result = await VCAI.upscale(file, {
        model: options.ai_model,
        scale: Number(options.upscale),
        signal: aiAbort.signal,
        onProgress: (f, stage) => setProgress(f, `${stage} ${f > 0 ? Math.round(f * 100) + "%" : ""}`, f === 0),
      });
    } catch (err) {
      aiAbort = null;
      if (err && err.name === "AbortError") { $("start").disabled = false; return show("setup"); }
      // AI can't run in this browser (no GPU access, a hanging in-app browser, a timeout…):
      // don't leave the visitor stuck — do the same enlargement with the server's regular upscaler.
      console.warn("AI upscale failed, falling back to the server:", err);
      aiToggle.checked = false;
      syncAiControls();
      $("ai-status").textContent = "⚠️ الذكاء الاصطناعي ما اشتغل في هذا المتصفح، فاستخدمنا التكبير العادي.";
      resultNote = "تم بالتكبير العادي لأن الذكاء الاصطناعي ما اشتغل في هذا المتصفح (جرّب Chrome مباشرة).";
      phase = { base: 0, span: 1 };
      return send(file, file.name, { ...options, upscale: options.upscale });
    }
    aiAbort = null;
    aiInfo = `✨ ${result.label} ×${options.upscale} على كرت الشاشة (${result.backend})`;
    const stem = file.name.replace(/\.[^.]+$/, "") || "image";

    // 2) Colours + saving happen right here: no upload, no queue, no download.
    const filter = colourFilter(options);
    if (!filter || canvasFilterWorks()) {
      phase = { base: 0.9, span: 0.1 };
      setProgress(0.5, "حفظ النتيجة…", true);
      try {
        return await finishOnDevice(result.canvas, filter, options, stem);
      } catch { /* fall back to the server below */ }
    }

    // Fallback (old browsers without canvas filters): the server applies colours and format.
    phase = { base: 0.5, span: 0.5 };
    const png = await new Promise((r) => result.canvas.toBlob(r, "image/png"));
    send(png, `${stem}.png`, { ...options, upscale: "none", resolution: "source", denoise: 0, sharpness: 0 });
  }

  // ---------- Finishing on the device (AI mode) ----------
  // Same intent as the server's eq/colorbalance filters (web/jobs.py → video_engine._color_filters).
  const STYLE_FILTERS = {
    none: "",
    natural: "contrast(1.025) saturate(1.035)",
    warm: "contrast(1.045) saturate(1.055) sepia(0.06)",
    bright: "brightness(1.03) contrast(1.02) saturate(1.02)",
    cinematic: "contrast(1.07) saturate(0.945) brightness(0.99)",
  };
  function colourFilter(o) {
    const parts = [];
    const b = 1 + Number(o.brightness) / 250, c = 1 + Number(o.contrast) / 160, sat = 1 + Number(o.saturation) / 110;
    if (b !== 1) parts.push(`brightness(${b.toFixed(3)})`);
    if (c !== 1) parts.push(`contrast(${c.toFixed(3)})`);
    if (sat !== 1) parts.push(`saturate(${sat.toFixed(3)})`);
    if (STYLE_FILTERS[o.color_style]) parts.push(STYLE_FILTERS[o.color_style]);
    return parts.join(" ");
  }

  let filterSupport = null;
  function canvasFilterWorks() {
    if (filterSupport !== null) return filterSupport;
    try {
      const ctx = document.createElement("canvas").getContext("2d");
      ctx.filter = "brightness(0.5)";
      ctx.fillStyle = "#fff";
      ctx.fillRect(0, 0, 1, 1);
      filterSupport = ctx.getImageData(0, 0, 1, 1).data[0] < 200;
    } catch { filterSupport = false; }
    return filterSupport;
  }

  async function finishOnDevice(canvas, filter, options, stem) {
    let out = canvas;
    if (filter) {
      out = document.createElement("canvas");
      out.width = canvas.width;
      out.height = canvas.height;
      const ctx = out.getContext("2d");
      ctx.filter = filter;
      ctx.drawImage(canvas, 0, 0);
    }
    const type = { jpg: "image/jpeg", webp: "image/webp" }[options.image_format] || "image/png";
    const quality = 0.97 - ((Number(options.quality) - 14) / 16) * 0.25; // 14 → 0.97 … 30 → 0.72
    const blob = await new Promise((resolve, reject) =>
      out.toBlob((b) => (b ? resolve(b) : reject(new Error("toBlob failed"))), type, quality));
    // Some browsers (older Safari) can't write WebP and silently give PNG.
    const ext = { "image/jpeg": "jpg", "image/webp": "webp" }[blob.type] || "png";

    if (localResultUrl) URL.revokeObjectURL(localResultUrl);
    localResultUrl = URL.createObjectURL(blob);
    $("start").disabled = false;
    showResult({
      url: localResultUrl,
      downloadUrl: localResultUrl,
      downloadName: `${stem}-videocraft.${ext}`,
      kind: "image",
      info: { width: out.width, height: out.height },
    });
  }

  function send(body, filename, options) {
    setProgress(0, "جاري الرفع… 0%");
    const params = new URLSearchParams({ filename, options: JSON.stringify(options) });
    upload = new XMLHttpRequest();
    upload.open("POST", `/api/jobs?${params}`);
    upload.setRequestHeader("Content-Type", "application/octet-stream");
    upload.upload.onprogress = (e) => {
      if (e.lengthComputable) setProgress(e.loaded / e.total * 0.3, `جاري الرفع… ${Math.round(e.loaded / e.total * 100)}%`);
    };
    upload.onload = () => {
      $("start").disabled = false;
      let data = {};
      try { data = JSON.parse(upload.responseText); } catch { /* non-JSON error page */ }
      upload = null;
      if (data.id) {
        jobId = data.id;
        store.set({ id: jobId, kind, name: file.name });
        poll();
      } else {
        fail(data.error || "تعذّر رفع الملف. تحقق من الاتصال وحاول مرة ثانية.");
      }
    };
    upload.onerror = () => { upload = null; $("start").disabled = false; fail("انقطع الاتصال أثناء الرفع. حاول مرة ثانية."); };
    upload.onabort = () => { upload = null; $("start").disabled = false; show("setup"); };
    upload.send(body);
  }

  async function poll() {
    clearTimeout(pollTimer);
    if (!jobId) return;
    let job;
    let response;
    try {
      response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, { cache: "no-store" });
      job = await response.json();
    } catch {
      // Network blip or the free instance waking up: keep waiting instead of failing.
      pollTimer = setTimeout(poll, 4000);
      return;
    }
    if (!response.ok) {
      store.clear();
      return fail(job.error || "انتهت صلاحية المهمة. ارفع الملف مرة ثانية.");
    }

    if (job.status === "queued") {
      setProgress(0.3, job.queue_position > 1 ? `في الطابور… ترتيبك ${job.queue_position}` : "في الطابور… يبدأ قريبًا", true);
    } else if (job.status === "running") {
      const indeterminate = job.kind === "image" || job.progress <= 0;
      setProgress(0.3 + job.progress * 0.7, `${job.message} ${indeterminate ? "" : Math.round(job.progress * 100) + "%"}`, indeterminate);
    } else if (job.status === "done") {
      return finish(job);
    } else if (job.status === "cancelled") {
      store.clear();
      return show(file ? "setup" : "pick");
    } else {
      store.clear();
      return fail(job.message);
    }
    pollTimer = setTimeout(poll, 1500);
  }

  $("cancel").addEventListener("click", async () => {
    if (aiAbort) return aiAbort.abort();
    if (upload) return upload.abort();
    clearTimeout(pollTimer);
    if (jobId) {
      try { await fetch(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" }); } catch { /* best effort */ }
    }
    jobId = null;
    store.clear();
    show(file ? "setup" : "pick");
  });

  // ---------- 4. Result ----------
  function finish(job) {
    const url = `/api/jobs/${encodeURIComponent(job.id)}/result`;
    showResult({ url, downloadUrl: `${url}?download=1`, downloadName: job.download_name, kind: job.kind, info: job.info || {} });
  }

  function showResult({ url, downloadUrl, downloadName, kind: resultKind, info }) {
    const dl = $("download");
    dl.href = downloadUrl;
    dl.setAttribute("download", downloadName);

    const isImage = resultKind === "image";
    $("compare-image").hidden = !isImage;
    $("compare-video").hidden = isImage;
    if (isImage) {
      // After a page reload the original file is gone, so the viewer shows the result alone.
      viewer.load(url, sourceUrl);
    } else {
      $("after-video").src = url;
      const before = $("before-video");
      before.closest("figure").hidden = !sourceUrl;
      if (sourceUrl) { before.src = sourceUrl; before.onerror = () => { before.closest("figure").hidden = true; }; }
    }
    const upscaled = info.output_width ? ` • بعد التكبير: ${ltr(`${info.output_width}×${info.output_height}`)}` : "";
    $("done-info").textContent = info.width ? `الأصل: ${ltr(`${info.width}×${info.height}`)}${upscaled}${info.duration ? ` • ${Math.round(info.duration)} ث` : ""}` : "";
    // After an AI pass the server only saw the enlarged picture, so report the true original.
    if (aiInfo && imageSize && info.width) {
      $("done-info").textContent = `الأصل: ${ltr(`${imageSize.w}×${imageSize.h}`)} • النتيجة: ${ltr(`${info.width}×${info.height}`)} • ${aiInfo}`;
    }
    if (resultNote) $("done-info").textContent += ` • ${resultNote}`;
    $("tweak").hidden = !file;
    show("done");
  }

  const viewer = CompareViewer($("compare-image")); // zoom/pan + before/after (viewer.js)

  $("tweak").addEventListener("click", () => show("setup"));
  $("retry").addEventListener("click", () => (file ? show("setup") : resetToPick()));
  $("restart").addEventListener("click", resetToPick);
  $("restart-2").addEventListener("click", resetToPick);

  // ---------- Errors / reset ----------
  function fail(message) {
    clearTimeout(pollTimer);
    $("failed-msg").textContent = message || "حدث خطأ غير متوقع.";
    show("failed");
  }

  function resetToPick() {
    clearTimeout(pollTimer);
    jobId = null;
    file = null;
    store.clear();
    $("file").value = "";
    ["after-video", "before-video"].forEach((id) => { const v = $(id); v.pause(); v.removeAttribute("src"); v.load(); });
    show("pick");
  }

  // ---------- Resume after reload ----------
  const saved = store.get();
  if (saved && saved.id) {
    jobId = saved.id;
    kind = saved.kind;
    show("work");
    setProgress(0.3, "جاري استرجاع حالة ملفك…", true);
    poll();
  }
})();
