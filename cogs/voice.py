import asyncio
import time

import discord
from discord import app_commands
from discord.ext import commands

import storage


class VoiceCog(commands.Cog):
    voice = app_commands.Group(name="語音控制", description="語音頻道小工具與臨時語音")

    def __init__(self, bot):
        self.bot = bot
        self.joined_at: dict[tuple[int,int], float] = {}
        self.temp_channels: set[int] = set()

    async def _delete_temp_channel(self, channel, *, reason: str) -> bool:
        """Delete a temporary channel before removing its persistent tracking record."""
        try:
            await channel.delete(reason=reason)
        except discord.NotFound:
            
            pass
        except (discord.Forbidden, discord.HTTPException):
            
            return False

        storage.remove_temp_channel(channel.id)
        self.temp_channels.discard(channel.id)
        return True

    @voice.command(name="臨時語音", description="在你目前的語音位置建立一個臨時語音頻道")
    @app_commands.describe(name="頻道名稱")
    async def temp(self, interaction, name: str = "臨時語音"):
        if not interaction.guild or not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("你要先進語音，我才知道要在哪裡建。", ephemeral=True)
            return
        source = interaction.user.voice.channel
        category = getattr(source, "category", None)
        channel = None
        await interaction.response.defer(thinking=True)
        try:
            channel = await interaction.guild.create_voice_channel(
                name=name[:100],
                category=category,
                reason=f"臨時語音 by {interaction.user}",
            )
            self.temp_channels.add(channel.id)
            storage.add_temp_channel(interaction.guild.id, channel.id)
            await interaction.user.move_to(channel)
            await interaction.followup.send(f"好了，幫你開了 {channel.mention}。沒人了我會幫你清掉。")
        except discord.Forbidden:
            if channel:
                await self._delete_temp_channel(channel, reason="臨時語音建立未完成")
            await interaction.followup.send("我沒有建立或移動語音頻道的權限。", ephemeral=True)
        except discord.HTTPException:
            if channel:
                storage.remove_temp_channel(channel.id)
                self.temp_channels.discard(channel.id)
                try:
                    await channel.delete(reason="臨時語音建立未完成")
                except discord.HTTPException:
                    pass
            await interaction.followup.send("Discord 這次建立語音頻道失敗了，再試一次。", ephemeral=True)
    @voice.command(name="加入語音", description="讓 Bot 加入指定的語音頻道")
    @app_commands.describe(channel="要讓 Bot 加入的語音頻道")
    async def join(self, interaction: discord.Interaction, channel: discord.VoiceChannel):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("請在伺服器內使用這個指令。", ephemeral=True)
            return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("這個指令需要「管理伺服器」權限。", ephemeral=True)
            return
        if channel.guild.id != interaction.guild.id:
            await interaction.response.send_message("只能選擇目前伺服器的語音頻道。", ephemeral=True)
            return

        bot_member = interaction.guild.me
        permissions = channel.permissions_for(bot_member)
        if not permissions.view_channel or not permissions.connect:
            await interaction.response.send_message(
                f"我沒有查看或連線到 {channel.mention} 的權限，請檢查頻道權限。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            voice_client = interaction.guild.voice_client
            if voice_client and voice_client.is_connected():
                if voice_client.channel.id == channel.id:
                    await interaction.followup.send(f"我已經在 {channel.mention} 裡了。", ephemeral=True)
                    return
                await voice_client.move_to(channel)
            else:
                await channel.connect()

            await interaction.followup.send(f"已加入語音頻道：{channel.mention}", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("Discord 拒絕了連線，請確認我有「查看頻道」和「連線」權限。", ephemeral=True)
        except discord.ClientException as exc:
            await interaction.followup.send(f"目前無法加入語音：{str(exc)[:300]}", ephemeral=True)
        except discord.HTTPException:
            await interaction.followup.send("Discord 連線失敗，請稍後再試。", ephemeral=True)

    @voice.command(name="語音通知", description="設定誰進出語音時要在哪個文字頻道通知")
    @app_commands.describe(channel="通知頻道")
    async def notice(self, interaction, channel: discord.TextChannel):
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("這個設定要管理伺服器權限。", ephemeral=True)
            return
        storage.set_setting(interaction.guild.id, "voice_notice_channel_id", channel.id)
        await interaction.response.send_message(f"好，以後語音進出會通知到 {channel.mention}。", ephemeral=True)

    @voice.command(name="關閉語音通知", description="關閉語音進出通知")
    async def noticeoff(self, interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("這個設定要管理伺服器權限。", ephemeral=True)
            return
        storage.set_setting(interaction.guild.id, "voice_notice_channel_id", 0)
        await interaction.response.send_message("語音通知關掉了。", ephemeral=True)

    @voice.command(name="語音統計", description="看看自己累積待在語音多久")
    async def stats(self, interaction):
        if not interaction.guild:
            await interaction.response.send_message("這個要在伺服器裡用。", ephemeral=True)
            return
        data = storage.get_profile(interaction.guild.id, interaction.user.id)
        total = int(data["voice_seconds"])
        h, rem = divmod(total, 3600)
        m = rem // 60
        await interaction.response.send_message(f"你總共待過語音 **{h} 小時 {m} 分**。")

    @commands.Cog.listener()
    async def on_ready(self):
        now = time.time()

        
        for row in storage.list_voice_sessions():
            guild = self.bot.get_guild(row["guild_id"])
            member = guild.get_member(row["user_id"]) if guild else None
            if not member or not member.voice or not member.voice.channel:
                elapsed = max(0, now - row["started_at"])
                if elapsed:
                    storage.add_voice_seconds(row["guild_id"], row["user_id"], elapsed)
                storage.finish_voice_session(row["guild_id"], row["user_id"])

        self.joined_at.clear()
        self.temp_channels.clear()

        for guild in self.bot.guilds:
            for voice_channel in guild.voice_channels:
                for member in voice_channel.members:
                    if member.bot:
                        continue
                    session = storage.get_voice_session(guild.id, member.id)
                    if session:
                        self.joined_at[(guild.id, member.id)] = session["started_at"]
                        if session["channel_id"] != voice_channel.id:
                            storage.start_voice_session(
                                guild.id,
                                member.id,
                                voice_channel.id,
                                session["started_at"],
                            )
                    else:
                        started_at = now
                        storage.start_voice_session(
                            guild.id,
                            member.id,
                            voice_channel.id,
                            started_at,
                        )
                        self.joined_at[(guild.id, member.id)] = started_at

        for row in storage.list_temp_channels():
            channel = self.bot.get_channel(row["channel_id"])
            if not channel:
                storage.remove_temp_channel(row["channel_id"])
                continue
            self.temp_channels.add(channel.id)
            if not channel.members:
                await self._delete_temp_channel(
                    channel,
                    reason="臨時語音無人自動刪除",
                )

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot or not member.guild:
            return
        key = (member.guild.id, member.id)
        if before.channel is None and after.channel is not None:
            started_at = time.time()
            self.joined_at[key] = started_at
            storage.start_voice_session(member.guild.id, member.id, after.channel.id, started_at)
            channel_id = storage.get_setting(member.guild.id, "voice_notice_channel_id")
            channel = member.guild.get_channel(int(channel_id)) if channel_id else None
            if channel:
                try:
                    await channel.send(f"🎤 {member.mention} 進了 {after.channel.mention}")
                except Exception:
                    pass
        elif before.channel is not None and after.channel is None:
            session = storage.finish_voice_session(member.guild.id, member.id)
            started = session["started_at"] if session else self.joined_at.pop(key, None)
            self.joined_at.pop(key, None)
            if started:
                storage.add_voice_seconds(member.guild.id, member.id, time.time() - started)
            channel_id = storage.get_setting(member.guild.id, "voice_notice_channel_id")
            channel = member.guild.get_channel(int(channel_id)) if channel_id else None
            if channel:
                try:
                    await channel.send(f"👋 {member.mention} 離開 {before.channel.mention}")
                except Exception:
                    pass
        elif before.channel != after.channel and after.channel is not None:
            session = storage.finish_voice_session(member.guild.id, member.id)
            started = session["started_at"] if session else self.joined_at.get(key)
            if started:
                storage.add_voice_seconds(member.guild.id, member.id, time.time() - started)
            started_at = time.time()
            self.joined_at[key] = started_at
            storage.start_voice_session(member.guild.id, member.id, after.channel.id, started_at)

        for ch in [before.channel, after.channel]:
            if ch and ch.id in self.temp_channels and len(ch.members) == 0:
                await self._delete_temp_channel(
                    ch,
                    reason="臨時語音無人自動刪除",
                )


async def setup(bot):
    await bot.add_cog(VoiceCog(bot))
