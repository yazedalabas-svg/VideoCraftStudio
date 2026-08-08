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
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
PERCENT = re.compile(r"\((\d+(?:\.\d+)?)%\)")
OUT_TIME = re.compile(r"out_time_(?:us|ms)=(\d+)")
CURRENT_CHILD: subprocess.Popen[str] | None = None


def ai_engine_available() -> bool:
    return VIDEO2X.exists() and (VIDEO2X.parent / "models").is_dir()


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


def _valid_video(path: Path) -> bool:
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
    return result.returncode == 0 and "," in result.stdout


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
    return min(2, scale) if settings.ai_fast_mode else scale


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
        "-c",
        "libx264",
    ]
    if lossless:
        # A single still is cheap to carry losslessly, so nothing the model
        # restored is thrown away before the final encode.
        common.extend(["--pix-fmt", "yuv444p", "-e", "preset=veryfast", "-e", "crf=0"])
    else:
        common.extend(
            [
                "-e",
                "preset=ultrafast" if settings.ai_fast_mode else "preset=veryfast",
                "-e",
                "crf=10" if settings.ai_fast_mode else "crf=8",
            ]
        )
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
    if model == "anime_extreme":
        # Pro weights are available at 2x/3x. The exact requested size is
        # produced by the high-quality final scaler after neural restoration.
        scale = min(3, scale)
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

    model_name = "realesrgan-plus" if model == "general" else "realesr-animevideov3"
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
        "-c",
        "libx264",
        "-e",
        "preset=ultrafast" if fast_encode else "preset=veryfast",
        "-e",
        "crf=10" if fast_encode else "crf=8",
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
        sharpness=85 if settings.ai_model == "anime_extreme" else 0,
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


def _run_image_pipeline(
    source: Path,
    output: Path,
    info: MediaInfo,
    settings: ExportSettings,
    video2x: Path,
    temp_root: Path,
) -> int:
    _emit("ai_stage", "upscale")
    upscaled = temp_root / "ai_upscaled.mkv"
    command, ai_width, ai_height = _video2x_upscale_command(
        video2x,
        source,
        upscaled,
        info,
        settings,
        lossless=True,
    )
    code, log = _run_streamed(
        command,
        cwd=video2x.parent,
        progress=_progress_mapper(0.0, 0.82),
    )
    if not _valid_video(upscaled):
        _log(f"AI_IMAGE_UPSCALE_FAILED code={code}\n{log}")
        return 3

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
        sharpness=85 if settings.ai_model == "anime_extreme" else 0,
    )
    final_size = image_target_box(info, settings.resolution) or (
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
        _log(f"AI_IMAGE_ENCODE_FAILED code={code}\n{log}")
        output.unlink(missing_ok=True)
        return 4
    _emit("ai_progress", "1.0")
    _emit("ai_stage", "done")
    return 0


def run_pipeline(spec: dict) -> int:
    source = Path(spec["source"])
    output = Path(spec["output"])
    video2x = Path(spec["video2x"])
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
        if code != 0 or not _valid_video(output):
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
