// VideoCraft Studio — AI upscaling that runs on the visitor's own GPU.
//
// Same model families as the Windows app (Real-ESRGAN, Real-CUGAN), converted
// to TensorFlow.js graph models with a fixed 64×64 input. The image is cut into
// overlapping tiles, each tile runs through the model on WebGPU (or WebGL), and
// only the centre of every tile is kept so seams never show.
//
// Usage: const { blob, width, height } = await VCAI.upscale(file, { model, scale, onProgress, signal });
window.VCAI = (() => {
  "use strict";

  const BASE = "/static";
  const TILE = 64; // model input size (fixed by the converted graphs)
  const PAD = 6; // context pixels discarded on each side of a tile
  const STEP = TILE - PAD * 2;

  // name → how to get the requested scale out of it
  const MODELS = {
    photo: { label: "Real-ESRGAN (صور)", path: () => "realesrgan/general_fast-64", native: () => 4 },
    anime: { label: "Real-ESRGAN Anime", path: () => "realesrgan/anime_fast-64", native: () => 4 },
    anime_extreme: { label: "Real-CUGAN", path: (s) => `realcugan/${s}x-conservative-64`, native: (s) => s },
    anime_clean: { label: "Real-CUGAN Denoise", path: (s) => `realcugan/${s}x-denoise3x-64`, native: (s) => s },
  };

  let backend = null;
  const loadedModels = new Map();

  // ---------- Runtime loading ----------
  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src;
      s.onload = resolve;
      s.onerror = () => reject(new Error(`تعذّر تحميل ${src}`));
      document.head.append(s);
    });
  }

  async function ensureRuntime() {
    if (backend) return backend;
    if (!window.tf) await loadScript(`${BASE}/vendor/tf.min.js`);
    if (navigator.gpu) {
      try {
        await loadScript(`${BASE}/vendor/tf-backend-webgpu.min.js`);
        if (await tf.setBackend("webgpu")) backend = "webgpu";
      } catch { /* fall through to WebGL */ }
    }
    if (!backend) {
      try { if (await tf.setBackend("webgl")) backend = "webgl"; } catch { /* no GPU path */ }
    }
    if (!backend) throw new Error("متصفحك لا يدعم تشغيل الذكاء الاصطناعي على كرت الشاشة (WebGPU / WebGL).");
    await tf.ready();
    return backend;
  }

  async function loadModel(path) {
    if (loadedModels.has(path)) return loadedModels.get(path);
    const cacheKey = `indexeddb://videocraft-${path.replace(/\//g, "-")}`;
    let model;
    try {
      model = await tf.loadGraphModel(cacheKey); // cached from an earlier visit
    } catch {
      model = await tf.loadGraphModel(`${BASE}/models/${path}/model.json`);
      try { await model.save(cacheKey); } catch { /* private mode / quota: fine */ }
    }
    loadedModels.set(path, model);
    return model;
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

  function nextFrame() { return new Promise((r) => requestAnimationFrame(() => r())); }

  // ---------- Inference ----------
  async function runModel(src, model, scale, onProgress, signal) {
    const outW = src.width * scale;
    const outH = src.height * scale;
    const out = new Uint8ClampedArray(outW * outH * 4);
    const tilesX = Math.ceil(src.width / STEP);
    const tilesY = Math.ceil(src.height / STEP);
    const total = tilesX * tilesY;
    const size = TILE * scale;
    const pad = PAD * scale;
    let done = 0;

    for (let ty = 0; ty < tilesY; ty++) {
      for (let tx = 0; tx < tilesX; tx++) {
        if (signal?.aborted) throw new DOMException("cancelled", "AbortError");
        const x0 = tx * STEP;
        const y0 = ty * STEP;

        const input = tf.tensor4d(readTile(src, x0, y0), [1, TILE, TILE, 3]);
        const result = tf.tidy(() => model.predict(input).clipByValue(0, 1).mul(255));
        const pixels = await result.data(); // async readback keeps the page responsive
        input.dispose();
        result.dispose();

        // Keep only this tile's centre (STEP×STEP source px), clipped to the image.
        const keepW = Math.min(STEP, src.width - x0) * scale;
        const keepH = Math.min(STEP, src.height - y0) * scale;
        for (let y = 0; y < keepH; y++) {
          let s = ((pad + y) * size + pad) * 3;
          let d = ((y0 * scale + y) * outW + x0 * scale) * 4;
          for (let x = 0; x < keepW; x++) {
            out[d++] = pixels[s++];
            out[d++] = pixels[s++];
            out[d++] = pixels[s++];
            out[d++] = 255;
          }
        }
        done++;
        onProgress?.(done / total);
        if (done % 8 === 0) await nextFrame(); // let the progress bar paint
      }
    }
    return new ImageData(out, outW, outH);
  }

  function toCanvas(imageData) {
    const c = document.createElement("canvas");
    c.width = imageData.width;
    c.height = imageData.height;
    c.getContext("2d").putImageData(imageData, 0, 0);
    return c;
  }

  // ---------- Public API ----------
  /**
   * @param {Blob} file         source image
   * @param {object} opts
   * @param {"photo"|"anime"|"anime_extreme"|"anime_clean"} opts.model
   * @param {2|4} opts.scale    requested enlargement
   * @param {(fraction:number, stage:string)=>void} [opts.onProgress]
   * @param {AbortSignal} [opts.signal]
   * @returns {Promise<{blob: Blob, width: number, height: number, backend: string, label: string}>}
   */
  async function upscale(file, { model = "photo", scale = 2, onProgress, signal } = {}) {
    const spec = MODELS[model] || MODELS.photo;
    scale = scale === 4 ? 4 : 2;

    onProgress?.(0, "تجهيز محرك الذكاء الاصطناعي…");
    const usedBackend = await ensureRuntime();
    onProgress?.(0, "تحميل النموذج… (أول مرة فقط)");
    const graph = await loadModel(spec.path(scale));
    const src = await decode(file);

    const native = spec.native(scale);
    let result = toCanvas(await runModel(src, graph, native, (f) => onProgress?.(f, "الذكاء الاصطناعي يكبّر الصورة على جهازك…"), signal));

    // 4× models asked for 2×: shrink the AI result (sharper than a plain 2× resize).
    if (native !== scale) {
      const c = document.createElement("canvas");
      c.width = src.width * scale;
      c.height = src.height * scale;
      const ctx = c.getContext("2d");
      ctx.imageSmoothingQuality = "high";
      ctx.drawImage(result, 0, 0, c.width, c.height);
      result = c;
    }

    const blob = await new Promise((resolve, reject) =>
      result.toBlob((b) => (b ? resolve(b) : reject(new Error("تعذّر حفظ الصورة."))), "image/png"));
    return { blob, width: result.width, height: result.height, backend: usedBackend, label: spec.label };
  }

  const supported = () => Boolean(navigator.gpu || document.createElement("canvas").getContext("webgl2"));

  return { upscale, supported, MODELS };
})();
