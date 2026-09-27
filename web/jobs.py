"""Background processing queue for uploaded files.

One worker thread processes jobs one at a time (the free Render instance has
a fraction of a CPU and 512 MB RAM, so parallel encodes would only thrash).
Jobs live in memory; results are deleted after RESULT_TTL seconds.
"""

from __future__ import annotations

import os
import queue
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from video_engine import (
    ExportSettings,
    IMAGE_OUTPUT_SUFFIXES,
    IMAGE_SUFFIXES,
    MediaInfo,
    VideoEngineError,
    build_ffmpeg_command,
    build_image_ffmpeg_command,
)

from . import media

# --- Limits (override with environment variables on Render) -------------------

MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "200"))
MAX_VIDEO_SECONDS = int(os.environ.get("MAX_VIDEO_SECONDS", "180"))
MAX_IMAGE_MEGAPIXELS = int(os.environ.get("MAX_IMAGE_MEGAPIXELS", "40"))
MAX_QUEUE = int(os.environ.get("MAX_QUEUE", "5"))
JOB_TIMEOUT = int(os.environ.get("JOB_TIMEOUT_SECONDS", "2700"))
RESULT_TTL = int(os.environ.get("RESULT_TTL_SECONDS", "3600"))

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".3gp", ".mts", ".ts", ".wmv", ".flv", ".gif"}
ALLOWED_SUFFIXES = VIDEO_SUFFIXES | IMAGE_SUFFIXES

VIDEO_RESOLUTIONS = ("source", "1080p")
IMAGE_RESOLUTIONS = ("source", "1080p", "2k", "4k")
COLOR_STYLES = ("none", "natural", "warm", "bright", "cinematic")

WORK_ROOT = Path(tempfile.gettempdir()) / "videocraft-jobs"


@dataclass
class Job:
    id: str
    filename: str
    folder: Path
    source: Path
    options: dict
    status: str = "queued"  # queued → running → done | error | cancelled
    progress: float = 0.0
    message: str = "في الانتظار…"
    output: Path | None = None
    download_name: str = ""
    kind: str = ""
    info: dict = field(default_factory=dict)
    created: float = field(default_factory=time.time)
    finished: float | None = None
    process: subprocess.Popen | None = None
    cancel_requested: bool = False

    def public(self, position: int | None) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "progress": round(self.progress, 4),
            "message": self.message,
            "kind": self.kind,
            "info": self.info,
            "filename": self.filename,
            "download_name": self.download_name,
            "queue_position": position,
        }


class QueueFull(Exception):
    pass


def _clamp(value, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def settings_from_options(options: dict, info: MediaInfo) -> ExportSettings:
    """Whitelist and clamp browser options into engine settings (never trust the client)."""
    resolutions = IMAGE_RESOLUTIONS if info.is_image else VIDEO_RESOLUTIONS
    resolution = options.get("resolution") if options.get("resolution") in resolutions else "source"

    source_fps = round(info.fps) if info.fps else 30
    fps_choice = str(options.get("fps", "source"))
    fps = {"30": 30, "60": 60}.get(fps_choice, source_fps)

    style = options.get("color_style") if options.get("color_style") in COLOR_STYLES else "natural"
    return ExportSettings(
        resolution=resolution,
        fps=max(15, min(60, fps)),
        interpolate=bool(options.get("interpolate", False)),
        denoise=_clamp(options.get("denoise"), 0, 100, 40),
        sharpness=_clamp(options.get("sharpness"), 0, 100, 40),
        brightness=_clamp(options.get("brightness"), -50, 50, 0),
        contrast=_clamp(options.get("contrast"), -50, 50, 10),
        saturation=_clamp(options.get("saturation"), -50, 50, 10),
        color_style=style,
        deinterlace=bool(options.get("deinterlace", False)),
        stabilize=bool(options.get("stabilize", False)),
        audio_normalize=bool(options.get("audio_normalize", True)),
        audio_clean=bool(options.get("audio_clean", False)),
        codec="h264",
        quality=_clamp(options.get("quality"), 14, 30, 20),
        hardware=False,  # no GPU on the server
        container="mp4",
        safe_mode=True,
        ai_enabled=False,  # neural models need the Windows app + a GPU
    )


def _fast_server_preset(command: list[str]) -> list[str]:
    """The desktop presets assume a big CPU; on a tiny server 'veryfast' keeps jobs finishing."""
    command = list(command)
    if "-preset" in command:
        command[command.index("-preset") + 1] = "veryfast"
    return command


def _safe_stem(filename: str) -> str:
    stem = Path(filename).stem
    stem = re.sub(r"[^\w\-. ]+", "", stem, flags=re.UNICODE).strip(" .") or "videocraft"
    return stem[:80]


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._queue: queue.Queue[str] = queue.Queue()
        WORK_ROOT.mkdir(parents=True, exist_ok=True)
        threading.Thread(target=self._worker, name="videocraft-worker", daemon=True).start()
        threading.Thread(target=self._janitor, name="videocraft-janitor", daemon=True).start()

    # --- public API used by the HTTP layer ---------------------------------

    def new_upload(self, filename: str) -> tuple[str, Path]:
        """Reserve a job folder for an incoming upload (before the body is read)."""
        with self._lock:
            waiting = sum(1 for job in self._jobs.values() if job.status in ("queued", "running"))
        if waiting >= MAX_QUEUE:
            raise QueueFull
        job_id = secrets.token_urlsafe(16)
        folder = WORK_ROOT / job_id
        folder.mkdir(parents=True)
        return job_id, folder / f"source{Path(filename).suffix.lower()}"

    def submit(self, job_id: str, filename: str, source: Path, options: dict) -> Job:
        job = Job(id=job_id, filename=filename, folder=source.parent, source=source, options=options)
        with self._lock:
            self._jobs[job_id] = job
        self._queue.put(job_id)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def position(self, job: Job) -> int | None:
        if job.status != "queued":
            return None
        with self._lock:
            queued = sorted(
                (j for j in self._jobs.values() if j.status == "queued"), key=lambda j: j.created
            )
        return next((i + 1 for i, j in enumerate(queued) if j.id == job.id), None)

    def cancel(self, job: Job) -> None:
        job.cancel_requested = True
        if job.status == "queued":
            self._finish(job, "cancelled", "تم الإلغاء.")
            job.source.unlink(missing_ok=True)
        elif job.process and job.process.poll() is None:
            job.process.kill()

    # --- worker ------------------------------------------------------------

    def _finish(self, job: Job, status: str, message: str) -> None:
        job.status = status
        job.message = message
        job.finished = time.time()
        job.process = None
        if status == "error":
            job.progress = 0.0

    def _worker(self) -> None:
        while True:
            job_id = self._queue.get()
            job = self.get(job_id)
            if not job or job.status != "queued":
                continue
            job.status = "running"
            job.message = "جاري تحليل الملف…"
            try:
                self._run(job)
            except VideoEngineError as exc:
                self._finish(job, "error", str(exc))
            except Exception as exc:  # keep the worker alive no matter what
                self._finish(job, "error", f"حدث خطأ غير متوقع: {exc}")
            finally:
                job.source.unlink(missing_ok=True)

    def _run(self, job: Job) -> None:
        info = media.probe(job.source)
        job.kind = "image" if info.is_image else "video"
        job.info = {
            "width": info.display_width,
            "height": info.display_height,
            "fps": round(info.fps, 2),
            "duration": round(info.duration, 2),
            "codec": info.video_codec,
            "has_audio": bool(info.audio_codec),
        }

        if info.is_image and info.width * info.height > MAX_IMAGE_MEGAPIXELS * 1_000_000:
            raise VideoEngineError(f"الصورة كبيرة جدًا (الحد {MAX_IMAGE_MEGAPIXELS} ميغابكسل).")
        if not info.is_image and info.duration > MAX_VIDEO_SECONDS:
            raise VideoEngineError(
                f"مدة الفيديو أطول من الحد المسموح على الموقع ({MAX_VIDEO_SECONDS // 60} دقائق). "
                "للفيديوهات الطويلة استخدم نسخة ويندوز."
            )

        settings = settings_from_options(job.options, info)
        stem = _safe_stem(job.filename)
        if info.is_image:
            fmt = job.options.get("image_format")
            suffix = IMAGE_OUTPUT_SUFFIXES.get(fmt, ".png")
            output = job.folder / f"result{suffix}"
            command = build_image_ffmpeg_command(job.source, output, info, settings)
        else:
            suffix = ".mp4"
            output = job.folder / "result.mp4"
            command = _fast_server_preset(build_ffmpeg_command(job.source, output, info, settings))

        job.message = "جاري التحسين…"
        self._execute(job, command, info.duration)

        if job.cancel_requested:
            output.unlink(missing_ok=True)
            self._finish(job, "cancelled", "تم الإلغاء.")
            return
        if not output.is_file() or output.stat().st_size == 0:
            raise VideoEngineError("لم ينتج ملف. جرّب إعدادات أخف أو ملفًا آخر.")

        job.output = output
        job.download_name = f"{stem}-videocraft{suffix}"
        job.progress = 1.0
        self._finish(job, "done", "جاهز للتحميل.")

    def _execute(self, job: Job, command: list[str], duration: float) -> None:
        log_path = job.folder / "ffmpeg.log"
        with open(log_path, "w", encoding="utf-8", errors="replace") as log:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=log,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            job.process = process
            timer = threading.Timer(JOB_TIMEOUT, process.kill)
            timer.start()
            try:
                assert process.stdout is not None
                for line in process.stdout:
                    key, _, value = line.strip().partition("=")
                    if key == "out_time_us" and duration > 0 and value.isdigit():
                        job.progress = max(0.0, min(0.99, int(value) / 1_000_000 / duration))
                process.wait()
            finally:
                timer.cancel()

        if job.cancel_requested:
            return
        if process.returncode != 0:
            tail = log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:]
            if process.returncode in (-9, 137):
                raise VideoEngineError("استغرقت المعالجة وقتًا أطول من المسموح. جرّب ملفًا أقصر أو دقة أقل.")
            raise VideoEngineError("فشلت المعالجة: " + (" ".join(tail)[-300:] or "خطأ غير معروف"))

    # --- cleanup -------------------------------------------------------------

    def _janitor(self) -> None:
        while True:
            time.sleep(300)
            now = time.time()
            with self._lock:
                expired = [
                    job for job in self._jobs.values()
                    if job.finished and now - job.finished > RESULT_TTL
                ]
                for job in expired:
                    del self._jobs[job.id]
            for job in expired:
                shutil.rmtree(job.folder, ignore_errors=True)
            # Remove abandoned upload folders (e.g. connection dropped mid-upload).
            known = {job.folder for job in self._jobs.values()}
            for folder in WORK_ROOT.glob("*"):
                if folder not in known and now - folder.stat().st_mtime > RESULT_TTL:
                    shutil.rmtree(folder, ignore_errors=True)
