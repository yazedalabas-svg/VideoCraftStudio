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
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from video_engine import (
    ExportSettings,
    IMAGE_OUTPUT_SUFFIXES,
    IMAGE_SUFFIXES,
    MediaInfo,
    VideoEngineError,
    _color_filters,
    build_audio_filters,
    build_image_ffmpeg_command,
    build_video_filters,
    find_binary,
)

from . import aivideo, media

# --- Limits (override with environment variables on Render) -------------------

MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "8192"))
MAX_VIDEO_SECONDS = int(os.environ.get("MAX_VIDEO_SECONDS", "3600"))
# Clips longer than this get the faster settings (see FAST_* below): on a fraction of a
# CPU an hour of video would otherwise take most of a day.
LONG_VIDEO_SECONDS = int(os.environ.get("LONG_VIDEO_SECONDS", "600"))
MAX_IMAGE_MEGAPIXELS = int(os.environ.get("MAX_IMAGE_MEGAPIXELS", "40"))
# Upscaled output is capped so a ×4 of a big photo can't exhaust the 512 MB instance.
MAX_OUTPUT_MEGAPIXELS = int(os.environ.get("MAX_OUTPUT_MEGAPIXELS", "36"))
MAX_QUEUE = int(os.environ.get("MAX_QUEUE", "5"))
# A job may take this long at least, or 40× the clip length for long clips on the tiny CPU.
JOB_TIMEOUT = int(os.environ.get("JOB_TIMEOUT_SECONDS", "2700"))
# The input, a temporary copy (heavy sources) and the output live on disk while a job runs.
DISK_HEADROOM = 2.5


def server_memory_mb() -> int:
    """Memory this instance may use: MEMORY_LIMIT_MB, else the container's cgroup limit."""
    if os.environ.get("MEMORY_LIMIT_MB", "").isdigit():
        return int(os.environ["MEMORY_LIMIT_MB"])
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            value = Path(path).read_text().strip()
        except OSError:
            continue
        if value.isdigit() and int(value) < 1 << 50:  # "max" / huge = no limit set
            return int(value) // (1024 * 1024)
    if os.environ.get("RENDER"):  # on Render without a readable limit: assume the free plan
        return 512
    return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // (1024 * 1024)


# Encoding 4K video peaked at ~1.1 GB even with every trick here, so 4K *video* output
# is offered only on instances with room for it (e.g. Render Standard, 2 GB).
VIDEO_4K_OK = server_memory_mb() >= 1500
# Long jobs can finish while nobody is watching, so results wait 6 hours for a visit.
RESULT_TTL = int(os.environ.get("RESULT_TTL_SECONDS", "21600"))

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".3gp", ".mts", ".ts", ".wmv", ".flv", ".gif"}
ALLOWED_SUFFIXES = VIDEO_SUFFIXES | IMAGE_SUFFIXES

VIDEO_RESOLUTIONS = ("source", "1080p", "2k", "4k")
IMAGE_RESOLUTIONS = ("source", "1080p", "2k", "4k")
IMAGE_UPSCALES = ("none", "1080p", "2k", "4k", "2", "3", "4")
# Big video presets are heavy for the free instance, so they get shorter clips.
VIDEO_SECONDS_BY_RESOLUTION = {
    "2k": int(os.environ.get("MAX_VIDEO_SECONDS_2K", "90")),
    "4k": int(os.environ.get("MAX_VIDEO_SECONDS_4K", "30")),
}
PRESET_BOXES = {"1080p": (1920, 1080), "2k": (2560, 1440), "4k": (3840, 2160)}
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
    eta: int | None = None  # seconds left, estimated from the encoding speed
    kill: object = None  # stops every FFmpeg process of the running job
    process: subprocess.Popen | None = None
    cancel_requested: bool = False
    # AI video (web/aivideo.py): "" → normal job; "frames" → waiting for the browser;
    # "assemble" → queued/running the final join.
    phase: str = ""
    ai: dict = field(default_factory=dict)  # frame plan: fps, width, height, frames, out size
    frames_done: set = field(default_factory=set)
    touched: float = field(default_factory=time.time)  # last browser activity (frames)
    before: Path | None = None  # small "before" clip for the comparison

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
            "eta": self.eta if self.status == "running" else None,
            "ai": self._ai_public() if self.ai else None,
            "has_before": bool(self.before and self.before.is_file()),
        }

    def next_missing(self) -> int | None:
        return next((n for n in range(1, self.ai.get("frames", 0) + 1) if n not in self.frames_done), None)

    def _ai_public(self) -> dict:
        return {**self.ai, "done": len(self.frames_done), "next_missing": self.next_missing()}


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
    if info.is_image and options.get("upscale") in IMAGE_UPSCALES:
        # The upscale choice (see upscale_size) replaces the resolution preset for stills.
        resolution = "source"

    source_fps = round(info.fps) if info.fps else 30
    fps_choice = str(options.get("fps", "source"))
    fps = {"30": 30, "60": 60}.get(fps_choice, source_fps)

    style = options.get("color_style") if options.get("color_style") in COLOR_STYLES else "natural"
    # "Speed" priority (the default for long clips): skip the two slowest filters.
    speed = options.get("priority") == "speed" and not info.is_image
    return ExportSettings(
        resolution=resolution,
        fps=max(15, min(60, fps)),
        interpolate=bool(options.get("interpolate", False)) and not speed,
        denoise=0 if speed else _clamp(options.get("denoise"), 0, 100, 40),
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


def upscale_size(info: MediaInfo, choice) -> tuple[int, int] | None:
    """Exact output size for ×2/×3/×4 or "fit 1080p/2K/4K", shrunk to fit MAX_OUTPUT_MEGAPIXELS.

    Presets follow the picture's orientation (portrait/square boxes, like the
    desktop app). Never returns a size smaller than the source: upscaling only enlarges.
    """
    src_w, src_h = info.display_width, info.display_height
    choice = str(choice)
    if choice in ("2", "3", "4"):
        factor = float(choice)
    elif choice in PRESET_BOXES:
        long_side, short_side = PRESET_BOXES[choice]
        box_w, box_h = (
            (short_side, long_side) if src_h > src_w
            else (short_side, short_side) if src_w == src_h
            else (long_side, short_side)
        )
        factor = min(box_w / src_w, box_h / src_h)
    else:
        return None
    width, height = int(src_w * factor), int(src_h * factor)
    limit = MAX_OUTPUT_MEGAPIXELS * 1_000_000
    if width * height > limit:
        shrink = (limit / (width * height)) ** 0.5
        width, height = int(width * shrink), int(height * shrink)
    width, height = max(2, width - width % 2), max(2, height - height % 2)
    if width <= src_w and height <= src_h:
        return None  # already that big (or at the cap): nothing to enlarge
    return width, height


# HDR (HLG / PQ) → SDR BT.709. Without this, phone HDR videos come out washed out
# and wrongly tagged. Raw frames arrive through a pipe without colour metadata, so
# setparams stamps the source's HDR properties back on before zscale converts them.
def tonemap_filter(transfer: str, light: bool = False) -> str:
    """Filmic tone mapping (float frames), or a direct transfer conversion when `light`.

    The light path is for 4K HDR sources: at that size the float path peaked at ~455 MB
    in the decoding process alone (vs ~265 MB), which doesn't fit the 512 MB instance.
    HLG (what phones record) is designed to survive this; PQ may look a little flatter.
    """
    params = f"setparams=color_primaries=bt2020:color_trc={transfer}:colorspace=bt2020nc:range=tv,"
    if light:
        return params + "zscale=t=bt709:p=bt709:m=bt709:r=tv:dither=none,format=yuv420p"
    return (
        f"setparams=color_primaries=bt2020:color_trc={transfer}:colorspace=bt2020nc:range=tv,"
        "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
        "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
    )


# x264 settings measured for the 512 MB instance: a 5-frame look-ahead and 2 B-frames
# keep quality and file size (SSIM 0.996, same size as the defaults) at ~215 MB instead
# of ~300 MB; one thread is as fast as more on a fraction of a CPU.
X264_LEAN = "threads=1:rc-lookahead=5:bframes=2"
X264_PRESET = "veryfast"
X264_PRESET_FAST = os.environ.get("X264_PRESET_FAST", "superfast")  # long clips
# Bigger than ~1.5× 1080p: decoding alone takes most of the memory budget.
HEAVY_SOURCE_PIXELS = 1920 * 1080 * 3 // 2


def server_video_pipeline(
    source, output, info: MediaInfo, settings: ExportSettings, transfer: str | None, audio_source=None,
    fast: bool = False,
) -> list[list[str]]:
    """FFmpeg processes joined by pipes: decode + filters → encode (HDR: decode → tone map + filters → encode).

    Fitted to a 512 MB / fraction-of-a-CPU server:
    - Frames are shrunk to the output box right after decoding, so denoise, colour,
      frame interpolation and encoding never work on 4K phone frames. "source" means
      "keep the size, up to 1080p" on the server; 2K/4K only when asked for.
    - HDR (HLG / PQ) is tone-mapped to SDR and tagged BT.709.
    - Separate processes: inside one process FFmpeg 7 lets a fast stage run ahead of
      a slow one and queues hundreds of MB of frames (a 4K HEVC clip peaked at 1.2 GB
      and got the instance killed). Pipes cap each queue at 64 KB.
    - The encoder uses X264_LEAN; one thread is as fast as more on a fraction of a CPU.
    """
    ffmpeg = find_binary("ffmpeg")
    long_side, short_side = PRESET_BOXES.get(settings.resolution, PRESET_BOXES["1080p"])
    w, h = info.display_width, info.display_height
    box_w, box_h = (short_side, long_side) if h > w else (short_side, short_side) if w == h else (long_side, short_side)
    # Raw frames travel between processes in NUT: any pixel format (HDR keeps its
    # 10 bits until tone mapping) and exact timestamps.
    # FFmpeg keeps its own frame/packet queues on both sides of a pipe; with raw frames
    # (6 MB at 1080p 10-bit) its defaults hold hundreds of MB, so both are kept tiny.
    pipe_out = ["-c:v", "rawvideo", "-max_muxing_queue_size", "2", "-muxing_queue_data_threshold", "1", "-f", "nut", "-"]
    pipe_in = ["-thread_queue_size", "2", "-f", "nut", "-i", "-"]

    prescale = []
    if w > box_w or h > box_h:
        prescale = [f"scale=w={box_w}:h={box_h}:force_original_aspect_ratio=decrease:"
                    "force_divisible_by=2:flags=bicubic"]
    filters = build_video_filters(info, settings) + ["format=yuv420p"]
    decode = [ffmpeg, "-hide_banner", "-nostdin", "-threads", "1", "-i", str(source), "-map", "0:v:0", "-filter_threads", "1"]

    # Measured on a 1080p HLG HEVC clip: tone mapping in the decoding process peaks at
    # far less than giving it its own process (every extra process holds its own copies).
    tone = [tonemap_filter(transfer)] if transfer else []
    decode += ["-vf", ",".join(prescale + tone + filters), *pipe_out]
    stages = [decode]

    quality = max(14, min(30, int(settings.quality)))
    encode = [
        ffmpeg, "-hide_banner", "-nostdin", "-y",
        *pipe_in,
        "-i", str(audio_source or source),  # audio, metadata and chapters come from the original
        "-map", "0:v:0", "-map", "1:a?",
        "-c:v", "libx264", "-preset", X264_PRESET_FAST if fast else X264_PRESET, "-crf", str(quality), "-x264-params", X264_LEAN,
        "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
    ]
    audio_filters = build_audio_filters(settings)
    if audio_filters:
        encode += ["-af", ",".join(audio_filters)]
    encode += [
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-map_metadata", "1", "-map_chapters", "1", "-movflags", "+faststart",
        "-max_muxing_queue_size", "1024", "-progress", "pipe:1", "-nostats", str(output),
    ]
    return stages + [encode]


def normalize_command(source, output, info: MediaInfo, resolution: str, transfer: str | None) -> list[str]:
    """First pass for heavy sources (4K phone footage): shrink to the output box and
    convert HDR, into a near-lossless temporary file.

    Done in one go, the enhancement filters are slower than decoding, and FFmpeg
    queues decoded 4K frames in front of them (~130 MB more). Shrinking first in a
    fast pass keeps both passes around 300 MB on the 512 MB instance.
    """
    long_side, short_side = PRESET_BOXES.get(resolution, PRESET_BOXES["1080p"])
    w, h = info.display_width, info.display_height
    box_w, box_h = (short_side, long_side) if h > w else (short_side, short_side) if w == h else (long_side, short_side)
    filters = [f"scale=w={box_w}:h={box_h}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=bicubic"]
    if transfer:
        filters.append(tonemap_filter(transfer, light=True))
    filters.append("format=yuv420p")
    return [
        find_binary("ffmpeg"), "-hide_banner", "-nostdin", "-y", "-threads", "1", "-i", str(source),
        "-map", "0:v:0", "-filter_threads", "1", "-vf", ",".join(filters),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-x264-params", "threads=1",
        "-an", "-progress", "pipe:1", "-nostats", str(output),
    ]


def _frames_info(job: Job, p: dict) -> MediaInfo:
    """MediaInfo describing the upscaled frames (for settings/filters at assembly)."""
    return MediaInfo(path=str(job.folder / "up"), width=p["out_width"], height=p["out_height"],
                     fps=float(p["fps"]), duration=p["frames"] / p["fps"], size_bytes=0,
                     video_codec="mjpeg", audio_codec="aac", format_name="image2")


def disk_has_room(size_bytes: int) -> bool:
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(WORK_ROOT).free > size_bytes * DISK_HEADROOM


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
        threading.Thread(target=self._keep_awake, name="videocraft-keepalive", daemon=True).start()

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
        if job.status == "awaiting_ai":
            self._finish(job, "cancelled", "تم الإلغاء.")
            job.source.unlink(missing_ok=True)
        elif job.status == "queued":
            self._finish(job, "cancelled", "تم الإلغاء.")
            job.source.unlink(missing_ok=True)
        elif job.kill:
            job.kill()

    # --- worker ------------------------------------------------------------

    def _finish(self, job: Job, status: str, message: str) -> None:
        job.status = status
        job.message = message
        job.finished = time.time()
        job.process = None
        job.kill = None
        if status == "error":
            job.progress = 0.0

    def _worker(self) -> None:
        while True:
            job_id = self._queue.get()
            job = self.get(job_id)
            if not job or job.status != "queued":
                continue
            job.status = "running"
            job.message = "جاري تحليل الملف…" if job.phase != "assemble" else "جاري تجميع الفيديو…"
            try:
                if job.phase == "assemble":
                    self._assemble(job)
                else:
                    self._run(job)
            except VideoEngineError as exc:
                self._finish(job, "error", str(exc))
            except Exception as exc:  # keep the worker alive no matter what
                self._finish(job, "error", f"حدث خطأ غير متوقع: {exc}")
            finally:
                # An AI video still needs the original for its audio (and a possible
                # fallback to regular processing) while the browser upscales frames.
                if job.status != "awaiting_ai":
                    job.source.unlink(missing_ok=True)

    def _run(self, job: Job) -> None:
        info, extras = media.probe_details(job.source)
        job.kind = "image" if info.is_image else "video"
        job.info = {
            "width": info.display_width,
            "height": info.display_height,
            "fps": round(info.fps, 2),
            "duration": round(info.duration, 2),
            "codec": info.video_codec,
            "has_audio": bool(info.audio_codec),
            "hdr": extras["hdr"],
        }

        if info.is_image and info.width * info.height > MAX_IMAGE_MEGAPIXELS * 1_000_000:
            raise VideoEngineError(f"الصورة كبيرة جدًا (الحد {MAX_IMAGE_MEGAPIXELS} ميغابكسل).")
        if not info.is_image and info.duration > MAX_VIDEO_SECONDS:
            raise VideoEngineError(
                f"مدة الفيديو أطول من الحد المسموح على الموقع ({MAX_VIDEO_SECONDS // 60} دقائق). قصّه أولًا، أو "
                "للفيديوهات الطويلة استخدم نسخة ويندوز."
            )

        settings = settings_from_options(job.options, info)
        if not info.is_image and settings.resolution == "4k" and not VIDEO_4K_OK:
            raise VideoEngineError(
                "إخراج الفيديو بدقة 4K يحتاج خادم بذاكرة 2GB أو أكثر، والخادم الحالي أصغر. "
                "اختر 2K أو 1080p، أو استخدم نسخة ويندوز."
            )
        big_limit = VIDEO_SECONDS_BY_RESOLUTION.get(settings.resolution)
        if not info.is_image and big_limit and info.duration > big_limit:
            raise VideoEngineError(
                f"دقة {settings.resolution.upper()} على الموقع للمقاطع حتى {big_limit} ثانية "
                "(الخادم المجاني محدود). اختر 1080p أو استخدم نسخة ويندوز."
            )
        if not info.is_image and job.options.get("ai_video"):
            return self._extract_for_ai(job, info, settings, extras)
        stem = _safe_stem(job.filename)
        if info.is_image:
            fmt = job.options.get("image_format")
            suffix = IMAGE_OUTPUT_SUFFIXES.get(fmt, ".png")
            output = job.folder / f"result{suffix}"
            force_size = upscale_size(info, job.options.get("upscale"))
            command = build_image_ffmpeg_command(job.source, output, info, settings, force_size=force_size)
            if force_size:
                job.info["output_width"], job.info["output_height"] = force_size
        else:
            suffix = ".mp4"
            output = job.folder / "result.mp4"
            command = None
            fast = job.options.get("priority") == "speed"
            if info.display_width * info.display_height > HEAVY_SOURCE_PIXELS:
                # Heavy source: shrink (and convert HDR) first, then enhance the lighter file.
                job.message = "تجهيز الفيديو (تصغير الدقة)…"
                normalized = job.folder / "normalized.mp4"
                self._execute(job, normalize_command(job.source, normalized, info, settings.resolution, extras["transfer"]),
                              info.duration, span=(0.0, 0.35))
                if job.cancel_requested:
                    normalized.unlink(missing_ok=True)
                    self._finish(job, "cancelled", "تم الإلغاء.")
                    return
                light_info = media.probe(normalized)
                command = server_video_pipeline(normalized, output, light_info, settings, None,
                                                audio_source=job.source, fast=fast)
                span = (0.35, 1.0)
            else:
                command = server_video_pipeline(job.source, output, info, settings, extras["transfer"], fast=fast)
                span = (0.0, 1.0)

        job.message = "جاري التحسين…"
        if info.is_image:
            self._execute(job, command, info.duration)
        else:
            self._execute(job, command, info.duration, span=span)
            (job.folder / "normalized.mp4").unlink(missing_ok=True)

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

    # --- AI video (see web/aivideo.py) ---------------------------------------

    def _extract_for_ai(self, job: Job, info: MediaInfo, settings: ExportSettings, extras: dict) -> None:
        if info.duration > aivideo.MAX_SECONDS:
            raise VideoEngineError(
                f"الذكاء الاصطناعي للفيديو للمقاطع حتى {aivideo.MAX_SECONDS} ثانية (عشان ما يتعب جوالك). "
                "طفّه لهذا المقطع أو قصّه."
            )
        p = aivideo.plan(info, job.options)
        if p["frames"] > aivideo.MAX_FRAMES:
            raise VideoEngineError("عدد الإطارات كبير على الذكاء الاصطناعي. اختر إطارات أقل أو قصّ المقطع.")
        frames_dir = job.folder / "frames"
        frames_dir.mkdir(exist_ok=True)
        (job.folder / "up").mkdir(exist_ok=True)
        job.message = "تجهيز الإطارات لجهازك…"
        self._execute(job, aivideo.extract_command(job.source, frames_dir, p, extras["transfer"]),
                      info.duration, span=(0.0, 0.8))
        if job.cancel_requested:
            self._finish(job, "cancelled", "تم الإلغاء.")
            return
        p["frames"] = len(list(frames_dir.glob("*.jpg")))
        if not p["frames"]:
            raise VideoEngineError("ما قدرت أستخرج إطارات من هذا الفيديو.")
        before = job.folder / "before.mp4"
        self._execute(job, aivideo.before_command(frames_dir, p, before), p["frames"] / p["fps"], span=(0.8, 1.0))
        job.before = before if before.is_file() else None
        job.ai = p
        job.phase = "frames"
        job.progress = 0.0
        job.touched = time.time()
        job.status = "awaiting_ai"
        job.message = "جهازك يحسّن الإطارات بالذكاء الاصطناعي…"
        job.process = job.kill = None

    def frame_received(self, job: Job, n: int) -> None:
        job.frames_done.add(n)
        job.touched = time.time()

    def start_assemble(self, job: Job) -> bool:
        """Queue the final join once every frame is back. False if frames are missing."""
        if job.status != "awaiting_ai" or job.next_missing() is not None:
            return False
        job.phase = "assemble"
        job.status = "queued"
        job.message = "في الانتظار…"
        self._queue.put(job.id)
        return True

    def start_regular(self, job: Job) -> bool:
        """AI didn't work on this device: process the same upload the normal way."""
        if job.status != "awaiting_ai" or not job.source.is_file():
            return False
        job.options = {**job.options, "ai_video": False}
        job.phase, job.ai, job.frames_done, job.before = "", {}, set(), None
        shutil.rmtree(job.folder / "frames", ignore_errors=True)
        shutil.rmtree(job.folder / "up", ignore_errors=True)
        job.status = "queued"
        job.message = "في الانتظار…"
        self._queue.put(job.id)
        return True

    def _assemble(self, job: Job) -> None:
        p = job.ai
        settings = settings_from_options({**job.options, "priority": "quality"}, _frames_info(job, p))
        colour = _color_filters(settings)
        if settings.stabilize:
            colour = ["deshake=rx=16:ry=16:edge=mirror:blocksize=8:contrast=125:search=less"] + colour
        output = job.folder / "result.mp4"
        self._execute(job, aivideo.assemble_command(job.folder / "up", job.source, p, settings, output, colour),
                      p["frames"] / p["fps"])
        if job.cancel_requested:
            self._finish(job, "cancelled", "تم الإلغاء.")
            return
        if not output.is_file() or output.stat().st_size == 0:
            raise VideoEngineError("ما قدرت أجمّع الفيديو. جرّب مرة ثانية.")
        shutil.rmtree(job.folder / "up", ignore_errors=True)  # the result holds them now
        job.output = output
        job.download_name = f"{_safe_stem(job.filename)}-videocraft-ai.mp4"
        job.info["output_width"], job.info["output_height"] = p["out_width"], p["out_height"]
        job.progress = 1.0
        self._finish(job, "done", "جاهز للتحميل.")

    def _execute(self, job: Job, command, duration: float, span: tuple[float, float] = (0.0, 1.0)) -> None:
        """Run one FFmpeg command, or a pipeline (list of commands joined by pipes).

        The last command reports progress on stdout (-progress pipe:1).
        """
        stages = command if command and isinstance(command[0], list) else [command]
        logs = [job.folder / f"ffmpeg{i}.log" for i in range(len(stages))]
        processes: list[subprocess.Popen] = []
        handles = [open(path, "w", encoding="utf-8", errors="replace") for path in logs]
        try:
            previous = None
            for i, argv in enumerate(stages):
                last = i == len(stages) - 1
                proc = subprocess.Popen(
                    argv,
                    stdin=previous.stdout if previous else subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=handles[i],
                    text=last,
                    encoding="utf-8" if last else None,
                    errors="replace" if last else None,
                )
                if previous:
                    previous.stdout.close()  # the next stage owns the pipe now
                processes.append(proc)
                previous = proc

            def kill_all():
                for proc in processes:
                    if proc.poll() is None:
                        proc.kill()

            job.process = processes[-1]
            job.kill = kill_all
            timer = threading.Timer(max(JOB_TIMEOUT, duration * 40), kill_all)
            timer.start()
            started = time.time()
            try:
                for line in processes[-1].stdout:
                    key, _, value = line.strip().partition("=")
                    if key == "out_time_us" and duration > 0 and value.isdigit():
                        done = max(0.0, min(1.0, int(value) / 1_000_000 / duration))
                        job.progress = min(0.99, span[0] + done * (span[1] - span[0]))
                        if done > 0.02:  # enough to estimate the speed of this pass
                            left = (time.time() - started) * (1 - done) / done
                            later = (1 - span[1]) / max(0.01, span[1] - span[0]) * (time.time() - started) / done
                            job.eta = int(left + later)
                for proc in processes:
                    proc.wait()
            finally:
                timer.cancel()
        finally:
            for handle in handles:
                handle.close()

        if job.cancel_requested:
            return
        # When a later stage dies, the earlier ones fail on a broken pipe, so the
        # last failing stage is the one with the real error.
        failed = next((i for i in reversed(range(len(processes))) if processes[i].returncode != 0), None)
        if failed is not None:
            if any(proc.returncode in (-9, 137) for proc in processes):
                raise VideoEngineError("استغرقت المعالجة وقتًا أطول من المسموح. جرّب ملفًا أقصر أو دقة أقل.")
            tail = logs[failed].read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:]
            raise VideoEngineError("فشلت المعالجة: " + (" ".join(tail)[-300:] or "خطأ غير معروف"))

    # --- cleanup -------------------------------------------------------------

    def _keep_awake(self) -> None:
        """Render's free instances sleep after ~15 min without *incoming* requests, even
        mid-job, which would kill a long encode once the visitor closes the page. While
        work is queued or running, visit our own public URL every 4 minutes."""
        url = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("KEEPALIVE_URL")
        if not url:
            return
        while True:
            time.sleep(240)
            with self._lock:
                busy = any(
                    job.status in ("queued", "running")
                    or (job.status == "awaiting_ai" and time.time() - job.touched < 900)
                    for job in self._jobs.values()
                )
            if busy:
                try:
                    urllib.request.urlopen(url.rstrip("/") + "/healthz", timeout=20).close()
                except OSError:
                    pass  # a missed ping is fine; the next one comes in 4 minutes

    def _janitor(self) -> None:
        while True:
            time.sleep(300)
            now = time.time()
            with self._lock:
                expired = [
                    job for job in self._jobs.values()
                    if (job.finished and now - job.finished > RESULT_TTL)
                    or (job.status == "awaiting_ai" and now - job.touched > RESULT_TTL)
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
