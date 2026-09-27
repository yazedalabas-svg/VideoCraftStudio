// VideoCraft Studio — result viewer: zoom/pan + before/after split.
//
// Both pictures sit in the same box with object-fit: contain and get the same
// transform, so "before" and "after" stay pixel-aligned at every zoom level.
// The split line lives in screen space (clip-path), so it keeps working while zoomed.
//
// Videos work the same way: both clips play in sync (one set of controls), so the
// split line and zoom compare the same moment.
//
// Usage: const viewer = CompareViewer(document.getElementById("compare-image"));
//        viewer.load(afterUrl, beforeUrlOrNull, { video: false });
window.CompareViewer = (root) => {
  "use strict";

  const view = root.querySelector(".compare");
  const pairs = {
    image: [root.querySelector("#after-img"), root.querySelector("#before-img")],
    video: [root.querySelector("#after-vid"), root.querySelector("#before-vid")],
  };
  let [after, before] = pairs.image;
  const natW = (el) => el.naturalWidth || el.videoWidth || 0;
  const natH = (el) => el.naturalHeight || el.videoHeight || 0;
  const beforeWrap = root.querySelector(".compare-before");
  const split = root.querySelector(".split");
  const level = root.querySelector(".zoom-level");
  const MAX_PIXEL_ZOOM = 8; // up to 800% of the real pixels

  let z = 1, tx = 0, ty = 0; // zoom relative to "fit", translation in CSS px
  let splitPct = 50;
  const pointers = new Map();
  let gesture = null;

  // ---------- Geometry ----------
  // Where the fitted (object-fit: contain) picture sits inside the box at zoom 1.
  function fitRect() {
    const cw = view.clientWidth, ch = view.clientHeight;
    const nw = natW(after) || cw, nh = natH(after) || ch;
    const s = Math.min(cw / nw, ch / nh);
    const dw = nw * s, dh = nh * s;
    return { cw, ch, s, dw, dh, ox: (cw - dw) / 2, oy: (ch - dh) / 2 };
  }
  const maxZoom = () => Math.max(4, MAX_PIXEL_ZOOM / fitRect().s);
  // Small results are stretched to fill the frame; allow zooming out to their real 1:1 size.
  const minZoom = () => Math.min(1, 1 / fitRect().s);

  // Keep the picture covering the box when it is bigger, centred when smaller.
  function clampAxis(t, size, offset, box) {
    const scaled = z * size;
    if (scaled <= box) return (box - scaled) / 2 - z * offset;
    return Math.min(-z * offset, Math.max(box - z * (offset + size), t));
  }

  function apply() {
    const r = fitRect();
    tx = clampAxis(tx, r.dw, r.ox, r.cw);
    ty = clampAxis(ty, r.dh, r.oy, r.ch);
    const t = `translate(${tx}px, ${ty}px) scale(${z})`;
    after.style.transform = t;
    before.style.transform = t;
    const pixelZoom = z * r.s; // 1 = one picture pixel per CSS pixel
    level.textContent = `${Math.round(pixelZoom * 100)}%`;
    view.classList.toggle("is-zoomed", z > 1.001);
    view.classList.toggle("is-pixelated", pixelZoom >= 2);
    root.querySelector('[data-zoom="out"]').disabled = z <= minZoom() + 0.001;
    root.querySelector('[data-zoom="in"]').disabled = z >= maxZoom() - 0.001;
  }

  // Zoom so the picture point under (cx, cy) stays under it.
  function zoomAt(newZ, cx, cy) {
    newZ = Math.min(maxZoom(), Math.max(minZoom(), newZ));
    tx = cx - (cx - tx) * (newZ / z);
    ty = cy - (cy - ty) * (newZ / z);
    z = newZ;
    apply();
  }
  const center = () => [view.clientWidth / 2, view.clientHeight / 2];
  const local = (e) => {
    const r = view.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  };

  // ---------- Split line ----------
  function setSplit(pct) {
    splitPct = Math.min(100, Math.max(0, pct));
    view.style.setProperty("--split", `${splitPct}%`);
    beforeWrap.style.clipPath = `inset(0 ${100 - splitPct}% 0 0)`;
    split.setAttribute("aria-valuenow", String(Math.round(splitPct)));
  }

  split.addEventListener("pointerdown", (e) => {
    e.stopPropagation();
    split.setPointerCapture(e.pointerId);
    const move = (ev) => {
      const r = view.getBoundingClientRect();
      setSplit(((ev.clientX - r.left) / r.width) * 100);
    };
    const up = () => {
      split.removeEventListener("pointermove", move);
      split.removeEventListener("pointerup", up);
      split.removeEventListener("pointercancel", up);
    };
    split.addEventListener("pointermove", move);
    split.addEventListener("pointerup", up);
    split.addEventListener("pointercancel", up);
  });
  split.addEventListener("keydown", (e) => {
    const step = e.shiftKey ? 10 : 2;
    const keys = { ArrowLeft: -step, ArrowRight: step, Home: -100, End: 100 };
    if (e.key in keys) { e.preventDefault(); setSplit(splitPct + keys[e.key]); }
  });
  split.addEventListener("dblclick", (e) => e.stopPropagation());

  // ---------- Pan + pinch ----------
  view.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 && e.pointerType === "mouse") return;
    pointers.set(e.pointerId, local(e));
    view.setPointerCapture(e.pointerId);
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      gesture = { type: "pinch", dist: Math.hypot(a[0] - b[0], a[1] - b[1]), z };
    } else if (pointers.size === 1 && z > 1.001) {
      gesture = { type: "pan", x: e.clientX, y: e.clientY, tx, ty };
      view.classList.add("is-panning");
    }
  });
  view.addEventListener("pointermove", (e) => {
    if (!pointers.has(e.pointerId)) return;
    pointers.set(e.pointerId, local(e));
    if (gesture?.type === "pan") {
      tx = gesture.tx + (e.clientX - gesture.x);
      ty = gesture.ty + (e.clientY - gesture.y);
      apply();
    } else if (gesture?.type === "pinch" && pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      const dist = Math.hypot(a[0] - b[0], a[1] - b[1]);
      zoomAt(gesture.z * (dist / gesture.dist), (a[0] + b[0]) / 2, (a[1] + b[1]) / 2);
    }
  });
  const endPointer = (e) => {
    pointers.delete(e.pointerId);
    if (pointers.size === 0) { gesture = null; view.classList.remove("is-panning"); }
    else if (pointers.size === 1 && z > 1.001) {
      const [p] = [...pointers.values()];
      const r = view.getBoundingClientRect();
      gesture = { type: "pan", x: p[0] + r.left, y: p[1] + r.top, tx, ty };
    }
  };
  view.addEventListener("pointerup", endPointer);
  view.addEventListener("pointercancel", endPointer);

  // Double click: zoom in on that spot (to real pixels, or 3× if the picture is small), again to fit.
  view.addEventListener("dblclick", (e) => {
    const [x, y] = local(e);
    if (z > 1.001) zoomAt(1, x, y);
    else zoomAt(Math.max(3, 2 / fitRect().s), x, y);
  });

  // Wheel zooms when the user clearly means it (Ctrl/⌘, already zoomed, or full screen);
  // otherwise the page scrolls normally.
  view.addEventListener("wheel", (e) => {
    if (!(e.ctrlKey || e.metaKey || z > 1.001 || document.fullscreenElement === root)) return;
    e.preventDefault();
    const [x, y] = local(e);
    zoomAt(z * Math.exp(-e.deltaY * 0.0015), x, y);
  }, { passive: false });

  // ---------- Toolbar ----------
  root.querySelector(".viewer-tools").addEventListener("click", (e) => {
    const action = e.target.closest("[data-zoom]")?.dataset.zoom;
    const [cx, cy] = center();
    if (action === "in") zoomAt(z * 1.6, cx, cy);
    else if (action === "out") zoomAt(z / 1.6, cx, cy);
    else if (action === "fit") zoomAt(1, cx, cy);
    else if (action === "actual") zoomAt(1 / fitRect().s, cx, cy);
    else if (action === "full") {
      if (document.fullscreenElement) document.exitFullscreen?.();
      else root.requestFullscreen?.().catch(() => {});
    }
  });
  if (!root.requestFullscreen) root.querySelector('[data-zoom="full"]').hidden = true; // e.g. iPhone Safari

  // Keyboard shortcuts while the viewer has focus: + / - / 0
  root.addEventListener("keydown", (e) => {
    if (e.target === split && e.key.startsWith("Arrow")) return;
    const [cx, cy] = center();
    if (e.key === "+" || e.key === "=") { e.preventDefault(); zoomAt(z * 1.6, cx, cy); }
    else if (e.key === "-") { e.preventDefault(); zoomAt(z / 1.6, cx, cy); }
    else if (e.key === "0") { e.preventDefault(); zoomAt(1, cx, cy); }
  });

  new ResizeObserver(() => apply()).observe(view);
  document.addEventListener("fullscreenchange", () => requestAnimationFrame(apply));

  // ---------- Video playback (both clips as one) ----------
  const player = root.querySelector(".player");
  const playBtn = root.querySelector("#play-toggle");
  const seek = root.querySelector("#seek");
  const clock = root.querySelector("#clock");
  const fmt = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
  const videoMode = () => after === pairs.video[0];

  function syncBefore(force = false) {
    const b = pairs.video[1];
    if (beforeWrap.hidden || !b.src) return;
    if (force || Math.abs(b.currentTime - after.currentTime) > 0.12) b.currentTime = after.currentTime;
  }
  function setPlaying(playing) {
    playBtn.setAttribute("aria-label", playing ? "إيقاف مؤقت" : "تشغيل");
    playBtn.dataset.state = playing ? "playing" : "paused";
  }
  playBtn.addEventListener("click", () => {
    if (after.paused) after.play().catch(() => {});
    else after.pause();
  });
  seek.addEventListener("input", () => {
    if (after.duration) after.currentTime = (Number(seek.value) / 1000) * after.duration;
  });
  const v = pairs.video[0];
  v.addEventListener("play", () => { setPlaying(true); syncBefore(true); pairs.video[1].play().catch(() => {}); });
  v.addEventListener("pause", () => { setPlaying(false); pairs.video[1].pause(); syncBefore(true); });
  v.addEventListener("seeked", () => syncBefore(true));
  v.addEventListener("ratechange", () => { pairs.video[1].playbackRate = v.playbackRate; });
  v.addEventListener("timeupdate", () => {
    if (v.duration) seek.value = String(Math.round((v.currentTime / v.duration) * 1000));
    clock.textContent = `${fmt(v.currentTime)} / ${fmt(v.duration || 0)}`;
    syncBefore();
  });
  v.addEventListener("ended", () => setPlaying(false));
  // A "before" clip the browser can't play (e.g. HEVC on some phones): compare is off, result stays.
  pairs.video[1].addEventListener("error", () => { if (videoMode()) setSingle(true); });

  function setSingle(single) {
    view.classList.toggle("is-single", single);
    beforeWrap.hidden = single;
    root.querySelectorAll(".tag").forEach((t) => { t.hidden = single; });
    setSplit(single ? 100 : 50);
  }

  // ---------- Public ----------
  function load(afterUrl, beforeUrl, { video = false } = {}) {
    z = 1; tx = 0; ty = 0;
    // Stop whatever played before and switch between the picture and the video pair.
    pairs.video.forEach((el) => { el.pause(); el.removeAttribute("src"); el.load(); });
    [after, before] = video ? pairs.video : pairs.image;
    Object.entries(pairs).forEach(([mode, els]) => els.forEach((el) => { el.hidden = (mode === "video") !== video; }));
    player.hidden = !video;
    setSingle(!beforeUrl);
    const ready = () => {
      view.style.setProperty("--ratio", `${natW(after)} / ${natH(after)}`);
      apply();
    };
    if (video) {
      after.onloadedmetadata = ready;
      setPlaying(false);
      seek.value = "0";
      clock.textContent = "0:00";
    } else {
      after.onload = ready;
    }
    if (beforeUrl) before.src = beforeUrl;
    after.src = afterUrl;
    apply();
  }

  function stop() { pairs.video.forEach((el) => el.pause()); }

  return { load, setSplit, stop };
};
