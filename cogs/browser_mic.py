from __future__ import annotations

import asyncio
import secrets
import time
from queue import Empty, Full, Queue
from typing import Optional

import discord
from discord.ext import commands


class BrowserMicAudioSource(discord.AudioSource):
    """PCM audio arriving from an authenticated browser microphone WebSocket."""

    SAMPLES_PER_FRAME = 960
    MONO_BYTES_PER_FRAME = SAMPLES_PER_FRAME * 2
    STEREO_BYTES_PER_FRAME = SAMPLES_PER_FRAME * 2 * 2
    SILENCE = bytes(STEREO_BYTES_PER_FRAME)

    def __init__(self) -> None:
        self._frames: Queue[bytes] = Queue(maxsize=40)
        self._closed = False
        self.dropped_frames = 0

    def push(self, mono_pcm: bytes) -> None:
        if self._closed or len(mono_pcm) != self.MONO_BYTES_PER_FRAME:
            return
        # Discord PCM audio is signed 16-bit, 48 kHz, stereo. Duplicate mono to L/R.
        stereo = bytearray(self.STEREO_BYTES_PER_FRAME)
        stereo_view = memoryview(stereo)
        for offset in range(0, self.MONO_BYTES_PER_FRAME, 2):
            sample = mono_pcm[offset:offset + 2]
            target = offset * 2
            stereo_view[target:target + 2] = sample
            stereo_view[target + 2:target + 4] = sample
        try:
            self._frames.put_nowait(bytes(stereo))
        except Full:
            try:
                self._frames.get_nowait()
            except Empty:
                pass
            self.dropped_frames += 1
            try:
                self._frames.put_nowait(bytes(stereo))
            except Full:
                self.dropped_frames += 1

    def read(self) -> bytes:
        if self._closed:
            return b""
        try:
            return self._frames.get(timeout=0.1)
        except Empty:
            return self.SILENCE

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        self._closed = True
        while True:
            try:
                self._frames.get_nowait()
            except Empty:
                break


class BrowserMicCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.sources: dict[int, BrowserMicAudioSource] = {}
        self.tickets: dict[str, tuple[int, float]] = {}

    def issue_ticket(self, guild_id: int) -> str:
        now = time.monotonic()
        self.tickets = {key: value for key, value in self.tickets.items() if value[1] > now}
        ticket = secrets.token_urlsafe(32)
        self.tickets[ticket] = (guild_id, now + 30)
        return ticket

    def consume_ticket(self, ticket: str) -> Optional[int]:
        item = self.tickets.pop(ticket, None)
        if not item or item[1] <= time.monotonic():
            return None
        return item[0]

    async def start_stream(self, channel: discord.VoiceChannel) -> tuple[bool, str, str | None]:
        guild = channel.guild
        me = guild.me
        perms = channel.permissions_for(me) if me else None
        if not perms or not perms.view_channel or not perms.connect or not perms.speak:
            return False, "Bot 在這個語音頻道需要查看頻道、連線和說話權限。", None
        if guild.id in self.sources:
            return False, "目前已有瀏覽器麥克風串流，請先停止再重新開始。", None
        speech_cog = self.bot.get_cog("SpeechControlCog")
        vc = guild.voice_client
        claimed = False
        source: BrowserMicAudioSource | None = None
        try:
            if speech_cog and hasattr(speech_cog, "prepare_local_audio"):
                vc = await speech_cog.prepare_local_audio(channel)
                claimed = True
            else:
                try:
                    import wavelink
                    if isinstance(vc, wavelink.Player):
                        return False, "音樂播放器目前無法切換成本機麥克風，請先更新語音控制模組。", None
                except ImportError:
                    pass
                if vc and (vc.is_playing() or vc.is_paused()):
                    return False, "Bot 正在播放音樂或其他音訊，請先停止播放再使用麥克風。", None
                if not vc or not vc.is_connected():
                    vc = await channel.connect()
                elif vc.channel != channel:
                    await vc.move_to(channel)
            source = BrowserMicAudioSource()
            self.sources[guild.id] = source
            loop = asyncio.get_running_loop()

            def after_playback(error):
                if error:
                    print(f"[Browser mic] playback error: {type(error).__name__}: {error}")
                if not loop.is_closed():
                    asyncio.run_coroutine_threadsafe(self.stop_stream(guild.id), loop)

            vc.play(source, after=after_playback)
            if claimed:
                speech_cog.local_audio_started(guild.id)
            return True, f"已連接「{channel.name}」，等待瀏覽器麥克風。", self.issue_ticket(guild.id)
        except discord.Forbidden:
            self.sources.pop(guild.id, None)
            if source is not None:
                source.cleanup()
                if vc and getattr(vc, "source", None) is source:
                    vc.stop()
            if claimed:
                await speech_cog.finish_local_audio(guild.id)
            return False, "Discord 拒絕 Bot 連線，請檢查頻道權限。", None
        except Exception as exc:
            self.sources.pop(guild.id, None)
            if source is not None:
                source.cleanup()
                if vc and getattr(vc, "source", None) is source:
                    vc.stop()
            if claimed:
                await speech_cog.finish_local_audio(guild.id)
            print(f"[Browser mic] start failed: {type(exc).__name__}: {exc}")
            return False, "無法啟動麥克風串流，請確認 Bot 的語音連線正常。", None

    async def stop_stream(self, guild_id: int) -> bool:
        source = self.sources.pop(guild_id, None)
        guild = self.bot.get_guild(guild_id)
        if not source:
            return False
        if guild and guild.voice_client:
            try:
                import wavelink
                is_wavelink = isinstance(guild.voice_client, wavelink.Player)
            except ImportError:
                is_wavelink = False
            if not is_wavelink and (guild.voice_client.is_playing() or guild.voice_client.is_paused()):
                guild.voice_client.stop()
        source.cleanup()
        speech_cog = self.bot.get_cog("SpeechControlCog")
        if speech_cog and hasattr(speech_cog, "finish_local_audio"):
            await speech_cog.finish_local_audio(guild_id)
        return True

    def feed_frame(self, guild_id: int, data: bytes) -> bool:
        source = self.sources.get(guild_id)
        if not source or len(data) != BrowserMicAudioSource.MONO_BYTES_PER_FRAME:
            return False
        source.push(data)
        return True


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BrowserMicCog(bot))
