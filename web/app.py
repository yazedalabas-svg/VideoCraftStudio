"""HTTP API + pages for the web version of VideoCraft Studio.

Routes
  GET  /                      the web app (web/static/index.html)
  GET  /about/                the original landing page (docs/)
  GET  /healthz               health check for Render
  GET  /api/config            limits and options the UI should offer
  POST /api/uploads           start a resumable upload; ?filename=…&size=N
  PUT  /api/uploads/{id}      one chunk; ?offset=N (idempotent, replies {"received": N})
  POST /api/uploads/{id}/finish  create the job; ?options=<json>
  POST /api/jobs              raw file body in one request; ?filename=…&options=<json>
  GET  /api/jobs/{id}         status/progress
  POST /api/jobs/{id}/cancel
  GET  /api/jobs/{id}/result  processed file (?download=1 for attachment)
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import quote

from starlette.applications import Starlette
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import aivideo, jobs, media

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"

manager = jobs.JobManager()
FFMPEG = media.ensure_ffmpeg()


def error(message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def too_big() -> JSONResponse:
    mb = jobs.MAX_UPLOAD_MB
    limit = f"{mb / 1024:g} غيغابايت" if mb >= 1024 else f"{mb} ميغابايت"
    return error(f"الملف أكبر من الحد المسموح ({limit}).", 413)


async def read_capped(request: Request, limit: int) -> bytes | None:
    """The request body, or None if it is bigger than `limit` (read no further: the
    instance has 512 MB, and `request.body()` would hold any size in memory)."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        return None
    parts, size = [], 0
    async for part in request.stream():
        size += len(part)
        if size > limit:
            return None
        parts.append(part)
    return b"".join(parts)


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
            "video_seconds_by_resolution": jobs.VIDEO_SECONDS_BY_RESOLUTION,
            "video_4k": jobs.VIDEO_4K_OK,
            "long_video_seconds": jobs.LONG_VIDEO_SECONDS,
            "ai_video": {"max_seconds": aivideo.MAX_SECONDS, "fps": list(aivideo.FPS_CHOICES),
                         "default_fps": aivideo.DEFAULT_FPS, "sizes": list(aivideo.SIZE_CHOICES),
                         "default_size": aivideo.DEFAULT_SIZE},
            "result_ttl_seconds": jobs.RESULT_TTL,
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
        return too_big()
    if declared and declared.isdigit() and not jobs.disk_has_room(int(declared)):
        return error("مساحة الخادم ما تكفي لهذا الملف الآن. جرّب ملفًا أصغر أو بعد شوي.", 507)

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
        return too_big()
    except Exception:
        source.unlink(missing_ok=True)
        return error("انقطع الرفع. حاول مرة ثانية.", 400)

    if received == 0:
        source.unlink(missing_ok=True)
        return error("الملف فارغ.", 400)

    job = manager.submit(job_id, filename, source, options)
    return JSONResponse(job.public(manager.position(job)), status_code=201)


# ---------- Resumable uploads ----------
# Phones on mobile data (and Render's free instance waking up or restarting) drop long
# requests, so files go up in small chunks. Each chunk is idempotent: re-sending the same
# offset overwrites the same bytes, and the reply always says how much the server has.

CHUNK_SIZE = 4 * 1024 * 1024  # what the browser sends per request (fewer round trips for GB files)
MAX_CHUNK = 8 * 1024 * 1024  # what the server accepts per request
UPLOADS: dict[str, dict] = {}  # upload id (= future job id) → {"source", "filename", "size"}


def _upload_or_none(request: Request) -> dict | None:
    upload = UPLOADS.get(request.path_params["upload_id"])
    if upload and not upload["source"].parent.exists():  # cleaned up by the janitor
        UPLOADS.pop(request.path_params["upload_id"], None)
        return None
    return upload


def _gone() -> JSONResponse:
    # The instance restarted (or the upload expired): the browser starts the upload over.
    return JSONResponse({"error": "انتهت جلسة الرفع.", "restart": True}, status_code=404)


async def upload_start(request: Request) -> JSONResponse:
    if not FFMPEG:
        return error("محرك المعالجة غير متوفر على الخادم حاليًا.", 503)
    filename = (request.query_params.get("filename") or "").strip()[:200]
    if Path(filename).suffix.lower() not in jobs.ALLOWED_SUFFIXES:
        return error("نوع الملف غير مدعوم.", 415)
    size = request.query_params.get("size", "")
    if not size.isdigit() or int(size) == 0:
        return error("الملف فارغ.", 400)
    if int(size) > jobs.MAX_UPLOAD_MB * 1024 * 1024:
        return too_big()
    if not jobs.disk_has_room(int(size)):
        return error("مساحة الخادم ما تكفي لهذا الملف الآن. جرّب ملفًا أصغر أو بعد شوي.", 507)
    try:
        upload_id, source = manager.new_upload(filename)
    except jobs.QueueFull:
        return error("الخادم مشغول بملفات أخرى الآن. حاول بعد دقائق.", 503)
    source.touch()
    UPLOADS[upload_id] = {"source": source, "filename": filename, "size": int(size)}
    return JSONResponse({"upload_id": upload_id, "chunk_size": CHUNK_SIZE, "received": 0}, status_code=201)


async def upload_chunk(request: Request) -> JSONResponse:
    upload = _upload_or_none(request)
    if not upload:
        return _gone()
    source: Path = upload["source"]
    have = source.stat().st_size
    offset = request.query_params.get("offset", "")
    if not offset.isdigit() or int(offset) > have:
        return JSONResponse({"error": "ترتيب الأجزاء غير صحيح.", "received": have}, status_code=409)
    offset = int(offset)
    body = await read_capped(request, MAX_CHUNK)
    if not body or offset + len(body) > upload["size"]:
        return error("جزء غير صالح.", 400)
    with open(source, "r+b") as out:
        out.seek(offset)
        out.write(body)
        out.truncate(offset + len(body))
    return JSONResponse({"received": offset + len(body)})


async def upload_finish(request: Request) -> JSONResponse:
    upload_id = request.path_params["upload_id"]
    upload = _upload_or_none(request)
    if not upload:
        # A retried "finish" whose first reply was lost: the job already exists.
        job = manager.get(upload_id)
        return JSONResponse(job.public(manager.position(job))) if job else _gone()
    have = upload["source"].stat().st_size
    if have != upload["size"]:
        return JSONResponse({"error": "الملف لم يكتمل رفعه.", "received": have}, status_code=409)
    try:
        options = json.loads(request.query_params.get("options") or "{}")
        if not isinstance(options, dict):
            raise ValueError
    except ValueError:
        return error("إعدادات غير صالحة.", 400)
    UPLOADS.pop(upload_id, None)
    job = manager.submit(upload_id, upload["filename"], upload["source"], options)
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
    if job.status in ("queued", "running", "awaiting_ai"):
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


# ---------- AI video: frames go to the browser and come back upscaled ----------
MAX_FRAME_BYTES = 8 * 1024 * 1024


def _ai_job(request: Request):
    job = manager.get(request.path_params["job_id"])
    if not job or not job.ai or job.status != "awaiting_ai":
        return None, None
    n = request.path_params.get("n")
    if n is not None and not 1 <= n <= job.ai["frames"]:
        return job, -1
    return job, n


async def frame_get(request: Request):
    job, n = _ai_job(request)
    if not job or n == -1:
        return error("الإطار غير موجود.", 404)
    job.touched = time.time()
    return FileResponse(aivideo.frame_path(job.folder, "frames", n), media_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})


async def frame_put(request: Request) -> JSONResponse:
    job, n = _ai_job(request)
    if not job or n == -1:
        return error("الإطار غير موجود.", 404)
    body = await read_capped(request, MAX_FRAME_BYTES)
    if not body or not body.startswith(b"\xff\xd8"):
        return error("إطار غير صالح (المطلوب JPEG).", 400)
    target = aivideo.frame_path(job.folder, "up", n)
    tmp = target.with_suffix(".part")
    tmp.write_bytes(body)
    tmp.replace(target)  # a retried or cut-off upload never leaves half a frame
    manager.frame_received(job, n)
    return JSONResponse({"done": len(job.frames_done), "next_missing": job.next_missing()})


async def ai_assemble(request: Request) -> JSONResponse:
    job = manager.get(request.path_params["job_id"])
    if not job:
        return error("المهمة غير موجودة.", 404)
    if job.status == "awaiting_ai" and not manager.start_assemble(job):
        return JSONResponse({"error": "باقي إطارات ما وصلت.", "next_missing": job.next_missing()}, status_code=409)
    return JSONResponse(job.public(manager.position(job)))  # also answers a retried request


async def ai_regular(request: Request) -> JSONResponse:
    job = manager.get(request.path_params["job_id"])
    if not job:
        return error("المهمة غير موجودة.", 404)
    if job.status == "awaiting_ai" and not manager.start_regular(job):
        return error("ما قدرت أبدأ المعالجة العادية.", 409)
    return JSONResponse(job.public(manager.position(job)))


async def job_before(request: Request):
    job = _job_or_404(request)
    if not job or not job.before or not job.before.is_file():
        return error("غير متوفر.", 404)
    return FileResponse(job.before, media_type="video/mp4", headers={"Cache-Control": "private, max-age=3600"})


class SecurityHeaders:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        # Models and the TF.js runtime never change between deploys of the same file name,
        # so browsers may keep them for a week (the AI models are also cached in IndexedDB).
        long_cache = scope["path"].startswith(("/static/vendor/", "/static/models/"))

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append((b"x-content-type-options", b"nosniff"))
                headers.append((b"referrer-policy", b"strict-origin-when-cross-origin"))
                if long_cache and message.get("status") == 200:
                    headers.append((b"cache-control", b"public, max-age=604800"))
            await send(message)

        await self.app(scope, receive, send_with_headers)


class TextGZip:
    """Gzip pages, scripts, styles and model JSON only.

    TF.js (1.4 MB) shrinks to about a quarter, which matters on phones. Images,
    videos and model weights are already compressed, and gzipping them would
    only burn the small server's CPU, so they pass through untouched.
    """

    TEXT = (".js", ".css", ".json", ".html", ".svg")

    def __init__(self, app) -> None:
        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=1024)

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "") if scope["type"] == "http" else ""
        if path == "/" or path.startswith("/api/config") or path.endswith(self.TEXT):
            return await self.gzip(scope, receive, send)
        return await self.app(scope, receive, send)


routes = [
    Route("/", index),
    Route("/healthz", healthz),
    Route("/api/config", config),
    Route("/api/jobs", create_job, methods=["POST"]),  # single-request upload (kept for API clients)
    Route("/api/uploads", upload_start, methods=["POST"]),
    Route("/api/uploads/{upload_id}", upload_chunk, methods=["PUT"]),
    Route("/api/uploads/{upload_id}/finish", upload_finish, methods=["POST"]),
    Route("/api/jobs/{job_id}", job_status),
    Route("/api/jobs/{job_id}/cancel", job_cancel, methods=["POST"]),
    Route("/api/jobs/{job_id}/result", job_result),
    Route("/api/jobs/{job_id}/before", job_before),
    Route("/api/jobs/{job_id}/frames/{n:int}", frame_get, methods=["GET"]),
    Route("/api/jobs/{job_id}/frames/{n:int}", frame_put, methods=["PUT"]),
    Route("/api/jobs/{job_id}/assemble", ai_assemble, methods=["POST"]),
    Route("/api/jobs/{job_id}/regular", ai_regular, methods=["POST"]),
    Route("/about", about_redirect),
    Mount("/about", StaticFiles(directory=ROOT / "docs", html=True), name="about"),
    Mount("/static", StaticFiles(directory=STATIC), name="static"),
]

app = SecurityHeaders(TextGZip(Starlette(routes=routes)))
