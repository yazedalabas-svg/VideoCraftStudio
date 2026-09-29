// VideoCraft Studio — AI upscaling worker (runs off the page's main thread).
//
// Compiling GPU shaders and running the models can block a thread for a long
// time on phones. Doing it here keeps the page responsive, and lets the page
// kill this worker outright if the GPU stops answering (see ai.js).
//
// Messages in:  { type: "prepare", path }
//               { type: "run", path, native, scale, file (Blob), finish }
//                 finish = { type: "image/jpeg"|"image/png"|"image/webp", quality, filter, target: [w, h] }
// Messages out: { type: "status", text, fraction }      while preparing
//               { type: "ready", backend }
//               { type: "progress", fraction }          while running
//               { type: "done", width, height, blob, filtered }   the finished, encoded file
//
// Everything heavy happens here — decoding the photo, the model, resizing, colours and
// the final JPEG/PNG encoding — so the page never holds big pixel buffers and never
// freezes, even on phones.
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
// Decode off the main thread, honouring the EXIF rotation of phone photos.
async function decode(file) {
  let bitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch {
    bitmap = await createImageBitmap(file);
  }
  const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(bitmap, 0, 0);
  bitmap.close();
  const data = ctx.getImageData(0, 0, canvas.width, canvas.height);
  canvas.width = canvas.height = 0; // free the backing store now (matters on iPhone)
  return data;
}

let filterSupport = null;
function canvasFilterWorks() {
  if (filterSupport === null) {
    try {
      const ctx = new OffscreenCanvas(1, 1).getContext("2d");
      ctx.filter = "brightness(0.5)";
      ctx.fillStyle = "#fff";
      ctx.fillRect(0, 0, 1, 1);
      filterSupport = ctx.getImageData(0, 0, 1, 1).data[0] < 200;
    } catch {
      filterSupport = false;
    }
  }
  return filterSupport;
}

// Pixels → finished file: fit to the requested size, apply colours, encode.
// Each step hands its canvas over and frees the previous one to keep memory low.
async function encode(pixels, w, h, finish = {}) {
  let canvas = new OffscreenCanvas(w, h);
  canvas.getContext("2d").putImageData(new ImageData(pixels, w, h), 0, 0);
  const step = (width, height, draw) => {
    const next = new OffscreenCanvas(width, height);
    const ctx = next.getContext("2d");
    draw(ctx);
    ctx.drawImage(canvas, 0, 0, width, height);
    canvas.width = canvas.height = 0;
    canvas = next;
  };
  const [tw, th] = finish.target || [w, h];
  if (tw !== w || th !== h) step(tw, th, (ctx) => { ctx.imageSmoothingQuality = "high"; });
  const filtered = !finish.filter || canvasFilterWorks();
  if (finish.filter && filtered) step(canvas.width, canvas.height, (ctx) => { ctx.filter = finish.filter; });
  // Without canvas filters (older Safari) the page asks the server to do the colours,
  // so hand over a lossless PNG instead of the requested format.
  const type = filtered ? finish.type || "image/png" : "image/png";
  const blob = await canvas.convertToBlob({ type, quality: finish.quality });
  const size = [canvas.width, canvas.height];
  canvas.width = canvas.height = 0;
  return { blob, filtered, width: size[0], height: size[1] };
}

async function run({ id, path, native, scale, file, finish }) {
  const model = await prepare(path);
  const decoded = await decode(file);
  const { width: w, height: h } = decoded;
  const src = decoded.data;
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
  // Encoding a big PNG can take a while on a phone: tell the page to allow for it.
  post({ type: "status", text: "حفظ النتيجة…", fraction: null, quiet: 180000 });
  const result = await encode(out, outW, outH, finish);
  post({ type: "done", id, backend, ...result });
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
