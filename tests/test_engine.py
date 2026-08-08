from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
