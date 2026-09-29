// VideoCraft Studio — AI upscaling that runs on the visitor's own GPU.
//
// Same model families as the Windows app (Real-ESRGAN, Real-CUGAN), converted to
// TensorFlow.js. All the heavy work happens in a Web Worker (ai-worker.js), so:
//  • compiling GPU shaders or a slow GPU never freezes the page;
//  • a watchdog here kills the worker if it goes quiet (a stuck GPU or an
//    in-app browser that never answers), and the page falls back to the
//    server's regular upscaler instead of hanging.
//
// Usage:
//   VCAI.prepare("photo", 2, onStatus);                 // optional warm-up
//   const { blob } = await VCAI.upscale(file, { model, scale, onProgress, signal, finish });
window.VCAI = (() => {
  "use strict";

  const QUIET_LIMIT = 40000; // ms without any message from the worker = stuck
  let quietLimit = QUIET_LIMIT; // the worker may ask for longer (e.g. while encoding a big file)

  const MODELS = {
    photo: { label: "Real-ESRGAN (صور)", path: () => "realesrgan/general_fast-64", native: () => 4 },
    anime: { label: "Real-ESRGAN Anime", path: () => "realesrgan/anime_fast-64", native: () => 4 },
    anime_extreme: { label: "Real-CUGAN", path: (s) => `realcugan/${s}x-conservative-64`, native: (s) => s },
    anime_clean: { label: "Real-CUGAN Denoise", path: (s) => `realcugan/${s}x-denoise3x-64`, native: (s) => s },
  };

  // ---------- Worker plumbing ----------
  let worker = null;
  let nextId = 1;
  let lastMessage = 0;
  let lastStatus = null; // replayed to late listeners, e.g. "تحميل النموذج… 40%"
  const pending = new Map(); // id → { resolve, reject, onStatus, onProgress }
  const ready = new Map(); // model path → Promise<backend>
  let watchdog = null;

  function getWorker() {
    if (worker) return worker;
    worker = new Worker("/static/ai-worker.js");
    worker.onmessage = ({ data: msg }) => {
      lastMessage = Date.now();
      quietLimit = msg.quiet || QUIET_LIMIT;
      if (msg.type === "status") {
        lastStatus = [msg.text, msg.fraction];
        pending.forEach((p) => p.onStatus?.(msg.text, msg.fraction));
      } else if (msg.type === "progress") {
        pending.forEach((p) => p.onProgress?.(msg.fraction));
      } else if (pending.has(msg.id)) {
        const p = pending.get(msg.id);
        pending.delete(msg.id);
        if (msg.type === "error") p.reject(new Error(msg.message));
        else p.resolve(msg);
      }
    };
    worker.onerror = (e) => reset(new Error(e.message || "تعطّل محرك الذكاء الاصطناعي"));
    return worker;
  }

  // Stop the worker and fail everything that was waiting on it.
  function reset(error) {
    worker?.terminate();
    worker = null;
    ready.clear();
    lastStatus = null;
    clearInterval(watchdog);
    watchdog = null;
    const waiting = [...pending.values()];
    pending.clear();
    waiting.forEach((p) => p.reject(error));
  }

  function request(msg, handlers = {}, transfer = []) {
    const w = getWorker();
    const id = nextId++;
    lastMessage = Date.now();
    watchdog ??= setInterval(() => {
      if (pending.size && Date.now() - lastMessage > quietLimit) {
        reset(new Error("كرت الشاشة في هذا الجهاز ما استجاب"));
      }
    }, 1000);
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject, ...handlers });
      if (handlers.onStatus && lastStatus) handlers.onStatus(...lastStatus);
      w.postMessage({ ...msg, id }, transfer);
    });
  }

  // ---------- Public API ----------
  /**
   * Download the model and compile its GPU shaders ahead of the real run.
   * @param {(text: string, fraction: number|null) => void} [onStatus]
   * @returns {Promise<string>} the GPU backend ("webgpu" | "webgl")
   */
  function prepare(model = "photo", scale = 2, onStatus) {
    const spec = MODELS[model] || MODELS.photo;
    const path = spec.path(scale === 4 ? 4 : 2);
    if (!ready.has(path)) {
      const job = request({ type: "prepare", path }, { onStatus }).then((m) => m.backend);
      job.catch(() => ready.delete(path));
      ready.set(path, job);
      return job;
    }
    // Already loading or loaded: still show this caller the live status.
    const job = ready.get(path);
    if (onStatus) {
      if (lastStatus) onStatus(...lastStatus);
      const listener = { onStatus, resolve() {}, reject() {} };
      const key = `status-${nextId++}`;
      pending.set(key, listener);
      job.finally(() => pending.delete(key)).catch(() => {});
    }
    return job;
  }



  /**
   * @param {Blob} file         source image
   * @param {object} opts
   * @param {"photo"|"anime"|"anime_extreme"|"anime_clean"} opts.model
   * @param {2|4} opts.scale    requested enlargement
   * @param {(fraction:number, stage:string)=>void} [opts.onProgress]
   * @param {AbortSignal} [opts.signal]  aborting kills the worker at once
   * @param {{type?: string, quality?: number, filter?: string, target?: [number, number]}} [opts.finish]
   *        output format, CSS filter for colours, and exact output size (all applied in the worker)
   * @returns {Promise<{blob: Blob, width: number, height: number, filtered: boolean, backend: string, label: string}>}
   *          filtered=false: this browser can't apply canvas filters, the blob is PNG without colours
   */
  async function upscale(file, { model = "photo", scale = 2, onProgress, signal, finish } = {}) {
    const spec = MODELS[model] || MODELS.photo;
    scale = scale === 4 ? 4 : 2;
    const onAbort = () => reset(new DOMException("cancelled", "AbortError"));
    signal?.addEventListener("abort", onAbort, { once: true });
    try {
      await prepare(model, scale, (text) => onProgress?.(0, text));
      // The worker decodes, upscales and encodes: only the finished file comes back.
      const res = await request(
        { type: "run", path: spec.path(scale), native: spec.native(scale), scale, file, finish },
        {
          onProgress: (f) => onProgress?.(f, "الذكاء الاصطناعي يكبّر الصورة على جهازك…"),
          onStatus: (text) => onProgress?.(1, text),
        },
      );
      return { blob: res.blob, width: res.width, height: res.height, filtered: res.filtered, backend: res.backend, label: spec.label };
    } finally {
      signal?.removeEventListener("abort", onAbort);
    }
  }


  function supported() {
    if (typeof Worker === "undefined" || typeof OffscreenCanvas === "undefined") return false;
    if (navigator.gpu) return true;
    try { return Boolean(new OffscreenCanvas(1, 1).getContext("webgl2")); } catch { return false; }
  }

  return { upscale, prepare, supported, MODELS };
})();
