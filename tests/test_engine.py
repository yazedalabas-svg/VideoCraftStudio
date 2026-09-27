import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from video_engine import (
    ExportSettings,
    MediaInfo,
    build_image_ffmpeg_command,
    build_image_filters,
    build_video_filters,
    image_target_box,
    target_box,
)
import ai_engine
from ai_engine import (
    _image_normalization_command,
    _intermediate_codec_args,
    _looks_blank,
    _target_box_for,
    _video2x_upscale_command,
    _waifu2x_scale,
)


LANDSCAPE = MediaInfo(
    path=str(Path("sample.mp4")),
    width=1920,
    height=1080,
    fps=30.0,
    duration=10.0,
    size_bytes=1_000_000,
    video_codec="h264",
    audio_codec="aac",
    format_name="MP4",
)

PORTRAIT = MediaInfo(
    path=str(Path("portrait.mp4")),
    width=1080,
    height=1920,
    fps=30.0,
    duration=10.0,
    size_bytes=1_000_000,
    video_codec="h264",
    audio_codec="aac",
    format_name="MP4",
)


SMALL_PHOTO = MediaInfo(
    path=str(Path("small.jpg")),
    width=1200,
    height=800,
    fps=0.0,
    duration=0.0,
    size_bytes=400_000,
    video_codec="mjpeg",
    audio_codec=None,
    format_name="JPEG",
    is_image=True,
)

LARGE_PHOTO = MediaInfo(
    path=str(Path("large.jpg")),
    width=6000,
    height=4000,
    fps=0.0,
    duration=0.0,
    size_bytes=9_000_000,
    video_codec="mjpeg",
    audio_codec=None,
    format_name="JPEG",
    is_image=True,
)


class VideoEngineTests(unittest.TestCase):
    def test_resolution_boxes_follow_orientation(self) -> None:
        self.assertEqual(target_box(LANDSCAPE, "4k"), (3840, 2160))
        self.assertEqual(target_box(PORTRAIT, "4k"), (2160, 3840))

    def test_motion_interpolation_runs_before_scaling(self) -> None:
        filters = build_video_filters(LANDSCAPE, ExportSettings(resolution="4k", fps=60))
        interpolation = next(
            index for index, value in enumerate(filters) if value.startswith("minterpolate")
        )
        scaling = next(index for index, value in enumerate(filters) if value.startswith("scale="))
        self.assertLess(interpolation, scaling)

    def test_final_sharpening_runs_after_scaling(self) -> None:
        filters = build_video_filters(
            LANDSCAPE, ExportSettings(resolution="4k", sharpness=25)
        )
        scaling = next(index for index, value in enumerate(filters) if value.startswith("scale="))
        sharpening = next(index for index, value in enumerate(filters) if value.startswith("unsharp="))
        self.assertLess(scaling, sharpening)

    def test_strict_4k120_uses_adaptive_reliable_filters(self) -> None:
        filters = build_video_filters(
            LANDSCAPE,
            ExportSettings(resolution="4k", fps=120, interpolate=True, safe_mode=True),
        )
        self.assertTrue(any("mi_mode=blend" in value for value in filters))
        self.assertTrue(any("flags=bicubic" in value for value in filters))
        self.assertTrue(any(value.startswith("unsharp=3:3") for value in filters))

    def test_manual_mode_keeps_full_motion_estimation(self) -> None:
        filters = build_video_filters(
            LANDSCAPE,
            ExportSettings(resolution="4k", fps=120, interpolate=True, safe_mode=False),
        )
        self.assertTrue(any("mi_mode=mci" in value for value in filters))
        self.assertTrue(any("flags=lanczos" in value for value in filters))


class ImageEngineTests(unittest.TestCase):
    def test_presets_enlarge_small_pictures_only(self) -> None:
        self.assertEqual(image_target_box(SMALL_PHOTO, "4k"), (3840, 2160))
        self.assertIsNone(image_target_box(LARGE_PHOTO, "4k"))
        self.assertIsNone(image_target_box(SMALL_PHOTO, "source"))

    def test_still_chain_drops_motion_filters(self) -> None:
        filters = build_image_filters(
            SMALL_PHOTO,
            ExportSettings(resolution="4k", fps=60, interpolate=True, stabilize=True, deinterlace=True),
        )
        joined = " ".join(filters)
        for motion_filter in ("minterpolate", "fps=", "deshake", "yadif"):
            self.assertNotIn(motion_filter, joined)
        scaling = next(index for index, value in enumerate(filters) if value.startswith("scale="))
        sharpening = next(index for index, value in enumerate(filters) if value.startswith("unsharp="))
        self.assertLess(scaling, sharpening)

    def test_output_encoder_follows_the_chosen_suffix(self) -> None:
        settings = ExportSettings(resolution="source")
        for suffix, encoder in ((".png", "png"), (".jpg", "mjpeg"), (".webp", "libwebp")):
            command = build_image_ffmpeg_command(
                SMALL_PHOTO.path, Path(f"out{suffix}"), SMALL_PHOTO, settings
            )
            self.assertEqual(command[command.index("-c:v") + 1], encoder)
            self.assertIn("-update", command)
            self.assertIn("-an", command)

    def test_forced_size_overrides_the_preset(self) -> None:
        filters = build_image_filters(
            LARGE_PHOTO, ExportSettings(resolution="4k"), force_size=(6000, 4000)
        )
        self.assertTrue(any("scale=w=6000:h=4000" in value for value in filters))

    def test_photo_restore_settings_are_serializable(self) -> None:
        settings = ExportSettings(photo_restore=True, photo_restore_scale=4)
        self.assertTrue(settings.photo_restore)
        self.assertEqual(settings.photo_restore_scale, 4)

    def test_photo_restore_runs_real_esrgan_at_the_selected_scale(self) -> None:
        settings = ExportSettings(
            resolution="source",
            photo_restore=True,
            photo_restore_scale=4,
            ai_model="photo_restore",
            ai_fast_mode=True,
        )
        self.assertEqual(_target_box_for(SMALL_PHOTO, settings), (4800, 3200))
        command, width, height = _video2x_upscale_command(
            Path("video2x.exe"), Path(SMALL_PHOTO.path), Path("out.mkv"), SMALL_PHOTO, settings
        )
        self.assertEqual((width, height), (4800, 3200))
        self.assertEqual(command[command.index("-s") + 1], "4")
        self.assertEqual(command[command.index("--realesrgan-model") + 1], "realesrgan-plus")

    def test_waifu2x_restoration_uses_native_anime_scale(self) -> None:
        settings = ExportSettings(
            resolution="source", photo_restore=True, photo_restore_scale=4
        )
        self.assertEqual(_waifu2x_scale(SMALL_PHOTO, settings), 4)

    def test_anime_supreme_always_reconstructs_at_native_three_x(self) -> None:
        settings = ExportSettings(
            resolution="source",
            photo_restore=True,
            photo_restore_scale=2,
            ai_model="anime_supreme",
        )
        command, width, height = _video2x_upscale_command(
            Path("video2x.exe"), Path(SMALL_PHOTO.path), Path("out.mkv"), SMALL_PHOTO, settings
        )
        self.assertEqual((width, height), (3600, 2400))
        self.assertEqual(command[command.index("-s") + 1], "3")
        self.assertEqual(
            command[command.index("--realcugan-model") + 1],
            "models-pro",
        )

    def test_stills_are_normalized_before_video2x(self) -> None:
        command = _image_normalization_command("ffmpeg", "input.png", "prepared.mkv")
        self.assertIn("ffv1", command)
        self.assertEqual(command[command.index("-pix_fmt") + 1], "yuv444p")
        self.assertEqual(command[-1], "prepared.mkv")




class AiPipelineSpeedTests(unittest.TestCase):
    def setUp(self) -> None:
        # Keep the suite hermetic: never probe the real GPU encoder here.
        self._original_probe = ai_engine._video2x_nvenc_usable

    def tearDown(self) -> None:
        ai_engine._video2x_nvenc_usable = self._original_probe

    def test_fast_mode_uses_nvenc_intermediate_when_available(self) -> None:
        ai_engine._video2x_nvenc_usable = lambda _video2x: True
        settings = ExportSettings(ai_enabled=True, ai_fast_mode=True)
        args = _intermediate_codec_args(settings, Path("video2x.exe"))
        self.assertEqual(args[args.index("-c") + 1], "h264_nvenc")

    def test_fast_mode_falls_back_to_ultrafast_x264_without_gpu_encoder(
        self,
    ) -> None:
        ai_engine._video2x_nvenc_usable = lambda _video2x: False
        settings = ExportSettings(ai_enabled=True, ai_fast_mode=True)
        args = _intermediate_codec_args(settings, Path("video2x.exe"))
        self.assertEqual(args[args.index("-c") + 1], "libx264")
        self.assertIn("preset=ultrafast", " ".join(args))

    def test_quality_mode_keeps_careful_x264_intermediate(self) -> None:
        ai_engine._video2x_nvenc_usable = lambda _video2x: True
        settings = ExportSettings(ai_enabled=True, ai_fast_mode=False)
        args = _intermediate_codec_args(settings, Path("video2x.exe"))
        self.assertEqual(args[args.index("-c") + 1], "libx264")
        self.assertIn("crf=12", " ".join(args))

    def test_upscale_command_routes_through_the_chosen_intermediate(self) -> None:
        ai_engine._video2x_nvenc_usable = lambda _video2x: True
        settings = ExportSettings(resolution="1080p", ai_model="anime_fast", ai_fast_mode=True)
        command, _width, _height = _video2x_upscale_command(
            Path("video2x.exe"), "clip.mp4", Path("out.mp4"), LANDSCAPE, settings
        )
        joined = " ".join(command)
        self.assertIn("h264_nvenc", joined)


class BlankDetectionTests(unittest.TestCase):
    @staticmethod
    def _render(name: str, arguments: list[str]) -> Path:
        import subprocess
        import tempfile

        target = Path(tempfile.gettempdir()) / name
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", *arguments, str(target)],
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return target

    def test_black_frame_is_rejected(self) -> None:
        target = self._render(
            "videocraft_blank_black.png",
            ["-f", "lavfi", "-i", "color=c=black:s=160x120:d=1", "-frames:v", "1"],
        )
        self.assertTrue(_looks_blank(target))

    def test_flat_wash_is_rejected(self) -> None:
        target = self._render(
            "videocraft_blank_white.png",
            ["-f", "lavfi", "-i", "color=c=white:s=160x120:d=1", "-frames:v", "1"],
        )
        self.assertTrue(_looks_blank(target))

    def test_real_content_passes(self) -> None:
        target = self._render(
            "videocraft_real_smpte.jpg",
            ["-f", "lavfi", "-i", "smptebars=s=320x240:d=1", "-frames:v", "1", "-q:v", "2"],
        )
        self.assertFalse(_looks_blank(target))


if __name__ == "__main__":
    unittest.main()
