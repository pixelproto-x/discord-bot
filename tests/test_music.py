import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from cogs.music import MusicCog, Song


class MusicQueueTests(unittest.IsolatedAsyncioTestCase):

    async def test_full_queue_rejects_new_song_before_connecting(self):
        from cogs.music import MAX_QUEUE_SIZE, Player

        cog = MusicCog(SimpleNamespace())
        cog.ffmpeg_executable = lambda: "ffmpeg"
        guild_id = 321
        voice_channel = SimpleNamespace(connect=AsyncMock())
        player = Player(queue=[
            Song(title=f"Song {i}", url="", webpage_url="")
            for i in range(MAX_QUEUE_SIZE)
        ])
        cog.players[guild_id] = player
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=guild_id),
            user=SimpleNamespace(
                id=654,
                voice=SimpleNamespace(channel=voice_channel),
            ),
        )

        success, message = await cog.enqueue_song(
            interaction,
            Song(title="Overflow song", url="", webpage_url=""),
        )

        self.assertFalse(success)
        self.assertIn(str(MAX_QUEUE_SIZE), message)
        voice_channel.connect.assert_not_awaited()
        self.assertEqual(len(player.queue), MAX_QUEUE_SIZE)

    async def test_enqueue_reports_failure_when_failed_song_was_dropped(self):
        cog = object.__new__(MusicCog)
        guild_id = 123
        voice_channel = object()
        song = Song(
            title="Test song",
            url="https://example.com/audio",
            webpage_url="https://example.com/watch",
        )
        voice = SimpleNamespace(
            channel=voice_channel,
            is_connected=Mock(return_value=True),
            is_playing=Mock(return_value=False),
            is_paused=Mock(return_value=False),
        )
        player = SimpleNamespace(queue=[], current=None, voice=voice)
        lock = asyncio.Lock()

        cog.lock_for = lambda _guild_id: lock
        cog.ffmpeg_executable = lambda: "ffmpeg"
        cog.player = lambda _guild_id: player

        async def drop_failed_song(_guild_id):
            player.queue.remove(song)

        cog._play_next_locked = AsyncMock(side_effect=drop_failed_song)
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=guild_id),
            user=SimpleNamespace(
                id=456,
                voice=SimpleNamespace(channel=voice_channel),
            ),
        )

        success, message = await cog.enqueue_song(interaction, song)

        self.assertFalse(success)
        self.assertIn("沒能播放", message)
        self.assertNotIn(song, player.queue)



class MusicInputValidationTests(unittest.TestCase):
    def test_plain_search_is_trimmed(self):
        from cogs.music import normalize_music_query

        self.assertEqual(normalize_music_query("  song title  "), ("song title", None))

    def test_youtube_urls_are_accepted(self):
        from cogs.music import normalize_music_query

        self.assertEqual(
            normalize_music_query("https://www.youtube.com/watch?v=example"),
            ("https://www.youtube.com/watch?v=example", None),
        )
        self.assertEqual(
            normalize_music_query("https://youtu.be/example"),
            ("https://youtu.be/example", None),
        )

    def test_non_youtube_urls_and_nonstandard_ports_are_rejected(self):
        from cogs.music import normalize_music_query

        self.assertIsNotNone(normalize_music_query("https://example.com/watch")[1])
        self.assertIsNotNone(normalize_music_query("https://youtube.com:8443/watch")[1])
        self.assertIsNotNone(normalize_music_query("https://youtube.com.evil.example/watch")[1])

    def test_query_length_is_bounded(self):
        from cogs.music import MAX_QUERY_LENGTH, normalize_music_query

        self.assertIsNotNone(normalize_music_query("x" * (MAX_QUERY_LENGTH + 1))[1])


if __name__ == "__main__":
    unittest.main()
