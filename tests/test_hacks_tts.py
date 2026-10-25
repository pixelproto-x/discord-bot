import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from cogs.hacks import Hacks


class AutomaticTTSAudioHandoffTests(unittest.IsolatedAsyncioTestCase):
    def build_message(self, loop):
        voice_channel = SimpleNamespace(id=12, name="voice")
        author = SimpleNamespace(
            id=34,
            bot=False,
            voice=SimpleNamespace(channel=voice_channel),
        )
        guild = SimpleNamespace(id=56, voice_client=None)
        channel = SimpleNamespace(id=78)
        message = SimpleNamespace(
            guild=guild,
            author=author,
            channel=channel,
            webhook_id=None,
            content="hello there",
        )
        bot = SimpleNamespace(
            loop=loop,
            get_cog=lambda name: {"SpeechControlCog": self.speech_cog}.get(name),
        )
        voice_client = SimpleNamespace(
            is_playing=Mock(return_value=False),
            is_paused=Mock(return_value=False),
            play=Mock(side_effect=lambda source, after: loop.call_soon(after, None)),
        )
        self.speech_cog = SimpleNamespace(
            prepare_local_audio=AsyncMock(return_value=voice_client),
            local_audio_started=Mock(),
            finish_local_audio=AsyncMock(return_value=True),
        )
        return Hacks(bot), message, voice_channel, voice_client

    async def test_automatic_tts_uses_shared_voice_handoff_and_restores_music(self):
        loop = asyncio.get_running_loop()
        cog, message, voice_channel, voice_client = self.build_message(loop)
        cog._generate_tts_audio = AsyncMock()

        with (
            patch.object(Hacks, "_read_tts_settings", return_value={
                "56": {"enabled": True, "channel_id": 78, "member_id": 34}
            }),
            patch("cogs.hacks.discord.FFmpegPCMAudio", return_value=object()) as audio_source,
            patch("cogs.hacks.imageio_ffmpeg.get_ffmpeg_exe", return_value="ffmpeg"),
        ):
            await cog.on_message(message)

        self.speech_cog.prepare_local_audio.assert_awaited_once_with(voice_channel)
        self.speech_cog.local_audio_started.assert_called_once_with(56)
        self.speech_cog.finish_local_audio.assert_awaited_once_with(56)
        voice_client.play.assert_called_once()
        audio_source.assert_called_once()

    async def test_audio_playback_failure_still_restores_music_handoff(self):
        loop = asyncio.get_running_loop()
        cog, message, voice_channel, voice_client = self.build_message(loop)
        cog._generate_tts_audio = AsyncMock()
        voice_client.play.side_effect = discord.ClientException("simulated play failure")

        with (
            patch.object(Hacks, "_read_tts_settings", return_value={
                "56": {"enabled": True, "channel_id": 78, "member_id": 34}
            }),
            patch("cogs.hacks.discord.FFmpegPCMAudio", return_value=object()),
            patch("cogs.hacks.imageio_ffmpeg.get_ffmpeg_exe", return_value="ffmpeg"),
        ):
            await cog.on_message(message)

        self.speech_cog.prepare_local_audio.assert_awaited_once_with(voice_channel)
        self.speech_cog.finish_local_audio.assert_awaited_once_with(56)


if __name__ == "__main__":
    unittest.main()
