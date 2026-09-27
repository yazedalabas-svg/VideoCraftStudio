from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import asdict, replace
from pathlib import Path
from typing import Callable

from video_engine import (
    ExportSettings,
    MediaInfo,
    build_ffmpeg_command,
    build_image_ffmpeg_command,
    image_target_box,
    target_box,
)


ROOT = Path(__file__).resolve().parent
VIDEO2X = ROOT / "tools" / "video2x" / "video2x.exe"
WAIFU2X_ROOT = ROOT / "tools" / "waifu2x"
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
PERCENT = re.compile(r"\((\d+(?:\.\d+)?)%\)")
OUT_TIME = re.compile(r"out_time_(?:us|ms)=(\d+)")
CURRENT_CHILD: subprocess.Popen[str] | None = None


def ai_engine_available() -> bool:
    return VIDEO2X.exists() and (VIDEO2X.parent / "models").is_dir()


def waifu2x_binary() -> Path | None:
    """Locate the portable Waifu2x release without hard-coding its version."""
    candidates = sorted(WAIFU2X_ROOT.glob("*/waifu2x-ncnn-vulkan.exe"), reverse=True)
    return candidates[0] if candidates else None


def waifu2x_available() -> bool:
    binary = waifu2x_binary()
    return bool(binary and (binary.parent / "models-cunet").is_dir())


def vulkan_driver_manifest() -> Path | None:
    """Find NVIDIA's Vulkan ICD when the driver installer missed its registry entry."""
    if os.name != "nt":
        return None
    driver_store = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "DriverStore" / "FileRepository"
    candidates = list(driver_store.glob("nv_dispi.inf_amd64_*/nv-vk64.json"))
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _console_python() -> str:
    executable = Path(sys.executable)
    lowered = executable.name.lower()
    if "pythonw" in lowered:
        candidate = executable.with_name(executable.name.lower().replace("pythonw", "python"))
        if candidate.exists():
            return str(candidate)
    return str(executable)


def build_ai_pipeline_command(
    source: str | Path,
    output: str | Path,
    info: MediaInfo,
    settings: ExportSettings,
    *,
    preview: bool = False,
    preview_start: float = 0.0,
) -> list[str]:
    if settings.ai_model == "waifu2x_anime" and not waifu2x_available():
        raise RuntimeError("محرك Waifu2x المخصص للرسوم غير مثبت.")
    if not ai_engine_available():
        raise RuntimeError("محرك الذكاء الاصطناعي المحلي غير مثبت.")
    spec = {
        "source": str(source),
        "output": str(output),
        "info": asdict(info),
        "settings": asdict(settings),
        "preview": bool(preview),
        "preview_start": max(0.0, float(preview_start)),
        "video2x": str(VIDEO2X),
        "waifu2x": str(waifu2x_binary() or ""),
    }
    payload = base64.urlsafe_b64encode(
        json.dumps(spec, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    return [_console_python(), str(Path(__file__).resolve()), "--run", payload]


def _emit(name: str, value: str | float) -> None:
    print(f"{name}={value}", flush=True)


def _log(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


def _stop_child(*_args) -> None:
    global CURRENT_CHILD
    if CURRENT_CHILD and CURRENT_CHILD.poll() is None:
        try:
            CURRENT_CHILD.terminate()
        except OSError:
            pass


def _run_streamed(
    command: list[str],
    *,
    cwd: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[int, str]:
    global CURRENT_CHILD
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    CURRENT_CHILD = subprocess.Popen(
        command,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=creationflags,
    )
    captured: list[str] = []
    assert CURRENT_CHILD.stdout is not None
    for raw in CURRENT_CHILD.stdout:
        for part in raw.replace("\r", "\n").splitlines():
            line = ANSI_ESCAPE.sub("", part).strip()
            if not line:
                continue
            captured.append(line)
            _log(line)
            if progress:
                progress(line)
    return_code = CURRENT_CHILD.wait()
    CURRENT_CHILD = None
    return return_code, "\n".join(captured[-200:])


def detect_display_encoder() -> bool:
    """Report whether NVIDIA NVENC is usable for the throwaway intermediate.

    The AI child re-encodes its output anyway, so a hardware encoder here
    only removes x264 CPU time from the middle of every job. Probing with a
    real one-frame encode catches broken drivers that list the encoder but
    fail at creation time.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    probe = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=256x256:d=0.1",
        "-frames:v",
        "1",
        "-c:v",
        "h264_nvenc",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(
            probe,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _looks_blank(path: Path) -> bool:
    """Detect a dead result: fully black OR a flat colourless frame.

    Video2X can exit zero and still write a healthy-looking file of black
    frames when the Vulkan backend loses the image on the way in. Some
    driver/model combinations keep luminance but drop chroma, which shows up
    as a grey or washed-out picture with no colour left at all. A tiny
    signalstats probe on one early frame tells a real restoration from either
    failure in well under a second.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-vf",
        "select='eq(n\\,0)+gte(t\\,0.5)',signalstats,metadata=print:file=-",
        "-frames:v",
        "1",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=25,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    text = (result.stdout or "") + (result.stderr or "")
    # signalstats reports per-frame metrics. Two dead signatures matter:
    #   1. Black: video-range black sits at Y≈16, so a failed pass yields
    #      YMAX≈16-20 — not one pixel carries real luminance.
    #   2. Flattened: YMAX minus YMIN collapses to almost nothing — a white,
    #      grey, or single-colour wash with no picture left. Every natural
    #      image keeps more than this much luminance spread.
    ymax_values = re.findall(r"YMAX=([0-9.]+)", text)
    ymin_values = re.findall(r"YMIN=([0-9.-]+)", text)
    if not ymax_values or not ymin_values:
        # No metrics usually means the probe itself failed; assume the file
        # is fine rather than discarding a healthy result.
        return False
    if max(float(v) for v in ymax_values) <= 20.0:
        return True
    return max(
        float(ymax) - float(ymin)
        for ymax, ymin in zip(ymax_values, ymin_values)
    ) <= 6.0


# Backwards-compatible alias: existing callers still reference the original
# black-frame probe name.
_looks_black = _looks_blank


def _valid_video(path: Path, *, require_pixels: bool = False) -> bool:
    if not path.exists() or path.stat().st_size < 1024:
        return False
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return True
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode != 0 or "," not in result.stdout:
        return False
    if require_pixels and _looks_blank(path):
        return False
    return True


def _progress_mapper(start: float, end: float) -> Callable[[str], None]:
    span = max(0.0, end - start)

    def update(line: str) -> None:
        match = PERCENT.search(line)
        if match:
            ratio = max(0.0, min(1.0, float(match.group(1)) / 100))
            _emit("ai_progress", f"{start + ratio * span:.5f}")

    return update


def _ffmpeg_progress(start: float, end: float, duration: float) -> Callable[[str], None]:
    span = max(0.0, end - start)
    duration = max(0.1, duration)

    def update(line: str) -> None:
        match = OUT_TIME.search(line)
        if match:
            elapsed = int(match.group(1)) / 1_000_000
            ratio = max(0.0, min(1.0, elapsed / duration))
            _emit("ai_progress", f"{start + ratio * span:.5f}")

    return update


def _target_box_for(info: MediaInfo, settings: ExportSettings) -> tuple[int, int] | None:
    if info.is_image:
        if settings.photo_restore:
            # The restore option is useful even when the user keeps the source
            # preset: process and export a genuinely larger still, rather than
            # restoring it at 2x then shrinking the recovered pixels away.
            restore_scale = max(2, min(4, int(settings.photo_restore_scale)))
            preset = image_target_box(info, settings.resolution)
            if preset:
                restore_scale = max(
                    restore_scale,
                    math.ceil(
                        max(
                            preset[0] / max(1, info.display_width),
                            preset[1] / max(1, info.display_height),
                        )
                    ),
                )
            return (
                info.display_width * restore_scale,
                info.display_height * restore_scale,
            )
        return image_target_box(info, settings.resolution)
    return target_box(info, settings.resolution)


def _scale_for_target(info: MediaInfo, settings: ExportSettings) -> int:
    box = _target_box_for(info, settings)
    if not box:
        required = 1.0
    else:
        required = max(
            box[0] / max(1, info.display_width),
            box[1] / max(1, info.display_height),
        )
    scale = max(2, min(4, int(math.ceil(required - 1e-6))))
    # 2x is the efficiency sweet spot on 4 GB GPUs. The remaining resize uses
    # accurate Lanczos and is dramatically cheaper than neural 3x/4x output.
    if settings.photo_restore:
        # A photo restoration request must run at the scale the user selected;
        # otherwise a fast 2x model pass followed by a 4x conventional resize
        # would make the “×4” option misleading.
        return scale
    return min(2, scale) if settings.ai_fast_mode else scale


_NVENC_VIDEO2X_STATE: bool | None = None


def reset_nvenc_validation() -> None:
    """Forget the cached Video2X/NVENC verdict (used by tests and retries)."""
    global _NVENC_VIDEO2X_STATE
    _NVENC_VIDEO2X_STATE = None


def _video2x_nvenc_usable(video2x: Path) -> bool:
    """Validate NVENC through Video2X's own bundled FFmpeg, exactly once.

    System FFmpeg accepting h264_nvenc does NOT mean Video2X's older bundled
    build can use it: some combinations (GTX 16xx + certain drivers) fail with
    "multiple reference frames are not supported" only inside Video2X. One
    tiny throwaway render settles the question for the whole session; the
    result is cached so long jobs never repeat the cost.
    """
    global _NVENC_VIDEO2X_STATE
    if _NVENC_VIDEO2X_STATE is not None:
        return _NVENC_VIDEO2X_STATE
    if not detect_display_encoder():
        _NVENC_VIDEO2X_STATE = False
        return False
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        _NVENC_VIDEO2X_STATE = False
        return False
    temp_dir = Path(tempfile.mkdtemp(prefix="videocraft_nvenc_"))
    try:
        sample = temp_dir / "probe_src.mkv"
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=s=320x240:r=15:d=1",
                "-c:v",
                "ffv1",
                str(sample),
            ],
            capture_output=True,
            timeout=60,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if not sample.exists():
            _NVENC_VIDEO2X_STATE = False
            return False
        probe_out = temp_dir / "probe_out.mp4"
        result = subprocess.run(
            [
                str(video2x),
                "-i",
                str(sample),
                "-o",
                str(probe_out),
                "-d",
                "0",
                "-c",
                "h264_nvenc",
                "-p",
                "libplacebo",
                "-w",
                "320",
                "-h",
                "240",
                "--no-progress",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        healthy = (
            result.returncode == 0
            and probe_out.exists()
            and probe_out.stat().st_size > 1024
            and _valid_video(probe_out)
        )
        _log(f"NVENC_INTERMEDIATE_PROBE={'OK' if healthy else 'UNAVAILABLE'}")
        _NVENC_VIDEO2X_STATE = healthy
        return healthy
    except (OSError, subprocess.TimeoutExpired):
        _NVENC_VIDEO2X_STATE = False
        return False
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _intermediate_codec_args(settings: ExportSettings, video2x: Path) -> list[str]:
    """Encoder settings for the throwaway Video2X output.

    The intermediate is fully re-encoded by the final pass, so the only thing
    that matters here is speed. When NVENC survives Video2X's own pipeline it
    removes x264 CPU time from the middle of every job; otherwise ultrafast
    x264 stays the safe choice.
    """
    if settings.ai_fast_mode and _video2x_nvenc_usable(video2x):
        return ["-c", "h264_nvenc", "-e", "preset=p4", "-e", "rc=vbr", "-e", "cq=16"]
    return [
        "-c",
        "libx264",
        "-e",
        "preset=ultrafast" if settings.ai_fast_mode else "preset=faster",
        "-e",
        "crf=14" if settings.ai_fast_mode else "crf=12",
    ]


def _video2x_upscale_command(
    video2x: Path,
    source: Path,
    output: Path,
    info: MediaInfo,
    settings: ExportSettings,
    *,
    lossless: bool = False,
) -> tuple[list[str], int, int]:
    box = _target_box_for(info, settings)
    target_width, target_height = box or (info.display_width, info.display_height)
    model = settings.ai_model
    common = [
        str(video2x),
        "-i",
        str(source),
        "-o",
        str(output),
        "-d",
        "0",
    ]
    if lossless:
        # A single still is cheap to carry losslessly, so nothing the model
        # restored is thrown away before the final encode.
        common.extend(
            [
                "-c",
                "libx264",
                "--pix-fmt",
                "yuv444p",
                "-e",
                "preset=veryfast",
                "-e",
                "crf=0",
            ]
        )
    else:
        # The intermediate only feeds the final encode, so speed wins here;
        # quality is decided by the final pass, not by this throwaway file.
        common.extend(_intermediate_codec_args(settings, video2x))
    if model == "anime_shader" or (
        info.display_width * info.display_height > 2_300_000
        and target_width <= info.display_width
        and target_height <= info.display_height
    ):
        command = common + [
            "-p",
            "libplacebo",
            "-w",
            str(target_width),
            "-h",
            str(target_height),
            "--libplacebo-shader",
            "anime4k-v4.1-gan",
        ]
        return command, target_width, target_height

    scale = _scale_for_target(info, settings)
    if model in {"anime_extreme", "anime_supreme"}:
        # Pro weights are available at 2x/3x. The exact requested size is
        # produced by the high-quality final scaler after neural restoration.
        # Supreme always reconstructs at native 3x first, even for a 2x export,
        # to give the network more room to repair eyes and broken ink lines.
        scale = 3 if model == "anime_supreme" else min(3, scale)
        command = common + [
            "-p",
            "realcugan",
            "-s",
            str(scale),
            "-n",
            "3",
            "--realcugan-model",
            "models-pro",
            "--realcugan-threads",
            "1",
            "--realcugan-syncgap",
            "1",
        ]
        return command, info.display_width * scale, info.display_height * scale

    # Photo Restore intentionally uses the realistic Real-ESRGAN weights, not
    # the anime model or an artistic shader. It is the most conservative route
    # for repairing JPEG blocks, pixelation, and softened camera detail.
    model_name = (
        "realesrgan-plus"
        if model in {"general", "photo_restore"}
        else "realesr-animevideov3"
    )
    command = common + [
        "-p",
        "realesrgan",
        "-s",
        str(scale),
        "--realesrgan-model",
        model_name,
    ]
    return command, info.display_width * scale, info.display_height * scale


def _video2x_rife_command(
    video2x: Path,
    source: Path,
    output: Path,
    multiplier: int,
    *,
    uhd: bool,
    fast_encode: bool,
) -> list[str]:
    if fast_encode and _video2x_nvenc_usable(video2x):
        codec = ["-c", "h264_nvenc", "-e", "preset=p4", "-e", "rc=vbr", "-e", "cq=16"]
    else:
        codec = [
            "-c",
            "libx264",
            "-e",
            "preset=ultrafast" if fast_encode else "preset=faster",
            "-e",
            "crf=14" if fast_encode else "crf=12",
        ]
    command = [
        str(video2x),
        "-i",
        str(source),
        "-o",
        str(output),
        "-d",
        "0",
        "-p",
        "rife",
        "-m",
        str(multiplier),
        "--rife-model",
        "rife-v4.26",
        *codec,
    ]
    if uhd:
        command.append("--rife-uhd")
    return command


def _trim_preview(
    source: Path,
    output: Path,
    start: float,
    duration: float,
) -> tuple[int, str]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return 1, "FFmpeg is not available"
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(source),
        "-t",
        f"{duration:.3f}",
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "8",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-progress",
        "pipe:1",
        "-nostats",
        str(output),
    ]
    return _run_streamed(command, progress=_ffmpeg_progress(0.0, 0.08, duration))


def _append_tail_and_duration(
    command: list[str],
    duration: float,
    force_size: tuple[int, int] | None = None,
) -> list[str]:
    command = list(command)
    additions: list[str] = []
    if force_size:
        additions.append(
            f"scale=w={force_size[0]}:h={force_size[1]}:"
            "force_original_aspect_ratio=decrease:force_divisible_by=2:"
            "flags=lanczos+accurate_rnd+full_chroma_int"
        )
        additions.append("setsar=1")
    additions.append("tpad=stop_mode=clone:stop_duration=0.25")
    if "-vf" in command:
        index = command.index("-vf") + 1
        command[index] = f"{command[index]},{','.join(additions)}"
    else:
        insert_at = command.index("-filter_threads") + 2
        command[insert_at:insert_at] = [
            "-vf",
            ",".join(additions),
        ]
    progress_at = command.index("-progress")
    command[progress_at:progress_at] = ["-t", f"{duration:.3f}"]
    return command


def _run_final_encode(
    intermediate: Path,
    output: Path,
    original_info: MediaInfo,
    settings: ExportSettings,
    width: int,
    height: int,
    source_fps: float,
    duration: float,
    *,
    rife_used: bool,
) -> tuple[int, str]:
    intermediate_info = replace(
        original_info,
        path=str(intermediate),
        width=width,
        height=height,
        fps=source_fps,
        duration=duration,
        size_bytes=intermediate.stat().st_size,
        rotation=0,
    )
    post_settings = replace(
        settings,
        denoise=0,
        sharpness=85 if settings.ai_model in {"anime_extreme", "anime_supreme"} else 0,
        interpolate=settings.interpolate and not rife_used,
        ai_motion=False,
    )
    force_size = (
        (original_info.display_width, original_info.display_height)
        if settings.resolution.lower() == "source"
        else None
    )
    command = build_ffmpeg_command(intermediate, output, intermediate_info, post_settings)
    command = _append_tail_and_duration(command, duration, force_size)
    code, log = _run_streamed(
        command,
        progress=_ffmpeg_progress(0.88, 1.0, duration),
    )
    if code != 0 and post_settings.hardware:
        _log("AI_FINAL_ENCODER_RETRY=CPU")
        output.unlink(missing_ok=True)
        post_settings = replace(post_settings, hardware=False)
        command = build_ffmpeg_command(intermediate, output, intermediate_info, post_settings)
        command = _append_tail_and_duration(command, duration, force_size)
        code, log = _run_streamed(
            command,
            progress=_ffmpeg_progress(0.88, 1.0, duration),
        )
    return code, log


def _libplacebo_image_fallback_command(
    video2x: Path,
    source: Path,
    output: Path,
    info: MediaInfo,
    settings: ExportSettings,
) -> tuple[list[str], int, int]:
    """Last GPU resort for stills: the Anime4K GAN shader instead of a GAN.

    The shader reads pixels through a completely different Vulkan path than
    RealCUGAN/RealESRGAN, so it usually survives driver states that break the
    neural models. It resizes straight to the requested box, so no separate
    normalization pass is needed.
    """
    box = _target_box_for(info, settings)
    width, height = box or (info.display_width, info.display_height)
    command = [
        str(video2x),
        "-i",
        str(source),
        "-o",
        str(output),
        "-d",
        "0",
        *(_intermediate_codec_args(settings, video2x)),
        "--pix-fmt",
        "yuv444p",
        "-e",
        "crf=0",
        "-p",
        "libplacebo",
        "-w",
        str(width),
        "-h",
        str(height),
        "--libplacebo-shader",
        "anime4k-v4.1-gan",
    ]
    return command, width, height


def _run_image_pipeline(
    source: Path,
    output: Path,
    info: MediaInfo,
    settings: ExportSettings,
    video2x: Path,
    temp_root: Path,
) -> int:
    _emit("ai_stage", "prepare")
    prepared = temp_root / "image_input_yuv444.mkv"
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        _log("FFMPEG_NOT_FOUND_FOR_IMAGE_PREPARE")
        return 2
    prepare_command = _image_normalization_command(ffmpeg, source, prepared)
    code, log = _run_streamed(prepare_command)
    if code != 0 or not _valid_video(prepared):
        _log(f"AI_IMAGE_PREPARE_FAILED code={code}\n{log}")
        return 2

    _emit("ai_progress", "0.06")
    _emit("ai_stage", "upscale")
    attempts: list[tuple[str, Path]] = [("yuv444", prepared)]
    attempt_logs: list[str] = []
    upscaled = temp_root / "ai_upscaled.mkv"

    code = 0
    log = ""
    ai_width = 0
    ai_height = 0

    def run_neural_attempt(prepared_input: Path) -> bool:
        nonlocal code, log, ai_width, ai_height
        command, ai_width, ai_height = _video2x_upscale_command(
            video2x,
            prepared_input,
            upscaled,
            info,
            settings,
            lossless=True,
        )
        code, log = _run_streamed(
            command,
            cwd=video2x.parent,
            progress=_progress_mapper(0.06, 0.82),
        )
        if not _valid_video(upscaled, require_pixels=True):
            return False
        if _looks_blank(upscaled):
            # The GPU pass can exit zero while handing back a dead frame.
            _log("AI_IMAGE_UPSCALE_BLANK_FRAMES")
            return False
        return True

    if not run_neural_attempt(prepared):
        attempt_logs.append(f"AI_IMAGE_UPSCALE_ATTEMPT=yuv444_FAILED code={code}\n{log}")
        # Attempt 2: the plain-RGB handoff, the configuration that reliably
        # survives broken pixel-format paths on most drivers.
        _log("AI_IMAGE_UPSCALE_RETRY=RGB_HANDOFF")
        upscaled.unlink(missing_ok=True)
        rgb_input = temp_root / "image_input_rgb.mkv"
        retry_code, retry_log = _run_streamed(_image_rgb_handoff_command(ffmpeg, source, rgb_input))
        if retry_code == 0 and _valid_video(rgb_input):
            attempts.append(("rgb_handoff", rgb_input))
            if run_neural_attempt(rgb_input):
                attempt_logs.clear()
        else:
            attempt_logs.append(f"AI_IMAGE_RGB_PREPARE_FAILED code={retry_code}\n{retry_log}")

    if attempt_logs:
        # Attempt 3: the Anime4K shader through libplacebo — a different GPU
        # path that usually survives driver states which break the GANs.
        _log("AI_IMAGE_UPSCALE_RETRY=LIBPLACEBO_SHADER")
        _log("\n".join(attempt_logs))
        upscaled.unlink(missing_ok=True)
        shader_source = attempts[-1][1]
        shader_command, shader_width, shader_height = _libplacebo_image_fallback_command(
            video2x,
            shader_source,
            upscaled,
            info,
            settings,
        )
        shader_code, shader_log = _run_streamed(
            shader_command,
            cwd=video2x.parent,
            progress=_progress_mapper(0.06, 0.82),
        )
        if (
            _valid_video(upscaled, require_pixels=True)
            and not _looks_blank(upscaled)
        ):
            ai_width, ai_height = shader_width, shader_height
        else:
            _log(f"AI_IMAGE_UPSCALE_ALL_ATTEMPTS_FAILED code={shader_code}\n{shader_log}")
            return 3
    else:
        _log("AI_IMAGE_UPSCALE_OK")

    _emit("ai_stage", "encode")
    _emit("ai_progress", "0.86")
    # The neural pass already restored detail, so the final pass only resizes to
    # the requested box and re-applies the deliberate look.
    upscaled_info = replace(
        info,
        path=str(upscaled),
        width=ai_width,
        height=ai_height,
        size_bytes=upscaled.stat().st_size,
        rotation=0,
    )
    post_settings = replace(
        settings,
        denoise=0,
        sharpness=85 if settings.ai_model in {"anime_extreme", "anime_supreme"} else 0,
    )
    final_size = _target_box_for(info, settings) or (
        info.display_width,
        info.display_height,
    )
    if settings.photo_restore:
        # This profile is restoration, not a look: avoid all colour filters,
        # conventional denoising, and extra sharpening after the neural pass.
        post_settings = replace(
            post_settings,
            color_style="none",
            brightness=0,
            contrast=0,
            saturation=0,
            sharpness=0,
        )
    command = build_image_ffmpeg_command(
        upscaled,
        output,
        upscaled_info,
        post_settings,
        force_size=final_size,
    )
    code, log = _run_streamed(command)
    if code != 0 or not output.exists() or output.stat().st_size < 512:
        _log(f"AI_IMAGE_ENCODE_FAILED code={code}\n{log}")
        output.unlink(missing_ok=True)
        return 4
    # The user-facing deliverable is the still itself: never hand over a dead
    # result even when every encoder step exited zero.
    blank_probe = temp_root / "final_probe.mkv"
    probe_code, probe_log = _run_streamed(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(output),
            "-frames:v",
            "1",
            "-c:v",
            "ffv1",
            str(blank_probe),
        ]
    )
    if probe_code != 0 or _looks_blank(blank_probe):
        _log(f"AI_IMAGE_FINAL_BLANK code={probe_code}\n{probe_log}")
        blank_probe.unlink(missing_ok=True)
        output.unlink(missing_ok=True)
        return 3
    blank_probe.unlink(missing_ok=True)
    _emit("ai_progress", "1.0")
    _emit("ai_stage", "done")
    return 0


def _image_rgb_handoff_command(
    ffmpeg: str | Path,
    source: str | Path,
    output: str | Path,
) -> list[str]:
    """Fallback still preparation: plain RGB handoff, no YUV conversion.

    Some driver/model combinations drop the luma plane on the YUV444 route
    and hand the neural model a black frame. RGB keeps the pixels intact all
    the way into the GPU, so the retry uses it even though Video2X then
    re-derives YUV itself.
    """
    return [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-sws_flags",
        "spline+accurate_rnd+full_chroma_int",
        "-loop",
        "1",
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-vf",
        "scale=trunc(iw/2)*2:trunc(ih/2)*2:flags=spline+accurate_rnd+full_chroma_int",
        "-c:v",
        "ffv1",
        "-pix_fmt",
        "bgr0",
        str(output),
    ]


def _image_normalization_command(
    ffmpeg: str | Path,
    source: str | Path,
    output: str | Path,
) -> list[str]:
    """Wrap a still as lossless YUV444 so Video2X preserves RGB channel order.

    YUV planes require even dimensions. A picture with an odd width or height
    would make this conversion fail or emit a black frame, so every still is
    padded down to even numbers with a high-quality scaler before encoding.
    """
    return [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-sws_flags",
        "spline+accurate_rnd+full_chroma_int",
        "-loop",
        "1",
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-vf",
        "scale=trunc(iw/2)*2:trunc(ih/2)*2:flags=spline+accurate_rnd+full_chroma_int",
        "-c:v",
        "ffv1",
        "-pix_fmt",
        "yuv444p",
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        str(output),
    ]


def _waifu2x_scale(info: MediaInfo, settings: ExportSettings) -> int:
    """Return a native Waifu2x scale that meets the requested output size."""
    target = _target_box_for(info, settings) or (
        info.display_width,
        info.display_height,
    )
    required = max(
        target[0] / max(1, info.display_width),
        target[1] / max(1, info.display_height),
    )
    return 2 if required <= 2 else 4


def _run_waifu2x_image_pipeline(
    source: Path,
    output: Path,
    info: MediaInfo,
    settings: ExportSettings,
    waifu2x: Path,
    temp_root: Path,
) -> int:
    """Strong, line-art-safe restoration for pixelated anime and drawings."""
    _emit("ai_stage", "upscale")
    upscaled = temp_root / "waifu2x_upscaled.png"
    model_dir = waifu2x.parent / "models-cunet"
    scale = _waifu2x_scale(info, settings)
    command = [
        str(waifu2x),
        "-i",
        str(source),
        "-o",
        str(upscaled),
        "-n",
        "3",
        "-s",
        str(scale),
        "-m",
        str(model_dir),
        "-x",
        "-f",
        "png",
    ]
    code, log = _run_streamed(
        command,
        cwd=waifu2x.parent,
        progress=_progress_mapper(0.0, 0.86),
    )
    if code != 0 or not upscaled.exists() or upscaled.stat().st_size < 512:
        _log(f"WAIFU2X_IMAGE_UPSCALE_FAILED code={code}\n{log}")
        return 3

    _emit("ai_stage", "encode")
    _emit("ai_progress", "0.90")
    upscaled_info = replace(
        info,
        path=str(upscaled),
        width=info.display_width * scale,
        height=info.display_height * scale,
        size_bytes=upscaled.stat().st_size,
        rotation=0,
    )
    post_settings = replace(
        settings,
        denoise=0,
        sharpness=0,
        color_style="none",
        brightness=0,
        contrast=0,
        saturation=0,
    )
    final_size = _target_box_for(info, settings) or (
        info.display_width,
        info.display_height,
    )
    command = build_image_ffmpeg_command(
        upscaled,
        output,
        upscaled_info,
        post_settings,
        force_size=final_size,
    )
    code, log = _run_streamed(command)
    if code != 0 or not output.exists() or output.stat().st_size < 512:
        _log(f"WAIFU2X_IMAGE_ENCODE_FAILED code={code}\n{log}")
        output.unlink(missing_ok=True)
        return 4
    _emit("ai_progress", "1.0")
    _emit("ai_stage", "done")
    return 0


def run_pipeline(spec: dict) -> int:
    source = Path(spec["source"])
    output = Path(spec["output"])
    video2x = Path(spec["video2x"])
    waifu2x_value = str(spec.get("waifu2x") or "")
    waifu2x = Path(waifu2x_value) if waifu2x_value else None
    info = MediaInfo(**spec["info"])
    settings = ExportSettings(**spec["settings"])
    preview = bool(spec.get("preview"))
    preview_start = float(spec.get("preview_start", 0.0))
    duration = (
        min(6.0, max(0.1, info.duration - preview_start))
        if preview
        else max(0.1, info.duration)
    )

    temp_root = Path(tempfile.mkdtemp(prefix="videocraft_ai_"))
    try:
        output.unlink(missing_ok=True)
        if info.is_image:
            if settings.ai_model == "waifu2x_anime":
                if waifu2x is None or not waifu2x.is_file():
                    _log("WAIFU2X_NOT_FOUND")
                    return 2
                return _run_waifu2x_image_pipeline(
                    source, output, info, settings, waifu2x, temp_root
                )
            return _run_image_pipeline(source, output, info, settings, video2x, temp_root)
        working_source = source
        if preview:
            _emit("ai_stage", "prepare")
            clip = temp_root / "preview_source.mp4"
            code, log = _trim_preview(source, clip, preview_start, duration)
            if code != 0 or not _valid_video(clip):
                _log(f"AI_PREVIEW_PREPARE_FAILED\n{log}")
                return 2
            working_source = clip

        _emit("ai_stage", "upscale")
        ai_output = temp_root / "ai_upscaled.mp4"
        command, ai_width, ai_height = _video2x_upscale_command(
            video2x,
            working_source,
            ai_output,
            info,
            settings,
        )
        upscale_end = 0.66 if settings.ai_motion and settings.fps > info.fps + 0.5 else 0.84
        code, log = _run_streamed(
            command,
            cwd=video2x.parent,
            progress=_progress_mapper(0.08 if preview else 0.0, upscale_end),
        )
        # Video2X 6.4 can exit with a Windows teardown error after muxing a
        # healthy file. File probing is the source of truth for this stage.
        if not _valid_video(ai_output):
            _log(f"AI_UPSCALE_FAILED code={code}\n{log}")
            return 3
        if _looks_blank(ai_output):
            # A zero-exit GPU pass that hands back black frames must not
            # continue: failing here lets the app's reliable non-AI path
            # take over instead of exporting a black video.
            _log("AI_UPSCALE_BLACK_FRAMES")
            return 3

        intermediate = ai_output
        intermediate_fps = info.fps
        rife_used = False
        if settings.ai_motion and info.fps > 0 and settings.fps > info.fps + 0.5:
            multiplier = max(2, min(8, int(math.ceil(settings.fps / info.fps))))
            _emit("ai_stage", "motion")
            rife_output = temp_root / "ai_motion.mp4"
            rife_command = _video2x_rife_command(
                video2x,
                intermediate,
                rife_output,
                multiplier,
                uhd=ai_width * ai_height > 2_300_000,
                fast_encode=settings.ai_fast_mode,
            )
            rife_code, rife_log = _run_streamed(
                rife_command,
                cwd=video2x.parent,
                progress=_progress_mapper(upscale_end, 0.88),
            )
            if _valid_video(rife_output):
                intermediate = rife_output
                intermediate_fps = info.fps * multiplier
                rife_used = True
            else:
                _log(f"AI_RIFE_SKIPPED code={rife_code}\n{rife_log}")

        _emit("ai_stage", "encode")
        code, log = _run_final_encode(
            intermediate,
            output,
            info,
            settings,
            ai_width,
            ai_height,
            intermediate_fps,
            duration,
            rife_used=rife_used,
        )
        if code != 0 or not _valid_video(output, require_pixels=True):
            _log(f"AI_FINAL_ENCODE_FAILED code={code}\n{log}")
            output.unlink(missing_ok=True)
            return 4
        _emit("ai_progress", "1.0")
        _emit("ai_stage", "done")
        return 0
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run")
    args = parser.parse_args()
    if not args.run:
        parser.error("--run is required")
    spec = json.loads(base64.urlsafe_b64decode(args.run.encode("ascii")).decode("utf-8"))
    return run_pipeline(spec)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _stop_child)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop_child)
    raise SystemExit(main())
