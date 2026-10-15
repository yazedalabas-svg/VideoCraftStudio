// نبض AI: يقرأ feed.json ويعرضه كأخبار ومقاطع، ويعيد الفحص كل 10 دقائق.
"use strict";

const REFRESH_MS = 10 * 60 * 1000;
const KIND_LABEL = { news: "خبر", research: "بحث", clip: "مقطع" };

const $ = (sel) => document.querySelector(sel);
const state = { items: [], updated: null, filter: "الكل", view: "news" };

// ---------- helpers ----------
const store = {
  get(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ } },
};
const likes = new Set(store.get("likes", []));

const rtf = new Intl.RelativeTimeFormat("ar", { numeric: "auto" });
function timeAgo(iso) {
  const sec = (new Date(iso) - Date.now()) / 1000;
  const units = [["year", 31536000], ["month", 2592000], ["day", 86400], ["hour", 3600], ["minute", 60]];
  for (const [unit, s] of units) if (Math.abs(sec) >= s) return rtf.format(Math.round(sec / s), unit);
  return "الآن";
}

function hue(text) {
  let h = 0;
  for (const ch of text) h = (h * 31 + ch.codePointAt(0)) % 360;
  return h;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children) if (c != null) node.append(c);
  return node;
}

const ICON = {
  link: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>',
  heart: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20s-7-4.4-9-9a5 5 0 0 1 9-3 5 5 0 0 1 9 3c-2 4.6-9 9-9 9z"/></svg>',
  share: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12v7a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-7M16 6l-4-4-4 4M12 2v13"/></svg>',
};
const iconSpan = (name) => { const s = el("span"); s.innerHTML = ICON[name]; return s.firstChild; };

// ---------- news ----------
function categoryOf(item) { return item.category || KIND_LABEL[item.kind] || "خبر"; }

function renderChips() {
  const cats = ["الكل", ...new Set(state.items.filter((i) => i.kind !== "clip").map(categoryOf))];
  const box = $("#chips");
  box.replaceChildren(...cats.map((c) =>
    el("button", { "aria-pressed": String(c === state.filter), onclick: () => { state.filter = c; renderNews(); renderChips(); } }, c)));
}

function postCard(item) {
  const liked = likes.has(item.id);
  const likeBtn = el("button", { "aria-pressed": String(liked), "aria-label": "إعجاب", class: liked ? "liked" : "" }, iconSpan("heart"));
  likeBtn.addEventListener("click", () => {
    const on = !likes.has(item.id);
    on ? likes.add(item.id) : likes.delete(item.id);
    store.set("likes", [...likes]);
    likeBtn.classList.toggle("liked", on);
    likeBtn.setAttribute("aria-pressed", String(on));
  });

  const shareBtn = el("button", { "aria-label": "مشاركة" }, iconSpan("share"));
  shareBtn.addEventListener("click", async () => {
    const data = { title: item.title, url: item.url };
    try {
      if (navigator.share) await navigator.share(data);
      else { await navigator.clipboard.writeText(item.url); shareBtn.title = "تم نسخ الرابط"; }
    } catch { /* user cancelled */ }
  });

  const text = item.post || item.title;
  return el("article", { class: "post" },
    el("div", { class: "avatar", style: `background:hsl(${hue(item.source)} 55% 45%)`, "aria-hidden": "true" }, item.source.trim()[0] || "?"),
    el("div", {},
      el("div", { class: "meta" },
        el("strong", {}, item.source),
        el("time", { datetime: item.date }, timeAgo(item.date)),
        el("span", { class: "tag" }, categoryOf(item))),
      el("p", { class: "text", dir: "auto" }, text),
      el("div", { class: "actions" },
        el("a", { href: item.url, target: "_blank", rel: "noopener noreferrer" }, iconSpan("link"), "المصدر"),
        likeBtn, shareBtn)));
}

function renderNews() {
  const box = $("#news");
  box.setAttribute("aria-busy", "false");
  const list = state.items.filter((i) => i.kind !== "clip" && (state.filter === "الكل" || categoryOf(i) === state.filter));
  box.replaceChildren(...(list.length ? list.map(postCard) : [el("p", { class: "state" }, "ما فيه منشورات هنا للحين.")]));
}

// ---------- clips ----------
let clipObserver;
function renderClips() {
  const box = $("#view-clips");
  const clips = state.items.filter((i) => i.kind === "clip" && i.video_id);
  if (!clips.length) {
    box.replaceChildren(el("p", { class: "state" }, "ما فيه مقاطع للحين. تنضاف تلقائياً من قنوات YouTube."));
    return;
  }
  clipObserver?.disconnect();
  // يحمّل الفيديو فقط لما يظهر، ويوقفه لما يطلع من الشاشة.
  clipObserver = new IntersectionObserver((entries) => {
    for (const e of entries) {
      const frame = e.target.querySelector(".frame");
      if (e.isIntersecting && !frame.firstChild) {
        frame.append(el("iframe", {
          src: `https://www.youtube-nocookie.com/embed/${encodeURIComponent(e.target.dataset.vid)}?playsinline=1&rel=0`,
          title: e.target.dataset.title, loading: "lazy",
          allow: "accelerometer; autoplay; encrypted-media; gyroscope; picture-in-picture", allowfullscreen: "",
        }));
      } else if (!e.isIntersecting && frame.firstChild) {
        frame.replaceChildren();
      }
    }
  }, { root: box, threshold: 0.6 });

  box.replaceChildren(...clips.map((c) => {
    const node = el("div", { class: "clip", "data-vid": c.video_id, "data-title": c.title },
      el("div", { class: "frame" }),
      el("div", { class: "caption" }, el("strong", {}, c.source), el("span", { dir: "auto" }, c.post || c.title)));
    clipObserver.observe(node);
    return node;
  }));
}

// ---------- navigation ----------
function show(view) {
  state.view = view;
  for (const v of ["news", "clips", "soon"]) $(`#view-${v}`).hidden = v !== view;
  document.querySelectorAll(".bottom button").forEach((b) => {
    if (b.dataset.view === view) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  });
  if (view !== "clips") $("#view-clips").querySelectorAll(".frame").forEach((f) => f.replaceChildren());
}
document.querySelectorAll(".bottom button").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));

// ---------- data ----------
async function load({ silent = false } = {}) {
  try {
    const res = await fetch(`feed.json?t=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) throw new Error(res.status);
    const data = await res.json();
    if (silent && data.updated === state.updated) return;
    const fresh = silent ? data.items.filter((i) => !state.items.some((o) => o.id === i.id)).length : 0;
    state.items = data.items;
    state.updated = data.updated;
    $("#updated").textContent = `آخر تحديث ${timeAgo(data.updated)}`;
    if (silent && fresh && window.scrollY > 200) {
      const btn = $("#fresh");
      btn.textContent = `${fresh} منشور جديد ↑`;
      btn.hidden = false;
    }
    renderChips(); renderNews(); renderClips();
  } catch {
    if (!silent) $("#news").replaceChildren(el("p", { class: "state" }, "تعذّر تحميل الأخبار. حاول بعد شوي."));
  }
}

$("#fresh").addEventListener("click", (e) => { e.currentTarget.hidden = true; window.scrollTo({ top: 0, behavior: "smooth" }); });
load().then(() => {
  // Right after the server wakes up it serves the seed and refreshes in the background:
  // check again shortly instead of waiting the full interval.
  if (state.updated && Date.now() - new Date(state.updated) > 2 * 3600 * 1000) {
    setTimeout(() => load({ silent: true }), 30 * 1000);
  }
});
setInterval(() => load({ silent: true }), REFRESH_MS);
