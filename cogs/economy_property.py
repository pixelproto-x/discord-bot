from __future__ import annotations

import sqlite3
import time
from contextlib import closing

import discord
from discord import app_commands
from discord.ext import commands

from cogs.economy_world import DB_PATH, MAX_BALANCE, WorldEconomy, _is_inf, _money
from cogs.public_response import PublicizeView

MAX_PROPERTY_LEVEL = 20
PROPERTIES = {
    "studio": ("小套房", 50000, 350),
    "apartment": ("市區公寓", 180000, 1200),
    "storefront": ("街邊店面", 450000, 3200),
    "office": ("商辦樓層", 1200000, 9000),
    "mall": ("小型購物中心", 5000000, 42000),
}


class PropertyStore:
    def __init__(self):
        self.world = WorldEconomy()
        with closing(self.connect()) as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS world_properties (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    owner_id INTEGER NOT NULL,
                    property_type TEXT NOT NULL,
                    level INTEGER NOT NULL DEFAULT 1,
                    last_collect INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_world_properties_owner
                    ON world_properties(guild_id, owner_id, id);
            """)

    def connect(self):
        con = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=10000")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def list_properties(self, guild_id: int, owner_id: int | None = None):
        with closing(self.connect()) as con:
            if owner_id is None:
                return con.execute(
                    "SELECT id,owner_id,property_type,level,last_collect FROM world_properties WHERE guild_id=? ORDER BY id DESC LIMIT 10",
                    (guild_id,),
                ).fetchall()
            return con.execute(
                "SELECT id,owner_id,property_type,level,last_collect FROM world_properties WHERE guild_id=? AND owner_id=? ORDER BY id",
                (guild_id, owner_id),
            ).fetchall()

    def buy(self, guild_id: int, user_id: int, property_type: str):
        if property_type not in PROPERTIES:
            return False, "找不到這種房產。"
        label, cost, rent = PROPERTIES[property_type]
        now = int(time.time())
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            wallet = self.world.wallet(con, guild_id, user_id)
            if not _is_inf(wallet) and int(wallet) < cost:
                con.rollback()
                return False, f"購買 {label} 需要 {_money(cost)} 現金，目前有 {_money(wallet)}。"
            if not _is_inf(wallet):
                self.world.set_wallet(con, user_id, str(int(wallet) - cost))
            cur = con.execute(
                "INSERT INTO world_properties(guild_id,owner_id,property_type,level,last_collect,created_at) VALUES(?,?,?,1,?,?)",
                (guild_id, user_id, property_type, now, now),
            )
            self.world.ledger(con, guild_id, user_id, "property_buy", -cost, f"購買房產：{label} #{cur.lastrowid}")
            con.commit()
            return True, f"成功購買 {label}（房產編號 #{cur.lastrowid}）\n購置價格：{cost:,}\n每 24 小時基礎租金：{rent:,}\n可使用 /升級房產提高租金。"

    def collect(self, guild_id: int, user_id: int, property_id: int | None = None):
        now = int(time.time())
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            if property_id is None:
                rows = con.execute("SELECT * FROM world_properties WHERE guild_id=? AND owner_id=? ORDER BY id", (guild_id, user_id)).fetchall()
            else:
                rows = con.execute("SELECT * FROM world_properties WHERE guild_id=? AND owner_id=? AND id=?", (guild_id, user_id, property_id)).fetchall()
            if not rows:
                con.rollback()
                return False, "找不到你持有的房產。"
            collected = []
            total = 0
            for row in rows:
                elapsed = max(0, now - int(row["last_collect"]))
                periods = min(30, elapsed // 86400)
                if periods < 1:
                    continue
                label, _, base_rent = PROPERTIES[row["property_type"]]
                income = int(periods) * base_rent * int(row["level"])
                total += income
                collected.append((row, int(periods), income, label))
            if not collected:
                con.rollback()
                return False, "租金尚未累積滿 24 小時。"
            wallet = self.world.wallet(con, guild_id, user_id)
            if not _is_inf(wallet) and int(wallet) + total > MAX_BALANCE:
                con.rollback()
                return False, "入帳後現金會超出系統上限，租金未領取。"
            if not _is_inf(wallet):
                self.world.set_wallet(con, user_id, str(int(wallet) + total))
            lines = []
            for row, periods, income, label in collected:
                con.execute("UPDATE world_properties SET last_collect=last_collect+? WHERE id=? AND guild_id=? AND owner_id=?", (periods * 86400, row["id"], guild_id, user_id))
                self.world.ledger(con, guild_id, user_id, "property_rent", income, f"房產租金：{label} #{row['id']}，{periods} 期")
                lines.append(f"房產 #{row['id']} {label}：{income:,}（{periods} 期）")
            con.commit()
            return True, "租金已入帳\n" + "\n".join(lines) + f"\n合計：{total:,}"

    def upgrade(self, guild_id: int, user_id: int, property_id: int):
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM world_properties WHERE guild_id=? AND owner_id=? AND id=?", (guild_id, user_id, property_id)).fetchone()
            if not row:
                con.rollback()
                return False, "找不到你持有的這筆房產。"
            level = int(row["level"])
            if level >= MAX_PROPERTY_LEVEL:
                con.rollback()
                return False, f"房產已達最高等級 {MAX_PROPERTY_LEVEL}。"
            label, base_cost, base_rent = PROPERTIES[row["property_type"]]
            cost = max(1000, base_cost * level // 2)
            wallet = self.world.wallet(con, guild_id, user_id)
            if not _is_inf(wallet) and int(wallet) < cost:
                con.rollback()
                return False, f"升級需要 {cost:,} 現金，目前有 {_money(wallet)}。"
            if not _is_inf(wallet):
                self.world.set_wallet(con, user_id, str(int(wallet) - cost))
            con.execute("UPDATE world_properties SET level=level+1 WHERE guild_id=? AND owner_id=? AND id=?", (guild_id, user_id, property_id))
            self.world.ledger(con, guild_id, user_id, "property_upgrade", -cost, f"房產 #{property_id} 升級至 {level+1} 級")
            con.commit()
            return True, f"{label} #{property_id} 已升至 Lv.{level+1}\n升級花費：{cost:,}\n每 24 小時基礎租金：{base_rent * (level+1):,}"

    def sell(self, guild_id: int, user_id: int, property_id: int):
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM world_properties WHERE guild_id=? AND owner_id=? AND id=?", (guild_id, user_id, property_id)).fetchone()
            if not row:
                con.rollback()
                return False, "找不到你持有的這筆房產。"
            label, base_cost, _ = PROPERTIES[row["property_type"]]
            refund = base_cost * int(row["level"]) * 60 // 100
            wallet = self.world.wallet(con, guild_id, user_id)
            if not _is_inf(wallet) and int(wallet) + refund > MAX_BALANCE:
                con.rollback()
                return False, "出售後現金會超出系統上限。"
            if not _is_inf(wallet):
                self.world.set_wallet(con, user_id, str(int(wallet) + refund))
            con.execute("DELETE FROM world_properties WHERE id=? AND guild_id=? AND owner_id=?", (property_id, guild_id, user_id))
            self.world.ledger(con, guild_id, user_id, "property_sell", refund, f"出售房產：{label} #{property_id}")
            con.commit()
            return True, f"已出售 {label} #{property_id}，回收 {refund:,} 現金（估值 60% × 等級）。"


class PropertyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = PropertyStore()

    async def _guild(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id is None:
            await interaction.response.send_message("房產系統只能在伺服器裡使用。", ephemeral=True)
            return False
        return True

    async def _reply(self, interaction: discord.Interaction, content: str, public: bool):
        if public:
            await interaction.response.send_message(content, ephemeral=False, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.response.send_message(content, ephemeral=True, view=PublicizeView(interaction.user.id, content=content), allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name=app_commands.locale_str("properties", zh_TW="房產"), description="查看自己的房產或伺服器近期房產")
    @app_commands.choices(scope=[
        app_commands.Choice(name="我的房產", value="mine"),
        app_commands.Choice(name="伺服器近期房產", value="server"),
    ])
    @app_commands.rename(scope=app_commands.locale_str("scope", zh_TW="範圍"), public=app_commands.locale_str("public", zh_TW="公開"))
    @app_commands.describe(public="是否將查詢結果公開發到頻道")
    async def properties(self, interaction: discord.Interaction, scope: app_commands.Choice[str] = None, public: bool = False):
        if not await self._guild(interaction):
            return
        rows = self.store.list_properties(interaction.guild_id, interaction.user.id if scope is None or scope.value == "mine" else None)
        if not rows:
            text = "目前沒有房產紀錄。"
        else:
            lines = []
            for row in rows:
                label, _, rent = PROPERTIES[row["property_type"]]
                ready_in = max(0, 86400 - (int(time.time()) - int(row["last_collect"])))
                rent_status = "可領取" if ready_in == 0 else f"約 {ready_in//3600} 小時後可領"
                lines.append(f"#{row['id']} {label} · Lv.{row['level']}\n持有人：<@{row['owner_id']}>｜基礎日租：{rent * int(row['level']):,}｜{rent_status}")
            text = "房產清單\n\n" + "\n\n".join(lines)
        await self._reply(interaction, text, public)

    @app_commands.command(name=app_commands.locale_str("buy-property", zh_TW="購買房產"), description="購買一筆新的模擬房產")
    @app_commands.choices(property_type=[
        app_commands.Choice(name="小套房（50,000）", value="studio"),
        app_commands.Choice(name="市區公寓（180,000）", value="apartment"),
        app_commands.Choice(name="街邊店面（450,000）", value="storefront"),
        app_commands.Choice(name="商辦樓層（1,200,000）", value="office"),
        app_commands.Choice(name="小型購物中心（5,000,000）", value="mall"),
    ])
    @app_commands.rename(property_type=app_commands.locale_str("property_type", zh_TW="房產類型"), public=app_commands.locale_str("public", zh_TW="公開"))
    @app_commands.describe(public="是否將購買結果公開發到頻道")
    async def buy_property(self, interaction: discord.Interaction, property_type: app_commands.Choice[str], public: bool = False):
        if not await self._guild(interaction):
            return
        _, result = self.store.buy(interaction.guild_id, interaction.user.id, property_type.value)
        await self._reply(interaction, result, public)

    @app_commands.command(name=app_commands.locale_str("collect-rent", zh_TW="領取租金"), description="領取已累積的房產租金")
    @app_commands.rename(property_id=app_commands.locale_str("property_id", zh_TW="房產編號"), public=app_commands.locale_str("public", zh_TW="公開"))
    @app_commands.describe(property_id="留空即可領取所有已到期房產的租金", public="是否將領租結果公開發到頻道")
    async def collect_rent(self, interaction: discord.Interaction, property_id: app_commands.Range[int, 1, 2147483647] = None, public: bool = False):
        if not await self._guild(interaction):
            return
        _, result = self.store.collect(interaction.guild_id, interaction.user.id, property_id)
        await self._reply(interaction, result, public)

    @app_commands.command(name=app_commands.locale_str("upgrade-property", zh_TW="升級房產"), description="升級房產，提高每日租金")
    @app_commands.rename(property_id=app_commands.locale_str("property_id", zh_TW="房產編號"), public=app_commands.locale_str("public", zh_TW="公開"))
    @app_commands.describe(property_id="你持有的房產編號", public="是否將升級結果公開發到頻道")
    async def upgrade_property(self, interaction: discord.Interaction, property_id: app_commands.Range[int, 1, 2147483647], public: bool = False):
        if not await self._guild(interaction):
            return
        _, result = self.store.upgrade(interaction.guild_id, interaction.user.id, property_id)
        await self._reply(interaction, result, public)

    @app_commands.command(name=app_commands.locale_str("sell-property", zh_TW="出售房產"), description="出售房產並回收部分資金")
    @app_commands.rename(property_id=app_commands.locale_str("property_id", zh_TW="房產編號"), public=app_commands.locale_str("public", zh_TW="公開"))
    @app_commands.describe(property_id="你持有的房產編號", public="是否將出售結果公開發到頻道")
    async def sell_property(self, interaction: discord.Interaction, property_id: app_commands.Range[int, 1, 2147483647], public: bool = False):
        if not await self._guild(interaction):
            return
        _, result = self.store.sell(interaction.guild_id, interaction.user.id, property_id)
        await self._reply(interaction, result, public)


async def setup(bot: commands.Bot):
    await bot.add_cog(PropertyCog(bot))
