from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

DB_PATH = __import__("pathlib").Path(__file__).resolve().parent.parent / "economy.db"
MAX_AMOUNT = 9_000_000_000_000_000
UTC = timezone.utc


def _money(value: int) -> str:
    return f"{int(value):,}"


def _now() -> int:
    return int(time.time())


class GuildEconomyStore:
    """Per-guild shared treasury and cooperative timed events."""

    def __init__(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS guild_treasuries (
                    guild_id INTEGER PRIMARY KEY,
                    balance TEXT NOT NULL DEFAULT '0',
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS guild_treasury_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    balance_after TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS guild_economy_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    creator_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    goal INTEGER NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    reward_pool TEXT NOT NULL,
                    ends_at INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at INTEGER NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_guild_event
                ON guild_economy_events(guild_id) WHERE status='active';
                CREATE TABLE IF NOT EXISTS guild_economy_event_members (
                    event_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    contribution INTEGER NOT NULL DEFAULT 0,
                    joined_at INTEGER NOT NULL,
                    PRIMARY KEY(event_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS guild_economy_event_claims (
                    event_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    amount TEXT NOT NULL,
                    claimed_at INTEGER NOT NULL,
                    PRIMARY KEY(event_id, user_id)
                );
            """)

    def connect(self):
        con = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=10000")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _ensure_treasury(self, con, guild_id: int):
        con.execute(
            "INSERT OR IGNORE INTO guild_treasuries(guild_id,balance,updated_at) VALUES(?, '0', ?)",
            (guild_id, _now()),
        )

    def _ledger(self, con, guild_id, user_id, action, amount, balance, detail):
        con.execute(
            "INSERT INTO guild_treasury_ledger(guild_id,user_id,action,amount,balance_after,detail,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (guild_id, user_id, action, str(amount), str(balance), detail[:300], _now()),
        )

    def treasury(self, guild_id: int):
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            self._ensure_treasury(con, guild_id)
            row = con.execute(
                "SELECT balance,updated_at FROM guild_treasuries WHERE guild_id=?", (guild_id,)
            ).fetchone()
            recent = con.execute(
                "SELECT action,amount,detail,created_at FROM guild_treasury_ledger "
                "WHERE guild_id=? ORDER BY id DESC LIMIT 5", (guild_id,)
            ).fetchall()
            con.commit()
        return row, recent

    def transfer(self, guild_id: int, user_id: int, amount: int, deposit: bool):
        if not 1 <= amount <= MAX_AMOUNT:
            return False, "金額必須介於 1 至 9,000,000,000,000,000。"
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            self._ensure_treasury(con, guild_id)
            user = con.execute(
                "SELECT wallet FROM users WHERE user_id=?", (user_id,)
            ).fetchone()
            if user is None:
                con.execute(
                    "INSERT INTO users(user_id,guild_id,wallet,bank) VALUES(?,?, '1000','0')",
                    (user_id, guild_id),
                )
                wallet = "1000"
            else:
                wallet = str(user["wallet"])
            treasury = con.execute(
                "SELECT balance FROM guild_treasuries WHERE guild_id=?", (guild_id,)
            ).fetchone()
            balance = int(treasury["balance"])
            if deposit:
                if wallet.strip().lower() != "inf" and int(wallet) < amount:
                    con.rollback()
                    return False, f"現金不足，你目前有 {_money(wallet)} 金幣。"
                if wallet.strip().lower() != "inf":
                    con.execute("UPDATE users SET wallet=? WHERE user_id=?", (str(int(wallet)-amount), user_id))
                balance += amount
                action, detail = "deposit", "成員捐款"
            else:
                member = con.execute(
                    "SELECT 1 FROM guild_treasury_ledger WHERE guild_id=? AND user_id=? AND action='withdraw' LIMIT 1",
                    (guild_id, user_id),
                ).fetchone()
                # Permission is enforced by the command handler; this branch only performs atomic accounting.
                if balance < amount:
                    con.rollback()
                    return False, f"公會金庫不足，目前有 {_money(balance)} 金幣。"
                current_wallet = 0 if wallet.strip().lower() == "inf" else int(wallet)
                if wallet.strip().lower() != "inf" and current_wallet + amount > int("9" * 2000):
                    con.rollback()
                    return False, "提領後會超出錢包上限，交易已取消。"
                if wallet.strip().lower() != "inf":
                    con.execute("UPDATE users SET wallet=? WHERE user_id=?", (str(current_wallet+amount), user_id))
                balance -= amount
                action, detail = "withdraw", "公會金庫提領"
            con.execute(
                "UPDATE guild_treasuries SET balance=?,updated_at=? WHERE guild_id=?",
                (str(balance), _now(), guild_id),
            )
            self._ledger(con, guild_id, user_id, action, amount, balance, detail)
            con.commit()
            return True, f"{'已捐入' if deposit else '已提領'} **{_money(amount)}** 金幣。公會金庫餘額：**{_money(balance)}**。"

    def create_event(self, guild_id, creator_id, title, description, goal, reward_pool, duration_hours):
        now = _now()
        if not 2 <= len(title.strip()) <= 80:
            return False, "活動名稱必須是 2 至 80 個字。"
        if not 1 <= len(description.strip()) <= 500:
            return False, "活動說明必須是 1 至 500 個字。"
        if not 1 <= goal <= 1_000_000:
            return False, "共同目標必須介於 1 至 1,000,000。"
        if not 1 <= reward_pool <= MAX_AMOUNT:
            return False, "獎勵總額必須大於 0。"
        if not 1 <= duration_hours <= 168:
            return False, "活動時間必須介於 1 至 168 小時。"
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            self._ensure_treasury(con, guild_id)
            active = con.execute(
                "SELECT id FROM guild_economy_events WHERE guild_id=? AND status='active'", (guild_id,)
            ).fetchone()
            if active:
                con.rollback()
                return False, f"目前已有進行中的公會活動（#{active['id']}），請先結束或等待活動到期。"
            balance = int(con.execute(
                "SELECT balance FROM guild_treasuries WHERE guild_id=?", (guild_id,)
            ).fetchone()["balance"])
            if balance < reward_pool:
                con.rollback()
                return False, f"公會金庫不足以預留獎勵，需要 {_money(reward_pool)}，目前 {_money(balance)}。"
            ends_at = now + duration_hours * 3600
            cur = con.execute(
                "INSERT INTO guild_economy_events(guild_id,creator_id,title,description,goal,progress,reward_pool,ends_at,status,created_at) "
                "VALUES(?,?,?,?,?,0,?,?, 'active',?)",
                (guild_id, creator_id, title.strip(), description.strip(), goal, str(reward_pool), ends_at, now),
            )
            con.execute(
                "UPDATE guild_treasuries SET balance=?,updated_at=? WHERE guild_id=?",
                (str(balance-reward_pool), now, guild_id),
            )
            self._ledger(con, guild_id, creator_id, "event_reserve", -reward_pool, balance-reward_pool,
                         f"活動 #{cur.lastrowid} 預留獎勵")
            con.commit()
            return True, f"活動已建立：**{title.strip()}**（#{cur.lastrowid}），目標 {goal:,}，獎勵池 {_money(reward_pool)} 已從金庫預留。"

    def contribute(self, guild_id, user_id, amount):
        if not 1 <= amount <= MAX_AMOUNT:
            return False, "捐款金額不正確。"
        now = _now()
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            event = con.execute(
                "SELECT * FROM guild_economy_events WHERE guild_id=? AND status='active' ORDER BY id DESC LIMIT 1",
                (guild_id,),
            ).fetchone()
            if not event:
                con.rollback()
                return False, "目前沒有進行中的公會活動。"
            if now >= event["ends_at"]:
                con.execute("UPDATE guild_economy_events SET status='expired' WHERE id=?", (event["id"],))
                con.commit()
                return False, "活動已到期，不能再增加進度。"
            self._ensure_treasury(con, guild_id)
            user = con.execute("SELECT wallet FROM users WHERE user_id=?", (user_id,)).fetchone()
            if not user:
                con.execute("INSERT INTO users(user_id,guild_id,wallet,bank) VALUES(?,?, '1000','0')", (user_id,guild_id))
                wallet = "1000"
            else:
                wallet = str(user["wallet"])
            if wallet.strip().lower() != "inf" and int(wallet) < amount:
                con.rollback()
                return False, f"現金不足，你目前有 {_money(wallet)} 金幣。"
            if wallet.strip().lower() != "inf":
                con.execute("UPDATE users SET wallet=? WHERE user_id=?", (str(int(wallet)-amount), user_id))
            treasury = int(con.execute("SELECT balance FROM guild_treasuries WHERE guild_id=?", (guild_id,)).fetchone()["balance"])
            treasury += amount
            con.execute("UPDATE guild_treasuries SET balance=?,updated_at=? WHERE guild_id=?", (str(treasury), now, guild_id))
            con.execute(
                "INSERT INTO guild_economy_event_members(event_id,user_id,contribution,joined_at) VALUES(?,?,?,?) "
                "ON CONFLICT(event_id,user_id) DO UPDATE SET contribution=contribution+excluded.contribution",
                (event["id"], user_id, amount, now),
            )
            progress = min(int(event["goal"]), int(event["progress"]) + amount)
            status = "completed" if progress >= int(event["goal"]) else "active"
            con.execute("UPDATE guild_economy_events SET progress=?,status=? WHERE id=?", (progress,status,event["id"]))
            self._ledger(con, guild_id, user_id, "event_contribution", amount, treasury,
                         f"公會活動 #{event['id']} 共同捐款")
            con.commit()
            return True, f"已投入 **{_money(amount)}** 金幣。活動進度：**{progress:,}/{int(event['goal']):,}**。"

    def event_info(self, guild_id):
        now = _now()
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            event = con.execute(
                "SELECT * FROM guild_economy_events WHERE guild_id=? ORDER BY id DESC LIMIT 1", (guild_id,)
            ).fetchone()
            if event and event["status"] == "active" and now >= event["ends_at"]:
                con.execute("UPDATE guild_economy_events SET status='expired' WHERE id=?", (event["id"],))
                event = dict(event)
                event["status"] = "expired"
            if event:
                participants = con.execute(
                    "SELECT COUNT(*) FROM guild_economy_event_members WHERE event_id=? AND contribution>0",
                    (event["id"],),
                ).fetchone()[0]
                claimed = con.execute(
                    "SELECT COUNT(*) FROM guild_economy_event_claims WHERE event_id=?", (event["id"],)
                ).fetchone()[0]
                event = dict(event)
                event["participants"] = participants
                event["claimed"] = claimed
            con.commit()
        return event

    def claim_reward(self, guild_id, user_id):
        now = _now()
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            event = con.execute(
                "SELECT * FROM guild_economy_events WHERE guild_id=? ORDER BY id DESC LIMIT 1", (guild_id,)
            ).fetchone()
            if not event:
                con.rollback()
                return False, "目前沒有公會活動紀錄。"
            if event["status"] != "completed" or now >= event["ends_at"]:
                con.rollback()
                return False, "活動尚未達標、已到期或已結束，不能領取獎勵。"
            member = con.execute(
                "SELECT contribution FROM guild_economy_event_members WHERE event_id=? AND user_id=?",
                (event["id"], user_id),
            ).fetchone()
            if not member or int(member["contribution"]) <= 0:
                con.rollback()
                return False, "只有為本次活動貢獻過金幣的成員可以領獎。"
            already = con.execute(
                "SELECT 1 FROM guild_economy_event_claims WHERE event_id=? AND user_id=?",
                (event["id"], user_id),
            ).fetchone()
            if already:
                con.rollback()
                return False, "你已經領過這次活動獎勵。"
            total_members = int(con.execute(
                "SELECT COUNT(*) FROM guild_economy_event_members WHERE event_id=? AND contribution>0",
                (event["id"],),
            ).fetchone()[0])
            claimed_rows = con.execute(
                "SELECT amount FROM guild_economy_event_claims WHERE event_id=?", (event["id"],)
            ).fetchall()
            pool = int(event["reward_pool"])
            paid = sum(int(row["amount"]) for row in claimed_rows)
            remaining_claimants = total_members - len(claimed_rows)
            payout = pool - paid if remaining_claimants == 1 else pool // total_members
            if payout <= 0:
                con.rollback()
                return False, "本次活動獎勵已全部發放。"
            con.execute(
                "INSERT INTO guild_economy_event_claims(event_id,user_id,amount,claimed_at) VALUES(?,?,?,?)",
                (event["id"], user_id, str(payout), now),
            )
            con.execute(
                "UPDATE users SET wallet=CASE WHEN lower(wallet)='inf' THEN 'inf' ELSE CAST(CAST(wallet AS INTEGER)+? AS TEXT) END "
                "WHERE user_id=?", (payout, user_id)
            )
            con.commit()
            return True, f"已領取公會活動獎勵 **{_money(payout)}** 金幣。"

    def end_event(self, guild_id, actor_id):
        now = _now()
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            event = con.execute(
                "SELECT * FROM guild_economy_events WHERE guild_id=? AND status IN ('active','completed') ORDER BY id DESC LIMIT 1",
                (guild_id,),
            ).fetchone()
            if not event:
                con.rollback()
                return False, "沒有可結束的公會活動。"
            if event["status"] == "completed":
                # Reward pool was reserved; refund only the undistributed remainder.
                paid_rows = con.execute(
                    "SELECT amount FROM guild_economy_event_claims WHERE event_id=?", (event["id"],)
                ).fetchall()
                refund = max(0, int(event["reward_pool"]) - sum(int(row["amount"]) for row in paid_rows))
            else:
                refund = int(event["reward_pool"])
            self._ensure_treasury(con, guild_id)
            balance = int(con.execute(
                "SELECT balance FROM guild_treasuries WHERE guild_id=?", (guild_id,)
            ).fetchone()["balance"])
            balance += refund
            con.execute("UPDATE guild_treasuries SET balance=?,updated_at=? WHERE guild_id=?", (str(balance),now,guild_id))
            con.execute("UPDATE guild_economy_events SET status='ended' WHERE id=?", (event["id"],))
            self._ledger(con, guild_id, actor_id, "event_refund", refund, balance, f"結束活動 #{event['id']} 退回未發獎金")
            con.commit()
            return True, f"活動已結束，未發出的獎勵 **{_money(refund)}** 已退回公會金庫。"


class GuildEconomyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.store = GuildEconomyStore()

    async def _guild_only(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is not None:
            return True
        await interaction.response.send_message("這項功能只能在 Discord 伺服器使用。", ephemeral=True)
        return False

    async def _reply(self, interaction: discord.Interaction, message: str, *, public: bool = False):
        await interaction.response.send_message(message, ephemeral=not public)

    @app_commands.command(name=app_commands.locale_str("guild-bank", zh_TW="公會銀行"), description="查看金庫、捐款或由管理員提領共同資金")
    @app_commands.choices(action=[
        app_commands.Choice(name="查看金庫", value="view"),
        app_commands.Choice(name="捐款", value="deposit"),
        app_commands.Choice(name="管理員提領", value="withdraw"),
    ])
    @app_commands.rename(action=app_commands.locale_str("動作", zh_TW="動作"), amount=app_commands.locale_str("金額", zh_TW="金額"), public=app_commands.locale_str("公開", zh_TW="公開"))
    async def guild_bank(self, interaction: discord.Interaction, action: app_commands.Choice[str], amount: app_commands.Range[int, 1, MAX_AMOUNT] = 1, public: bool = False):
        if not await self._guild_only(interaction):
            return
        if action.value == "view":
            row, recent = self.store.treasury(interaction.guild_id)
            lines = [f"金庫餘額：**{_money(int(row['balance']))}** 金幣"]
            if recent:
                lines.append("\n**近期收支**")
                lines.extend(f"• <t:{int(r['created_at'])}:R> {r['detail']}：{int(r['amount']):+,}" for r in recent)
            await self._reply(interaction, "\n".join(lines), public=public)
            return
        if action.value == "withdraw":
            member = interaction.user
            if not isinstance(member, discord.Member) or not (member.guild_permissions.manage_guild or member.guild_permissions.administrator):
                await self._reply(interaction, "只有具備「管理伺服器」或管理員權限的成員可以提領公會資金。", public=False)
                return
        ok, message = self.store.transfer(interaction.guild_id, interaction.user.id, amount, action.value == "deposit")
        await self._reply(interaction, message, public=public if ok else False)

    @app_commands.command(name=app_commands.locale_str("guild-event", zh_TW="公會活動"), description="建立共同任務、投入金幣、查看進度及領取獎勵")
    @app_commands.choices(action=[
        app_commands.Choice(name="查看活動", value="view"),
        app_commands.Choice(name="建立活動（管理員）", value="create"),
        app_commands.Choice(name="投入共同目標", value="contribute"),
        app_commands.Choice(name="領取獎勵", value="claim"),
        app_commands.Choice(name="結束活動（管理員）", value="end"),
    ])
    @app_commands.rename(action=app_commands.locale_str("動作", zh_TW="動作"), title=app_commands.locale_str("活動名稱", zh_TW="活動名稱"), description=app_commands.locale_str("活動說明", zh_TW="活動說明"), goal=app_commands.locale_str("共同目標", zh_TW="共同目標"), reward_pool=app_commands.locale_str("獎勵總額", zh_TW="獎勵總額"), duration_hours=app_commands.locale_str("時數", zh_TW="時數"), amount=app_commands.locale_str("投入金額", zh_TW="投入金額"), public=app_commands.locale_str("公開", zh_TW="公開"))
    async def guild_event(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        title: str = "",
        description: str = "",
        goal: app_commands.Range[int, 1, 1_000_000] = 1000,
        reward_pool: app_commands.Range[int, 1, MAX_AMOUNT] = 1000,
        duration_hours: app_commands.Range[int, 1, 168] = 24,
        amount: app_commands.Range[int, 1, MAX_AMOUNT] = 100,
        public: bool = False,
    ):
        if not await self._guild_only(interaction):
            return
        admin = isinstance(interaction.user, discord.Member) and (
            interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator
        )
        if action.value in {"create", "end"} and not admin:
            await self._reply(interaction, "只有具備「管理伺服器」或管理員權限的成員可以管理公會活動。", public=False)
            return
        if action.value == "create":
            result = self.store.create_event(
                interaction.guild_id, interaction.user.id, title, description, goal, reward_pool, duration_hours
            )
            await self._reply(interaction, result[1], public=public if result[0] else False)
        elif action.value == "contribute":
            result = self.store.contribute(interaction.guild_id, interaction.user.id, amount)
            await self._reply(interaction, result[1], public=public if result[0] else False)
        elif action.value == "claim":
            result = self.store.claim_reward(interaction.guild_id, interaction.user.id)
            await self._reply(interaction, result[1], public=public if result[0] else False)
        elif action.value == "end":
            result = self.store.end_event(interaction.guild_id, interaction.user.id)
            await self._reply(interaction, result[1], public=public if result[0] else False)
        else:
            event = self.store.event_info(interaction.guild_id)
            if not event:
                await self._reply(interaction, "這個伺服器目前沒有公會活動紀錄。", public=public)
                return
            status = {"active": "進行中", "completed": "已達標", "expired": "已到期", "ended": "已結束"}.get(event["status"], event["status"])
            embed = discord.Embed(
                title=f"公會活動｜{event['title']}",
                description=f"{event['description']}\n\n**進度：** {int(event['progress']):,}/{int(event['goal']):,}\n**獎勵池：** {_money(int(event['reward_pool']))} 金幣\n**參與人數：** {event['participants']}\n**已領獎：** {event['claimed']} 人\n**狀態：** {status}\n**截止：** <t:{int(event['ends_at'])}:R>",
                color=0x5865F2 if event["status"] == "active" else 0x57F287 if event["status"] == "completed" else 0x95A5A6,
                timestamp=datetime.fromtimestamp(int(event["created_at"]), UTC),
            )
            await interaction.response.send_message(embed=embed, ephemeral=not public)


async def setup(bot: commands.Bot):
    await bot.add_cog(GuildEconomyCog(bot))
