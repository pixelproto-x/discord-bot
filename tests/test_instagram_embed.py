import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from cogs.media import (
    rewrite_instagram_embed_url,
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

    def test_rewrites_to_oginstagram_and_preserves_query(self):
        self.assertEqual(
            rewrite_instagram_embed_url(
                "https://www.instagram.com/reel/ABC123/?img_index=2"
            ),
            "https://oginstagram.com/reel/ABC123/?img_index=2",
        )

    def test_rejects_non_instagram_hosts(self):
        with self.assertRaises(ValueError):
            rewrite_instagram_embed_url("https://evil.example/reel/ABC123/")


class InstagramEmbedDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_posts_proxy_url_without_uploading_video_then_deletes_original(self):
        from cogs.media import SearchCog

        sent = AsyncMock()
        channel = SimpleNamespace(
            parent=None,
            permissions_for=lambda _member: SimpleNamespace(
                send_messages=True,
                manage_messages=True,
            ),
            send=sent,
        )
        message = SimpleNamespace(
            channel=channel,
            content="這個好看 https://www.instagram.com/reel/ABC123/",
            guild=SimpleNamespace(me=object()),
            author=SimpleNamespace(id=123),
            delete=AsyncMock(),
        )
        notice = SimpleNamespace(delete=AsyncMock(), edit=AsyncMock())
        cog = SearchCog.__new__(SearchCog)

        await cog._replace_instagram_message(message, processing_notice=notice)

        sent.assert_awaited_once()
        kwargs = sent.await_args.kwargs
        self.assertIn("https://oginstagram.com/reel/ABC123/", kwargs["content"])
        self.assertNotIn("file", kwargs)
        self.assertNotIn("embed", kwargs)
        message.delete.assert_awaited_once()
        notice.delete.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
