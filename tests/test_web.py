"""Tests for the web version's pure helpers (no server or FFmpeg needed)."""

import unittest
from pathlib import Path

from web.jobs import settings_from_options
from web.media import parse_ffmpeg_info
from video_engine import MediaInfo

VIDEO_BANNER = """Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'clip.mp4':
  Duration: 00:01:02.50, start: 0.000000, bitrate: 5136 kb/s
  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(progressive), 1920x1080 [SAR 1:1 DAR 16:9], 5104 kb/s, 29.97 fps, 29.97 tbr, 30k tbn (default)
      Side data:
        displaymatrix: rotation of -90.00 degrees
  Stream #0:1[0x2](und): Audio: aac (LC) (mp4a / 0x6134706D), 48000 Hz, stereo, fltp, 128 kb/s (default)
"""

IMAGE_BANNER = """Input #0, png_pipe, from 'photo.png':
  Duration: N/A, bitrate: N/A
  Stream #0:0: Video: png, rgb24(pc, gbr/unknown/unknown), 400x300, 25 fps, 25 tbr, 25 tbn
"""


def _video(**overrides) -> MediaInfo:
    base = dict(path="x", width=1280, height=720, fps=25.0, duration=10.0, size_bytes=1,
                video_codec="h264", audio_codec="aac", format_name="mp4")
    base.update(overrides)
    return MediaInfo(**base)


class ParseFfmpegInfoTests(unittest.TestCase):
    def test_video_banner(self):
        info = parse_ffmpeg_info(VIDEO_BANNER, Path("clip.mp4"), 123)
        self.assertEqual((info.width, info.height), (1920, 1080))
        self.assertAlmostEqual(info.fps, 29.97)
        self.assertAlmostEqual(info.duration, 62.5)
        self.assertEqual(info.rotation, -90)
        self.assertTrue(info.is_portrait)
        self.assertEqual(info.audio_codec, "aac")
        self.assertFalse(info.is_image)

    def test_image_banner(self):
        info = parse_ffmpeg_info(IMAGE_BANNER, Path("photo.png"), 10)
        self.assertTrue(info.is_image)
        self.assertEqual((info.width, info.height, info.fps, info.duration), (400, 300, 0.0, 0.0))

    def test_rejects_non_media(self):
        from video_engine import VideoEngineError
        with self.assertRaises(VideoEngineError):
            parse_ffmpeg_info("Invalid data found when processing input", Path("x.mp4"), 1)


class SettingsFromOptionsTests(unittest.TestCase):
    def test_clamps_and_whitelists_client_values(self):
        s = settings_from_options(
            {"resolution": "4k", "fps": "240", "denoise": 999, "contrast": -500,
             "color_style": "<script>", "quality": 1},
            _video(),
        )
        self.assertEqual(s.resolution, "source")  # 4K video is not offered on the server
        self.assertEqual(s.fps, 25)  # unknown choice keeps the source rate
        self.assertEqual((s.denoise, s.contrast, s.quality), (100, -50, 14))
        self.assertEqual(s.color_style, "natural")
        self.assertFalse(s.hardware)
        self.assertFalse(s.ai_enabled)

    def test_images_may_use_4k(self):
        s = settings_from_options({"resolution": "4k"}, _video(is_image=True, fps=0.0, duration=0.0))
        self.assertEqual(s.resolution, "4k")


if __name__ == "__main__":
    unittest.main()
