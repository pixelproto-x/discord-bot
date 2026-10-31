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

from cogs.public_response import PublicizeView

DB_PATH = Path(__file__).resolve().parent.parent / "economy.db"
MAX_DIGITS = 2000
MAX_BALANCE = int("9" * MAX_DIGITS)
UTC = timezone.utc
COOLDOWNS = {"daily": 86400, "work": 60, "fish": 30, "rob": 600}
# These two users may rob each other repeatedly without the normal cooldown.
MUTUAL_UNLIMITED_ROB_USER_IDS = frozenset({1140900506198351924, 1338493569781989407})
# The second account remains protected from everyone outside the explicit mutual pair.
PERMANENT_ANTI_ROB_USER_IDS = frozenset({1338493569781989407})
PERMANENT_ANTI_ROB_ALLOWED_ROBBER_IDS = MUTUAL_UNLIMITED_ROB_USER_IDS
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

ITEMS = {
    "anti_rob": ("防搶卡", 800, "啟動 1 小時防搶保護"),
    "lucky_charm": ("幸運符", 500, "下一次打工或釣魚收益增加 50%"),
    "premium_bait": ("高級魚餌", 250, "下一次釣魚收益加倍"),
    "work_ticket": ("打工加成券", 700, "下一次打工收益加倍"),
}




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


def is_infinite(value):
    return str(value).strip().lower() in {"inf", "-inf"}


def format_coins(value):
    """Format finite and unlimited database balances without oversized Discord output."""
    raw = str(value).strip().lower()
    if raw == "inf":
        return "∞（無限）"
    if raw == "-inf":
        return "-∞（負無限）"
    amount = int(value)
    digits_text = str(abs(amount))
    digits = len(digits_text)
    # Keep the magnitude visible while avoiding huge Discord messages.
    if digits > 20:
        significant = digits_text[:6].rstrip("0") or "0"
        mantissa = significant[0]
        if len(significant) > 1:
            mantissa += "." + significant[1:]
        sign = "-" if amount < 0 else ""
        return f"{sign}{mantissa}×10^{digits - 1}"
    return f"{amount:,}"


def add_balance(value, amount):
    return ("-inf" if str(value).strip().lower() == "-inf" else "inf") if is_infinite(value) else str(int(value) + amount)


def subtract_balance(value, amount):
    return ("-inf" if str(value).strip().lower() == "-inf" else "inf") if is_infinite(value) else str(int(value) - amount)


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
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    wallet TEXT NOT NULL DEFAULT '1000',
                    bank TEXT NOT NULL DEFAULT '0',
                    last_daily TEXT, last_work TEXT, last_fish TEXT, last_rob TEXT,
                    PRIMARY KEY (guild_id, user_id)
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS economy_inventory (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    item_key TEXT NOT NULL,
                    quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity >= 0),
                    PRIMARY KEY (guild_id, user_id, item_key)
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS economy_effects (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    effect_key TEXT NOT NULL,
                    charges INTEGER NOT NULL DEFAULT 0 CHECK(charges >= 0),
                    expires_at TEXT,
                    PRIMARY KEY (guild_id, user_id, effect_key)
                )
            """)
            columns = {row["name"]: row for row in con.execute("PRAGMA table_info(users)")}
            user_pk = columns.get("user_id")
            guild_pk = columns.get("guild_id")
            # Migrate legacy globally-keyed accounts to per-server accounts. Keep
            # each existing balance with its recorded guild; other guilds get defaults.
            if user_pk and int(user_pk["pk"]) == 1 and (not guild_pk or int(guild_pk["pk"]) != 1):
                con.execute("BEGIN IMMEDIATE")
                con.execute("""
                    CREATE TABLE users_scoped (
                        guild_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        wallet TEXT NOT NULL DEFAULT '1000',
                        bank TEXT NOT NULL DEFAULT '0',
                        last_daily TEXT, last_work TEXT, last_fish TEXT, last_rob TEXT,
                        PRIMARY KEY (guild_id, user_id)
                    )
                """)
                con.execute("""
                    INSERT INTO users_scoped
                    (guild_id,user_id,wallet,bank,last_daily,last_work,last_fish,last_rob)
                    SELECT COALESCE(guild_id,0),user_id,CAST(wallet AS TEXT),CAST(bank AS TEXT),
                           last_daily,last_work,last_fish,last_rob
                    FROM users
                """)
                con.execute("DROP TABLE users")
                con.execute("ALTER TABLE users_scoped RENAME TO users")
                con.commit()

            # One-time safety copy before merging every guild-scoped row.
            backup_path = DB_PATH.with_name("economy.db.pre-global-backup")
            if not backup_path.exists():
                with sqlite3.connect(backup_path) as backup:
                    con.backup(backup)

            con.execute("BEGIN IMMEDIATE")
            user_ids = [row["user_id"] for row in con.execute("SELECT DISTINCT user_id FROM users")]
            for user_id in user_ids:
                rows = con.execute("SELECT * FROM users WHERE user_id=? ORDER BY guild_id", (user_id,)).fetchall()
                wallets = [str(row["wallet"]) for row in rows]
                banks = [str(row["bank"]) for row in rows]
                wallet = "inf" if any(str(v).strip().lower() == "inf" for v in wallets) else ("-inf" if any(str(v).strip().lower() == "-inf" for v in wallets) else str(sum(int(v) for v in wallets)))
                bank = "inf" if any(str(v).strip().lower() == "inf" for v in banks) else ("-inf" if any(str(v).strip().lower() == "-inf" for v in banks) else str(sum(int(v) for v in banks)))
                times = {}
                for key in ("last_daily", "last_work", "last_fish", "last_rob"):
                    valid = [v for v in (row[key] for row in rows) if parse_time(v)]
                    times[key] = max(valid, key=lambda v: parse_time(v)) if valid else None
                con.execute(
                    "INSERT INTO users(guild_id,user_id,wallet,bank,last_daily,last_work,last_fish,last_rob) "
                    "VALUES(0,?,?,?,?,?,?,?) ON CONFLICT(guild_id,user_id) DO UPDATE SET "
                    "wallet=excluded.wallet,bank=excluded.bank,last_daily=excluded.last_daily,"
                    "last_work=excluded.last_work,last_fish=excluded.last_fish,last_rob=excluded.last_rob",
                    (user_id,wallet,bank,times["last_daily"],times["last_work"],times["last_fish"],times["last_rob"]),
                )
                con.execute("DELETE FROM users WHERE user_id=? AND guild_id<>0", (user_id,))
                for item in con.execute(
                    "SELECT item_key,SUM(quantity) quantity FROM economy_inventory WHERE user_id=? GROUP BY item_key",
                    (user_id,),
                ).fetchall():
                    con.execute(
                        "INSERT INTO economy_inventory(guild_id,user_id,item_key,quantity) VALUES(0,?,?,?) "
                        "ON CONFLICT(guild_id,user_id,item_key) DO UPDATE SET quantity=excluded.quantity",
                        (user_id,item["item_key"],item["quantity"]),
                    )
                con.execute("DELETE FROM economy_inventory WHERE user_id=? AND guild_id<>0", (user_id,))
                for effect in con.execute(
                    "SELECT effect_key,SUM(charges) charges,MAX(expires_at) expires_at "
                    "FROM economy_effects WHERE user_id=? GROUP BY effect_key", (user_id,),
                ).fetchall():
                    con.execute(
                        "INSERT INTO economy_effects(guild_id,user_id,effect_key,charges,expires_at) VALUES(0,?,?,?,?) "
                        "ON CONFLICT(guild_id,user_id,effect_key) DO UPDATE SET "
                        "charges=excluded.charges,expires_at=excluded.expires_at",
                        (user_id,effect["effect_key"],effect["charges"],effect["expires_at"]),
                    )
                con.execute("DELETE FROM economy_effects WHERE user_id=? AND guild_id<>0", (user_id,))
            con.commit()

    def connect(self):
        con = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=10000")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def ensure(self, con, guild_id, user_id):
        guild_id = 0
        con.execute("INSERT OR IGNORE INTO users(guild_id, user_id) VALUES(?, ?)", (guild_id, user_id))

    def get(self, guild_id, user_id):
        guild_id = 0
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            self.ensure(con, guild_id, user_id)
            row = dict(con.execute("SELECT * FROM users WHERE guild_id=? AND user_id=?", (guild_id, user_id)).fetchone())
            con.commit()
            return row

    def claim(self, guild_id, user_id, kind, amount):
        guild_id = 0
        column = {"daily": "last_daily", "work": "last_work", "fish": "last_fish"}[kind]
        now = now_utc()
        effect_used = []
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            self.ensure(con, guild_id, user_id)
            row = con.execute(f"SELECT {column}, wallet FROM users WHERE guild_id=? AND user_id=?", (guild_id, user_id)).fetchone()
            last = parse_time(row[column])
            if last and now < last + timedelta(seconds=COOLDOWNS[kind]):
                con.rollback()
                return False, max(1, int((last + timedelta(seconds=COOLDOWNS[kind]) - now).total_seconds())), None
            effect_key = {"work": "work_ticket", "fish": "premium_bait"}.get(kind)
            if effect_key:
                effect = con.execute(
                    "SELECT charges FROM economy_effects WHERE guild_id=? AND user_id=? AND effect_key=?",
                    (guild_id, user_id, effect_key),
                ).fetchone()
                if effect and int(effect["charges"]) > 0:
                    amount *= 2
                    effect_used.append(ITEMS[effect_key][0])
                    con.execute(
                        "UPDATE economy_effects SET charges=charges-1 WHERE guild_id=? AND user_id=? AND effect_key=?",
                        (guild_id, user_id, effect_key),
                    )
            lucky = con.execute(
                "SELECT charges FROM economy_effects WHERE guild_id=? AND user_id=? AND effect_key='lucky_charm'",
                (guild_id, user_id),
            ).fetchone()
            if kind in {"work", "fish"} and lucky and int(lucky["charges"]) > 0:
                amount = int(amount * 1.5)
                effect_used.append("幸運符")
                con.execute(
                    "UPDATE economy_effects SET charges=charges-1 WHERE guild_id=? AND user_id=? AND effect_key='lucky_charm'",
                    (guild_id, user_id),
                )
            wallet = row["wallet"]
            if not is_infinite(wallet) and int(wallet) + amount > MAX_BALANCE:
                con.rollback()
                return False, -1, None
            con.execute(f"UPDATE users SET wallet=?, {column}=? WHERE guild_id=? AND user_id=?",
                        (add_balance(wallet, amount), now.isoformat(), guild_id, user_id))
            con.commit()
            return True, amount, "、".join(effect_used) if effect_used else None

    def move(self, guild_id, user_id, amount, deposit):
        guild_id = 0
        if not valid_amount(amount):
            return False
        source, destination = ("wallet", "bank") if deposit else ("bank", "wallet")
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            self.ensure(con, guild_id, user_id)
            row = con.execute(f"SELECT {source}, {destination} FROM users WHERE guild_id=? AND user_id=?", (guild_id, user_id)).fetchone()
            source_value = row[source]
            destination_value = row[destination]
            if not is_infinite(source_value) and int(source_value) < amount:
                con.rollback()
                return False
            if not is_infinite(destination_value) and int(destination_value) + amount > MAX_BALANCE:
                con.rollback()
                return False
            # Keep the balance check and write inside one immediate transaction.
            # Also compare the values read above so a stale/unexpected row cannot
            # be overwritten, and never commit a negative finite source balance.
            new_source = subtract_balance(source_value, amount)
            if not is_infinite(new_source) and int(new_source) < 0:
                con.rollback()
                return False
            cursor = con.execute(
                f"UPDATE users SET {source}=?, {destination}=? "
                f"WHERE guild_id=? AND user_id=? AND {source}=? AND {destination}=?",
                (new_source, add_balance(destination_value, amount),
                 guild_id, user_id, source_value, destination_value),
            )
            if cursor.rowcount != 1:
                con.rollback()
                return False
            con.commit()
            return True

    def transfer(self, guild_id, sender, receiver, amount):
        guild_id = 0
        if not valid_amount(amount) or sender == receiver:
            return False
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            self.ensure(con, guild_id, sender)
            self.ensure(con, guild_id, receiver)
            a = con.execute("SELECT wallet FROM users WHERE guild_id=? AND user_id=?", (guild_id, sender)).fetchone()["wallet"]
            b = con.execute("SELECT wallet FROM users WHERE guild_id=? AND user_id=?", (guild_id, receiver)).fetchone()["wallet"]
            if not is_infinite(a) and int(a) < amount:
                con.rollback()
                return False
            if not is_infinite(b) and int(b) + amount > MAX_BALANCE:
                con.rollback()
                return False
            con.execute("UPDATE users SET wallet=? WHERE guild_id=? AND user_id=?", (subtract_balance(a, amount), guild_id, sender))
            con.execute("UPDATE users SET wallet=? WHERE guild_id=? AND user_id=?", (add_balance(b, amount), guild_id, receiver))
            con.commit()
            return True

    def dice(self, guild_id, user_id, amount, payout):
        guild_id = 0
        if not valid_amount(amount) or type(payout) is not int or payout < 0 or payout > MAX_BALANCE:
            return False
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            self.ensure(con, guild_id, user_id)
            row = con.execute("SELECT wallet FROM users WHERE guild_id=? AND user_id=?", (guild_id, user_id)).fetchone()
            wallet = row["wallet"]
            # A finite wallet must contain the full stake. Treat zero and
            # negative balances as insufficient funds, never as credit.
            if not is_infinite(wallet):
                current = int(wallet)
                if current < 0 or current < amount:
                    con.rollback()
                    return False
                updated_value = current - amount + payout
                if updated_value < 0 or updated_value > MAX_BALANCE:
                    con.rollback()
                    return False
                updated = str(updated_value)
            else:
                updated = "-inf" if str(wallet).strip().lower() == "-inf" else "inf"
            cursor = con.execute(
                "UPDATE users SET wallet=? WHERE guild_id=? AND user_id=? AND wallet=?",
                (updated, guild_id, user_id, wallet),
            )
            if cursor.rowcount != 1:
                con.rollback()
                return False
            con.commit()
            return True

    def shop_buy(self, guild_id, user_id, item_key, quantity):
        guild_id = 0
        if item_key not in ITEMS or type(quantity) is not int or not 1 <= quantity <= 99:
            return False, "商品或數量不正確，每次最多購買 99 件。"
        name, price, _ = ITEMS[item_key]
        total = price * quantity
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            self.ensure(con, guild_id, user_id)
            wallet = con.execute("SELECT wallet FROM users WHERE guild_id=? AND user_id=?", (guild_id, user_id)).fetchone()["wallet"]
            if not is_infinite(wallet) and int(wallet) < total:
                con.rollback()
                return False, f"金幣不足，需要 {total:,}，目前現金 {format_coins(wallet)}。"
            if not is_infinite(wallet):
                con.execute("UPDATE users SET wallet=? WHERE guild_id=? AND user_id=?", (str(int(wallet) - total), guild_id, user_id))
            con.execute(
                "INSERT INTO economy_inventory(guild_id,user_id,item_key,quantity) VALUES(?,?,?,?) "
                "ON CONFLICT(guild_id,user_id,item_key) DO UPDATE SET quantity=quantity+excluded.quantity",
                (guild_id, user_id, item_key, quantity),
            )
            con.commit()
            return True, f"購買成功：**{name} × {quantity}**，共花費 **{total:,}** 金幣。"

    def inventory(self, guild_id, user_id):
        guild_id = 0
        with closing(self.connect()) as con:
            rows = con.execute(
                "SELECT item_key,quantity FROM economy_inventory "
                "WHERE guild_id=? AND user_id=? AND quantity>0 ORDER BY item_key",
                (guild_id, user_id),
            ).fetchall()
        return [(row["item_key"], int(row["quantity"])) for row in rows if row["item_key"] in ITEMS]

    def use_item(self, guild_id, user_id, item_key):
        guild_id = 0
        if item_key not in ITEMS:
            return False, "找不到這個商品。"
        name = ITEMS[item_key][0]
        now = now_utc()
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            self.ensure(con, guild_id, user_id)
            row = con.execute(
                "SELECT quantity FROM economy_inventory WHERE guild_id=? AND user_id=? AND item_key=?",
                (guild_id, user_id, item_key),
            ).fetchone()
            if not row or int(row["quantity"]) < 1:
                con.rollback()
                return False, f"你的背包沒有「{name}」。"
            if item_key == "anti_rob":
                expiry = now + timedelta(hours=1)
                existing = con.execute(
                    "SELECT expires_at FROM economy_effects WHERE guild_id=? AND user_id=? AND effect_key='anti_rob'",
                    (guild_id, user_id),
                ).fetchone()
                current_expiry = parse_time(existing["expires_at"]) if existing else None
                if current_expiry and current_expiry > now:
                    con.rollback()
                    return False, f"防搶保護尚未結束，剩餘 {cooldown_text(int((current_expiry-now).total_seconds()))}，不會消耗道具。"
                con.execute(
                    "INSERT INTO economy_effects(guild_id,user_id,effect_key,charges,expires_at) VALUES(?,?, 'anti_rob',0,?) "
                    "ON CONFLICT(guild_id,user_id,effect_key) DO UPDATE SET expires_at=excluded.expires_at",
                    (guild_id, user_id, expiry.isoformat()),
                )
                detail = "已啟動 1 小時防搶保護。"
            else:
                con.execute(
                    "INSERT INTO economy_effects(guild_id,user_id,effect_key,charges,expires_at) VALUES(?,?,?,1,NULL) "
                    "ON CONFLICT(guild_id,user_id,effect_key) DO UPDATE SET charges=charges+1",
                    (guild_id, user_id, item_key),
                )
                detail = {
                    "lucky_charm": "下一次打工或釣魚收益增加 50%。",
                    "premium_bait": "下一次釣魚收益加倍。",
                    "work_ticket": "下一次打工收益加倍。",
                }[item_key]
            con.execute(
                "UPDATE economy_inventory SET quantity=quantity-1 WHERE guild_id=? AND user_id=? AND item_key=?",
                (guild_id, user_id, item_key),
            )
            con.commit()
            return True, f"已使用 **{name}**。{detail}"

    def rob(self, guild_id, robber_id, target_id, success, fraction, fine_fraction):
        guild_id = 0
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            if (target_id in PERMANENT_ANTI_ROB_USER_IDS
                    and robber_id not in PERMANENT_ANTI_ROB_ALLOWED_ROBBER_IDS):
                con.rollback()
                return "protected_permanent", 0
            self.ensure(con, guild_id, robber_id)
            self.ensure(con, guild_id, target_id)
            robber = con.execute("SELECT wallet,bank,last_rob FROM users WHERE guild_id=? AND user_id=?", (guild_id, robber_id)).fetchone()
            target = con.execute("SELECT wallet FROM users WHERE guild_id=? AND user_id=?", (guild_id, target_id)).fetchone()
            now = now_utc()
            last = parse_time(robber["last_rob"])
            mutual_unlimited_rob = (
                robber_id in MUTUAL_UNLIMITED_ROB_USER_IDS
                and target_id in MUTUAL_UNLIMITED_ROB_USER_IDS
                and robber_id != target_id
            )
            if (not mutual_unlimited_rob and last
                    and now < last + timedelta(seconds=COOLDOWNS["rob"])):
                con.rollback()
                return "cooldown", max(1, int((last + timedelta(seconds=COOLDOWNS["rob"]) - now).total_seconds()))
            protection = con.execute(
                "SELECT expires_at FROM economy_effects WHERE guild_id=? AND user_id=? AND effect_key='anti_rob'",
                (guild_id, target_id),
            ).fetchone()
            protected_until = parse_time(protection["expires_at"]) if protection else None
            if protected_until and protected_until > now:
                con.rollback()
                return "protected", max(1, int((protected_until - now).total_seconds()))
            if success:
                if is_infinite(target["wallet"]):
                    con.rollback()
                    return "unstealable", 0
                target_wallet = int(target["wallet"])
                if target_wallet <= 0:
                    con.rollback()
                    return "empty", 0
                # A successful robbery must transfer at least one coin when the target has cash.
                stolen = max(1, int(Decimal(target_wallet) * Decimal(str(fraction))))
                if not is_infinite(robber["wallet"]) and int(robber["wallet"]) + stolen > MAX_BALANCE:
                    con.rollback()
                    return "cap", 0
                con.execute("UPDATE users SET wallet=? WHERE guild_id=? AND user_id=?",
                            (str(int(target["wallet"]) - stolen), guild_id, target_id))
                con.execute("UPDATE users SET wallet=? WHERE guild_id=? AND user_id=?",
                            (add_balance(robber["wallet"], stolen), guild_id, robber_id))
                result = ("success", stolen)
            else:
                wallet = str(robber["wallet"])
                bank = str(robber["bank"])
                # Calculate the penalty from the player's total finite funds so
                # depositing everything before a robbery cannot reduce it to zero.
                if is_infinite(wallet):
                    fine = 0
                else:
                    cash = int(wallet)
                    savings = 0 if is_infinite(bank) else int(bank)
                    available = max(0, cash) + max(0, savings)
                    fine = min(
                        available,
                        max(1, int(Decimal(available) * Decimal(str(fine_fraction))))
                        if available > 0 else 0,
                    )
                    cash_paid = min(max(0, cash), fine)
                    bank_paid = fine - cash_paid
                    if cash_paid:
                        wallet = str(cash - cash_paid)
                    if bank_paid:
                        bank = str(int(bank) - bank_paid)
                con.execute(
                    "UPDATE users SET wallet=?,bank=? WHERE guild_id=? AND user_id=?",
                    (wallet, bank, guild_id, robber_id),
                )
                result = ("failed", fine)
            # Every resolved robbery attempt starts the 10-minute cooldown,
            # whether it succeeds or fails. Earlier exits are only for blocked,
            # invalid, or impossible attempts (cooldown/protection/empty target/cap).
            con.execute("UPDATE users SET last_rob=? WHERE guild_id=? AND user_id=?", (now.isoformat(), guild_id, robber_id))
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
            view = None if public else PublicizeView(target.user.id, content, embed)
            # discord.py expects the view argument to be omitted when no view is
            # attached. Passing view=None can call None.is_finished() internally.
            response_kwargs = {"embed": embed, "ephemeral": not public}
            if view is not None:
                response_kwargs["view"] = view
            if target.response.is_done():
                await target.followup.send(content, **response_kwargs)
            else:
                await target.response.send_message(content, **response_kwargs)
        else:
            await target.reply(content, embed=embed, mention_author=False)

    async def _wallet(self, target, member=None, public=True):
        if not await self._guild(target):
            return
        user = member or (target.user if isinstance(target, discord.Interaction) else target.author)
        row = self.store.get(target.guild.id, user.id)
        embed = discord.Embed(title=f"💰 {user.display_name} 的錢包", color=0xF1C40F)
        embed.set_thumbnail(url=user.display_avatar.url)
        wallet = row["wallet"]
        bank = row["bank"]
        total = "inf" if "inf" in {str(wallet).strip().lower(), str(bank).strip().lower()} else ("-inf" if "-inf" in {str(wallet).strip().lower(), str(bank).strip().lower()} else str(int(wallet) + int(bank)))
        embed.add_field(name="現金", value=f"{format_coins(wallet)} 金幣")
        embed.add_field(name="銀行存款", value=f"{format_coins(bank)} 金幣")
        embed.add_field(name="總資產", value=f"{format_coins(total)} 金幣", inline=False)
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
        ok, detail, effect_used = self.store.claim(target.guild.id, user.id, kind, amount)
        if not ok:
            message = "錢包已達系統上限，無法增加金幣。" if detail == -1 else f"冷卻中，還要等 **{cooldown_text(detail)}**。"
            await self._reply(target, message, public=public)
            return
        effect_text = f"\\n道具效果：{effect_used}" if effect_used else ""
        await self._reply(target, f"**{title}**\\n{line}，獲得 **{detail:,}** 金幣。{effect_text}", public=public)

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
        if raw.strip().lower() in {"all", "全部"}:
            amount = int(available) if not is_infinite(available) else None
        else:
            amount = parse_amount(raw)
        if amount is None:
            await self._reply(target, "請輸入大於 0 的正整數；無限餘額請指定實際轉移金額。", public=False)
            return
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
        if outcome == "protected_permanent":
            message = f"{member.mention} 已設定永久防搶，任何人都不能搶劫這個使用者。"
        elif outcome == "cooldown":
            message = f"你剛搶過人，再等 **{cooldown_text(value)}**。"
        elif outcome == "cap":
            message = "你的錢包接近系統上限，這次無法完成。"
        elif outcome == "protected":
            message = f"{member.mention} 使用了防搶卡，還有 **{cooldown_text(value)}** 保護時間，這次搶劫無效。"
        elif outcome == "unstealable":
            message = f"{member.mention} 的錢包是無限金額，無法搶走。"
        elif outcome == "empty":
            message = f"{member.mention} 的錢包沒有可搶的現金，這次沒搶到錢。"
        elif outcome == "success" and value > 0:
            message = f"搶劫成功！你從 {member.mention} 那裡搶到 **{format_coins(value)}** 金幣。"
        elif outcome == "success":
            # Fail closed: never tell users a zero-value robbery succeeded.
            message = "搶劫沒有完成，沒有轉移金幣。請稍後再試。"
        else:
            message = f"搶劫失敗！你被罰款 **{format_coins(value)}** 金幣（以錢包現金為限）。"
        await self._reply(target, message, public=public)

    @app_commands.command(name=app_commands.locale_str("shop", zh_TW="商店"), description="查看固定商品與價格")
    @app_commands.describe(public=app_commands.locale_str("是否公開顯示回覆", zh_TW="是否公開顯示回覆"))
    @app_commands.rename(public=app_commands.locale_str("公開", zh_TW="公開"))
    async def shop_slash(self, interaction: discord.Interaction, public: bool = False):
        if not await self._guild(interaction):
            return
        embed = discord.Embed(title="固定商品商店", description="使用 /購買 選擇商品與數量。", color=0x3498DB)
        for _, (name, price, description) in ITEMS.items():
            embed.add_field(name=f"{name}｜{price:,} 金幣", value=description, inline=False)
        await self._reply(interaction, "", embed=embed, public=public)

    @app_commands.command(name=app_commands.locale_str("backpack", zh_TW="背包"), description="查看自己持有的固定商店商品")
    @app_commands.describe(public=app_commands.locale_str("是否公開顯示回覆", zh_TW="是否公開顯示回覆"))
    @app_commands.rename(public=app_commands.locale_str("公開", zh_TW="公開"))
    async def backpack_slash(self, interaction: discord.Interaction, public: bool = False):
        if not await self._guild(interaction):
            return
        rows = self.store.inventory(interaction.guild.id, interaction.user.id)
        if not rows:
            await self._reply(interaction, "你的背包目前是空的，可以先到 /商店 查看商品。", public=public)
            return
        lines = [f"**{ITEMS[key][0]}** × {quantity}" for key, quantity in rows]
        embed = discord.Embed(title="我的背包", description="\n".join(lines), color=0x1ABC9C)
        await self._reply(interaction, "", embed=embed, public=public)

    @app_commands.command(name=app_commands.locale_str("buy-item", zh_TW="購買"), description="購買固定商店商品")
    @app_commands.choices(item=[
        app_commands.Choice(name="防搶卡", value="anti_rob"),
        app_commands.Choice(name="幸運符", value="lucky_charm"),
        app_commands.Choice(name="高級魚餌", value="premium_bait"),
        app_commands.Choice(name="打工加成券", value="work_ticket"),
    ])
    @app_commands.describe(item="要購買的固定商品", quantity="購買數量，最多 99 件", public=app_commands.locale_str("是否公開顯示回覆", zh_TW="是否公開顯示回覆"))
    @app_commands.rename(public=app_commands.locale_str("公開", zh_TW="公開"))
    @app_commands.rename(item=app_commands.locale_str("商品", zh_TW="商品"), quantity=app_commands.locale_str("數量", zh_TW="數量"))
    async def buy_item_slash(
        self,
        interaction: discord.Interaction,
        item: app_commands.Choice[str],
        quantity: app_commands.Range[int, 1, 99] = 1,
        public: bool = False,
    ):
        if not await self._guild(interaction):
            return
        ok, result = self.store.shop_buy(interaction.guild.id, interaction.user.id, item.value, quantity)
        await self._reply(
            interaction,
            "",
            embed=discord.Embed(description=result, color=0x2ECC71 if ok else 0xE74C3C),
            public=public,
        )

    @app_commands.command(name=app_commands.locale_str("use-item", zh_TW="使用道具"), description="使用背包中的道具")
    @app_commands.choices(item=[
        app_commands.Choice(name="防搶卡", value="anti_rob"),
        app_commands.Choice(name="幸運符", value="lucky_charm"),
        app_commands.Choice(name="高級魚餌", value="premium_bait"),
        app_commands.Choice(name="打工加成券", value="work_ticket"),
    ])
    @app_commands.rename(item=app_commands.locale_str("商品", zh_TW="商品"))
    @app_commands.describe(public=app_commands.locale_str("是否公開顯示回覆", zh_TW="是否公開顯示回覆"))
    @app_commands.rename(public=app_commands.locale_str("公開", zh_TW="公開"))
    async def use_item_slash(self, interaction: discord.Interaction, item: app_commands.Choice[str], public: bool = False):
        if not await self._guild(interaction):
            return
        ok, result = self.store.use_item(interaction.guild.id, interaction.user.id, item.value)
        await self._reply(
            interaction, "",
            embed=discord.Embed(description=result, color=0x2ECC71 if ok else 0xE74C3C),
            public=public,
        )

    @app_commands.command(name=app_commands.locale_str("rob", zh_TW="搶劫"), description="嘗試搶劫另一位成員（成功或失敗後均冷卻 10 分鐘）")
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
