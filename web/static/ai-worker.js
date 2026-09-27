// VideoCraft Studio — AI upscaling worker (runs off the page's main thread).
//
// Compiling GPU shaders and running the models can block a thread for a long
// time on phones. Doing it here keeps the page responsive, and lets the page
// kill this worker outright if the GPU stops answering (see ai.js).
//
// Messages in:  { type: "prepare", path }
//               { type: "run", path, native, scale, width, height, pixels (ArrayBuffer, RGBA) }
// Messages out: { type: "status", text, fraction }      while preparing
//               { type: "ready", backend }
//               { type: "progress", fraction }          while running
//               { type: "done", width, height, pixels } (ArrayBuffer, RGBA)
//               { type: "error", message }
/* global tf */
"use strict";

const TILE = 64; // model input size (fixed by the converted graphs)
const PAD = 6; // context pixels discarded on each side of a tile
const STEP = TILE - PAD * 2;
const GROUP = 12; // tiles queued on the GPU before one read-back

let backend = null;
const models = new Map(); // path → Promise<GraphModel> (downloaded + shaders compiled)

const post = (msg, transfer) => self.postMessage(msg, transfer || []);
const status = (text, fraction = null) => post({ type: "status", text, fraction });

async function ensureRuntime() {
  if (backend) return backend;
  status("تجهيز كرت الشاشة…");
  importScripts("/static/vendor/tf.min.js");
  if (self.navigator && navigator.gpu) {
    try {
      importScripts("/static/vendor/tf-backend-webgpu.min.js");
      if (await tf.setBackend("webgpu")) backend = "webgpu";
    } catch { /* fall back to WebGL */ }
  }
  if (!backend) {
    // Half-precision textures are about twice as fast on phone GPUs; image quality is unaffected.
    if (/Android|iPhone|iPad|Mobile/i.test(navigator.userAgent)) tf.env().set("WEBGL_FORCE_F16_TEXTURES", true);
    if (await tf.setBackend("webgl")) backend = "webgl"; // WebGL on an OffscreenCanvas
  }
  if (!backend) throw new Error("لا يوجد وصول لكرت الشاشة في هذا المتصفح.");
  await tf.ready();
  return backend;
}

// A warm-up and a run can ask for the same model at once; both share one load.
function prepare(path) {
  if (!models.has(path)) {
    const job = load(path);
    job.catch(() => models.delete(path));
    models.set(path, job);
  }
  return models.get(path);
}

async function load(path) {
  await ensureRuntime();
  status("تحميل النموذج… 0%", 0);
  const model = await tf.loadGraphModel(`/static/models/${path}/model.json`, {
    onProgress: (f) => status(`تحميل النموذج… ${Math.round(f * 100)}%`, f * 0.8),
  });
  status("تسخين كرت الشاشة… (أول مرة فقط)", 0.85);
  const warm = model.predict(tf.zeros([1, TILE, TILE, 3])); // compiles every shader once
  await warm.data();
  warm.dispose();
  return model;
}

// Tile input with edge clamping, so borders and tiny images need no special case.
function readTile(src, w, h, x0, y0) {
  const out = new Float32Array(TILE * TILE * 3);
  let o = 0;
  for (let y = 0; y < TILE; y++) {
    const sy = Math.min(h - 1, Math.max(0, y0 - PAD + y));
    for (let x = 0; x < TILE; x++) {
      const sx = Math.min(w - 1, Math.max(0, x0 - PAD + x));
      const i = (sy * w + sx) * 4;
      out[o++] = src[i] / 255;
      out[o++] = src[i + 1] / 255;
      out[o++] = src[i + 2] / 255;
    }
  }
  return out;
}

// `native` is the model's own factor; `scale` is what the visitor asked for (≤ native).
async function run({ id, path, native, scale, width: w, height: h, pixels }) {
  const model = await prepare(path);
  const src = new Uint8ClampedArray(pixels);
  const outW = w * scale;
  const outH = h * scale;
  const out = new Uint8ClampedArray(outW * outH * 4);
  const shrink = native / scale; // 1, or 2 when a ×4 model serves a ×2 request
  const tilesX = Math.ceil(w / STEP);
  const tilesY = Math.ceil(h / STEP);
  const total = tilesX * tilesY;
  let done = 0;

  for (let ty = 0; ty < tilesY; ty++) {
    const y0 = ty * STEP;
    const keepH = Math.min(STEP, h - y0);
    for (let first = 0; first < tilesX; first += GROUP) {
      const last = Math.min(tilesX, first + GROUP);
      // Queue this group on the GPU and join the kept tile centres into one strip.
      const strip = tf.tidy(() => {
        const parts = [];
        for (let tx = first; tx < last; tx++) {
          const x0 = tx * STEP;
          const keepW = Math.min(STEP, w - x0);
          const y = model.predict(tf.tensor4d(readTile(src, w, h, x0, y0), [1, TILE, TILE, 3]));
          parts.push(y.slice([0, PAD * native, PAD * native, 0], [1, keepH * native, keepW * native, 3]));
        }
        let joined = parts.length > 1 ? tf.concat(parts, 2) : parts[0];
        if (shrink > 1) joined = tf.avgPool(joined, shrink, shrink, "valid"); // area downscale on the GPU
        return joined.clipByValue(0, 1).mul(255);
      });
      const [, sh, sw] = strip.shape;
      const data = await strip.data(); // the only GPU → JS wait for this group
      strip.dispose();

      const dx = first * STEP * scale;
      const dy = y0 * scale;
      for (let y = 0; y < sh; y++) {
        let s = y * sw * 3;
        let d = ((dy + y) * outW + dx) * 4;
        for (let x = 0; x < sw; x++) {
          out[d++] = data[s++];
          out[d++] = data[s++];
          out[d++] = data[s++];
          out[d++] = 255;
        }
      }
      done += last - first;
      post({ type: "progress", fraction: done / total });
    }
  }
  post({ type: "done", id, backend, width: outW, height: outH, pixels: out.buffer }, [out.buffer]);
}

self.onmessage = async ({ data: msg }) => {
  try {
    if (msg.type === "prepare") {
      await prepare(msg.path);
      post({ type: "ready", id: msg.id, backend });
    } else if (msg.type === "run") {
      await run(msg);
    }
  } catch (err) {
    post({ type: "error", id: msg.id, message: (err && err.message) || String(err) });
  }
};
