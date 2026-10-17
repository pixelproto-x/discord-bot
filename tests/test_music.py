import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from cogs.music import MusicCog, Song


class MusicQueueTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
