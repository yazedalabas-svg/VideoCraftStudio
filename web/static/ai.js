// VideoCraft Studio — AI upscaling that runs on the visitor's own GPU.
//
// Same model families as the Windows app (Real-ESRGAN, Real-CUGAN), converted
// to TensorFlow.js graph models. The converted graphs only accept a fixed
// 64×64 input (the shape is baked into their weights), so the image is cut into
// overlapping tiles and only the centre of every tile is kept, so seams never show.
//
// Speed notes (the slow part on phones is waiting for the GPU, not the maths):
//  • tiles are queued on the GPU in groups and read back once per group, not once per tile;
//  • tile centres are cropped, joined and (for ×2 from a ×4 model) shrunk on the GPU,
//    so far less data travels back to JavaScript;
//  • prepare() downloads the model and compiles the GPU shaders ahead of time,
//    while the visitor is still choosing settings.
//
// Usage:
//   VCAI.prepare("photo", 2, onStatus);                 // optional warm-up
//   const { canvas } = await VCAI.upscale(file, { model, scale, onProgress, signal });
window.VCAI = (() => {
  "use strict";

  const BASE = "/static";
  const TILE = 64; // model input size (fixed by the converted graphs)
  const PAD = 6; // context pixels discarded on each side of a tile
  const STEP = TILE - PAD * 2;
  const GROUP = 12; // tiles queued on the GPU before one read-back

  const MODELS = {
    photo: { label: "Real-ESRGAN (صور)", path: () => "realesrgan/general_fast-64", native: () => 4 },
    anime: { label: "Real-ESRGAN Anime", path: () => "realesrgan/anime_fast-64", native: () => 4 },
    anime_extreme: { label: "Real-CUGAN", path: (s) => `realcugan/${s}x-conservative-64`, native: (s) => s },
    anime_clean: { label: "Real-CUGAN Denoise", path: (s) => `realcugan/${s}x-denoise3x-64`, native: (s) => s },
  };

  let runtime = null; // Promise<backend name>
  const prepared = new Map(); // model path → Promise<GraphModel> (downloaded + shaders compiled)

  // ---------- Runtime ----------
  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src;
      s.onload = resolve;
      s.onerror = () => reject(new Error(`تعذّر تحميل ${src}`));
      document.head.append(s);
    });
  }

  function ensureRuntime() {
    runtime ??= (async () => {
      if (!window.tf) await loadScript(`${BASE}/vendor/tf.min.js`);
      let backend = null;
      if (navigator.gpu) {
        try {
          await loadScript(`${BASE}/vendor/tf-backend-webgpu.min.js`);
          if (await tf.setBackend("webgpu")) backend = "webgpu";
        } catch { /* fall back to WebGL */ }
      }
      if (!backend) {
        // Half-precision textures are about twice as fast on phone GPUs; image quality is unaffected.
        if (/Android|iPhone|iPad|Mobile/i.test(navigator.userAgent)) tf.env().set("WEBGL_FORCE_F16_TEXTURES", true);
        try { if (await tf.setBackend("webgl")) backend = "webgl"; } catch { /* no GPU path */ }
      }
      if (!backend) throw new Error("متصفحك لا يدعم تشغيل الذكاء الاصطناعي على كرت الشاشة (WebGPU / WebGL).");
      await tf.ready();
      return backend;
    })();
    runtime.catch(() => { runtime = null; }); // allow a retry after a failure
    return runtime;
  }

  async function loadModel(path, onFraction) {
    const cacheKey = `indexeddb://videocraft-${path.replace(/\//g, "-")}`;
    try {
      const cached = await tf.loadGraphModel(cacheKey); // saved on an earlier visit
      onFraction?.(1);
      return cached;
    } catch { /* not cached yet */ }
    const model = await tf.loadGraphModel(`${BASE}/models/${path}/model.json`, { onProgress: onFraction });
    model.save(cacheKey).catch(() => { /* private mode / quota: fine */ });
    return model;
  }

  /**
   * Download the model and compile its GPU shaders so the real run starts at full speed.
   * Safe to call many times; the work happens once per model.
   * @param {(text: string, fraction: number|null) => void} [onStatus]
   */
  function prepare(model = "photo", scale = 2, onStatus) {
    const spec = MODELS[model] || MODELS.photo;
    const path = spec.path(scale === 4 ? 4 : 2);
    if (!prepared.has(path)) {
      const job = (async () => {
        onStatus?.("تجهيز كرت الشاشة…", null);
        await ensureRuntime();
        const graph = await loadModel(path, (f) => onStatus?.(`تحميل النموذج… ${Math.round(f * 100)}%`, f * 0.8));
        onStatus?.("تسخين كرت الشاشة… (أول مرة فقط)", 0.85);
        const warm = graph.predict(tf.zeros([1, TILE, TILE, 3])); // compiles every shader once
        await warm.data();
        warm.dispose();
        return graph;
      })();
      job.catch(() => prepared.delete(path));
      prepared.set(path, job);
    }
    return prepared.get(path);
  }

  // ---------- Image helpers ----------
  async function decode(file) {
    const bitmap = await createImageBitmap(file);
    const canvas = document.createElement("canvas");
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(bitmap, 0, 0);
    bitmap.close?.();
    return ctx.getImageData(0, 0, canvas.width, canvas.height);
  }

  // Tile input with edge clamping, so borders and tiny images need no special case.
  function readTile(src, x0, y0) {
    const { width: w, height: h, data } = src;
    const out = new Float32Array(TILE * TILE * 3);
    let o = 0;
    for (let y = 0; y < TILE; y++) {
      const sy = Math.min(h - 1, Math.max(0, y0 - PAD + y));
      for (let x = 0; x < TILE; x++) {
        const sx = Math.min(w - 1, Math.max(0, x0 - PAD + x));
        const i = (sy * w + sx) * 4;
        out[o++] = data[i] / 255;
        out[o++] = data[i + 1] / 255;
        out[o++] = data[i + 2] / 255;
      }
    }
    return out;
  }

  const nextFrame = () => new Promise((r) => requestAnimationFrame(() => r()));

  // ---------- Inference ----------
  // `native` is the model's own factor; `scale` is what the visitor asked for (≤ native).
  async function runModel(src, model, native, scale, onProgress, signal) {
    const outW = src.width * scale;
    const outH = src.height * scale;
    const out = new Uint8ClampedArray(outW * outH * 4);
    const shrink = native / scale; // 1, or 2 when a ×4 model serves a ×2 request
    const tilesX = Math.ceil(src.width / STEP);
    const tilesY = Math.ceil(src.height / STEP);
    const total = tilesX * tilesY;
    let done = 0;

    for (let ty = 0; ty < tilesY; ty++) {
      const y0 = ty * STEP;
      const keepH = Math.min(STEP, src.height - y0);

      for (let first = 0; first < tilesX; first += GROUP) {
        if (signal?.aborted) throw new DOMException("cancelled", "AbortError");
        const last = Math.min(tilesX, first + GROUP);

        // Queue this group on the GPU and join the kept centres into one strip.
        const strip = tf.tidy(() => {
          const parts = [];
          for (let tx = first; tx < last; tx++) {
            const x0 = tx * STEP;
            const keepW = Math.min(STEP, src.width - x0);
            const y = model.predict(tf.tensor4d(readTile(src, x0, y0), [1, TILE, TILE, 3]));
            parts.push(y.slice([0, PAD * native, PAD * native, 0], [1, keepH * native, keepW * native, 3]));
          }
          let joined = parts.length > 1 ? tf.concat(parts, 2) : parts[0];
          if (shrink > 1) joined = tf.avgPool(joined, shrink, shrink, "valid"); // area downscale on the GPU
          return joined.clipByValue(0, 1).mul(255);
        });

        const [, sh, sw] = strip.shape;
        const pixels = await strip.data(); // the only GPU → JS wait for this group
        strip.dispose();

        const dx = first * STEP * scale;
        const dy = y0 * scale;
        for (let y = 0; y < sh; y++) {
          let s = y * sw * 3;
          let d = ((dy + y) * outW + dx) * 4;
          for (let x = 0; x < sw; x++) {
            out[d++] = pixels[s++];
            out[d++] = pixels[s++];
            out[d++] = pixels[s++];
            out[d++] = 255;
          }
        }
        done += last - first;
        onProgress?.(done / total);
        await nextFrame(); // let the progress bar paint
      }
    }
    return new ImageData(out, outW, outH);
  }

  // ---------- Public API ----------
  /**
   * @param {Blob} file         source image
   * @param {object} opts
   * @param {"photo"|"anime"|"anime_extreme"|"anime_clean"} opts.model
   * @param {2|4} opts.scale    requested enlargement
   * @param {(fraction:number, stage:string)=>void} [opts.onProgress]
   * @param {AbortSignal} [opts.signal]
   * @returns {Promise<{canvas: HTMLCanvasElement, width: number, height: number, backend: string, label: string}>}
   */
  async function upscale(file, { model = "photo", scale = 2, onProgress, signal } = {}) {
    const spec = MODELS[model] || MODELS.photo;
    scale = scale === 4 ? 4 : 2;

    const graph = await prepare(model, scale, (text) => onProgress?.(0, text));
    const backend = await ensureRuntime();
    const src = await decode(file);
    const imageData = await runModel(src, graph, spec.native(scale), scale,
      (f) => onProgress?.(f, "الذكاء الاصطناعي يكبّر الصورة على جهازك…"), signal);

    const canvas = document.createElement("canvas");
    canvas.width = imageData.width;
    canvas.height = imageData.height;
    canvas.getContext("2d").putImageData(imageData, 0, 0);
    return { canvas, width: canvas.width, height: canvas.height, backend, label: spec.label };
  }

  const supported = () => Boolean(navigator.gpu || document.createElement("canvas").getContext("webgl2"));

  return { upscale, prepare, supported, MODELS };
})();
