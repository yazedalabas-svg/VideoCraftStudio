"""FFmpeg plumbing for the web version.

The desktop app expects ffmpeg/ffprobe on PATH. On the server we use the
FFmpeg binary bundled inside the `imageio-ffmpeg` wheel (no system install,
no runtime download), expose it on PATH as `ffmpeg`, and read media details
from `ffmpeg -i` since that wheel ships no ffprobe.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from video_engine import IMAGE_DEMUXERS, IMAGE_SUFFIXES, MediaInfo, VideoEngineError

_BIN_DIR = Path(tempfile.gettempdir()) / "videocraft-bin"


def ensure_ffmpeg() -> str | None:
    """Put an `ffmpeg` executable on PATH and return its path (None if unavailable)."""
    existing = shutil.which("ffmpeg")
    if existing:
        return existing
    try:
        import imageio_ffmpeg
    except ImportError:
        return None
    bundled = Path(imageio_ffmpeg.get_ffmpeg_exe())
    _BIN_DIR.mkdir(parents=True, exist_ok=True)
    link = _BIN_DIR / "ffmpeg"
    if not link.exists():
        try:
            link.symlink_to(bundled)
        except OSError:
            shutil.copy2(bundled, link)
            link.chmod(0o755)
    os.environ["PATH"] = f"{_BIN_DIR}{os.pathsep}{os.environ.get('PATH', '')}"
    return shutil.which("ffmpeg")


# --- `ffmpeg -i` parsing -----------------------------------------------------

_INPUT_RE = re.compile(r"^Input #0, (.+?), from ", re.M)
_DURATION_RE = re.compile(r"Duration: (\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_VIDEO_RE = re.compile(r"Stream #0:\d+.*?: Video: (\w+).*$", re.M)
_SIZE_RE = re.compile(r", (\d{2,5})x(\d{2,5})")
_FPS_RE = re.compile(r", ([\d.]+) fps")
_TBR_RE = re.compile(r", ([\d.]+)(k?) tbr")
_AUDIO_RE = re.compile(r"Stream #0:\d+.*?: Audio: (\w+)")
_ROTATION_RE = re.compile(r"rotation of (-?[\d.]+) degrees")


def parse_ffmpeg_info(text: str, path: Path, size_bytes: int) -> MediaInfo:
    """Turn the banner that `ffmpeg -i` prints into the engine's MediaInfo."""
    video = _VIDEO_RE.search(text)
    if not video:
        raise VideoEngineError("لم أجد مسار فيديو أو صورة قابلًا للمعالجة داخل الملف.")
    line = video.group(0)
    size = _SIZE_RE.search(line)
    if not size:
        raise VideoEngineError("تعذّر معرفة أبعاد الملف.")

    fps = 0.0
    if match := _FPS_RE.search(line):
        fps = float(match.group(1))
    elif match := _TBR_RE.search(line):
        fps = float(match.group(1)) * (1000 if match.group(2) else 1)

    duration = 0.0
    if match := _DURATION_RE.search(text):
        hours, minutes, seconds = match.groups()
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)

    rotation = 0
    if match := _ROTATION_RE.search(text[video.end():video.end() + 400]):
        rotation = int(round(float(match.group(1))))

    fmt = _INPUT_RE.search(text)
    format_name = fmt.group(1) if fmt else "media"
    demuxers = {part.strip() for part in format_name.split(",")}
    is_image = (path.suffix.lower() in IMAGE_SUFFIXES or bool(demuxers & IMAGE_DEMUXERS)) and duration <= 0.4

    audio = _AUDIO_RE.search(text)
    return MediaInfo(
        path=str(path.resolve()),
        width=int(size.group(1)),
        height=int(size.group(2)),
        fps=0.0 if is_image else fps,
        duration=0.0 if is_image else duration,
        size_bytes=size_bytes,
        video_codec=video.group(1),
        audio_codec=None if is_image or not audio else audio.group(1),
        format_name=format_name,
        rotation=rotation,
        is_image=is_image,
    )


def probe(path: Path) -> MediaInfo:
    ffmpeg = ensure_ffmpeg()
    if not ffmpeg:
        raise VideoEngineError("محرك FFmpeg غير متوفر على الخادم.")
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VideoEngineError(f"تعذّر قراءة الملف: {exc}") from exc
    # `ffmpeg -i` with no output always exits 1; the banner is what we need.
    return parse_ffmpeg_info(result.stderr, path, path.stat().st_size)
