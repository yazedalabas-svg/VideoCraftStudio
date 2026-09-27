# VideoCraft Studio — notes for Claude

## Design work
- Always use the **Canva** connector (mcp__Canva__* tools) for visual/design work:
  landing-page images, social preview images, icons, banners, release graphics.
  Export from Canva and commit the result under `docs/` (landing page) or `web/static/` (web app).

## Layout
- `app.py`, `video_engine.py`, `ai_engine.py` — PyQt6 Windows desktop app (GPU AI via Video2X/waifu2x).
- `web/` — the web app deployed on Render:
  - `app.py` HTTP routes, `jobs.py` single-worker FFmpeg queue (reuses `video_engine.py`), `media.py` bundled FFmpeg + probing,
    `aivideo.py` AI video: server extracts frames → the browser upscales them → server assembles.
  - `static/` UI: `app.js` (flow), `ai.js` + `ai-worker.js` (in-browser Real-ESRGAN / Real-CUGAN on the visitor's GPU via TF.js, run in a Web Worker with a watchdog),
    `viewer.js` (zoom/pan + before/after for images and synced videos), `models/` (TF.js weights, licenses in `models/NOTICE.md`).
- `docs/` — landing page, served at `/about/`.
- `server.py` — starts the web app on `$PORT`.
- `deploy/render_start/` — provides a `0` command because the Render service's Start Command is `0`. Keep it.

## Checks
- `python -m unittest tests.test_engine tests.test_web` (engine tests need `ffmpeg` on PATH).
- `node --check web/static/*.js`.

## Deploy
- Render auto-deploys `main`. Web deps install on Linux only; PyQt6 on Windows only (see `requirements.txt` markers).
