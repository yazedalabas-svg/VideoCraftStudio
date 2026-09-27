"""HTTP API + pages for the web version of VideoCraft Studio.

Routes
  GET  /                      the web app (web/static/index.html)
  GET  /about/                the original landing page (docs/)
  GET  /healthz               health check for Render
  GET  /api/config            limits and options the UI should offer
  POST /api/jobs              raw file body; ?filename=…&options=<json>
  GET  /api/jobs/{id}         status/progress
  POST /api/jobs/{id}/cancel
  GET  /api/jobs/{id}/result  processed file (?download=1 for attachment)
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import jobs, media

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"

manager = jobs.JobManager()
FFMPEG = media.ensure_ffmpeg()


def error(message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


async def index(_: Request) -> FileResponse:
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


async def about_redirect(_: Request) -> RedirectResponse:
    return RedirectResponse("/about/")


async def healthz(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "ffmpeg": bool(FFMPEG)})


async def config(_: Request) -> JSONResponse:
    return JSONResponse(
        {
            "max_upload_mb": jobs.MAX_UPLOAD_MB,
            "max_video_seconds": jobs.MAX_VIDEO_SECONDS,
            "max_image_megapixels": jobs.MAX_IMAGE_MEGAPIXELS,
            "video_resolutions": list(jobs.VIDEO_RESOLUTIONS),
            "image_resolutions": list(jobs.IMAGE_RESOLUTIONS),
            "image_upscales": list(jobs.IMAGE_UPSCALES),
            "max_output_megapixels": jobs.MAX_OUTPUT_MEGAPIXELS,
            "accept": sorted(jobs.ALLOWED_SUFFIXES),
            "ffmpeg": bool(FFMPEG),
        }
    )


async def create_job(request: Request) -> JSONResponse:
    if not FFMPEG:
        return error("محرك المعالجة غير متوفر على الخادم حاليًا.", 503)

    filename = (request.query_params.get("filename") or "").strip()[:200]
    suffix = Path(filename).suffix.lower()
    if suffix not in jobs.ALLOWED_SUFFIXES:
        return error("نوع الملف غير مدعوم.", 415)
    try:
        options = json.loads(request.query_params.get("options") or "{}")
        if not isinstance(options, dict):
            raise ValueError
    except ValueError:
        return error("إعدادات غير صالحة.", 400)

    limit = jobs.MAX_UPLOAD_MB * 1024 * 1024
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        return error(f"الملف أكبر من الحد المسموح ({jobs.MAX_UPLOAD_MB} ميغابايت).", 413)

    try:
        job_id, source = manager.new_upload(filename)
    except jobs.QueueFull:
        return error("الخادم مشغول بملفات أخرى الآن. حاول بعد دقائق.", 503)

    # Stream the body to disk so big uploads never sit in RAM.
    received = 0
    try:
        with open(source, "wb") as out:
            async for chunk in request.stream():
                received += len(chunk)
                if received > limit:
                    raise OverflowError
                out.write(chunk)
    except OverflowError:
        source.unlink(missing_ok=True)
        return error(f"الملف أكبر من الحد المسموح ({jobs.MAX_UPLOAD_MB} ميغابايت).", 413)
    except Exception:
        source.unlink(missing_ok=True)
        return error("انقطع الرفع. حاول مرة ثانية.", 400)

    if received == 0:
        source.unlink(missing_ok=True)
        return error("الملف فارغ.", 400)

    job = manager.submit(job_id, filename, source, options)
    return JSONResponse(job.public(manager.position(job)), status_code=201)


def _job_or_404(request: Request):
    return manager.get(request.path_params["job_id"])


async def job_status(request: Request) -> JSONResponse:
    job = _job_or_404(request)
    if not job:
        return error("المهمة غير موجودة أو انتهت صلاحيتها.", 404)
    return JSONResponse(job.public(manager.position(job)), headers={"Cache-Control": "no-store"})


async def job_cancel(request: Request) -> JSONResponse:
    job = _job_or_404(request)
    if not job:
        return error("المهمة غير موجودة.", 404)
    if job.status in ("queued", "running"):
        manager.cancel(job)
    return JSONResponse(job.public(None))


async def job_result(request: Request):
    job = _job_or_404(request)
    if not job or job.status != "done" or not job.output or not job.output.is_file():
        return error("النتيجة غير متوفرة.", 404)
    disposition = "attachment" if request.query_params.get("download") else "inline"
    return FileResponse(
        job.output,
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(job.download_name)}",
            "Cache-Control": "private, max-age=3600",
        },
    )


class SecurityHeaders:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append((b"x-content-type-options", b"nosniff"))
                headers.append((b"referrer-policy", b"strict-origin-when-cross-origin"))
            await send(message)

        await self.app(scope, receive, send_with_headers)


routes = [
    Route("/", index),
    Route("/healthz", healthz),
    Route("/api/config", config),
    Route("/api/jobs", create_job, methods=["POST"]),
    Route("/api/jobs/{job_id}", job_status),
    Route("/api/jobs/{job_id}/cancel", job_cancel, methods=["POST"]),
    Route("/api/jobs/{job_id}/result", job_result),
    Route("/about", about_redirect),
    Mount("/about", StaticFiles(directory=ROOT / "docs", html=True), name="about"),
    Mount("/static", StaticFiles(directory=STATIC), name="static"),
]

app = SecurityHeaders(Starlette(routes=routes))
