"""AI video: the visitor's device upscales the frames, the server does the rest.

The server has no GPU, but the visitor's browser can run Real-ESRGAN / Real-CUGAN
(web/static/ai-worker.js). So for an AI video job:

  1. extract   – the server pulls frames at a phone-friendly rate and size
                 (JPEG, e.g. 640×360 at 15 fps) and a small "before" clip;
  2. frames    – the browser downloads each frame, upscales it ×2 on its GPU and
                 uploads it back (resumable: the server tracks which are in);
  3. assemble  – the server joins the upscaled frames with the original audio
                 and the colour settings into the final MP4.

Frame rate and size are capped on purpose so a phone isn't pushed too hard.
"""

from __future__ import annotations

import os
from pathlib import Path

from video_engine import ExportSettings, MediaInfo, build_audio_filters, find_binary

MAX_SECONDS = int(os.environ.get("AI_VIDEO_MAX_SECONDS", "60"))
MAX_FRAMES = int(os.environ.get("AI_VIDEO_MAX_FRAMES", "1500"))
FPS_CHOICES = (12, 15, 24)
DEFAULT_FPS = 15
# Short side of the frames the phone upscales ×2: 360 → 720p out, 540 → 1080p out.
SIZE_CHOICES = {"720p": 360, "1080p": 540}
DEFAULT_SIZE = "720p"
SCALE = 2


def plan(info: MediaInfo, options: dict) -> dict:
    """Frame rate, frame size and count for this clip (never above the source)."""
    fps = int(options.get("ai_fps") or DEFAULT_FPS)
    fps = fps if fps in FPS_CHOICES else DEFAULT_FPS
    if info.fps:
        fps = min(fps, max(1, round(info.fps)))
    short = SIZE_CHOICES.get(options.get("ai_size"), SIZE_CHOICES[DEFAULT_SIZE])
    w, h = info.display_width, info.display_height
    k = min(1.0, short / min(w, h))
    fw, fh = max(2, int(w * k) // 2 * 2), max(2, int(h * k) // 2 * 2)
    return {"fps": fps, "width": fw, "height": fh, "frames": int(info.duration * fps) + 1,
            "out_width": fw * SCALE, "out_height": fh * SCALE}


def extract_command(source, frames_dir: Path, p: dict, transfer: str | None) -> list[str]:
    from .jobs import tonemap_filter  # shared HDR handling

    filters = [f"fps={p['fps']}", f"scale={p['width']}:{p['height']}:flags=bicubic"]
    if transfer:
        filters.append(tonemap_filter(transfer, light=True))
    filters.append("format=yuvj420p")
    return [
        find_binary("ffmpeg"), "-hide_banner", "-nostdin", "-y", "-threads", "1", "-i", str(source),
        "-map", "0:v:0", "-vf", ",".join(filters), "-q:v", "2",
        "-progress", "pipe:1", "-nostats", str(frames_dir / "%06d.jpg"),
    ]


def before_command(frames_dir: Path, p: dict, output: Path) -> list[str]:
    """A small clip of the original frames: same frames and timing as the result,
    so the before/after comparison stays in sync."""
    return [
        find_binary("ffmpeg"), "-hide_banner", "-nostdin", "-y",
        "-framerate", str(p["fps"]), "-i", str(frames_dir / "%06d.jpg"),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-x264-params", "threads=1", "-movflags", "+faststart", str(output),
    ]


def assemble_command(up_dir: Path, source, p: dict, settings: ExportSettings, output: Path,
                     colour_filters: list[str]) -> list[str]:
    from .jobs import X264_LEAN

    quality = max(14, min(30, int(settings.quality)))
    command = [
        find_binary("ffmpeg"), "-hide_banner", "-nostdin", "-y",
        "-framerate", str(p["fps"]), "-i", str(up_dir / "%06d.jpg"),
        "-i", str(source), "-map", "0:v:0", "-map", "1:a?",
        "-vf", ",".join(colour_filters + ["format=yuv420p"]),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(quality), "-x264-params", X264_LEAN,
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
    ]
    audio = build_audio_filters(settings)
    if audio:
        command += ["-af", ",".join(audio)]
    command += [
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest",
        "-map_metadata", "1", "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats", str(output),
    ]
    return command


def frame_path(folder: Path, kind: str, n: int) -> Path:
    """kind: "frames" (originals) or "up" (upscaled). n starts at 1, like FFmpeg's %06d."""
    return folder / kind / f"{n:06d}.jpg"
