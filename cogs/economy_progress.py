from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from cogs.public_response import PublicizeView

DB_PATH = Path(__file__).resolve().parent.parent / "economy.db"
UTC = timezone.utc

ACHIEVEMENTS = {
    "first_deposit": ("初次存款", "銀行存款達到 1,000 金幣", 100),
    "millionaire": ("百萬富翁", "現金與存款合計達到 1,000,000 金幣", 250),
    "collector": ("收藏家", "累計持有至少 10 件商店或市場商品", 150),
    "market_first": ("市場新手", "完成第一筆市場買賣", 100),
    "market_veteran": ("市場老手", "累計完成 10 筆市場買賣", 300),
    "entrepreneur": ("企業家", "擁有一家企業", 250),
    "creditworthy": ("信用良好", "成功清償至少一筆貸款", 200),
}


def _is_infinite(value) -> bool:
    return str(value).strip().lower() == "inf"


def _number(value) -> int:
    return 0 if _is_infinite(value) else int(value or 0)


def _money(value) -> str:
    return "∞（無限）" if _is_infinite(value) else f"{int(value):,}"


def _wealth_key(row):
    wallet = str(row["wallet"] or "0")
    bank = str(row["bank"] or "0")
    infinite = _is_infinite(wallet) or _is_infinite(bank)
    total = (0 if _is_infinite(wallet) else int(wallet)) + (0 if _is_infinite(bank) else int(bank))
    return (1 if infinite else 0, total, len(str(total)), str(total))


class EconomyProgressStore:
    def __init__(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS economy_achievement_unlocks (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    achievement_key TEXT NOT NULL,
                    unlocked_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, user_id, achievement_key)
                )
            """)

    def connect(self):
        con = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=10000")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def stats(self, guild_id: int, user_id: int):
        with closing(self.connect()) as con:
            user = con.execute(
                "SELECT wallet,bank FROM users WHERE guild_id=? AND user_id=?",
                (guild_id, user_id),
            ).fetchone()
            wallet = str(user["wallet"]) if user else "0"
            bank = str(user["bank"]) if user else "0"
            fixed_items = con.execute(
                "SELECT COALESCE(SUM(quantity),0) FROM economy_inventory WHERE guild_id=? AND user_id=?",
                (guild_id, user_id),
            ).fetchone()[0]
            market_items = con.execute(
                "SELECT COALESCE(SUM(quantity),0) FROM world_inventory WHERE guild_id=? AND user_id=?",
                (guild_id, user_id),
            ).fetchone()[0]
            trades = con.execute(
                "SELECT COUNT(*) FROM world_ledger WHERE guild_id=? AND user_id=? AND kind IN ('market_buy','market_sell')",
                (guild_id, user_id),
            ).fetchone()[0]
            company = con.execute(
                "SELECT 1 FROM world_companies WHERE guild_id=? AND owner_id=? LIMIT 1",
                (guild_id, user_id),
            ).fetchone()
            paid_loan = con.execute(
                "SELECT 1 FROM world_loans WHERE guild_id=? AND user_id=? AND status='paid' LIMIT 1",
                (guild_id, user_id),
            ).fetchone()
        wealth = None if _is_infinite(wallet) or _is_infinite(bank) else int(wallet) + int(bank)
        return {
            "wallet": wallet,
            "bank": bank,
            "wealth": wealth,
            "items": int(fixed_items or 0) + int(market_items or 0),
            "trades": int(trades or 0),
            "company": bool(company),
            "paid_loan": bool(paid_loan),
        }

    def evaluate(self, guild_id: int, user_id: int):
        stats = self.stats(guild_id, user_id)
        conditions = {
            "first_deposit": _is_infinite(stats["bank"]) or _number(stats["bank"]) >= 1000,
            "millionaire": stats["wealth"] is None or stats["wealth"] >= 1_000_000,
            "collector": stats["items"] >= 10,
            "market_first": stats["trades"] >= 1,
            "market_veteran": stats["trades"] >= 10,
            "entrepreneur": stats["company"],
            "creditworthy": stats["paid_loan"],
        }
        newly_unlocked = []
        now = datetime.now(UTC).isoformat()
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            for key, met in conditions.items():
                if not met:
                    continue
                cursor = con.execute(
                    "INSERT OR IGNORE INTO economy_achievement_unlocks(guild_id,user_id,achievement_key,unlocked_at) VALUES(?,?,?,?)",
                    (guild_id, user_id, key, now),
                )
                if cursor.rowcount:
                    newly_unlocked.append(key)
            rows = con.execute(
                "SELECT achievement_key,unlocked_at FROM economy_achievement_unlocks WHERE guild_id=? AND user_id=?",
                (guild_id, user_id),
            ).fetchall()
            con.commit()
        unlocked = {row["achievement_key"]: row["unlocked_at"] for row in rows}
        return stats, conditions, unlocked, newly_unlocked

    def leaderboard(self, guild_id: int, category: str):
        with closing(self.connect()) as con:
            rows = con.execute(
                "SELECT user_id,wallet,bank FROM users WHERE guild_id=?",
                (guild_id,),
            ).fetchall()
        if category == "wallet":
            key = lambda row: (1 if _is_infinite(row["wallet"]) else 0, _number(row["wallet"]), len(str(_number(row["wallet"]))), str(_number(row["wallet"])))
        elif category == "bank":
            key = lambda row: (1 if _is_infinite(row["bank"]) else 0, _number(row["bank"]), len(str(_number(row["bank"]))), str(_number(row["bank"])))
        else:
            key = _wealth_key
        return sorted(rows, key=key, reverse=True)[:10]


class EconomyProgressCog(commands.GroupCog, group_name=app_commands.locale_str("economy-progress", zh_TW="經濟進度"), group_description="經濟排行榜與成就"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = EconomyProgressStore()

    async def _guild_only(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id:
            return True
        await interaction.response.send_message(
            "這個經濟功能只能在伺服器使用。",
            ephemeral=True,
            view=PublicizeView(interaction.user.id, "這個經濟功能只能在伺服器使用。"),
        )
        return False

    @app_commands.command(name=app_commands.locale_str("leaderboard", zh_TW="排行榜"), description="查看伺服器經濟排行榜")
    @app_commands.choices(category=[
        app_commands.Choice(name="總資產", value="wealth"),
        app_commands.Choice(name="現金", value="wallet"),
        app_commands.Choice(name="銀行存款", value="bank"),
    ])
    @app_commands.rename(category=app_commands.locale_str("類別", zh_TW="類別"), public=app_commands.locale_str("公開", zh_TW="公開"))
    @app_commands.describe(public=app_commands.locale_str("是否公開顯示回覆", zh_TW="是否公開顯示回覆"))
    async def leaderboard(self, interaction: discord.Interaction, category: app_commands.Choice[str] = None, public: bool = False):
        if not await self._guild_only(interaction):
            return
        selected = category.value if category else "wealth"
        rows = self.store.leaderboard(interaction.guild_id, selected)
        title = {"wealth": "總資產排行榜", "wallet": "現金排行榜", "bank": "銀行存款排行榜"}[selected]
        if not rows:
            msg = "目前還沒有經濟資料，先使用經濟指令開始遊玩吧。"
            await interaction.response.send_message(msg, ephemeral=not public, view=None if public else PublicizeView(interaction.user.id, msg))
            return
        lines = []
        for index, row in enumerate(rows, 1):
            member = interaction.guild.get_member(int(row["user_id"]))
            label = member.mention if member else f"<@{int(row['user_id'])}>"
            if selected == "wallet":
                amount = _money(row["wallet"])
            elif selected == "bank":
                amount = _money(row["bank"])
            elif _is_infinite(row["wallet"]) or _is_infinite(row["bank"]):
                amount = "∞（無限）"
            else:
                amount = f"{int(row['wallet']) + int(row['bank']):,}"
            lines.append(f"**{index}.** {label} — **{amount}**")
        embed = discord.Embed(title=title, description="\n".join(lines), color=0xF1C40F, timestamp=datetime.now(UTC))
        embed.set_footer(text="僅計算本伺服器資料；總資產 = 現金 + 銀行存款")
        await interaction.response.send_message(embed=embed, ephemeral=not public, view=None if public else PublicizeView(interaction.user.id, None, embed))

    @app_commands.command(name=app_commands.locale_str("achievements", zh_TW="成就"), description="查看已解鎖成就與目前進度")
    @app_commands.describe(public=app_commands.locale_str("是否公開顯示回覆", zh_TW="是否公開顯示回覆"))
    @app_commands.rename(public=app_commands.locale_str("公開", zh_TW="公開"))
    async def achievements(self, interaction: discord.Interaction, public: bool = False):
        if not await self._guild_only(interaction):
            return
        stats, conditions, unlocked, newly = self.store.evaluate(interaction.guild_id, interaction.user.id)
        lines = []
        for key, (name, description, reward) in ACHIEVEMENTS.items():
            if key in unlocked:
                status = "🏆 已解鎖"
                detail = f"{description} · 解鎖時間 <t:{int(datetime.fromisoformat(unlocked[key]).timestamp())}:d>"
            else:
                status = "🔒 尚未解鎖"
                if key == "first_deposit":
                    progress = f"{_money(stats['bank'])} / 1,000 存款"
                elif key == "millionaire":
                    progress = "已達標" if stats["wealth"] is None else f"{_money(stats['wealth'])} / 1,000,000 總資產"
                elif key == "collector":
                    progress = f"{stats['items']:,} / 10 件商品"
                elif key == "market_first":
                    progress = f"{stats['trades']:,} / 1 筆市場交易"
                elif key == "market_veteran":
                    progress = f"{stats['trades']:,} / 10 筆市場交易"
                elif key == "entrepreneur":
                    progress = "尚未成立企業"
                else:
                    progress = "尚未清償貸款"
                detail = f"{description} · 進度：{progress}"
            lines.append(f"**{name}** — {status}\n{detail}\n獎勵：{reward:,} 成就點")
        embed = discord.Embed(title="個人成就", description="\n\n".join(lines), color=0x9B59B6)
        total_points = sum(ACHIEVEMENTS[key][2] for key in unlocked if key in ACHIEVEMENTS)
        embed.add_field(name="成就點數", value=f"**{total_points:,}** 點", inline=False)
        embed.set_footer(text=f"已解鎖 {len(unlocked)}/{len(ACHIEVEMENTS)} 項；成就點為遊戲紀錄，不會自動發放金幣")
        if newly:
            embed.add_field(name="新成就！", value="、".join(ACHIEVEMENTS[key][0] for key in newly), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=not public, view=None if public else PublicizeView(interaction.user.id, None, embed))


async def setup(bot: commands.Bot):
    await bot.add_cog(EconomyProgressCog(bot))
