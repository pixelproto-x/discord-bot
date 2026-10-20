import asyncio
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

import storage


def fmt_seconds(seconds: int) -> str:
    seconds = max(0, int(seconds))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, _ = divmod(seconds, 60)
    if days:
        return f"{days} 天 {hours} 小時"
    if hours:
        return f"{hours} 小時 {minutes} 分"
    return f"{minutes} 分"


class ProfileCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        result = storage.add_message_xp(message.guild.id, message.author.id)
        if result["level"] > result["old_level"]:
            try:
                await message.channel.send(f"{message.author.mention} 升到 **Lv.{result['level']}** 了，恭喜啦。🎉")
            except Exception:
                pass

    @app_commands.command(name=app_commands.locale_str("profile", zh_TW="個人檔案"), description=app_commands.locale_str("看看自己的等級、聊天、語音和遊戲資料", zh_TW="看看自己的等級、聊天、語音和遊戲資料"))
    @app_commands.describe(member="不填就是看自己")
    async def profile(self, interaction: discord.Interaction, member: discord.Member | None = None):
        if not interaction.guild:
            await interaction.response.send_message("這個要在伺服器裡用。", ephemeral=True)
            return
        target = member or interaction.user
        data = storage.get_profile(interaction.guild.id, target.id)
        next_xp = data["level"] * 500
        embed = discord.Embed(title=f"👤 {target.display_name}", color=0x5865F2)
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="等級", value=f"Lv.{data['level']}", inline=True)
        embed.add_field(name="XP", value=f"{data['xp']} / {next_xp}", inline=True)
        embed.add_field(name="聊天", value=f"{data['msg_count']} 則", inline=True)
        embed.add_field(name="語音", value=fmt_seconds(data["voice_seconds"]), inline=True)
        embed.add_field(name="遊戲", value=f"{data['games_won']} 勝 / {data['games_played']} 場", inline=True)
        achievements = []
        if data["msg_count"] >= 1: achievements.append("初次聊天")
        if data["msg_count"] >= 100: achievements.append("聊天破百")
        if data["msg_count"] >= 1000: achievements.append("聊天室常客")
        if data["games_won"] >= 1: achievements.append("第一次勝利")
        if data["games_won"] >= 10: achievements.append("遊戲常勝軍")
        if data["voice_seconds"] >= 36000: achievements.append("語音 10 小時")
        embed.add_field(name="成就", value="、".join(achievements[-6:]) if achievements else "還沒解鎖，再玩玩看。", inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name=app_commands.locale_str("leaderboard", zh_TW="排行榜"), description=app_commands.locale_str("看看伺服器目前的各種排行榜", zh_TW="看看伺服器目前的各種排行榜"))
    @app_commands.describe(kind="排行類型")
    @app_commands.choices(kind=[
        app_commands.Choice(name="XP", value="xp"),
        app_commands.Choice(name="聊天", value="msg_count"),
        app_commands.Choice(name="語音", value="voice_seconds"),
        app_commands.Choice(name="遊戲勝場", value="games_won"),
        app_commands.Choice(name="遊戲場次", value="games_played"),
    ])
    async def leaderboard(self, interaction, kind: app_commands.Choice[str] | None = None):
        if not interaction.guild:
            await interaction.response.send_message("這個要在伺服器裡用。", ephemeral=True)
            return
        field = kind.value if kind else "xp"
        rows = storage.leaderboard(interaction.guild.id, field, 10)
        if not rows:
            await interaction.response.send_message("現在還沒什麼資料可以排。", ephemeral=True)
            return
        lines = []
        for index, row in enumerate(rows, 1):
            member = interaction.guild.get_member(row["user_id"])
            name = member.display_name if member else f"User {row['user_id']}"
            value = row[field]
            if field == "voice_seconds":
                value = fmt_seconds(value)
            lines.append(f"**{index}.** {name} — {value}")
        title = {"xp":"XP 排行", "msg_count":"聊天排行", "voice_seconds":"語音排行", "games_won":"遊戲勝場排行", "games_played":"遊戲場次排行"}[field]
        await interaction.response.send_message("🏆 **" + title + "**\n" + "\n".join(lines))

    @app_commands.command(name=app_commands.locale_str("userinfo", zh_TW="成員資訊"), description=app_commands.locale_str("查看一位成員的基本 Discord 資訊", zh_TW="查看一位成員的基本 Discord 資訊"))
    async def userinfo(self, interaction, member: discord.Member | None = None):
        if not interaction.guild:
            await interaction.response.send_message("成員資訊只能在伺服器裡查看。", ephemeral=True)
            return
        target = member or interaction.user
        embed = discord.Embed(title=f"👤 {target}", color=0x5865F2)
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="ID", value=str(target.id), inline=False)
        embed.add_field(name="加入伺服器", value=discord.utils.format_dt(target.joined_at, "R") if target.joined_at else "不知道", inline=True)
        roles = [r.mention for r in target.roles[1:]][-10:]
        embed.add_field(name="身分組", value=" ".join(roles) if roles else "沒有額外身分組", inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name=app_commands.locale_str("serverinfo", zh_TW="伺服器資訊"), description=app_commands.locale_str("看看這個伺服器的基本資訊", zh_TW="看看這個伺服器的基本資訊"))
    async def serverinfo(self, interaction):
        if not interaction.guild:
            await interaction.response.send_message("這個要在伺服器裡用。", ephemeral=True)
            return
        guild = interaction.guild
        embed = discord.Embed(title=f"🏠 {guild.name}", color=0x5865F2)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="成員", value=str(guild.member_count or len(guild.members)), inline=True)
        embed.add_field(name="文字頻道", value=str(len(guild.text_channels)), inline=True)
        embed.add_field(name="語音頻道", value=str(len(guild.voice_channels)), inline=True)
        embed.add_field(name="身分組", value=str(len(guild.roles)), inline=True)
        embed.add_field(name="建立時間", value=discord.utils.format_dt(guild.created_at, "D"), inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name=app_commands.locale_str("avatar", zh_TW="查看頭像"), description=app_commands.locale_str("查看自己或別人的大圖頭像", zh_TW="查看自己或別人的大圖頭像"))
    async def avatar(self, interaction, member: discord.Member | None = None):
        target = member or interaction.user
        embed = discord.Embed(title=f"{target.display_name} 的頭像", color=0x5865F2)
        embed.set_image(url=target.display_avatar.with_size(1024).url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name=app_commands.locale_str("banner", zh_TW="查看橫幅"), description=app_commands.locale_str("查看成員的 Discord Banner", zh_TW="查看成員的 Discord Banner"))
    async def banner(self, interaction, member: discord.Member | None = None):
        target = member or interaction.user
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            user = await self.bot.fetch_user(target.id)
            if not user.banner:
                await interaction.followup.send("這個人目前沒有公開的 Banner。", ephemeral=True)
                return
            embed = discord.Embed(title=f"{target.display_name} 的 Banner", color=0x5865F2)
            embed.set_image(url=user.banner.with_size(1024).url)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as exc:
            print(f"Banner fetch error: {exc}")
            await interaction.followup.send("Banner 這次沒抓到，再試一次。", ephemeral=True)
    @app_commands.command(name=app_commands.locale_str("achievements", zh_TW="成就"), description=app_commands.locale_str("看看自己目前解鎖了哪些成就", zh_TW="看看自己目前解鎖了哪些成就"))
    async def achievements(self, interaction):
        if not interaction.guild:
            await interaction.response.send_message("這個要在伺服器裡用。", ephemeral=True)
            return
        data = storage.get_profile(interaction.guild.id, interaction.user.id)
        items = [
            (data["msg_count"] >= 1, "💬 第一次聊天"),
            (data["msg_count"] >= 100, "💬 聊天破百"),
            (data["msg_count"] >= 1000, "💬 聊天室常客"),
            (data["games_won"] >= 1, "🎮 第一次勝利"),
            (data["games_won"] >= 10, "🏆 遊戲常勝軍"),
            (data["voice_seconds"] >= 36000, "🎤 語音 10 小時"),
            (data["level"] >= 10, "⭐ Lv.10"),
            (data["level"] >= 25, "⭐ Lv.25"),
        ]
        unlocked = [label for ok, label in items if ok]
        locked = [label for ok, label in items if not ok]
        text = "已解鎖：\n" + ("\n".join(unlocked) if unlocked else "還沒有，慢慢玩就有了。")
        if locked:
            text += "\n\n還沒解鎖：\n" + "\n".join(locked)
        await interaction.response.send_message(text)

    @app_commands.command(name=app_commands.locale_str("membercount", zh_TW="成員數量"), description=app_commands.locale_str("看看伺服器現在有多少成員", zh_TW="看看伺服器現在有多少成員"))
    async def membercount(self, interaction):
        if not interaction.guild:
            await interaction.response.send_message("這個要在伺服器裡用。", ephemeral=True)
            return
        await interaction.response.send_message(f"現在這個伺服器有 **{interaction.guild.member_count}** 位成員。")


async def setup(bot):
    await bot.add_cog(ProfileCog(bot))
