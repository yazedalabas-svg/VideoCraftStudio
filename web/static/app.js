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
    // localStorage (not sessionStorage): a long job must survive closing the tab.
    get() { try { return JSON.parse(localStorage.getItem(STORE_KEY)); } catch { return null; } },
    set(v) { try { localStorage.setItem(STORE_KEY, JSON.stringify(v)); } catch { /* private mode */ } },
    clear() { try { localStorage.removeItem(STORE_KEY); } catch { /* ignore */ } },
  };

  // ---------- State ----------
  let config = { max_upload_mb: 4096, max_video_seconds: 300, video_resolutions: ["source", "1080p", "2k", "4k"], image_resolutions: ["source", "1080p", "2k", "4k"], video_4k: false };
  // Arabic: 3–10 دقائق, 11+ دقيقة.
  const minutesLabel = (m) => (m >= 3 && m <= 10 ? `${m} دقائق` : `${m} دقيقة`);
  const durationLabel = (sec) => (sec >= 3600 ? (sec === 3600 ? "ساعة" : `${+(sec / 3600).toFixed(1)} ساعة`) : minutesLabel(Math.max(1, Math.round(sec / 60))));
  const etaLabel = (sec) => (sec >= 5400 ? `باقي تقريبًا ${+(sec / 3600).toFixed(1)} ساعة`
    : sec >= 90 ? `باقي تقريبًا ${minutesLabel(Math.round(sec / 60))}` : "باقي أقل من دقيقتين");
  const sizeLabel = (mb) => (mb >= 1024 ? `${+(mb / 1024).toFixed(1)} غيغابايت` : `${mb} ميغابايت`);
  let file = null;
  let kind = "video";
  let sourceUrl = null;
  let jobId = null;
  let pollTimer = null;
  let imageSize = null; // natural size of the picked image, for the upscale preview
  let imageUndecodable = false; // the browser can't decode it (HEIC on Android…)
  let aiAutoOff = ""; // why AI was switched off for this photo (shown to the visitor)
  let videoDuration = 0; // seconds, when the browser can read it (checked before uploading)
  let aiAbort = null; // AbortController while the in-browser AI is running
  let aiInfo = ""; // e.g. "Real-CUGAN ×4 • webgpu", shown with the result
  let localResultUrl = null; // blob: URL of a result made on this device (AI mode)
  let resultNote = ""; // extra line under the result, e.g. why AI was skipped
  const AI_SUPPORTED = Boolean(window.VCAI && VCAI.supported());
  // iPhone/iPad Safari refuses canvases above ~16.7 MP (the AI result is drawn on one);
  // elsewhere the cap matches the server's and keeps browser memory sane.
  const IS_IOS = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const AI_MAX_OUTPUT_MP = IS_IOS ? 16 : 36;
  const IS_PHONE = IS_IOS || /Android|Mobile/i.test(navigator.userAgent);
  if (IS_PHONE) document.getElementById("image_format").value = "jpg";

  // ---------- Views ----------
  const views = ["pick", "setup", "work", "done", "failed"];
  // Phones pause pages when the screen turns off, which stalls long uploads; keep it
  // on while working (Screen Wake Lock, where supported).
  let wakeLock = null;
  async function keepAwake(on) {
    try {
      if (on && !wakeLock && navigator.wakeLock) {
        wakeLock = await navigator.wakeLock.request("screen");
        wakeLock.addEventListener("release", () => { wakeLock = null; });
      } else if (!on && wakeLock) {
        await wakeLock.release();
      }
    } catch { /* not allowed right now; uploads still resume on their own */ }
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden && !$("work").hidden) keepAwake(true); });

  function show(view) {
    views.forEach((v) => { $(v).hidden = v !== view; });
    keepAwake(view === "work");
    if (view !== "done") { try { viewer.stop(); } catch { /* viewer not created yet */ } }
    const heading = $(view).querySelector("h1, h2");
    if (heading && view !== "pick") { heading.tabIndex = -1; heading.focus({ preventScroll: false }); }
  }

  // ---------- Config ----------
  fetch("/api/config")
    .then((r) => (r.ok ? r.json() : Promise.reject()))
    .then((c) => {
      config = c;
      $("limits").textContent = `فيديو حتى ${durationLabel(c.max_video_seconds)} أو صورة • حتى ${sizeLabel(c.max_upload_mb)}`;
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
    if (f.size > config.max_upload_mb * 1024 * 1024) return showPickError(`الملف أكبر من ${sizeLabel(config.max_upload_mb)}.`);

    file = f;
    kind = isImage ? "image" : "video";
    // Phones (gallery / cloud photos, iOS after the tab was in the background) often lose
    // read access to a picked file a little later, and the upload then fails. Photos are
    // small, so copy them into memory now, while access is still fresh.
    if (isImage && f.size <= 150 * 1024 * 1024) {
      f.arrayBuffer()
        .then((buf) => { if (file === f) file = new File([buf], f.name, { type: f.type, lastModified: f.lastModified }); })
        .catch(() => { /* keep the original; the upload retries the read */ });
    }
    if (sourceUrl) URL.revokeObjectURL(sourceUrl);
    sourceUrl = URL.createObjectURL(f);

    // Thumbnail
    const thumb = $("thumb");
    thumb.replaceChildren();
    const media = document.createElement(kind === "image" ? "img" : "video");
    media.src = sourceUrl;
    imageSize = null;
    // AI switched off only because of the previous photo → back on for this one.
    if (aiAutoOff && AI_SUPPORTED && !aiFailedHere()) aiToggle.checked = true;
    aiAutoOff = "";
    if (kind === "image") {
      media.alt = "";
      imageUndecodable = false;
      media.onload = () => {
        imageSize = { w: media.naturalWidth, h: media.naturalHeight };
        fitChoicesToPhoto();
      };
      // This browser can't show the file (e.g. HEIC on Android): the server converts it,
      // but the on-device AI can't read it either.
      media.onerror = () => {
        media.remove();
        imageUndecodable = true;
        fitChoicesToPhoto();
      };
    } else {
      media.muted = true;
      media.preload = "metadata";
      media.onerror = () => media.remove();
    }
    thumb.append(media);

    // Duration check for video (when the browser can read it)
    if (kind === "video") {
      const probe = document.createElement("video");
      probe.preload = "metadata";
      videoDuration = 0;
      $("long-note").hidden = true; // shown only once the length is known and long
      probe.onloadedmetadata = () => {
        videoDuration = probe.duration || 0;
        if (!videoAiFits()) {
          $("ai-status").textContent = `ℹ️ الذكاء الاصطناعي للفيديو للمقاطع حتى ${videoAiMax()} ثانية، وهذا المقطع أطول، فبيتعالج بالطريقة العادية.`;
        }
        syncAiControls();
        const long = videoDuration > (config.long_video_seconds || 600);
        form.elements.priority.value = long ? "speed" : "quality";
        const note = $("long-note");
        note.hidden = !long;
        const hours = Math.round((config.result_ttl_seconds || 21600) / 3600);
        note.textContent = long
          ? `مقطع طويل (${durationLabel(Math.round(videoDuration))}): على الخادم المجاني المعالجة ممكن تأخذ ساعات. تقدر تسكّر الصفحة وترجع لها من نفس الجهاز، والنتيجة تنتظرك ${hours} ساعات بعد ما تخلص.`
          : "";
        if (probe.duration > config.max_video_seconds) {
          show("pick");
          showPickError(`الفيديو أطول من ${durationLabel(config.max_video_seconds)}. قصّه أولًا أو استخدم نسخة ويندوز.`);
        }
      };
      probe.src = sourceUrl;
    }

    $("setup-title").textContent = f.name;
    $("file-size").textContent = `${kind === "image" ? "صورة" : "فيديو"} • ${formatSize(f.size)}`;

    document.querySelectorAll("[data-only]").forEach((el) => { el.hidden = el.dataset.only !== kind; });
    if (kind !== "video") $("long-note").hidden = true;
    const res = $("resolution");
    const allowed = kind === "image" ? config.image_resolutions : config.video_resolutions;
    res.replaceChildren(...allowed.map((v) => {
      // Video on the server: "source" means up to 1080p, and 4K needs a bigger instance.
      const label = kind === "video" && v === "source" ? "الأصلية (حتى 1080p)" : RES_LABELS[v] || v;
      const o = new Option(label, v);
      if (kind === "video" && v === "4k" && !config.video_4k) { o.disabled = true; o.text = "4K (يحتاج خادم أقوى)"; }
      return o;
    }));
    res.value = "source";
    updateResolutionHint();
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

  // Mirrors web/jobs.py upscale_size(): ×N or "fit 1080p/2K/4K" (orientation-aware),
  // capped by the server's megapixel limit, never smaller than the source.
  const PRESET_BOXES = { "1080p": [1920, 1080], "2k": [2560, 1440], "4k": [3840, 2160] };
  function upscaleSize(w, h, choice) {
    let factor;
    if (["2", "3", "4"].includes(choice)) factor = Number(choice);
    else if (PRESET_BOXES[choice]) {
      const [long, short] = PRESET_BOXES[choice];
      const [bw, bh] = h > w ? [short, long] : w === h ? [short, short] : [long, short];
      factor = Math.min(bw / w, bh / h);
    } else return null;
    let ow = Math.floor(w * factor), oh = Math.floor(h * factor);
    const cap = (config.max_output_megapixels || 36) * 1e6;
    if (ow * oh > cap) { const k = Math.sqrt(cap / (ow * oh)); ow = Math.floor(ow * k); oh = Math.floor(oh * k); }
    ow = Math.max(2, ow - (ow % 2)); oh = Math.max(2, oh - (oh % 2));
    return ow <= w && oh <= h ? null : [ow, oh];
  }

  // AI models come in ×2 and ×4: pick the smallest that reaches the target (the result is
  // then fitted to the exact size), within the browser memory cap.
  function aiPlan(w, h, choice) {
    const target = upscaleSize(w, h, choice);
    if (!target) return null;
    const need = target[0] / w;
    let scale = need <= 2 ? 2 : 4;
    if (scale === 4 && w * h * 16 / 1e6 > AI_MAX_OUTPUT_MP) scale = 2;
    if (w * h * scale * scale / 1e6 > AI_MAX_OUTPUT_MP) return { target, scale: 0 };
    return { target, scale };
  }

  function updateUpscaleHint() {
    const hint = $("upscale-size");
    if (!imageSize) { hint.textContent = ""; return; }
    const { w, h } = imageSize;
    const choice = form.elements.upscale.value;
    if (useAI()) {
      const problem = aiSizeProblem();
      const plan = aiPlan(w, h, choice);
      hint.textContent = problem || `النتيجة: ${ltr(`${w}×${h} → ${plan.target[0]}×${plan.target[1]}`)} (ذكاء اصطناعي ×${plan.scale})`;
      return;
    }
    const out = upscaleSize(w, h, choice);
    hint.textContent = out
      ? `النتيجة: ${ltr(`${w}×${h} → ${out[0]}×${out[1]}`)}`
      : choice === "none" ? `المقاس: ${ltr(`${w}×${h}`)} (بدون تغيير)` : `المقاس ${ltr(`${w}×${h}`)} كبير أصلًا، ما يحتاج تكبير`;
  }
  form.elements.upscale.addEventListener("change", updateUpscaleHint);

  function updateResolutionHint() {
    const limits = config.video_seconds_by_resolution || { "2k": 90, "4k": 30 };
    const v = form.elements.resolution.value;
    $("resolution-hint").textContent = limits[v]
      ? `على الموقع للمقاطع حتى ${limits[v]} ثانية، وتأخذ وقت أطول.`
      : "";
  }
  form.elements.resolution.addEventListener("change", updateResolutionHint);

  // ---------- AI (runs in the browser on the visitor's GPU) ----------
  const aiToggle = form.elements.ai;
  if (!AI_SUPPORTED) {
    aiToggle.checked = false;
    aiToggle.disabled = true;
    $("ai-unsupported").hidden = false;
  }
  const useAI = () => kind === "image" && AI_SUPPORTED && aiToggle.checked;
  // Video AI: frames are upscaled on this device (web/aivideo.py). Short clips only.
  const videoAiMax = () => (config.ai_video && config.ai_video.max_seconds) || 60;
  const videoAiFits = () => !videoDuration || videoDuration <= videoAiMax();
  const useVideoAI = () => kind === "video" && AI_SUPPORTED && aiToggle.checked && videoAiFits();

  // If AI got stuck on this device before, start with it off so nobody waits again
  // (the visitor can still switch it back on; that clears the memory).
  const AI_OFF_KEY = "videocraft-ai-failed";
  const aiFailedHere = () => { try { return Date.now() - Number(localStorage.getItem(AI_OFF_KEY) || 0) < 7 * 864e5; } catch { return false; } };
  const rememberAiFailed = (failed) => { try { failed ? localStorage.setItem(AI_OFF_KEY, String(Date.now())) : localStorage.removeItem(AI_OFF_KEY); } catch { /* storage blocked */ } };
  if (AI_SUPPORTED && aiFailedHere()) {
    aiToggle.checked = false;
    $("ai-status").textContent = "ℹ️ الذكاء الاصطناعي علّق في هذا الجهاز قبل، فطفّيناه وصار التكبير عادي. تقدر تشغّله يدويًا.";
  }
  aiToggle.addEventListener("change", () => { if (aiToggle.checked) rememberAiFailed(false); });

  // AI always enlarges, so "كما هي" is the only choice it can't serve.
  function syncAiControls() {
    const on = useAI() || useVideoAI();
    $("ai-options").hidden = !on;
    $("ai-video-fields").hidden = kind !== "video";
    const select = form.elements.upscale;
    [...select.options].forEach((o) => { o.disabled = useAI() && o.value === "none"; });
    if (useAI() && select.value === "none") select.value = "2k";
    // The AI model already removes noise and restores edges; extra denoise/sharpen would only hurt.
    ["denoise", "sharpness"].forEach((k) => { form.elements[k].disabled = on; });
    // Video AI decides size and frame rate itself; these server-only choices don't apply.
    ["resolution", "fps", "priority"].forEach((k) => {
      form.elements[k].closest(".field").hidden = kind !== "video" || useVideoAI();
    });
    form.elements.interpolate.disabled = useVideoAI();
    $("ai-hint").textContent = kind === "video"
      ? `نفس نماذج نسخة ويندوز على كل إطار، على كرت الشاشة في جهازك. للمقاطع حتى ${videoAiMax()} ثانية، وبإطارات ودقة خفيفة على الجوال. تقدر تسكّر الصفحة وترجع تكمل.`
      : "نفس نماذج نسخة ويندوز، وتشتغل على كرت الشاشة في جهازك (WebGPU/WebGL)، والنتيجة تنحفظ على جهازك مباشرة بدون رفع. النموذج يتجهّز وأنت تختار الإعدادات.";
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
    if (!useAI() && !useVideoAI()) { if (!aiFailedHere()) status.textContent = ""; return; }
    const token = ++warmToken;
    const model = form.elements.ai_model.value;
    const plan = kind === "image" && imageSize && aiPlan(imageSize.w, imageSize.h, form.elements.upscale.value);
    const scale = plan && plan.scale ? plan.scale : 2; // video frames are always ×2
    // Only the latest warm-up may write, and only while AI is still on (it can be switched
    // off automatically once the photo's size is known).
    const current = () => token === warmToken && (useAI() || useVideoAI());
    VCAI.prepare(model, scale, (text) => { if (current()) status.textContent = `⏳ ${text}`; })
      .then(() => { if (current()) status.textContent = "✅ النموذج جاهز على كرت الشاشة"; })
      .catch(() => { if (current()) status.textContent = ""; }); // the real run reports errors
  }

  // Phone photos are usually 12 MP or more — already past 2K — so the defaults adapt:
  // big photos keep their size (the enhancement still applies), and AI switches itself
  // off with a reason when it can't serve the photo, instead of blocking the start button.
  function fitChoicesToPhoto() {
    if (kind !== "image") return;
    aiAutoOff = "";
    const select = form.elements.upscale;
    if (imageSize) {
      const { w, h } = imageSize;
      const reaches = (v) => Boolean(upscaleSize(w, h, v));
      if (!reaches(select.value)) select.value = ["2k", "4k"].find(reaches) || "none";
    }
    if (AI_SUPPORTED && aiToggle.checked) {
      if (imageUndecodable) {
        aiAutoOff = "ℹ️ متصفحك ما يقرأ صيغة هذه الصورة، فبيحسّنها الخادم بالطريقة العادية.";
      } else if (imageSize) {
        const plan = aiPlan(imageSize.w, imageSize.h, select.value === "none" ? "2" : select.value);
        if (!plan || !plan.scale) {
          aiAutoOff = `ℹ️ الصورة كبيرة وواضحة (${ltr(`${imageSize.w}×${imageSize.h}`)})، فالتحسين العادي أنسب لها من الذكاء الاصطناعي على الجوال.`;
        }
      }
      if (aiAutoOff) {
        aiToggle.checked = false;
        $("ai-status").textContent = aiAutoOff;
      }
    }
    syncAiControls();
    if (aiAutoOff) $("ai-status").textContent = aiAutoOff; // keep the reason visible
  }

  // Returns an Arabic message if the image is too big for an AI upscale, else "".
  function aiSizeProblem() {
    if (!useAI() || !imageSize) return "";
    const plan = aiPlan(imageSize.w, imageSize.h, form.elements.upscale.value);
    if (!plan) return `الصورة ${ltr(`${imageSize.w}×${imageSize.h}`)} أكبر من هذا المقاس أصلًا. اختر مقاس أكبر أو ×2.`;
    if (!plan.scale) return "الصورة كبيرة على التكبير بالذكاء الاصطناعي. طفّه واستخدم التكبير العادي.";
    return "";
  }

  function collectOptions() {
    const o = {};
    ["resolution", "upscale", "ai_model", "ai_fps", "ai_size", "priority", "fps", "color_style", "image_format", ...SLIDERS].forEach((k) => { o[k] = form.elements[k].value; });
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
    // Don't upload gigabytes just to be told the clip is too long for this resolution.
    const limits = config.video_seconds_by_resolution || {};
    const perRes = limits[form.elements.resolution.value];
    if (kind === "video" && videoDuration && perRes && videoDuration > perRes) {
      updateResolutionHint();
      $("resolution-hint").textContent = `هذا المقطع ${Math.round(videoDuration)} ثانية، وهذه الدقة للمقاطع حتى ${perRes} ثانية. اختر 1080p.`;
      form.elements.resolution.focus();
      return;
    }
    const problem = aiSizeProblem();
    if (problem) {
      // Don't dead-end: this choice doesn't suit AI, so do it the regular way and say why.
      aiToggle.checked = false;
      syncAiControls();
      $("ai-status").textContent = `ℹ️ ${problem.replace(/ ?طفّه.*$/, "")} استخدمنا التحسين العادي.`;
    }

    $("start").disabled = true;
    show("work");
    aiInfo = "";
    resultNote = "";
    resent = false;
    const options = collectOptions();

    if (useVideoAI()) {
      phase = { base: 0, span: 1 };
      return send(file, file.name, { ...options, ai_video: true });
    }
    if (!useAI()) {
      phase = { base: 0, span: 1 };
      return send(file, file.name, options);
    }

    // 1) AI upscale in the browser
    phase = { base: 0, span: 0.9 };
    setProgress(0, "تجهيز الذكاء الاصطناعي…", true);
    aiAbort = new AbortController();
    let result;
    const plan = aiPlan(imageSize.w, imageSize.h, options.upscale);
    const filter = colourFilter(options);
    try {
      // Fitting to the exact size, colours and encoding all happen in the AI worker,
      // so saving a big result never freezes the page (this is what phones choked on).
      result = await VCAI.upscale(file, {
        model: options.ai_model,
        scale: plan.scale,
        signal: aiAbort.signal,
        finish: { type: outputType(options), quality: outputQuality(options), filter, target: plan.target },
        onProgress: (f, stage) => setProgress(f, `${stage} ${f > 0 && f < 1 ? Math.round(f * 100) + "%" : ""}`, f === 0 || f === 1),
      });
    } catch (err) {
      aiAbort = null;
      if (err && err.name === "AbortError") { $("start").disabled = false; return show("setup"); }
      // AI can't run in this browser (no GPU access, a hanging in-app browser, a timeout…):
      // don't leave the visitor stuck — do the same enlargement with the server's regular upscaler.
      console.warn("AI upscale failed, falling back to the server:", err);
      rememberAiFailed(true);
      aiToggle.checked = false;
      syncAiControls();
      $("ai-status").textContent = "⚠️ الذكاء الاصطناعي ما اشتغل في هذا المتصفح، فاستخدمنا التكبير العادي.";
      resultNote = "تم بالتكبير العادي لأن الذكاء الاصطناعي ما اشتغل في هذا المتصفح (جرّب Chrome مباشرة).";
      phase = { base: 0, span: 1 };
      return send(file, file.name, { ...options, upscale: options.upscale });
    }
    aiAbort = null;
    aiInfo = `✨ ${result.label} ×${plan.scale} على كرت الشاشة (${result.backend})`;
    const stem = file.name.replace(/\.[^.]+$/, "") || "image";

    // 2) Done on the device: no upload, no queue, no download.
    if (result.filtered) return showLocalResult(result, stem);

    // Old browsers without canvas filters: the worker returned a PNG without colours,
    // so the server applies them (and the chosen format).
    phase = { base: 0.5, span: 0.5 };
    send(result.blob, `${stem}.png`, { ...options, upscale: "none", resolution: "source", denoise: 0, sharpness: 0 });
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



  const outputType = (o) => ({ jpg: "image/jpeg", webp: "image/webp" }[o.image_format] || "image/png");
  const outputQuality = (o) => 0.97 - ((Number(o.quality) - 14) / 16) * 0.25; // 14 → 0.97 … 30 → 0.72

  function showLocalResult(result, stem) {
    // Some browsers (older Safari) can't write WebP and silently give PNG.
    const ext = { "image/jpeg": "jpg", "image/webp": "webp" }[result.blob.type] || "png";
    if (localResultUrl) URL.revokeObjectURL(localResultUrl);
    localResultUrl = URL.createObjectURL(result.blob);
    $("start").disabled = false;
    showResult({
      url: localResultUrl,
      downloadUrl: localResultUrl,
      downloadName: `${stem}-videocraft.${ext}`,
      kind: "image",
      info: { width: result.width, height: result.height },
    });
  }

  // ---------- Reliable upload ----------
  // Mobile connections drop, and Render's free instance sleeps after 15 min and restarts on
  // deploys. So: wake the server first, send the file in 1 MB chunks, retry any chunk that
  // fails, and start over by itself if the server lost the upload (restart).
  let uploadAbort = null;
  let lastSend = null; // { body, filename, options } for one automatic re-send
  let resent = false;
  const sleep = (ms, signal) => new Promise((resolve, reject) => {
    const t = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => { clearTimeout(t); reject(new DOMException("cancelled", "AbortError")); }, { once: true });
  });

  class UploadError extends Error {}

  // fetch + JSON with retries for network errors and proxy hiccups (502/504, or HTML pages).
  async function api(method, url, body, signal) {
    let wait = 1000;
    for (let attempt = 1; ; attempt++) {
      try {
        const r = await fetch(url, { method, body, signal, cache: "no-store", headers: body ? { "Content-Type": "application/octet-stream" } : {} });
        let data = null;
        try { data = await r.json(); } catch { /* proxy error page */ }
        if (data && r.status !== 502 && r.status !== 504) return { status: r.status, data };
      } catch (err) {
        if (err.name === "AbortError") throw err;
      }
      if (attempt >= 6) throw new UploadError("الاتصال بالخادم ضعيف أو مقطوع. تحقق من الإنترنت وحاول مرة ثانية.");
      await sleep(wait, signal);
      wait = Math.min(wait * 2, 15000);
    }
  }

  async function wakeServer(signal) {
    const t0 = Date.now();
    for (;;) {
      try {
        const r = await fetch("/healthz", { cache: "no-store", signal });
        if (r.ok) return;
      } catch (err) { if (err.name === "AbortError") throw err; }
      if (Date.now() - t0 > 120000) throw new UploadError("الخادم ما رد. حاول بعد دقيقة.");
      setProgress(0, "تشغيل الخادم… (أول مرة بعد فترة يأخذ حتى دقيقة)", true);
      await sleep(3000, signal);
    }
  }

  async function uploadFile(body, filename, options, signal) {
    await wakeServer(signal);
    for (let round = 1; ; round++) {
      const q = new URLSearchParams({ filename, size: String(body.size) });
      const begin = await api("POST", `/api/uploads?${q}`, null, signal);
      if (begin.status !== 201) throw new UploadError(begin.data.error || "تعذّر بدء الرفع.");
      const id = begin.data.upload_id;
      const chunk = begin.data.chunk_size;
      let offset = 0;
      let lost = false;
      while (offset < body.size) {
        let bytes;
        for (let tries = 1; !bytes; tries++) {
          try {
            bytes = await body.slice(offset, offset + chunk).arrayBuffer();
          } catch {
            // A read can fail once while the phone brings the file back (cloud photo, app switch).
            if (tries < 3) { await sleep(700 * tries, signal); continue; }
            throw new UploadError("تعذّر قراءة الملف من جهازك. اختره مرة ثانية (ولو من «الملفات» بدل المعرض).");
          }
        }
        const put = await api("PUT", `/api/uploads/${encodeURIComponent(id)}?offset=${offset}`, bytes, signal);
        if (put.status === 404) { lost = true; break; } // server restarted: start this file over
        if (put.status !== 200 && put.status !== 409) throw new UploadError(put.data.error || "تعذّر رفع جزء من الملف.");
        offset = put.data.received;
        const f = offset / body.size;
        setProgress(f * 0.3, `جاري الرفع… ${Math.round(f * 100)}%`);
      }
      if (!lost) {
        const params = new URLSearchParams({ options: JSON.stringify(options) });
        const done = await api("POST", `/api/uploads/${encodeURIComponent(id)}/finish?${params}`, null, signal);
        if (done.data && done.data.id) return done.data;
        if (done.status !== 404) throw new UploadError(done.data.error || "تعذّر إنهاء الرفع.");
      }
      if (round >= 3) throw new UploadError("الخادم يعيد التشغيل. حاول بعد دقيقة.");
      setProgress(0, "الخادم أعاد التشغيل، نعيد الرفع…", true);
    }
  }

  async function send(body, filename, options) {
    lastSend = { body, filename, options };
    setProgress(0, "جاري الرفع… 0%");
    uploadAbort = new AbortController();
    try {
      const job = await uploadFile(body, filename, options, uploadAbort.signal);
      jobId = job.id;
      store.set({ id: jobId, kind, name: file ? file.name : filename, aiModel: options.ai_model });
      poll();
    } catch (err) {
      if (err.name === "AbortError") return show(file ? "setup" : "pick");
      fail(err instanceof UploadError ? err.message : "تعذّر رفع الملف. تحقق من الاتصال وحاول مرة ثانية.");
    } finally {
      uploadAbort = null;
      $("start").disabled = false;
    }
  }

  async function poll() {
    clearTimeout(pollTimer);
    if (!jobId) return;
    const id = jobId;
    let job;
    let response;
    try {
      response = await fetch(`/api/jobs/${encodeURIComponent(id)}`, { cache: "no-store" });
      job = await response.json();
    } catch {
      // Network blip or the free instance waking up: keep waiting instead of failing.
      if (id === jobId) pollTimer = setTimeout(poll, 4000);
      return;
    }
    if (id !== jobId) return; // cancelled (or a new file started) while this reply was on its way
    if (!response.ok) {
      store.clear();
      // A job saved from an earlier visit has expired: start fresh without an error.
      if (resuming && response.status === 404) { resuming = false; return show("pick"); }
      // The free instance restarted and lost the job: send the same file again once.
      if (response.status === 404 && lastSend && !resent) {
        resent = true;
        setProgress(0, "الخادم أعاد التشغيل، نعيد الإرسال…", true);
        return send(lastSend.body, lastSend.filename, lastSend.options);
      }
      return fail(job.error || "انتهت صلاحية المهمة. ارفع الملف مرة ثانية.");
    }

    resuming = false;
    if (job.status === "awaiting_ai") {
      return runAiFrames(job);
    }
    if (job.status === "queued") {
      setProgress(0.3, job.queue_position > 1 ? `في الطابور… ترتيبك ${job.queue_position}` : "في الطابور… يبدأ قريبًا", true);
    } else if (job.status === "running" && job.eta) {
      const eta = etaLabel(job.eta);
      setProgress(0.3 + job.progress * 0.7, `${job.message} ${Math.round(job.progress * 100)}% • ${eta}`);
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

  // ---------- AI video: upscale the server's frames on this device ----------
  let framesRunning = false;

  async function fetchFrame(url, signal) {
    let wait = 1000;
    for (let attempt = 1; ; attempt++) {
      try {
        const r = await fetch(url, { cache: "no-store", signal });
        if (r.ok) return await r.blob();
        if (r.status === 404) throw new UploadError("الإطارات ما عادت موجودة على الخادم.");
      } catch (err) {
        if (err.name === "AbortError" || err instanceof UploadError) throw err;
      }
      if (attempt >= 6) throw new UploadError("الاتصال ضعيف، ما قدرت أجيب الإطارات.");
      await sleep(wait, signal);
      wait = Math.min(wait * 2, 15000);
    }
  }

  async function runAiFrames(job) {
    if (framesRunning) return;
    framesRunning = true;
    const id = encodeURIComponent(job.id);
    const saved = store.get() || {};
    const model = saved.aiModel || form.elements.ai_model.value;
    const label = (VCAI.MODELS[model] || VCAI.MODELS.photo).label;
    const total = job.ai.frames;
    aiAbort = new AbortController();
    const signal = aiAbort.signal;
    const started = performance.now();
    let doneHere = 0;
    const frameUrl = (n) => `/api/jobs/${id}/frames/${n}`;
    try {
      let n = job.ai.next_missing;
      let pending = n ? fetchFrame(frameUrl(n), signal) : null;
      setProgress(0.3, "تجهيز الذكاء الاصطناعي على جهازك…", true);
      while (n) {
        const blob = await pending;
        const guess = n < total ? n + 1 : null;
        pending = guess ? fetchFrame(frameUrl(guess), signal) : null; // download the next frame meanwhile
        const res = await VCAI.upscale(blob, { model, scale: 2, signal, finish: { type: "image/jpeg", quality: 0.92 } });
        const put = await api("PUT", frameUrl(n), res.blob, signal);
        if (put.status !== 200) throw new UploadError(put.data.error || "تعذّر رفع إطار.");
        doneHere++;
        const done = put.data.done;
        const perFrame = (performance.now() - started) / doneHere / 1000;
        const eta = etaLabel(Math.round((total - done) * perFrame));
        setProgress(0.3 + (done / total) * 0.6, `الذكاء الاصطناعي على جهازك: إطار ${done} من ${total} • ${eta}`);
        const next = put.data.next_missing;
        if (next !== guess) pending = next ? fetchFrame(frameUrl(next), signal) : null;
        n = next;
        await sleep(15, signal); // a breath between frames keeps the phone responsive and cooler
      }
      const r = await api("POST", `/api/jobs/${id}/assemble`, null, signal);
      if (r.status !== 200) throw new UploadError(r.data.error || "تعذّر تجميع الفيديو.");
      aiInfo = `✨ ${label} ×2 على كرت الشاشة • ${job.ai.fps} إطار/ث`;
      framesRunning = false;
      aiAbort = null;
      return poll();
    } catch (err) {
      framesRunning = false;
      aiAbort = null;
      if (err.name === "AbortError") {
        try { await fetch(`/api/jobs/${id}/cancel`, { method: "POST" }); } catch { /* best effort */ }
        jobId = null;
        store.clear();
        return show(file ? "setup" : "pick");
      }
      // AI can't run here (or the network gave up): finish the same upload the regular way.
      console.warn("AI video failed, falling back to regular processing:", err);
      if (!(err instanceof UploadError)) rememberAiFailed(true);
      resultNote = "تم بالمعالجة العادية لأن الذكاء الاصطناعي ما اكتمل على هذا الجهاز.";
      try { await api("POST", `/api/jobs/${id}/regular`, null); } catch { /* poll reports the state */ }
      return poll();
    }
  }

  $("cancel").addEventListener("click", async () => {
    if (aiAbort) return aiAbort.abort();
    if (uploadAbort) return uploadAbort.abort();
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
    const before = job.has_before ? `/api/jobs/${encodeURIComponent(job.id)}/before` : null;
    showResult({ url, downloadUrl: `${url}?download=1`, downloadName: job.download_name, kind: job.kind, info: job.info || {}, before });
  }

  function showResult({ url, downloadUrl, downloadName, kind: resultKind, info, before = null }) {
    const dl = $("download");
    dl.href = downloadUrl;
    dl.setAttribute("download", downloadName);

    const isImage = resultKind === "image";
    $("compare-image").hidden = false;
    // "Before" = the server's matching clip (AI video: same frames and timing), else the
    // original file on this device (gone after a page reload → the result shows alone).
    viewer.load(url, before || sourceUrl, { video: !isImage });
    const upscaled = info.output_width ? ` • بعد التكبير: ${ltr(`${info.output_width}×${info.output_height}`)}` : "";
    $("done-info").textContent = info.width ? `الأصل: ${ltr(`${info.width}×${info.height}`)}${upscaled}${info.duration ? ` • ${Math.round(info.duration)} ث` : ""}` : "";
    // After an AI pass the server only saw the enlarged picture, so report the true original.
    if (aiInfo && imageSize && info.width) {
      $("done-info").textContent = `الأصل: ${ltr(`${imageSize.w}×${imageSize.h}`)} • النتيجة: ${ltr(`${info.width}×${info.height}`)} • ${aiInfo}`;
    }
    if (aiInfo && !isImage) $("done-info").textContent += ` • ${aiInfo}`;
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
    viewer.stop();
    show("pick");
  }

  // ---------- Resume after reload ----------
  let resuming = false;
  const saved = store.get();
  if (saved && saved.id) {
    resuming = true;
    jobId = saved.id;
    kind = saved.kind;
    show("work");
    setProgress(0.3, "جاري استرجاع حالة ملفك…", true);
    poll();
  }
})();
