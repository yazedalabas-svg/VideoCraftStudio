from __future__ import annotations

import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


class VideoEngineError(RuntimeError):
    """A readable error raised by the local video engine."""


# Still pictures travel through the same pipeline as video, so the source
# reader has to recognise them before any duration or frame-rate logic runs.
IMAGE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".jfif",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".avif",
    ".heic",
    ".heif",
}
IMAGE_DEMUXERS = {
    "image2",
    "png_pipe",
    "jpeg_pipe",
    "mjpeg",
    "webp_pipe",
    "bmp_pipe",
    "tiff_pipe",
    "jpegls_pipe",
}
IMAGE_OUTPUT_SUFFIXES = {"png": ".png", "jpg": ".jpg", "webp": ".webp"}


@dataclass(frozen=True)
class MediaInfo:
    path: str
    width: int
    height: int
    fps: float
    duration: float
    size_bytes: int
    video_codec: str
    audio_codec: str | None
    format_name: str
    rotation: int = 0
    is_image: bool = False

    @property
    def display_width(self) -> int:
        return self.height if abs(self.rotation) % 180 == 90 else self.width

    @property
    def display_height(self) -> int:
        return self.width if abs(self.rotation) % 180 == 90 else self.height

    @property
    def is_portrait(self) -> bool:
        return self.display_height > self.display_width


@dataclass
class ExportSettings:
    resolution: str = "1080p"
    fps: int = 60
    interpolate: bool = True
    denoise: int = 100
    sharpness: int = 100
    brightness: int = 0
    contrast: int = 20
    saturation: int = 16
    color_style: str = "natural"
    deinterlace: bool = False
    stabilize: bool = False
    audio_normalize: bool = True
    audio_clean: bool = False
    codec: str = "h264"
    quality: int = 14
    hardware: bool = True
    container: str = "mp4"
    safe_mode: bool = True
    ai_enabled: bool = True
    ai_model: str = "anime_extreme"
    ai_motion: bool = False
    ai_fast_mode: bool = True
    # A still-only restoration profile. It deliberately leaves colour and
    # exposure untouched while the neural model repairs compression blocks and
    # reconstructs missing edge detail.
    photo_restore: bool = False
    photo_restore_scale: int = 2


def _fraction(value: str | None) -> float:
    if not value or value in {"0/0", "N/A"}:
        return 0.0
    if "/" in value:
        left, right = value.split("/", 1)
        try:
            denominator = float(right)
            return float(left) / denominator if denominator else 0.0
        except ValueError:
            return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def find_binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise VideoEngineError(
            f"لم يتم العثور على {name}. ثبّت FFmpeg وأضفه إلى PATH ثم أعد تشغيل البرنامج."
        )
    return path


def probe_media(path: str | Path) -> MediaInfo:
    source = Path(path)
    if not source.is_file():
        raise VideoEngineError("ملف الفيديو غير موجود أو تعذّر الوصول إليه.")

    command = [
        find_binary("ffprobe"),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(source),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VideoEngineError(f"تعذّر قراءة معلومات الفيديو: {exc}") from exc

    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()
        raise VideoEngineError(detail[-1] if detail else "هذا الملف ليس فيديو صالحًا.")

    try:
        payload = json.loads(result.stdout)
        streams = payload.get("streams", [])
        video = next(stream for stream in streams if stream.get("codec_type") == "video")
        audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
        fmt = payload.get("format", {})
    except (json.JSONDecodeError, StopIteration, TypeError) as exc:
        raise VideoEngineError("لم أجد مسار فيديو قابلًا للمعالجة داخل الملف.") from exc

    duration = float(video.get("duration") or fmt.get("duration") or 0.0)
    fps = _fraction(video.get("avg_frame_rate")) or _fraction(video.get("r_frame_rate"))
    rotation = 0
    for side_data in video.get("side_data_list", []):
        if "rotation" in side_data:
            rotation = int(side_data["rotation"])
            break
    if not rotation:
        try:
            rotation = int(video.get("tags", {}).get("rotate", 0))
        except (TypeError, ValueError):
            rotation = 0

    # A single picture is read through an image demuxer and reports a token
    # duration of one frame. Anything longer is an animation and stays a video.
    demuxers = {part.strip() for part in str(fmt.get("format_name") or "").split(",")}
    is_image = (
        source.suffix.lower() in IMAGE_SUFFIXES or bool(demuxers & IMAGE_DEMUXERS)
    ) and duration <= 0.4
    if is_image:
        duration = 0.0
        fps = 0.0
        audio = None

    return MediaInfo(
        path=str(source.resolve()),
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        fps=fps,
        duration=duration,
        size_bytes=int(fmt.get("size") or source.stat().st_size),
        video_codec=str(video.get("codec_name") or "غير معروف"),
        audio_codec=str(audio.get("codec_name")) if audio else None,
        format_name=str(fmt.get("format_long_name") or fmt.get("format_name") or "فيديو"),
        rotation=rotation,
        is_image=is_image,
    )


def format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def format_size(size_bytes: int) -> str:
    value = float(max(0, size_bytes))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit in {"B", "KB"} else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def target_box(info: MediaInfo, preset: str) -> tuple[int, int] | None:
    sizes = {
        "1080p": (1920, 1080),
        "2k": (2560, 1440),
        "4k": (3840, 2160),
    }
    box = sizes.get(preset.lower())
    if not box:
        return None
    width, height = box
    if info.is_portrait:
        return height, width
    if info.display_width == info.display_height:
        return height, height
    return width, height


def image_target_box(info: MediaInfo, preset: str) -> tuple[int, int] | None:
    """Resolution presets act as a floor for stills, never as a ceiling.

    Shrinking a photo to fit a 4K box would throw away detail the source
    already has, so a preset that is smaller than the picture is ignored.
    """
    box = target_box(info, preset)
    if not box:
        return None
    if box[0] <= info.display_width and box[1] <= info.display_height:
        return None
    return box


def _denoise_filters(denoise: int) -> list[str]:
    denoise = max(0, min(100, denoise))
    if not denoise:
        return []
    luma = 0.8 + denoise * 0.052
    chroma = luma * 0.75
    temporal = 1.2 + denoise * 0.058
    chroma_temporal = temporal * 0.75
    filters = [f"hqdn3d={luma:.2f}:{chroma:.2f}:{temporal:.2f}:{chroma_temporal:.2f}"]
    if denoise >= 70:
        # Clean compression banding after strong noise reduction. The safe
        # retry lowers denoise below this threshold if a build lacks gradfun.
        filters.append("gradfun=strength=0.80:radius=12")
    return filters


def _color_filters(settings: ExportSettings) -> list[str]:
    filters: list[str] = []
    style_brightness = 0.0
    style_contrast = 0.0
    style_saturation = 0.0
    gamma = 1.0
    color_filter: str | None = None
    if settings.color_style == "natural":
        style_contrast, style_saturation = 0.025, 0.035
    elif settings.color_style == "warm":
        style_contrast, style_saturation = 0.045, 0.055
        color_filter = "colorbalance=rs=.025:gs=.006:bs=-.022"
    elif settings.color_style == "bright":
        style_brightness, style_contrast, style_saturation, gamma = 0.018, 0.02, 0.02, 1.025
    elif settings.color_style == "cinematic":
        style_contrast, style_saturation, gamma = 0.07, -0.055, 0.98
        color_filter = "colorbalance=rs=.018:gs=-.004:bs=-.014"

    brightness = max(-0.35, min(0.35, settings.brightness / 250 + style_brightness))
    contrast = max(0.5, min(1.7, 1 + settings.contrast / 160 + style_contrast))
    saturation = max(0.0, min(2.2, 1 + settings.saturation / 110 + style_saturation))
    if (
        abs(brightness) > 0.0001
        or abs(contrast - 1) > 0.0001
        or abs(saturation - 1) > 0.0001
        or abs(gamma - 1) > 0.0001
    ):
        filters.append(
            f"eq=brightness={brightness:.3f}:contrast={contrast:.3f}:"
            f"saturation={saturation:.3f}:gamma={gamma:.3f}"
        )
    if color_filter:
        filters.append(color_filter)
    return filters


def _sharpen_filter(sharpness: int, *, light_kernel: bool) -> str | None:
    """A restrained final-pass sharpener restores edge presence after scaling."""
    sharpness = max(0, min(100, sharpness))
    if not sharpness:
        return None
    amount = 0.12 + sharpness * 0.011
    kernel = 3 if light_kernel else 5
    return f"unsharp={kernel}:{kernel}:{amount:.2f}:{kernel}:{kernel}:0.0"


def build_video_filters(info: MediaInfo, settings: ExportSettings) -> list[str]:
    filters: list[str] = []

    if settings.deinterlace:
        filters.append("yadif=mode=send_frame:parity=auto:deint=interlaced")

    if settings.stabilize:
        filters.append("deshake=rx=16:ry=16:edge=mirror:blocksize=8:contrast=125:search=less")

    filters.extend(_denoise_filters(settings.denoise))
    filters.extend(_color_filters(settings))

    target_fps = max(15, min(120, int(settings.fps)))
    box = target_box(info, settings.resolution)
    source_pixels = max(1, info.display_width * info.display_height)
    output_pixels = (box[0] * box[1]) if box else source_pixels
    source_rate = source_pixels * target_fps
    output_rate = output_pixels * target_fps
    # AI jobs already spend the budget on the neural pass, so their final
    # encode interpolates the cheap way; full motion estimation here used to
    # make exports crawl long after the GPU work had finished.
    extreme_workload = settings.safe_mode and (
        target_fps > 60
        or source_rate > 185_000_000
        or output_rate > 310_000_000
        or settings.ai_enabled
    )
    # Full obmc/vsbmc motion search only pays off on small frames; on 1080p+
    # it multiplies render time for a difference nobody sees at playback.
    light_workload = source_rate <= 60_000_000 and output_rate <= 120_000_000

    # Motion analysis is done before enlargement. For heavy jobs, blend
    # interpolation avoids the long stalls of full motion estimation while
    # keeping the requested constant frame rate.
    if settings.interpolate and info.fps and target_fps > info.fps + 0.5:
        if extreme_workload:
            filters.append(f"minterpolate=fps={target_fps}:mi_mode=blend:scd=fdiff")
        elif light_workload:
            filters.append(
                f"minterpolate=fps={target_fps}:mi_mode=mci:mc_mode=aobmc:"
                "me_mode=bidir:vsbmc=1:scd=fdiff"
            )
        else:
            filters.append(
                f"minterpolate=fps={target_fps}:mi_mode=mci:mc_mode=obmc:"
                "me_mode=bidir:scd=fdiff"
            )
    elif not info.fps or not math.isclose(target_fps, info.fps, abs_tol=0.25):
        filters.append(f"fps=fps={target_fps}")

    if box:
        width, height = box
        scaling = (
            "bicubic+accurate_rnd"
            if extreme_workload
            else "lanczos+accurate_rnd+full_chroma_int"
        )
        filters.append(
            f"scale=w={width}:h={height}:force_original_aspect_ratio=decrease:"
            f"force_divisible_by=2:flags={scaling}"
        )
        filters.append("setsar=1")
    elif info.display_width % 2 or info.display_height % 2:
        filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")

    sharpen = _sharpen_filter(settings.sharpness, light_kernel=extreme_workload)
    if sharpen:
        filters.append(sharpen)

    return filters


def build_image_filters(
    info: MediaInfo,
    settings: ExportSettings,
    *,
    force_size: tuple[int, int] | None = None,
) -> list[str]:
    """Still-picture filter chain: no motion, no frame rate, no interlacing."""
    filters: list[str] = []
    filters.extend(_denoise_filters(settings.denoise))
    filters.extend(_color_filters(settings))

    box = force_size or image_target_box(info, settings.resolution)
    if box:
        filters.append(
            f"scale=w={box[0]}:h={box[1]}:force_original_aspect_ratio=decrease:"
            "flags=lanczos+accurate_rnd+full_chroma_int"
        )
        filters.append("setsar=1")

    sharpen = _sharpen_filter(settings.sharpness, light_kernel=False)
    if sharpen:
        filters.append(sharpen)

    return filters


def build_audio_filters(settings: ExportSettings) -> list[str]:
    filters: list[str] = []
    if settings.audio_clean:
        filters.extend(["highpass=f=65", "lowpass=f=16500"])
    if settings.audio_normalize:
        filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    return filters


def _video_encoder_args(settings: ExportSettings, preview: bool) -> list[str]:
    quality = max(14, min(30, int(settings.quality)))
    codec = settings.codec.lower()
    fast_ai_encode = settings.ai_enabled and settings.ai_fast_mode
    if settings.hardware:
        encoder = "hevc_nvenc" if codec == "h265" else "h264_nvenc"
        fast_job = settings.safe_mode and (settings.fps > 60 or settings.resolution.lower() == "4k")
        maximum_quality = quality <= 14 and not preview and not fast_ai_encode
        arguments = [
            "-c:v",
            encoder,
            "-preset",
            "p7" if maximum_quality else "p4" if preview or fast_job or fast_ai_encode else "p6",
            "-tune",
            "hq",
            "-rc",
            "vbr",
            "-cq",
            str(quality),
            "-b:v",
            "0",
        ]
        if maximum_quality:
            arguments.extend(
                [
                    "-multipass",
                    "fullres",
                    "-spatial_aq",
                    "1",
                    "-aq-strength",
                    "8",
                    "-rc-lookahead",
                    "32",
                ]
            )
        return arguments

    encoder = "libx265" if codec == "h265" else "libx264"
    cpu_preset = (
        "veryfast"
        if preview or fast_ai_encode
        else "medium"
        if quality <= 14
        else "fast"
        if settings.safe_mode and (settings.fps > 60 or settings.resolution.lower() == "4k")
        else "medium"
    )
    return [
        "-c:v",
        encoder,
        "-preset",
        cpu_preset,
        "-crf",
        str(quality),
    ]


def build_ffmpeg_command(
    source: str | Path,
    output: str | Path,
    info: MediaInfo,
    settings: ExportSettings,
    *,
    preview: bool = False,
    preview_start: float = 0.0,
    preview_duration: float = 6.0,
) -> list[str]:
    command: list[str] = [find_binary("ffmpeg"), "-hide_banner", "-y"]
    if preview:
        command.extend(["-ss", f"{max(0.0, preview_start):.3f}"])
    command.extend(["-i", str(source)])
    if preview:
        command.extend(["-t", f"{max(1.0, preview_duration):.3f}"])

    command.extend(["-map", "0:v:0", "-map", "0:a?", "-filter_threads", "0"])
    video_filters = build_video_filters(info, settings)
    if video_filters:
        command.extend(["-vf", ",".join(video_filters)])
    audio_filters = build_audio_filters(settings)
    if audio_filters:
        command.extend(["-af", ",".join(audio_filters)])

    command.extend(_video_encoder_args(settings, preview))
    command.extend(
        [
            "-pix_fmt",
            "yuv420p",
            "-fps_mode",
            "cfr",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-max_muxing_queue_size",
            "4096",
        ]
    )
    if settings.codec.lower() == "h265" and Path(output).suffix.lower() in {".mp4", ".mov", ".m4v"}:
        command.extend(["-tag:v", "hvc1"])
    command.extend(["-map_metadata", "0", "-map_chapters", "0"])
    if Path(output).suffix.lower() in {".mp4", ".mov", ".m4v"}:
        command.extend(["-movflags", "+faststart"])
    command.extend(["-progress", "pipe:1", "-nostats", str(output)])
    return command


def _image_encoder_args(output: Path, settings: ExportSettings) -> list[str]:
    # The export quality scale (14 = best … 30 = smallest) is remapped onto the
    # native scale of each still encoder so one control keeps meaning.
    quality = max(14, min(30, int(settings.quality)))
    position = (quality - 14) / 16
    suffix = output.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return [
            "-c:v",
            "mjpeg",
            "-q:v",
            str(2 + round(position * 7)),
            "-pix_fmt",
            "yuvj444p",
        ]
    if suffix == ".webp":
        return [
            "-c:v",
            "libwebp",
            "-quality",
            str(100 - round(position * 25)),
            "-preset",
            "picture",
            "-pix_fmt",
            "yuv420p",
        ]
    return ["-c:v", "png", "-pred", "mixed", "-pix_fmt", "rgb24"]


def build_image_ffmpeg_command(
    source: str | Path,
    output: str | Path,
    info: MediaInfo,
    settings: ExportSettings,
    *,
    force_size: tuple[int, int] | None = None,
) -> list[str]:
    command: list[str] = [find_binary("ffmpeg"), "-hide_banner", "-y", "-i", str(source)]
    command.extend(["-map", "0:v:0", "-frames:v", "1", "-an", "-filter_threads", "0"])
    filters = build_image_filters(info, settings, force_size=force_size)
    if filters:
        command.extend(["-vf", ",".join(filters)])
    command.extend(_image_encoder_args(Path(output), settings))
    command.extend(["-update", "1", "-progress", "pipe:1", "-nostats", str(output)])
    return command


def command_as_text(command: Sequence[str]) -> str:
    """Useful for diagnostics without being used to execute the command."""
    return subprocess.list2cmdline(list(command))
