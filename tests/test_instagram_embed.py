import os
import unittest
from unittest.mock import patch

from cogs.media import (
    _download_instagram_video_fast,
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
        fallback.assert_called_once_with("https://www.instagram.com/reel/ABC/", 1024)


if __name__ == "__main__":
    unittest.main()
