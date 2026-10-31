import os
import unittest
from unittest.mock import patch

from cogs.media import (
    MAX_VIDEO_SOURCE_BYTES,
    _download_instagram_video_fast,
    _fit_instagram_video_to_limit,
    rewrite_instagram_video_links,
)


class InstagramEmbedRewriteTests(unittest.TestCase):
    def test_rewrites_public_reel_and_post_links(self):
        links = rewrite_instagram_video_links(
            "Reel: https://www.instagram.com/reel/ABC123/?igsh=tracking "
            "Post: https://instagram.com/p/XYZ789/"
        )
        self.assertEqual(
            links,
            [
                "https://www.instagram.com/reel/ABC123/?igsh=tracking",
                "https://www.instagram.com/p/XYZ789/",
            ],
        )

    def test_supports_mobile_host_and_deduplicates(self):
        links = rewrite_instagram_video_links(
            "https://m.instagram.com/reels/ABC/ "
            "https://www.instagram.com/reels/ABC/"
        )
        self.assertEqual(links, ["https://www.instagram.com/reel/ABC/"])

    def test_ignores_profiles_and_non_instagram_urls(self):
        self.assertEqual(
            rewrite_instagram_video_links(
                "https://instagram.com/example/ https://example.com/reel/ABC"
            ),
            [],
        )

    def test_strips_trailing_sentence_punctuation(self):
        self.assertEqual(
            rewrite_instagram_video_links("看這個 https://instagram.com/reel/ABC123/."),
            ["https://www.instagram.com/reel/ABC123/"],
        )


class InstagramFastPathFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_ytdlp_when_no_cobalt_instance_is_configured(self):
        expected = (b"mp4-data", "instagram-video.mp4", {"like_count": 12})
        with (
            patch.dict(os.environ, {"COBALT_API_URL": ""}),
            patch("cogs.media._download_public_instagram_video", return_value=expected) as fallback,
        ):
            result = await _download_instagram_video_fast("https://www.instagram.com/reel/ABC/", 1024)

        self.assertEqual(result, expected)
        fallback.assert_called_once_with("https://www.instagram.com/reel/ABC/", MAX_VIDEO_SOURCE_BYTES)


class InstagramVideoCompatibilityTests(unittest.TestCase):
    def test_small_mp4_is_not_reencoded(self):
        source = b"\\x00\\x00\\x00\\x18ftypisom" + b"video"
        self.assertIs(_fit_instagram_video_to_limit(source, 1024), source)

    def test_source_processing_limit_is_bounded_but_not_30_seconds(self):
        self.assertEqual(MAX_VIDEO_SOURCE_BYTES, 250 * 1024 * 1024)


class InstagramCobaltRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_cobalt_once_before_using_result(self):
        import asyncio
        from unittest.mock import AsyncMock

        video = b"\\x00\\x00\\x00\\x18ftypisom" + b"video"
        cobalt = AsyncMock(side_effect=[RuntimeError("temporary upstream error"), (video, "instagram-video.mp4")])
        with (
            patch.dict(os.environ, {"COBALT_API_URL": "https://cobalt.example/api"}),
            patch("cogs.media._download_instagram_via_cobalt", cobalt),
            patch("cogs.media._extract_public_instagram_stats", return_value={"like_count": 1}),
        ):
            result = await _download_instagram_video_fast("https://www.instagram.com/reel/ABC/", 1024)

        self.assertEqual(result[0], video)
        self.assertEqual(result[1], "instagram-video.mp4")
        self.assertEqual(cobalt.await_count, 2)


if __name__ == "__main__":
    unittest.main()
