from __future__ import annotations

import random
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from cogs.public_response import PublicizeView
from cogs.economy import MAX_BALANCE, format_coins, is_infinite

DB_PATH = Path(__file__).resolve().parent.parent / "economy.db"
ADVENTURE_COOLDOWN = 60
CLASS_CHANGE_COOLDOWN = 7 * 86400
CLASS_CHANGE_COST = 10_000
MAX_LEVEL = 1000

CLASSES = {
    "warrior": {
        "name": "戰士",
        "description": "冒險成功率較高，適合穩定累積經驗。",
        "success": 0.82,
        "coin_bonus": 1.10,
        "xp_bonus": 1.00,
    },
    "merchant": {
        "name": "商人",
        "description": "冒險金幣收益提高 30%。",
        "success": 0.74,
        "coin_bonus": 1.30,
        "xp_bonus": 1.00,
    },
    "mage": {
        "name": "法師",
        "description": "冒險經驗值提高 30%，升級更快。",
        "success": 0.74,
        "coin_bonus": 1.00,
        "xp_bonus": 1.30,
    },
    "ranger": {
        "name": "遊俠",
        "description": "金幣與經驗值各提高 10%，均衡成長。",
        "success": 0.78,
        "coin_bonus": 1.10,
        "xp_bonus": 1.10,
    },
}


def _now() -> int:
    return int(time.time())


def xp_required(level: int) -> int:
    return max(100, int(level) * 100)


class EconomyClassStore:
    def __init__(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS economy_rpg_profiles (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    class_key TEXT,
                    level INTEGER NOT NULL DEFAULT 1,
                    xp INTEGER NOT NULL DEFAULT 0,
                    last_adventure_at INTEGER NOT NULL DEFAULT 0,
                    class_changed_at INTEGER NOT NULL DEFAULT 0,
                    adventures INTEGER NOT NULL DEFAULT 0,
                    wins INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(guild_id, user_id)
                )
            """)

    def connect(self):
        con = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=10000")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _ensure_user(self, con, guild_id: int, user_id: int):
        con.execute(
            "INSERT OR IGNORE INTO users(guild_id,user_id,wallet,bank) VALUES(?,?, '1000','0')",
            (guild_id, user_id),
        )
        con.execute(
            "INSERT OR IGNORE INTO economy_rpg_profiles(guild_id,user_id) VALUES(?,?)",
            (guild_id, user_id),
        )

    def profile(self, guild_id: int, user_id: int):
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            self._ensure_user(con, guild_id, user_id)
            row = dict(con.execute(
                "SELECT * FROM economy_rpg_profiles WHERE guild_id=? AND user_id=?",
                (guild_id, user_id),
            ).fetchone())
            con.commit()
            return row

    def choose_class(self, guild_id: int, user_id: int, class_key: str, now: int | None = None):
        if class_key not in CLASSES:
            return False, "找不到這個職業。"
        now = _now() if now is None else int(now)
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            self._ensure_user(con, guild_id, user_id)
            profile = con.execute(
                "SELECT * FROM economy_rpg_profiles WHERE guild_id=? AND user_id=?",
                (guild_id, user_id),
            ).fetchone()
            old_class = profile["class_key"]
            if old_class == class_key:
                con.rollback()
                return False, f"你已經是{CLASSES[class_key]['name']}了。"
            if old_class:
                next_change = int(profile["class_changed_at"]) + CLASS_CHANGE_COOLDOWN
                if now < next_change:
                    remaining = next_change - now
                    hours, rest = divmod(remaining, 3600)
                    minutes = rest // 60
                    con.rollback()
                    return False, f"轉職冷卻中，還要 {hours} 小時 {minutes} 分鐘。"
                user = con.execute("SELECT wallet FROM users WHERE guild_id=? AND user_id=?", (guild_id, user_id)).fetchone()
                wallet = str(user["wallet"])
                if not is_infinite(wallet) and int(wallet) < CLASS_CHANGE_COST:
                    con.rollback()
                    return False, f"轉職需要 {CLASS_CHANGE_COST:,} 金幣，你目前有 {format_coins(wallet)}。"
                if not is_infinite(wallet):
                    con.execute("UPDATE users SET wallet=? WHERE guild_id=? AND user_id=?", (str(int(wallet) - CLASS_CHANGE_COST), guild_id, user_id))
            con.execute(
                "UPDATE economy_rpg_profiles SET class_key=?,class_changed_at=? WHERE guild_id=? AND user_id=?",
                (class_key, now, guild_id, user_id),
            )
            con.commit()
        class_name = CLASSES[class_key]["name"]
        fee = f"\n轉職費用：{CLASS_CHANGE_COST:,} 金幣" if old_class else "\n首次選職業免費"
        return True, f"職業已設定為【{class_name}】。\n{CLASSES[class_key]['description']}{fee}"

    def adventure(self, guild_id: int, user_id: int, now: int | None = None, roll: float | None = None, reward: int | None = None, xp_gain: int | None = None):
        now = _now() if now is None else int(now)
        roll = random.random() if roll is None else float(roll)
        reward = random.randint(80, 220) if reward is None else int(reward)
        xp_gain = random.randint(25, 40) if xp_gain is None else int(xp_gain)
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            self._ensure_user(con, guild_id, user_id)
            profile = con.execute(
                "SELECT * FROM economy_rpg_profiles WHERE guild_id=? AND user_id=?",
                (guild_id, user_id),
            ).fetchone()
            class_key = profile["class_key"]
            if not class_key:
                con.rollback()
                return False, "你還沒有職業，先使用「/選擇職業」選一個職業。"
            remaining = int(profile["last_adventure_at"]) + ADVENTURE_COOLDOWN - now
            if remaining > 0:
                con.rollback()
                return False, f"冒險還在冷卻，請再等 {remaining} 秒。"
            character_class = CLASSES[class_key]
            success = roll < character_class["success"]
            if success:
                coins = max(1, int(reward * character_class["coin_bonus"]))
                earned_xp = max(1, int(xp_gain * character_class["xp_bonus"]))
                result_text = "冒險成功"
            else:
                coins = max(1, int(reward * 0.2))
                earned_xp = max(5, int(xp_gain * 0.5 * character_class["xp_bonus"]))
                result_text = "遭遇小挫折，但仍學到經驗"
            user = con.execute("SELECT wallet FROM users WHERE guild_id=? AND user_id=?", (guild_id, user_id)).fetchone()
            wallet = str(user["wallet"])
            if not is_infinite(wallet) and int(wallet) + coins > MAX_BALANCE:
                coins = max(0, MAX_BALANCE - int(wallet))
            if coins and not is_infinite(wallet):
                con.execute("UPDATE users SET wallet=? WHERE guild_id=? AND user_id=?", (str(int(wallet) + coins), guild_id, user_id))
            level = int(profile["level"])
            xp = int(profile["xp"]) + earned_xp
            levels_gained = 0
            while level < MAX_LEVEL and xp >= xp_required(level):
                xp -= xp_required(level)
                level += 1
                levels_gained += 1
            level_bonus = sum(250 * new_level for new_level in range(level - levels_gained + 1, level + 1))
            if level_bonus and not is_infinite(wallet):
                current_wallet = con.execute("SELECT wallet FROM users WHERE guild_id=? AND user_id=?", (guild_id, user_id)).fetchone()["wallet"]
                available = int(current_wallet)
                level_bonus = min(level_bonus, max(0, MAX_BALANCE - available))
                con.execute("UPDATE users SET wallet=? WHERE guild_id=? AND user_id=?", (str(available + level_bonus), guild_id, user_id))
            con.execute(
                "UPDATE economy_rpg_profiles SET level=?,xp=?,last_adventure_at=?,adventures=adventures+1,wins=wins+? WHERE guild_id=? AND user_id=?",
                (level, xp, now, int(success), guild_id, user_id),
            )
            con.commit()
        text = f"{result_text}！\n職業：{character_class['name']} Lv.{level}\n獲得金幣：{coins:,}\n獲得經驗：{earned_xp} XP"
        if levels_gained:
            text += f"\n升級：Lv.{level - levels_gained} → Lv.{level}\n升級獎勵：{level_bonus:,} 金幣"
        return True, text

    def leaderboard(self, guild_id: int):
        with closing(self.connect()) as con:
            return con.execute(
                "SELECT user_id,class_key,level,xp,adventures,wins FROM economy_rpg_profiles "
                "WHERE guild_id=? AND class_key IS NOT NULL ORDER BY level DESC,xp DESC,wins DESC LIMIT 10",
                (guild_id,),
            ).fetchall()


class EconomyClassesCog(commands.GroupCog, group_name=app_commands.locale_str("rpg", zh_TW="冒險系統"), group_description="角色職業與冒險"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = EconomyClassStore()

    async def _guild_only(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id:
            return True
        await interaction.response.send_message("職業與冒險系統只能在伺服器裡使用。", ephemeral=True)
        return False

    async def _reply(self, interaction: discord.Interaction, content: str, public: bool = False):
        await interaction.response.send_message(
            content,
            ephemeral=not public,
            view=None if public else PublicizeView(interaction.user.id, content=content),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name=app_commands.locale_str("class-info", zh_TW="職業資訊"), description="查看可選職業與專屬加成")
    @app_commands.describe(public="是否公開顯示")
    async def class_info(self, interaction: discord.Interaction, public: bool = False):
        if not await self._guild_only(interaction):
            return
        lines = [
            f"**{item['name']}** — {item['description']}\n冒險成功率：{int(item['success'] * 100)}%"
            for item in CLASSES.values()
        ]
        await self._reply(interaction, "**可選職業**\n\n" + "\n\n".join(lines) + f"\n\n首次選職業免費；之後轉職需 {CLASS_CHANGE_COST:,} 金幣，且有 7 天冷卻。", public)

    @app_commands.command(name=app_commands.locale_str("choose-class", zh_TW="選擇職業"), description="選擇或轉換你的冒險職業")
    @app_commands.choices(class_key=[
        app_commands.Choice(name="戰士", value="warrior"),
        app_commands.Choice(name="商人", value="merchant"),
        app_commands.Choice(name="法師", value="mage"),
        app_commands.Choice(name="遊俠", value="ranger"),
    ])
    @app_commands.rename(class_key=app_commands.locale_str("職業", zh_TW="職業"), public=app_commands.locale_str("公開", zh_TW="公開"))
    @app_commands.describe(public="是否公開顯示")
    async def choose_class(self, interaction: discord.Interaction, class_key: app_commands.Choice[str], public: bool = False):
        if not await self._guild_only(interaction):
            return
        _, message = self.store.choose_class(interaction.guild_id, interaction.user.id, class_key.value)
        await self._reply(interaction, message, public)

    @app_commands.command(name=app_commands.locale_str("adventure", zh_TW="冒險"), description="進行冒險，獲得職業經驗與金幣")
    @app_commands.describe(public="是否公開顯示")
    async def adventure(self, interaction: discord.Interaction, public: bool = False):
        if not await self._guild_only(interaction):
            return
        _, message = self.store.adventure(interaction.guild_id, interaction.user.id)
        await self._reply(interaction, message, public)

    @app_commands.command(name=app_commands.locale_str("character-status", zh_TW="角色狀態"), description="查看冒險等級、職業與經驗進度")
    @app_commands.describe(member="要查看的成員；不填則查看自己", public="是否公開顯示")
    @app_commands.rename(member=app_commands.locale_str("成員", zh_TW="成員"), public=app_commands.locale_str("公開", zh_TW="公開"))
    async def character_status(self, interaction: discord.Interaction, member: discord.Member | None = None, public: bool = False):
        if not await self._guild_only(interaction):
            return
        target = member or interaction.user
        profile = self.store.profile(interaction.guild_id, target.id)
        class_key = profile["class_key"]
        if not class_key:
            message = f"{target.display_name} 還沒有選擇職業，使用「/選擇職業」開始冒險。"
        else:
            current_class = CLASSES[class_key]
            next_xp = xp_required(int(profile["level"]))
            progress = min(100, int(int(profile["xp"]) * 100 / next_xp))
            message = (
                f"**{target.display_name} 的角色狀態**\n"
                f"職業：{current_class['name']}\n"
                f"冒險等級：Lv.{profile['level']}\n"
                f"經驗值：{profile['xp']} / {next_xp} XP（{progress}%）\n"
                f"冒險次數：{profile['adventures']}\n"
                f"成功次數：{profile['wins']}"
            )
        await interaction.response.send_message(
            message,
            ephemeral=not public,
            view=None if public or target.id != interaction.user.id else PublicizeView(interaction.user.id, content=message),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name=app_commands.locale_str("class-leaderboard", zh_TW="職業排行榜"), description="查看本伺服器冒險等級排行榜")
    @app_commands.describe(public="是否公開顯示")
    async def class_leaderboard(self, interaction: discord.Interaction, public: bool = False):
        if not await self._guild_only(interaction):
            return
        rows = self.store.leaderboard(interaction.guild_id)
        if not rows:
            await self._reply(interaction, "目前還沒有冒險紀錄，先選擇職業並使用「/冒險」吧。", public)
            return
        lines = []
        for rank, row in enumerate(rows, 1):
            user_id = int(row["user_id"])
            member = interaction.guild.get_member(user_id)
            if member is None:
                try:
                    member = await interaction.guild.fetch_member(user_id)
                except discord.NotFound:
                    member = None
                except discord.HTTPException:
                    member = None
            name = member.display_name if member else "無法取得名稱的成員"
            lines.append(f"**{rank}.** {name} — {CLASSES[row['class_key']]['name']} Lv.{row['level']}（{row['xp']} XP）")
        await self._reply(interaction, "**冒險等級排行榜**\n" + "\n".join(lines), public)


async def setup(bot: commands.Bot):
    await bot.add_cog(EconomyClassesCog(bot))
