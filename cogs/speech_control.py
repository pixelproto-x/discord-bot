import asyncio
import os
import shutil
import tempfile
from pathlib import Path

import discord
import edge_tts
import imageio_ffmpeg
from discord import app_commands
from discord.ext import commands


OWNER_ID = int(os.getenv("OWNER_ID", "1140900506198351924"))
DEFAULT_VOICE = os.getenv("TTS_VOICE", "zh-TW-HsiaoChenNeural").strip() or "zh-TW-HsiaoChenNeural"


class SpeechControlCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_guilds: set[int] = set()
        self.temp_files: dict[int, str] = {}

    def ffmpeg_executable(self):
        configured = os.getenv("FFMPEG_PATH", "").strip()
        if configured:
            return configured
        system_ffmpeg = shutil.which("ffmpeg")
        if system_ffmpeg:
            return system_ffmpeg
        try:
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return None

    async def owner_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("這個語音控制功能只有 Bot 主人能用。", ephemeral=True)
            return False
        if not interaction.guild:
            await interaction.response.send_message("請在 Discord 伺服器裡使用。", ephemeral=True)
            return False
        return True

    @app_commands.command(name="speak", description="讓 LogRelay 在你的語音頻道唸出指定文字（主人專用）")
    @app_commands.describe(text="要讓機器人唸出的內容")
    async def speak(self, interaction: discord.Interaction, text: app_commands.Range[str, 1, 500]):
        if not await self.owner_check(interaction):
            return
        member = interaction.user
        if not isinstance(member, discord.Member) or not member.voice or not member.voice.channel:
            await interaction.response.send_message("你先加入一個語音頻道，我才知道要去哪裡講話。", ephemeral=True)
            return
        text = text.strip()
        if not text:
            await interaction.response.send_message("你還沒輸入要唸的內容。", ephemeral=True)
            return
        ffmpeg = self.ffmpeg_executable()
        if not ffmpeg:
            await interaction.response.send_message("找不到 FFmpeg，暫時無法播放語音。", ephemeral=True)
            return

        guild = interaction.guild
        await interaction.response.defer(thinking=True, ephemeral=True)
        voice_client = guild.voice_client
        if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
            await interaction.followup.send("我現在正在播放音訊。先用 /speech-stop 停止，再讓我講下一句。", ephemeral=True)
            return

        filename = None
        try:
            with tempfile.NamedTemporaryFile(prefix="logrelay_tts_", suffix=".mp3", delete=False) as tmp:
                filename = tmp.name
            communicate = edge_tts.Communicate(text, DEFAULT_VOICE)
            await communicate.save(filename)

            if not voice_client or not voice_client.is_connected():
                voice_client = await member.voice.channel.connect()
            elif voice_client.channel != member.voice.channel:
                await voice_client.move_to(member.voice.channel)

            self.active_guilds.add(guild.id)
            self.temp_files[guild.id] = filename
            loop = asyncio.get_running_loop()

            def after_playback(error):
                if error:
                    print(f"[Speech] playback error: {type(error).__name__}")
                if not loop.is_closed():
                    asyncio.run_coroutine_threadsafe(self._cleanup(guild.id, filename), loop)

            source = discord.FFmpegPCMAudio(filename, executable=ffmpeg, options="-vn")
            voice_client.play(source, after=after_playback)
            await interaction.followup.send(f"好，我開始唸了：\n> {text[:180]}", ephemeral=True)
        except Exception as exc:
            print(f"[Speech] failed: {type(exc).__name__}")
            await self._cleanup(guild.id, filename)
            await interaction.followup.send(
                "語音播放失敗。確認網路正常、Bot 有連接／說話權限，再試一次。",
                ephemeral=True,
            )

    async def _cleanup(self, guild_id: int, filename: str | None):
        self.active_guilds.discard(guild_id)
        current = self.temp_files.get(guild_id)
        if current == filename:
            self.temp_files.pop(guild_id, None)
        if filename:
            for _ in range(10):
                try:
                    Path(filename).unlink(missing_ok=True)
                    break
                except PermissionError:
                    await asyncio.sleep(0.25)
                except OSError:
                    break

    @app_commands.command(name="speech-stop", description="停止 LogRelay 正在唸的語音（主人專用）")
    async def speech_stop(self, interaction: discord.Interaction):
        if not await self.owner_check(interaction):
            return
        guild = interaction.guild
        voice_client = guild.voice_client
        if guild.id not in self.active_guilds or not voice_client or not voice_client.is_playing():
            await interaction.response.send_message("我現在沒有正在唸的內容。", ephemeral=True)
            return
        voice_client.stop()
        await self._cleanup(guild.id, self.temp_files.get(guild.id))
        await interaction.response.send_message("好，停止唸了。", ephemeral=True)

    @app_commands.command(name="speech-leave", description="讓 LogRelay 離開語音頻道（主人專用；會停止目前音訊）")
    async def speech_leave(self, interaction: discord.Interaction):
        if not await self.owner_check(interaction):
            return
        guild = interaction.guild
        voice_client = guild.voice_client
        if not voice_client or not voice_client.is_connected():
            await interaction.response.send_message("我目前沒有在語音頻道裡。", ephemeral=True)
            return
        music_cog = self.bot.get_cog("MusicCog")
        player = getattr(music_cog, "players", {}).get(guild.id) if music_cog else None
        if player and (player.current or player.queue):
            await interaction.response.send_message("這個伺服器正在使用音樂播放。為避免把音樂一起中斷，請先停止音樂再讓我離開。", ephemeral=True)
            return
        if voice_client.is_playing() or voice_client.is_paused():
            voice_client.stop()
        await voice_client.disconnect(force=True)
        await self._cleanup(guild.id, self.temp_files.get(guild.id))
        await interaction.response.send_message("好，我離開語音頻道了。", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(SpeechControlCog(bot))
