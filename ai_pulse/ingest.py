#!/usr/bin/env python3
"""Collect AI news and clips from public sources into a feed.json.

Sources: company blogs (RSS), Hacker News, arXiv, and YouTube channel feeds.
If ANTHROPIC_API_KEY is set, new items get a short Arabic tweet-style post and
a category written by Claude. Without it, the original title is used.

The web app serves the feed at /ai/feed.json through current_feed(), which
refreshes it in a background thread once it is an hour old. The refreshed copy
lives in the temp dir, so updates never need a commit (a push to main redeploys
Render). ai_pulse/web/feed.json is only the seed shown right after a deploy.

Refresh the seed by hand: python -m ai_pulse.ingest
"""
import gzip
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

SEED_FEED = Path(__file__).resolve().parent / "web" / "feed.json"
RUNTIME_FEED = Path(tempfile.gettempdir()) / "ai-pulse-feed.json"
REFRESH_SECONDS = 3600
MAX_ITEMS = 300          # items kept in feed.json
MAX_SUMMARIES = 20       # new items summarized per run (caps API cost)
UA = "Mozilla/5.0 (compatible; ai-pulse/0.1)"
MODEL = os.environ.get("AI_PULSE_MODEL", "claude-opus-5-5")

# ---------------------------------------------------------------- sources

RSS_SOURCES = [
    ("OpenAI", "https://openai.com/news/rss.xml"),
    ("Google DeepMind", "https://deepmind.google/blog/rss.xml"),
    ("Hugging Face", "https://huggingface.co/blog/feed.xml"),
]

# YouTube channel IDs (the UC... part of the channel URL). Add or remove freely.
YOUTUBE_CHANNELS = [
    ("Two Minute Papers", "UCbfYPyITQ-7l4upoX8nvctg"),
]

HN_URL = ("https://hn.algolia.com/api/v1/search?tags=story&hitsPerPage=30"
          "&query=AI&numericFilters=points>80,created_at_i>{since}")
ARXIV_URL = ("http://export.arxiv.org/api/query?search_query=cat:cs.AI+OR+cat:cs.CL+OR+cat:cs.LG"
             "&sortBy=submittedDate&sortOrder=descending&max_results=10")

ATOM = "{http://www.w3.org/2005/Atom}"
YT = "{http://www.youtube.com/xml/schemas/2015}"
MEDIA = "{http://search.yahoo.com/mrss/}"


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
    # Some feeds (e.g. DeepMind) send gzip even when it was not requested.
    return gzip.decompress(body) if body[:2] == b"\x1f\x8b" else body


def iso(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_date(text):
    if not text:
        return iso(datetime.now(timezone.utc))
    text = text.strip()
    try:
        return iso(parsedate_to_datetime(text))          # RSS: RFC 822
    except (TypeError, ValueError):
        pass
    try:
        return iso(datetime.fromisoformat(text.replace("Z", "+00:00")))  # Atom: ISO 8601
    except ValueError:
        return iso(datetime.now(timezone.utc))


def clean(text, limit=400):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def item(kind, source, title, url, date, summary="", video_id=None):
    return {"id": url, "kind": kind, "source": source, "title": clean(title, 200),
            "url": url, "date": date, "summary": clean(summary), "video_id": video_id}


def from_rss(source, url):
    root = ET.fromstring(fetch(url))
    out = []
    for it in root.iter("item"):                          # RSS 2.0
        link = (it.findtext("link") or "").strip()
        if link:
            out.append(item("news", source, it.findtext("title"), link,
                            parse_date(it.findtext("pubDate")), it.findtext("description")))
    for e in root.iter(f"{ATOM}entry"):                   # Atom
        link_el = e.find(f"{ATOM}link")
        link = link_el.get("href") if link_el is not None else ""
        if link:
            out.append(item("news", source, e.findtext(f"{ATOM}title"), link,
                            parse_date(e.findtext(f"{ATOM}updated") or e.findtext(f"{ATOM}published")),
                            e.findtext(f"{ATOM}summary")))
    return out[:15]


def from_hn():
    since = int(time.time()) - 2 * 86400
    data = json.loads(fetch(HN_URL.format(since=since)))
    out = []
    for h in data.get("hits", []):
        url = h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}"
        out.append(item("news", "Hacker News", h.get("title"), url, parse_date(h.get("created_at"))))
    return out


def from_arxiv():
    root = ET.fromstring(fetch(ARXIV_URL))
    out = []
    for e in root.iter(f"{ATOM}entry"):
        out.append(item("research", "arXiv", e.findtext(f"{ATOM}title"),
                        (e.findtext(f"{ATOM}id") or "").strip(),
                        parse_date(e.findtext(f"{ATOM}published")), e.findtext(f"{ATOM}summary")))
    return out


def from_youtube(name, channel_id):
    root = ET.fromstring(fetch(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"))
    out = []
    for e in root.iter(f"{ATOM}entry"):
        vid = e.findtext(f"{YT}videoId")
        if not vid:
            continue
        group = e.find(f"{MEDIA}group")
        desc = group.findtext(f"{MEDIA}description") if group is not None else ""
        out.append(item("clip", name, e.findtext(f"{ATOM}title"),
                        f"https://www.youtube.com/watch?v={vid}",
                        parse_date(e.findtext(f"{ATOM}published")), desc, video_id=vid))
    return out[:10]


def collect():
    jobs = [(f"rss:{n}", lambda n=n, u=u: from_rss(n, u)) for n, u in RSS_SOURCES]
    jobs += [("hackernews", from_hn), ("arxiv", from_arxiv)]
    jobs += [(f"youtube:{n}", lambda n=n, c=c: from_youtube(n, c)) for n, c in YOUTUBE_CHANNELS]
    items, status = [], {}
    for name, job in jobs:
        try:
            got = job()
            items += got
            status[name] = len(got)
        except Exception as e:  # one broken source must not stop the run
            status[name] = f"error: {type(e).__name__}"
    return items, status


# ---------------------------------------------------------------- summaries

CATEGORIES = ["أدوات", "نماذج", "أبحاث", "شركات", "مشاريع مفتوحة", "فيديو"]

SYSTEM = (
    "أنت محرر أخبار ذكاء اصطناعي لحساب عربي. لكل عنصر في المدخلات اكتب منشوراً قصيراً "
    "بأسلوب التغريدة (أقل من 240 حرف) بلغة عربية واضحة، مع إبقاء أسماء الأدوات والشركات "
    "بالإنجليزية. لا تخترع معلومات غير موجودة في العنوان أو الوصف. اختر تصنيفاً واحداً من: "
    + "، ".join(CATEGORIES) + ".\n"
    "أعد JSON فقط بدون أي نص آخر، بالشكل: "
    '[{"id": "...", "post": "...", "category": "..."}]'
)


def summarize(new_items):
    """Return {id: {"post", "category"}} for up to MAX_SUMMARIES items, or {} without a key."""
    if not new_items or not os.environ.get("ANTHROPIC_API_KEY"):
        return {}
    try:
        import anthropic  # only needed when summarizing
    except ImportError:
        print("summaries skipped: the anthropic package is not installed", file=sys.stderr)
        return {}

    batch = new_items[:MAX_SUMMARIES]
    payload = [{"id": i["id"], "source": i["source"], "title": i["title"], "summary": i["summary"]}
               for i in batch]
    client = anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low"},
            system=SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
    except anthropic.APIStatusError as e:
        print(f"summaries skipped: API error {e.status_code}", file=sys.stderr)
        return {}
    except anthropic.APIConnectionError:
        print("summaries skipped: network error", file=sys.stderr)
        return {}

    if response.stop_reason == "refusal":
        print("summaries skipped: request declined", file=sys.stderr)
        return {}
    text = "".join(b.text for b in response.content if b.type == "text")
    match = re.search(r"\[.*\]", text, re.S)
    try:
        rows = json.loads(match.group(0)) if match else []
    except json.JSONDecodeError:
        print("summaries skipped: response was not valid JSON", file=sys.stderr)
        return {}
    return {r["id"]: {"post": r.get("post", ""), "category": r.get("category", "")}
            for r in rows if isinstance(r, dict) and r.get("id")}


# ---------------------------------------------------------------- run

def run(feed_path: Path) -> dict:
    """Merge fresh items into feed_path (keeping older ones) and return a short report."""
    old = {}
    if feed_path.exists():
        old = {i["id"]: i for i in json.loads(feed_path.read_text(encoding="utf-8")).get("items", [])}

    fresh, status = collect()
    new_items = []
    for i in fresh:
        if i["id"] in old:
            continue
        old[i["id"]] = i
        new_items.append(i)

    # Summarize new items first, then any older ones still missing a post.
    pending = new_items + [i for i in old.values() if not i.get("post") and i not in new_items]
    try:
        summaries = summarize(pending)
    except Exception as e:  # summaries are optional: never lose the fetched items over them
        print(f"summaries skipped: {type(e).__name__}: {e}", file=sys.stderr)
        summaries = {}
    for item_id, s in summaries.items():
        if item_id in old:
            old[item_id].update(s)

    items = sorted(old.values(), key=lambda i: i["date"], reverse=True)[:MAX_ITEMS]
    feed_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = feed_path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"updated": iso(datetime.now(timezone.utc)), "items": items},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, feed_path)  # readers never see a half-written file
    return {"new": len(new_items), "total": len(items), "sources": status}


# ---------------------------------------------------------------- server

_refresh_lock = threading.Lock()


def _refresh() -> None:
    try:
        if not RUNTIME_FEED.exists():
            shutil.copyfile(SEED_FEED, RUNTIME_FEED)  # start from the seed; also marks the attempt
        print("ai-pulse:", json.dumps(run(RUNTIME_FEED), ensure_ascii=False), flush=True)
    except Exception as e:  # a failed refresh keeps serving the last good feed
        print(f"ai-pulse refresh failed: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        if RUNTIME_FEED.exists():
            RUNTIME_FEED.touch()  # wait the full interval before retrying instead of on every request
    finally:
        _refresh_lock.release()


def _start_refresh() -> None:
    threading.Thread(target=_refresh, name="ai-pulse-refresh", daemon=True).start()


def current_feed() -> bytes:
    """The latest feed JSON. Starts a background refresh when it is older than an hour."""
    fresh = RUNTIME_FEED.exists() and time.time() - RUNTIME_FEED.stat().st_mtime < REFRESH_SECONDS
    if not fresh and _refresh_lock.acquire(blocking=False):
        _start_refresh()
    path = RUNTIME_FEED if RUNTIME_FEED.exists() else SEED_FEED
    return path.read_bytes()


if __name__ == "__main__":
    print(json.dumps(run(SEED_FEED), ensure_ascii=False))
