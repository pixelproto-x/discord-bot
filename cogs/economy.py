from __future__ import annotations

import random
import sqlite3
from decimal import Decimal
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

DB_PATH = Path(__file__).resolve().parent.parent / "economy.db"
MAX_DIGITS = 2000
MAX_BALANCE = int("9" * MAX_DIGITS)
UTC = timezone.utc
COOLDOWNS = {"daily": 86400, "work": 60, "fish": 30, "rob": 600}
WORK_LINES = [
    "你去咖啡店打工，忙到差點把自己也端給客人。",
    "你幫鄰居搬家具，最重的那箱竟然裝滿教科書。",
    "你接了份外送工作，導航帶你繞了半座城市。",
    "你在遊戲公司測試 Bug，意外找到一個超大的 Bug。",
    "你去寵物店打工，今天的主管是一隻不肯下班的貓。",
    "你幫忙整理倉庫，意外找到一箱被遺忘的零食。",
]
FISH = [
    (0.10, "垃圾", [("易開罐", 0, 10), ("爛鞋子", 0, 10)]),
    (0.68, "普通魚", [("吳郭魚", 50, 150), ("鱸魚", 50, 150)]),
    (0.19, "稀有大魚", [("鮭魚", 200, 500), ("鮪魚", 200, 500)]),
    (0.03, "傳奇寶箱", [("傳奇寶箱", 800, 2000)]),
]


def now_utc():
    return datetime.now(UTC)


def parse_time(value):
    try:
        result = datetime.fromisoformat(value) if value else None
        return result.replace(tzinfo=UTC) if result and result.tzinfo is None else result
    except (TypeError, ValueError):
        return None


def parse_amount(value):
    raw = str(value).strip()
    if not raw.isascii() or not raw.isdecimal() or not 1 <= len(raw) <= MAX_DIGITS:
        return None
    amount = int(raw)
    return amount if 0 < amount <= MAX_BALANCE else None


def valid_amount(value):
    return type(value) is int and 0 < value <= MAX_BALANCE


def cooldown_text(seconds):
    hours, rest = divmod(max(0, seconds), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours} 小時 {minutes} 分鐘"
    if minutes:
        return f"{minutes} 分鐘 {secs} 秒"
    return f"{secs} 秒"


class EconomyStore:
    def __init__(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    guild_id INTEGER,
                    wallet TEXT NOT NULL DEFAULT '1000',
                    bank TEXT NOT NULL DEFAULT '0',
                    last_daily TEXT, last_work TEXT, last_fish TEXT, last_rob TEXT
                )
            """)
            columns = {row["name"]: row for row in con.execute("PRAGMA table_info(users)")}
            # Existing INTEGER-affinity tables can still store decimal strings, but
            # rebuild to TEXT affinity so all 2000 digits remain exact.
            if columns.get("wallet") and "INT" in str(columns["wallet"]["type"]).upper():
                con.execute("BEGIN IMMEDIATE")
                con.execute("""
                    CREATE TABLE users_bigint (
                        user_id INTEGER PRIMARY KEY,
                        guild_id INTEGER,
                        wallet TEXT NOT NULL DEFAULT '1000',
                        bank TEXT NOT NULL DEFAULT '0',
                        last_daily TEXT, last_work TEXT, last_fish TEXT, last_rob TEXT
                    )
                """)
                con.execute("""
                    INSERT INTO users_bigint
                    SELECT user_id, guild_id, CAST(wallet AS TEXT), CAST(bank AS TEXT),
                           last_daily, last_work, last_fish, last_rob FROM users
                """)
                con.execute("DROP TABLE users")
                con.execute("ALTER TABLE users_bigint RENAME TO users")
                con.commit()

    def connect(self):
        con = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=10000")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def ensure(self, con, guild_id, user_id):
        con.execute("INSERT OR IGNORE INTO users(user_id, guild_id) VALUES(?, ?)", (user_id, guild_id))
        con.execute("UPDATE users SET guild_id=? WHERE user_id=?", (guild_id, user_id))

    def get(self, guild_id, user_id):
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            self.ensure(con, guild_id, user_id)
            row = dict(con.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone())
            con.commit()
            return row

    def claim(self, guild_id, user_id, kind, amount):
        column = {"daily": "last_daily", "work": "last_work", "fish": "last_fish"}[kind]
        now = now_utc()
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            self.ensure(con, guild_id, user_id)
            row = con.execute(f"SELECT {column}, wallet FROM users WHERE user_id=?", (user_id,)).fetchone()
            last = parse_time(row[column])
            if last and now < last + timedelta(seconds=COOLDOWNS[kind]):
                con.rollback()
                return False, max(1, int((last + timedelta(seconds=COOLDOWNS[kind]) - now).total_seconds()))
            wallet = int(row["wallet"])
            if wallet + amount > MAX_BALANCE:
                con.rollback()
                return False, -1
            con.execute(f"UPDATE users SET wallet=?, {column}=? WHERE user_id=?",
                        (str(wallet + amount), now.isoformat(), user_id))
            con.commit()
            return True, amount

    def move(self, guild_id, user_id, amount, deposit):
        source, destination = ("wallet", "bank") if deposit else ("bank", "wallet")
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            self.ensure(con, guild_id, user_id)
            row = con.execute(f"SELECT {source}, {destination} FROM users WHERE user_id=?", (user_id,)).fetchone()
            source_value = int(row[source])
            destination_value = int(row[destination])
            if amount > source_value or destination_value + amount > MAX_BALANCE:
                con.rollback()
                return False
            con.execute(f"UPDATE users SET {source}=?, {destination}=? WHERE user_id=?",
                        (str(source_value - amount), str(destination_value + amount), user_id))
            con.commit()
            return True

    def transfer(self, guild_id, sender, receiver, amount):
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            self.ensure(con, guild_id, sender)
            self.ensure(con, guild_id, receiver)
            a = int(con.execute("SELECT wallet FROM users WHERE user_id=?", (sender,)).fetchone()["wallet"])
            b = int(con.execute("SELECT wallet FROM users WHERE user_id=?", (receiver,)).fetchone()["wallet"])
            if a < amount or b + amount > MAX_BALANCE:
                con.rollback()
                return False
            con.execute("UPDATE users SET wallet=? WHERE user_id=?", (str(a - amount), sender))
            con.execute("UPDATE users SET wallet=? WHERE user_id=?", (str(b + amount), receiver))
            con.commit()
            return True

    def dice(self, guild_id, user_id, amount, payout):
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            self.ensure(con, guild_id, user_id)
            row = con.execute("SELECT wallet FROM users WHERE user_id=?", (user_id,)).fetchone()
            wallet = int(row["wallet"])
            if wallet < amount or wallet - amount + payout > MAX_BALANCE:
                con.rollback()
                return False
            con.execute("UPDATE users SET wallet=? WHERE user_id=?", (str(wallet - amount + payout), user_id))
            con.commit()
            return True

    def rob(self, guild_id, robber_id, target_id, success, fraction, fine_fraction):
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            self.ensure(con, guild_id, robber_id)
            self.ensure(con, guild_id, target_id)
            robber = con.execute("SELECT wallet,last_rob FROM users WHERE user_id=?", (robber_id,)).fetchone()
            target = con.execute("SELECT wallet FROM users WHERE user_id=?", (target_id,)).fetchone()
            now = now_utc()
            last = parse_time(robber["last_rob"])
            if last and now < last + timedelta(seconds=COOLDOWNS["rob"]):
                con.rollback()
                return "cooldown", max(1, int((last + timedelta(seconds=COOLDOWNS["rob"]) - now).total_seconds()))
            if success:
                stolen = int(Decimal(target["wallet"]) * Decimal(str(fraction)))
                if robber["wallet"] + stolen > MAX_BALANCE:
                    con.rollback()
                    return "cap", 0
                con.execute("UPDATE users SET wallet=? WHERE user_id=?", (str(target["wallet"] - stolen), target_id))
                con.execute("UPDATE users SET wallet=? WHERE user_id=?", (str(robber["wallet"] + stolen), robber_id))
                result = ("success", stolen)
            else:
                fine = min(robber["wallet"], max(1, int(Decimal(robber["wallet"]) * Decimal(str(fine_fraction))))) if robber["wallet"] else 0
                con.execute("UPDATE users SET wallet=? WHERE user_id=?", (str(robber["wallet"] - fine), robber_id))
                result = ("failed", fine)
            if success:
                con.execute("UPDATE users SET last_rob=? WHERE user_id=?", (now.isoformat(), robber_id))
            con.commit()
            return result


class EconomyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.store = EconomyStore()

    async def _guild(self, target):
        if target.guild is not None:
            return True
        msg = "經濟指令只能在伺服器中使用。"
        if isinstance(target, discord.Interaction):
            await target.response.send_message(msg, ephemeral=False)
        else:
            await target.reply(msg, mention_author=False)
        return False

    async def _reply(self, target, content, *, embed=None, public=True):
        if isinstance(target, discord.Interaction):
            if target.response.is_done():
                await target.followup.send(content, embed=embed, ephemeral=not public)
            else:
                await target.response.send_message(content, embed=embed, ephemeral=not public)
        else:
            await target.reply(content, embed=embed, mention_author=False)

    async def _wallet(self, target, member=None, public=True):
        if not await self._guild(target):
            return
        user = member or (target.user if isinstance(target, discord.Interaction) else target.author)
        row = self.store.get(target.guild.id, user.id)
        embed = discord.Embed(title=f"💰 {user.display_name} 的錢包", color=0xF1C40F)
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="現金", value=f"{row['wallet']:,} 金幣")
        embed.add_field(name="銀行存款", value=f"{row['bank']:,} 金幣")
        embed.add_field(name="總資產", value=f"{row['wallet'] + row['bank']:,} 金幣", inline=False)
        await self._reply(target, "", embed=embed, public=public)

    @app_commands.command(name=app_commands.locale_str("wallet", zh_TW="錢包"), description="查看成員的現金與銀行存款")
    @app_commands.describe(member="不填則查看自己")
    @app_commands.describe(public=app_commands.locale_str("是否公開顯示回覆", zh_TW="是否公開顯示回覆"))
    @app_commands.rename(public=app_commands.locale_str("公開", zh_TW="公開"))
    async def wallet_slash(self, interaction: discord.Interaction, member: discord.Member | None = None, public: bool = True):
        await self._wallet(interaction, member, public)

    @commands.command(name="錢包")
    async def wallet_prefix(self, ctx, member: discord.Member | None = None):
        await self._wallet(ctx, member)

    async def _earn(self, target, kind, public=True):
        if not await self._guild(target):
            return
        user = target.user if isinstance(target, discord.Interaction) else target.author
        if kind == "daily":
            amount, line = random.randint(500, 1500), "你完成今日簽到"
            title = "每日簽到"
        elif kind == "work":
            amount, line = random.randint(100, 400), random.choice(WORK_LINES)
            title = "打工完成"
        else:
            roll = random.random()
            cumulative = 0
            chosen = FISH[-1][2][0]
            for chance, _, items in FISH:
                cumulative += chance
                if roll < cumulative:
                    chosen = random.choice(items)
                    break
            name, low, high = chosen
            amount, line, title = random.randint(low, high), f"你釣到了 **{name}**", "釣魚結果"
        ok, detail = self.store.claim(target.guild.id, user.id, kind, amount)
        if not ok:
            message = "錢包已達系統上限，無法增加金幣。" if detail == -1 else f"冷卻中，還要等 **{cooldown_text(detail)}**。"
            await self._reply(target, message, public=public)
            return
        await self._reply(target, f"**{title}**\n{line}，獲得 **{amount:,}** 金幣。", public=public)

    @app_commands.command(name=app_commands.locale_str("daily", zh_TW="簽到"), description="每日簽到領取 500 至 1500 金幣")
    @app_commands.describe(public=app_commands.locale_str("是否公開顯示回覆", zh_TW="是否公開顯示回覆"))
    @app_commands.rename(public=app_commands.locale_str("公開", zh_TW="公開"))
    async def daily_slash(self, interaction: discord.Interaction, public: bool = True):
        await self._earn(interaction, "daily", public)

    @commands.command(name="簽到")
    async def daily_prefix(self, ctx):
        await self._earn(ctx, "daily")

    @app_commands.command(name=app_commands.locale_str("work", zh_TW="打工"), description="打工賺取 100 至 400 金幣")
    @app_commands.describe(public=app_commands.locale_str("是否公開顯示回覆", zh_TW="是否公開顯示回覆"))
    @app_commands.rename(public=app_commands.locale_str("公開", zh_TW="公開"))
    async def work_slash(self, interaction: discord.Interaction, public: bool = True):
        await self._earn(interaction, "work", public)

    @commands.command(name="打工")
    async def work_prefix(self, ctx):
        await self._earn(ctx, "work")

    @app_commands.command(name=app_commands.locale_str("fish", zh_TW="釣魚"), description="釣魚取得隨機金幣")
    @app_commands.describe(public=app_commands.locale_str("是否公開顯示回覆", zh_TW="是否公開顯示回覆"))
    @app_commands.rename(public=app_commands.locale_str("公開", zh_TW="公開"))
    async def fish_slash(self, interaction: discord.Interaction, public: bool = True):
        await self._earn(interaction, "fish", public)

    @commands.command(name="釣魚")
    async def fish_prefix(self, ctx):
        await self._earn(ctx, "fish")

    async def _bank(self, target, raw, deposit, public=True):
        if not await self._guild(target):
            return
        user = target.user if isinstance(target, discord.Interaction) else target.author
        row = self.store.get(target.guild.id, user.id)
        available = row["wallet"] if deposit else row["bank"]
        amount = int(available) if raw.strip().lower() in {"all", "全部"} else parse_amount(raw)
        if amount is None:
            amount = 0
        if not valid_amount(amount):
            await self._reply(target, "請輸入大於 0 的正整數或 all；餘額不足或餘額為 0 時不能操作。", public=False)
            return
        if not self.store.move(target.guild.id, user.id, amount, deposit):
            await self._reply(target, "餘額不足或超出系統上限，交易已取消。", public=False)
            return
        await self._reply(target, f"已{'存入銀行' if deposit else '提出現金'} **{amount:,}** 金幣。", public=public)

    @app_commands.command(name=app_commands.locale_str("deposit", zh_TW="存錢"), description="將現金存入銀行")
    @app_commands.describe(amount="正整數，或 all 代表全部")
    @app_commands.describe(public=app_commands.locale_str("是否公開顯示回覆", zh_TW="是否公開顯示回覆"))
    @app_commands.rename(public=app_commands.locale_str("公開", zh_TW="公開"))
    async def deposit_slash(self, interaction: discord.Interaction, amount: str, public: bool = True):
        await self._bank(interaction, amount, True, public)

    @commands.command(name="存錢")
    async def deposit_prefix(self, ctx, amount: str):
        await self._bank(ctx, amount, True)

    @app_commands.command(name=app_commands.locale_str("withdraw", zh_TW="提款"), description="從銀行提出現金")
    @app_commands.describe(amount="正整數，或 all 代表全部")
    @app_commands.describe(public=app_commands.locale_str("是否公開顯示回覆", zh_TW="是否公開顯示回覆"))
    @app_commands.rename(public=app_commands.locale_str("公開", zh_TW="公開"))
    async def withdraw_slash(self, interaction: discord.Interaction, amount: str, public: bool = True):
        await self._bank(interaction, amount, False, public)

    @commands.command(name="提款")
    async def withdraw_prefix(self, ctx, amount: str):
        await self._bank(ctx, amount, False)

    async def _transfer(self, target, member, amount, public=True):
        if not await self._guild(target):
            return
        user = target.user if isinstance(target, discord.Interaction) else target.author
        if member.bot or member.id == user.id:
            await self._reply(target, "不能轉帳給自己或機器人。", public=False)
            return
        amount = parse_amount(amount)
        if amount is None:
            await self._reply(target, f"金額必須是 1 至 {MAX_DIGITS} 位數的正整數。", public=False)
            return
        if not self.store.transfer(target.guild.id, user.id, member.id, amount):
            await self._reply(target, "餘額不足，或收款方資產已達上限；交易取消。", public=False)
            return
        await self._reply(target, f"已轉帳給 {member.mention} **{amount:,}** 金幣，手續費 0%。", public=public)

    @app_commands.command(name=app_commands.locale_str("transfer", zh_TW="轉帳"), description="轉帳給另一位成員")
    @app_commands.describe(member="收款成員", amount="大於 0 的整數金額")
    @app_commands.describe(public=app_commands.locale_str("是否公開顯示回覆", zh_TW="是否公開顯示回覆"))
    @app_commands.rename(public=app_commands.locale_str("公開", zh_TW="公開"))
    async def transfer_slash(self, interaction: discord.Interaction, member: discord.Member, amount: str, public: bool = True):
        await self._transfer(interaction, member, amount, public)

    @commands.command(name="轉帳")
    async def transfer_prefix(self, ctx, member: discord.Member, amount: str):
        await self._transfer(ctx, member, amount)

    async def _dice(self, target, amount, public=True):
        if not await self._guild(target):
            return
        user = target.user if isinstance(target, discord.Interaction) else target.author
        amount = parse_amount(amount)
        if amount is None:
            await self._reply(target, f"下注金額必須是 1 至 {MAX_DIGITS} 位數的正整數。", public=False)
            return
        player = (random.randint(1, 6), random.randint(1, 6))
        house = (random.randint(1, 6), random.randint(1, 6))
        a, b = sum(player), sum(house)
        payout = amount * 2 if a > b else amount if a == b else 0
        if not self.store.dice(target.guild.id, user.id, amount, payout):
            await self._reply(target, "錢包餘額不足，或交易超出系統上限。", public=False)
            return
        result = f"你贏了，拿回 {amount * 2:,} 金幣（淨賺 {amount:,}）。" if a > b else f"平手，退回本金 {amount:,} 金幣。" if a == b else f"你輸了，下注的 {amount:,} 金幣被沒收。"
        await self._reply(target, f"🎲 你的骰子 {player[0]} + {player[1]} = **{a}**\n莊家骰子 {house[0]} + {house[1]} = **{b}**\n{result}", public=public)

    @app_commands.command(name=app_commands.locale_str("economy_dice", zh_TW="骰子"), description="與莊家比骰子大小並下注")
    @app_commands.describe(amount="大於 0 的整數下注金額")
    @app_commands.describe(public=app_commands.locale_str("是否公開顯示回覆", zh_TW="是否公開顯示回覆"))
    @app_commands.rename(public=app_commands.locale_str("公開", zh_TW="公開"))
    async def dice_slash(self, interaction: discord.Interaction, amount: str, public: bool = True):
        await self._dice(interaction, amount, public)

    @commands.command(name="骰子")
    async def dice_prefix(self, ctx, amount: str):
        await self._dice(ctx, amount)

    async def _rob(self, target, member, public=True):
        if not await self._guild(target):
            return
        user = target.user if isinstance(target, discord.Interaction) else target.author
        if member.bot or member.id == user.id:
            await self._reply(target, "不能搶劫自己或機器人。", public=False)
            return
        outcome, value = self.store.rob(target.guild.id, user.id, member.id, random.random() < 0.40,
                                        random.uniform(0.10, 0.30), random.uniform(0.10, 0.20))
        if outcome == "cooldown":
            message = f"你剛搶過人，再等 **{cooldown_text(value)}**。"
        elif outcome == "cap":
            message = "你的錢包接近系統上限，這次無法完成。"
        elif outcome == "success":
            message = f"搶劫成功！你從 {member.mention} 那裡搶到 **{value:,}** 金幣。"
        else:
            message = f"搶劫失敗！你被罰款 **{value:,}** 金幣（以錢包現金為限）。"
        await self._reply(target, message, public=public)

    @app_commands.command(name=app_commands.locale_str("rob", zh_TW="搶劫"), description="嘗試搶劫另一位成員（成功後冷卻 10 分鐘，失敗不冷卻）")
    @app_commands.describe(member="要搶劫的成員")
    @app_commands.describe(public=app_commands.locale_str("是否公開顯示回覆", zh_TW="是否公開顯示回覆"))
    @app_commands.rename(public=app_commands.locale_str("公開", zh_TW="公開"))
    async def rob_slash(self, interaction: discord.Interaction, member: discord.Member, public: bool = True):
        await self._rob(interaction, member, public)

    @commands.command(name="搶劫")
    async def rob_prefix(self, ctx, member: discord.Member):
        await self._rob(ctx, member)


async def setup(bot: commands.Bot):
    await bot.add_cog(EconomyCog(bot))
