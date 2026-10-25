import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from cogs.speech_control import SpeechControlCog


class SpeechLocalAudioHandoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_yields_lavalink_connection_before_connecting_local_audio(self):
        guild_id = 111
        music_channel = SimpleNamespace()
        local_channel = SimpleNamespace()
        old_player = SimpleNamespace(channel=music_channel)
        local_voice_client = SimpleNamespace(
            channel=local_channel,
            is_connected=Mock(return_value=True),
            is_playing=Mock(return_value=False),
            is_paused=Mock(return_value=False),
            move_to=AsyncMock(),
        )
        guild = SimpleNamespace(id=guild_id, voice_client=old_player)
        local_channel.guild = guild
        local_channel.connect = AsyncMock(return_value=local_voice_client)

        class FakeMusicCog:
            def __init__(self):
                self.local_audio_handoffs = {}
                self.suspend_for_local_audio = AsyncMock(side_effect=self.suspend)

            async def suspend(self, target_guild, target_channel):
                self.local_audio_handoffs[target_guild.id] = {"channel": music_channel}
                target_guild.voice_client = None
                return True

        music_cog = FakeMusicCog()
        bot = SimpleNamespace(
            get_cog=lambda name: {"MusicCog": music_cog}.get(name),
            get_guild=lambda gid: guild if gid == guild_id else None,
        )
        speech = SpeechControlCog(bot)

        with patch("cogs.speech_control.is_lavalink_player", side_effect=lambda vc: vc is old_player):
            result = await speech.prepare_local_audio(local_channel)

        self.assertIs(result, local_voice_client)
        music_cog.suspend_for_local_audio.assert_awaited_once_with(guild, local_channel)
        local_channel.connect.assert_awaited_once()
        self.assertIn(guild_id, speech._local_audio_reserved)

        speech.local_audio_started(guild_id)
        self.assertNotIn(guild_id, speech._local_audio_reserved)

    async def test_finish_disconnects_temporary_voice_client_before_resuming_music(self):
        guild_id = 222
        voice_client = SimpleNamespace(
            is_connected=Mock(return_value=True),
            disconnect=AsyncMock(),
        )
        guild = SimpleNamespace(id=guild_id, voice_client=voice_client)
        handoffs = {guild_id: {"track": object()}}
        music_cog = SimpleNamespace(
            local_audio_handoffs=handoffs,
            resume_after_local_audio=AsyncMock(return_value=True),
        )
        bot = SimpleNamespace(
            get_cog=lambda name: {"MusicCog": music_cog}.get(name),
            get_guild=lambda gid: guild if gid == guild_id else None,
        )
        speech = SpeechControlCog(bot)

        self.assertTrue(await speech.finish_local_audio(guild_id))

        voice_client.disconnect.assert_awaited_once_with(force=True)
        music_cog.resume_after_local_audio.assert_awaited_once_with(guild_id)


    async def test_stale_cleanup_cannot_remove_another_active_tts_session(self):
        guild_id = 444
        music_cog = SimpleNamespace(
            local_audio_handoffs={guild_id: {"track": object()}},
            resume_after_local_audio=AsyncMock(return_value=True),
        )
        bot = SimpleNamespace(get_cog=lambda name: {"MusicCog": music_cog}.get(name))
        speech = SpeechControlCog(bot)
        speech.active_guilds.add(guild_id)
        speech.temp_files[guild_id] = "currently-playing.mp3"

        await speech._cleanup(guild_id, "late-callback.mp3")

        self.assertIn(guild_id, speech.active_guilds)
        self.assertEqual(speech.temp_files[guild_id], "currently-playing.mp3")
        music_cog.resume_after_local_audio.assert_not_awaited()

    async def test_second_local_audio_is_rejected_without_disturbing_music(self):
        guild_id = 333
        channel = SimpleNamespace(guild=SimpleNamespace(id=guild_id))
        bot = SimpleNamespace(get_cog=lambda name: None)
        speech = SpeechControlCog(bot)
        speech.active_guilds.add(guild_id)

        with self.assertRaisesRegex(RuntimeError, "已經有朗讀或麥克風串流"):
            await speech.prepare_local_audio(channel)


if __name__ == "__main__":
    unittest.main()
