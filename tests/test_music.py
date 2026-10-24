import asyncio
import io
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import AsyncMock, patch

import wavelink
from cogs import music
from cogs.music import (
    MAX_QUERY_LENGTH,
    normalize_query,
    track_duration,
    track_title,
    track_url,
)


class MusicInputValidationTests(unittest.TestCase):
    def test_plain_search_is_trimmed(self):
        self.assertEqual(normalize_query("  song title  "), ("song title", None))

    def test_youtube_and_spotify_urls_are_accepted(self):
        for url in (
            "https://www.youtube.com/watch?v=example",
            "https://youtu.be/example",
            "https://open.spotify.com/track/example",
        ):
            with self.subTest(url=url):
                self.assertEqual(normalize_query(url), (url, None))

    def test_empty_and_long_queries_are_rejected(self):
        self.assertIsNotNone(normalize_query("   ")[1])
        self.assertIsNotNone(normalize_query("x" * (MAX_QUERY_LENGTH + 1))[1])

    def test_unsafe_urls_are_rejected(self):
        for url in (
            "https://example.com/watch",
            "https://youtube.com:8443/watch",
            "https://youtube.com.evil.example/watch",
            "https://user:pass@youtube.com/watch",
        ):
            with self.subTest(url=url):
                self.assertIsNotNone(normalize_query(url)[1])


class MusicTrackDisplayTests(unittest.TestCase):
    def test_track_metadata_has_safe_fallbacks(self):
        class Track:
            title = "Test song"
            uri = "https://example.com/song"
            length = 125000

        track = Track()
        self.assertEqual(track_title(track), "Test song")
        self.assertEqual(track_url(track), "https://example.com/song")
        self.assertEqual(track_duration(track), "2:05")

    def test_missing_track_metadata_has_safe_fallbacks(self):
        track = object()
        self.assertEqual(track_title(track), "未知歌曲")
        self.assertEqual(track_url(track), "")
        self.assertEqual(track_duration(track), "直播／未知長度")


class MusicNodeStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_nodes_are_bounded_and_do_not_block_a_healthy_node(self):
        class FakeNode:
            def __init__(self, *, identifier, **kwargs):
                self.identifier = identifier
                self.status = wavelink.NodeStatus.CONNECTING
                self._session = None
                self.close = AsyncMock()

        created_nodes = []

        def make_node(**kwargs):
            node = FakeNode(**kwargs)
            created_nodes.append(node)
            return node

        async def connect(*, nodes, client):
            node = next(iter(nodes))
            if node.identifier == "offline":
                await asyncio.Future()

        cog = music.MusicCog(object())
        output = io.StringIO()
        started = time.monotonic()
        with (
            patch.object(music, "LAVALINK_NODES", (
                {"identifier": "offline", "uri": "http://offline.invalid:2333", "password": "x"},
                {"identifier": "healthy", "uri": "http://healthy.invalid:2333", "password": "x"},
            )),
            patch.object(music, "LAVALINK_CONNECT_TIMEOUT_SECONDS", 0.05),
            patch.object(music.wavelink, "Node", side_effect=make_node),
            patch.object(music.wavelink.Pool, "connect", side_effect=connect) as connect_mock,
            redirect_stdout(output),
        ):
            await cog.setup_nodes()

        self.assertLess(time.monotonic() - started, 0.2)
        self.assertEqual(connect_mock.await_count, 2)
        self.assertIsNone(cog.node_error)
        self.assertEqual(created_nodes[0].close.await_count, 1)
        self.assertEqual(created_nodes[1].close.await_count, 0)
        self.assertIn("1/2", output.getvalue())


if __name__ == "__main__":
    unittest.main()
