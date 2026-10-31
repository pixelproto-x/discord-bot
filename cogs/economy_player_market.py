from __future__ import annotations

import math
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from cogs.economy_world import DB_PATH, GOODS, MAX_BALANCE, WorldEconomy
from cogs.public_response import PublicizeView

UTC = timezone.utc
MAX_LISTING_PRICE = 1_000_000_000
MAX_QUANTITY = 100_000


class PlayerMarketStore:
    def __init__(self):
        self.world = WorldEconomy()
        with closing(self.connect()) as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS player_market_listings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    seller_id INTEGER NOT NULL,
                    item TEXT NOT NULL,
                    quantity INTEGER NOT NULL CHECK(quantity >= 0),
                    unit_price INTEGER NOT NULL CHECK(unit_price > 0),
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_player_market_active
                    ON player_market_listings(guild_id, quantity, id);
            """)

    def connect(self):
        con = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=10000")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    @staticmethod
    def _wallet_value(value: str) -> int | None:
        return None if str(value).strip().lower() in {"inf", "-inf"} else int(value)

    def list_items(self, guild_id: int, limit: int = 10):
        with closing(self.connect()) as con:
            return con.execute(
                "SELECT id,seller_id,item,quantity,unit_price,created_at "
                "FROM player_market_listings WHERE guild_id=? AND quantity>0 ORDER BY id DESC LIMIT ?",
                (guild_id, limit),
            ).fetchall()

    def create_listing(self, guild_id: int, user_id: int, item: str, quantity: int, unit_price: int):
        if item not in GOODS:
            return False, "這個商品不在可交易清單裡"
        if not 1 <= quantity <= MAX_QUANTITY:
            return False, f"上架數量必須介於 1 至 {MAX_QUANTITY:,}"
        if not 1 <= unit_price <= MAX_LISTING_PRICE:
            return False, f"單價必須介於 1 至 {MAX_LISTING_PRICE:,}"
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            owned = con.execute(
                "SELECT quantity FROM world_inventory WHERE guild_id=? AND user_id=? AND item=?",
                (guild_id, user_id, item),
            ).fetchone()
            have = int(owned["quantity"]) if owned else 0
            if have < quantity:
                con.rollback()
                return False, f"持有數量不足 目前只有 {have:,} 件"
            con.execute(
                "UPDATE world_inventory SET quantity=quantity-? WHERE guild_id=? AND user_id=? AND item=?",
                (quantity, guild_id, user_id, item),
            )
            cur = con.execute(
                "INSERT INTO player_market_listings(guild_id,seller_id,item,quantity,unit_price,created_at) VALUES(?,?,?,?,?,?)",
                (guild_id, user_id, item, quantity, unit_price, int(datetime.now(UTC).timestamp())),
            )
            con.commit()
            return True, f"已上架商品編號 **{cur.lastrowid}**\n商品 **{GOODS[item][0]}**\n數量 **{quantity:,}**\n單價 **NT$ {unit_price:,}**\n總標價 **NT$ {quantity * unit_price:,}**"

    def buy_listing(self, guild_id: int, buyer_id: int, listing_id: int, quantity: int):
        if quantity < 1 or quantity > MAX_QUANTITY:
            return False, f"購買數量必須介於 1 至 {MAX_QUANTITY:,}"
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            listing = con.execute(
                "SELECT * FROM player_market_listings WHERE guild_id=? AND id=? AND quantity>0",
                (guild_id, listing_id),
            ).fetchone()
            if not listing:
                con.rollback()
                return False, "找不到這筆上架商品 可能已售完或已下架"
            if int(listing["seller_id"]) == buyer_id:
                con.rollback()
                return False, "不能購買自己上架的商品"
            available = int(listing["quantity"])
            if quantity > available:
                con.rollback()
                return False, f"數量不足 這筆上架目前剩下 {available:,} 件"
            item = str(listing["item"])
            unit_price = int(listing["unit_price"])
            total = unit_price * quantity
            if total > MAX_BALANCE:
                con.rollback()
                return False, "這筆交易金額超過系統上限"
            self.world.ensure_user(con, guild_id, buyer_id)
            self.world.ensure_user(con, guild_id, int(listing["seller_id"]))
            buyer_wallet = self.world.wallet(con, guild_id, buyer_id)
            seller_wallet = self.world.wallet(con, guild_id, int(listing["seller_id"]))
            buyer_value = self._wallet_value(buyer_wallet)
            seller_value = self._wallet_value(seller_wallet)
            if buyer_value is not None and buyer_value < total:
                con.rollback()
                return False, f"現金不足 需要 NT$ {total:,} 目前有 {_money(buyer_wallet)}"
            if seller_value is not None and seller_value + total > MAX_BALANCE:
                con.rollback()
                return False, "賣家收款後會超出現金上限 交易取消"
            if buyer_value is not None:
                self.world.set_wallet(con, guild_id, buyer_id, str(buyer_value - total))
            if seller_value is not None:
                self.world.set_wallet(con, guild_id, int(listing["seller_id"]), str(seller_value + total))
            con.execute(
                "UPDATE player_market_listings SET quantity=quantity-? WHERE id=?",
                (quantity, listing_id),
            )
            con.execute(
                "INSERT INTO world_inventory(guild_id,user_id,item,quantity) VALUES(?,?,?,?) "
                "ON CONFLICT(guild_id,user_id,item) DO UPDATE SET quantity=quantity+excluded.quantity",
                (guild_id, buyer_id, item, quantity),
            )
            seller_id = int(listing["seller_id"])
            self.world.ledger(con, guild_id, buyer_id, "player_market_buy", -total, f"玩家市場買入 {GOODS[item][0]} x{quantity}")
            self.world.ledger(con, guild_id, seller_id, "player_market_sell", total, f"玩家市場售出 {GOODS[item][0]} x{quantity}")
            con.commit()
            return True, f"交易完成\n商品 **{GOODS[item][0]} × {quantity:,}**\n單價 **NT$ {unit_price:,}**\n總額 **NT$ {total:,}**\n賣家 <@{seller_id}>"

    def cancel_listing(self, guild_id: int, user_id: int, listing_id: int):
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            listing = con.execute(
                "SELECT * FROM player_market_listings WHERE guild_id=? AND id=? AND quantity>0",
                (guild_id, listing_id),
            ).fetchone()
            if not listing:
                con.rollback()
                return False, "找不到仍在販售中的商品"
            if int(listing["seller_id"]) != user_id:
                con.rollback()
                return False, "只能下架自己刊登的商品"
            item = str(listing["item"])
            quantity = int(listing["quantity"])
            con.execute(
                "UPDATE player_market_listings SET quantity=0 WHERE id=?",
                (listing_id,),
            )
            con.execute(
                "INSERT INTO world_inventory(guild_id,user_id,item,quantity) VALUES(?,?,?,?) "
                "ON CONFLICT(guild_id,user_id,item) DO UPDATE SET quantity=quantity+excluded.quantity",
                (guild_id, user_id, item, quantity),
            )
            con.commit()
            return True, f"已下架商品編號 **{listing_id}**\n返還 **{GOODS[item][0]} × {quantity:,}**"


def _money(value: str) -> str:
    return "∞（無限）" if str(value).strip().lower() == "inf" else f"{int(value):,}"


class PlayerMarketCog(commands.GroupCog, group_name=app_commands.locale_str("player-market", zh_TW="玩家市場"), group_description="玩家自訂商品交易"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = PlayerMarketStore()

    async def _guild_only(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id is None:
            await interaction.response.send_message("玩家交易市場只能在伺服器裡使用", ephemeral=True)
            return False
        return True

    async def _reply(self, interaction: discord.Interaction, content: str, public: bool):
        if public:
            await interaction.response.send_message(content, ephemeral=False, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.response.send_message(
                content,
                ephemeral=True,
                view=PublicizeView(interaction.user.id, content=content),
                allowed_mentions=discord.AllowedMentions.none(),
            )

    @app_commands.command(name=app_commands.locale_str("player-market", zh_TW="玩家市場"), description="查看玩家自行定價上架的商品")
    @app_commands.describe(public="是否將這次查詢公開發到頻道")
    @app_commands.rename(public=app_commands.locale_str("public", zh_TW="公開"))
    async def player_market(self, interaction: discord.Interaction, public: bool = False):
        if not await self._guild_only(interaction):
            return
        rows = self.store.list_items(interaction.guild_id, 10)
        if not rows:
            await self._reply(interaction, "目前沒有玩家上架的商品", public)
            return
        lines = []
        for row in rows:
            label = GOODS.get(row["item"], (row["item"],))[0]
            lines.append(
                f"**#{row['id']} {label}**\n數量 {int(row['quantity']):,}｜單價 NT$ {int(row['unit_price']):,}｜賣家 <@{int(row['seller_id'])}>"
            )
        await self._reply(interaction, "玩家交易市場｜最新 10 筆\n\n" + "\n\n".join(lines), public)

    @app_commands.command(name=app_commands.locale_str("player-list", zh_TW="玩家上架"), description="用自己持有的商品和自訂單價建立販售清單")
    @app_commands.choices(item=[
        app_commands.Choice(name="可口可樂 600ml", value="coca_cola_600"),
        app_commands.Choice(name="統一肉燥麵 5 入組", value="uni_noodles_5"),
        app_commands.Choice(name="義美小泡芙 57g", value="ime_puff"),
        app_commands.Choice(name="義美豆奶 400ml", value="ime_soymilk"),
        app_commands.Choice(name="麥當勞大麥克套餐", value="big_mac_meal"),
        app_commands.Choice(name="舒潔抽取式衛生紙 12 包", value="scott_tissue"),
        app_commands.Choice(name="白蘭洗衣精 1.8kg", value="persil_detergent"),
        app_commands.Choice(name="3M Scotch 膠帶", value="scotch_tape"),
        app_commands.Choice(name="Nike Air Force 1 '07", value="nike_af1"),
        app_commands.Choice(name="adidas 運動外套", value="adidas_track_top"),
        app_commands.Choice(name="Apple AirPods 4", value="airpods4"),
        app_commands.Choice(name="Apple iPhone 17 256GB", value="iphone17"),
        app_commands.Choice(name="Apple iPad 11 吋 128GB Wi-Fi", value="ipad11"),
        app_commands.Choice(name="Apple MacBook Air 13 吋", value="macbook_air"),
        app_commands.Choice(name="Sony PlayStation 5 Slim", value="ps5_slim"),
        app_commands.Choice(name="Nintendo Switch 2", value="switch2"),
        app_commands.Choice(name="NVIDIA RTX 5070 顯示卡", value="rtx5070"),
        app_commands.Choice(name="ASUS RTX 5070 電競筆電", value="asus_rtx5070_laptop"),
        app_commands.Choice(name="Apple iPhone 17 Pro 256GB", value="iphone17_pro"),
        app_commands.Choice(name="Toyota Corolla Cross 汽油豪華型", value="toyota_corolla_cross"),
    ])
    @app_commands.rename(item=app_commands.locale_str("商品", zh_TW="商品"), quantity=app_commands.locale_str("數量", zh_TW="數量"), unit_price=app_commands.locale_str("unit_price", zh_TW="單價"), public=app_commands.locale_str("公開", zh_TW="公開"))
    @app_commands.describe(quantity="要出售的數量", unit_price="每件商品的自訂售價（新台幣）", public="是否將上架結果公開發到頻道")
    async def player_list(self, interaction: discord.Interaction, item: app_commands.Choice[str], quantity: app_commands.Range[int, 1, MAX_QUANTITY], unit_price: app_commands.Range[int, 1, MAX_LISTING_PRICE], public: bool = False):
        if not await self._guild_only(interaction):
            return
        ok, message = self.store.create_listing(interaction.guild_id, interaction.user.id, item.value, quantity, unit_price)
        await self._reply(interaction, message, public)

    @app_commands.command(name=app_commands.locale_str("player-buy", zh_TW="玩家購買"), description="購買其他玩家上架的商品")
    @app_commands.rename(listing_id=app_commands.locale_str("listing_id", zh_TW="商品編號"), quantity=app_commands.locale_str("數量", zh_TW="數量"), public=app_commands.locale_str("公開", zh_TW="公開"))
    @app_commands.describe(listing_id="在玩家市場看到的商品編號", quantity="購買數量", public="是否將購買結果公開發到頻道")
    async def player_buy(self, interaction: discord.Interaction, listing_id: app_commands.Range[int, 1, 2_147_483_647], quantity: app_commands.Range[int, 1, MAX_QUANTITY] = 1, public: bool = False):
        if not await self._guild_only(interaction):
            return
        ok, message = self.store.buy_listing(interaction.guild_id, interaction.user.id, listing_id, quantity)
        await self._reply(interaction, message, public)

    @app_commands.command(name=app_commands.locale_str("player-unlist", zh_TW="玩家下架"), description="下架自己販售中的商品並取回未售出的數量")
    @app_commands.rename(listing_id=app_commands.locale_str("listing-id", zh_TW="商品編號"), public=app_commands.locale_str("公開", zh_TW="公開"))
    @app_commands.describe(listing_id="在玩家市場看到的商品編號", public="是否將下架結果公開發到頻道")
    async def player_unlist(self, interaction: discord.Interaction, listing_id: app_commands.Range[int, 1, 2_147_483_647], public: bool = False):
        if not await self._guild_only(interaction):
            return
        ok, message = self.store.cancel_listing(interaction.guild_id, interaction.user.id, listing_id)
        await self._reply(interaction, message, public)


async def setup(bot: commands.Bot):
    await bot.add_cog(PlayerMarketCog(bot))
