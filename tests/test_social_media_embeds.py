import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from cogs.media import rewrite_social_embed_url, rewrite_social_video_links


class SocialMediaEmbedRewriteTests(unittest.TestCase):
    def test_rewrites_tiktok_video_and_short_links(self):
        links = rewrite_social_video_links(
            "https://www.tiktok.com/@creator/video/123456789 "
            "https://vm.tiktok.com/ZMabc123/"
        )
        self.assertEqual([item[0] for item in links], ["TikTok", "TikTok"])
        self.assertEqual(
            [rewrite_social_embed_url(platform, url) for platform, url, _ in links],
            [
                "https://embeds.now/tiktok.com/@creator/video/123456789",
                "https://embeds.now/vm.tiktok.com/ZMabc123/",
            ],
        )

    def test_rewrites_x_and_legacy_twitter_status_links(self):
        links = rewrite_social_video_links(
            "https://x.com/example/status/123456789 "
            "https://twitter.com/another/status/987654321"
        )
        self.assertEqual([item[0] for item in links], ["X", "X"])
        self.assertEqual(
            [rewrite_social_embed_url(platform, url) for platform, url, _ in links],
            [
                "https://embeds.now/x.com/example/status/123456789",
                "https://embeds.now/x.com/another/status/987654321",
            ],
        )

    def test_rewrites_facebook_reels_watch_and_short_links(self):
        links = rewrite_social_video_links(
            "https://www.facebook.com/reel/123456 "
            "https://m.facebook.com/watch/?v=654321 "
            "https://fb.watch/AbCdEf/"
        )
        self.assertEqual([item[0] for item in links], ["Facebook", "Facebook", "Facebook"])
        self.assertEqual(
            [rewrite_social_embed_url(platform, url) for platform, url, _ in links],
            [
                "https://embeds.now/facebook.com/reel/123456",
                "https://embeds.now/facebook.com/watch/?v=654321",
                "https://embeds.now/fb.watch/AbCdEf/",
            ],
        )

    def test_ignores_profiles_searches_and_unrelated_domains(self):
        self.assertEqual(
            rewrite_social_video_links(
                "https://tiktok.com/@creator "
                "https://x.com/explore "
                "https://facebook.com/example "
                "https://notfacebook.com/reel/123"
            ),
            [],
        )

    def test_rejects_unsupported_embed_hosts(self):
        with self.assertRaises(ValueError):
            rewrite_social_embed_url("X", "https://evil.example/user/status/123")


class SocialMediaEmbedDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_posts_remote_embed_urls_without_uploading_media_then_deletes_original(self):
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
            content=(
                "三個平台 "
                "https://www.tiktok.com/@creator/video/123456789 "
                "https://x.com/example/status/123456789 "
                "https://www.facebook.com/reel/987654"
            ),
            guild=SimpleNamespace(me=object()),
            author=SimpleNamespace(id=123),
            delete=AsyncMock(),
        )
        notice = SimpleNamespace(delete=AsyncMock(), edit=AsyncMock())
        cog = SearchCog.__new__(SearchCog)

        await cog._replace_social_message(message, processing_notice=notice)

        sent.assert_awaited_once()
        kwargs = sent.await_args.kwargs
        self.assertIn("https://embeds.now/tiktok.com/@creator/video/123456789", kwargs["content"])
        self.assertIn("https://embeds.now/x.com/example/status/123456789", kwargs["content"])
        self.assertIn("https://embeds.now/facebook.com/reel/987654", kwargs["content"])
        self.assertNotIn("file", kwargs)
        self.assertNotIn("embed", kwargs)
        message.delete.assert_awaited_once()
        notice.delete.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
