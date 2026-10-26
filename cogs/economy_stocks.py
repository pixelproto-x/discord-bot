from __future__ import annotations

import random
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from cogs.economy_world import DB_PATH, MAX_BALANCE, WorldEconomy, _is_inf, _money
from cogs.public_response import PublicizeView

UTC = timezone.utc
STARTING_PRICES = {
    "TSMC": ("台積電", 98000, "半導體製造"),
    "AAPL": ("蘋果", 22500, "消費電子"),
    "NVDA": ("輝達", 14500, "AI 與 GPU"),
    "MSFT": ("微軟", 14500, "軟體與雲端"),
    "SONY": ("索尼", 8500, "遊戲與電子"),
    "TOYOTA": ("豐田汽車", 9000, "汽車製造"),
    "KO": ("可口可樂", 2500, "飲料與消費品"),
    "DIS": ("迪士尼", 4200, "娛樂與媒體"),
}
MAX_SHARES_PER_ORDER = 100000
MAX_PRICE = 10**18


class StockStore:
    def __init__(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.world = WorldEconomy()
        with closing(self.connect()) as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS stock_quotes (
                    symbol TEXT PRIMARY KEY,
                    price INTEGER NOT NULL,
                    previous_price INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS stock_holdings (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    shares INTEGER NOT NULL DEFAULT 0,
                    average_cost INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id, symbol)
                );
                CREATE TABLE IF NOT EXISTS stock_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    action TEXT NOT NULL,
                    shares INTEGER NOT NULL,
                    price INTEGER NOT NULL,
                    total INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                );
            """)
            now = int(time.time())
            for symbol, (_, price, _) in STARTING_PRICES.items():
                con.execute(
                    "INSERT OR IGNORE INTO stock_quotes(symbol,price,previous_price,updated_at) VALUES(?,?,?,?)",
                    (symbol, price, price, now),
                )

    def connect(self):
        con = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=10000")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _refresh_quotes(self, con):
        now = int(time.time())
        for symbol, (_, starting_price, _) in STARTING_PRICES.items():
            row = con.execute("SELECT * FROM stock_quotes WHERE symbol=?", (symbol,)).fetchone()
            if row is None:
                con.execute(
                    "INSERT INTO stock_quotes(symbol,price,previous_price,updated_at) VALUES(?,?,?,?)",
                    (symbol, starting_price, starting_price, now),
                )
                continue
            elapsed = max(0, now - int(row["updated_at"]))
            steps = min(24, elapsed // 300)
            if steps < 1:
                continue
            price = int(row["price"])
            previous = price
            for _ in range(int(steps)):
                # Simulated movement, capped per update so prices cannot go negative.
                change_pct = random.uniform(-0.035, 0.035)
                price = max(1, min(MAX_PRICE, int(price * (1 + change_pct))))
            con.execute(
                "UPDATE stock_quotes SET price=?,previous_price=?,updated_at=? WHERE symbol=?",
                (price, previous, now, symbol),
            )

    def quotes(self, guild_id: int):
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            self._refresh_quotes(con)
            rows = con.execute("SELECT * FROM stock_quotes ORDER BY symbol").fetchall()
            con.commit()
            return rows

    def trade(self, guild_id: int, user_id: int, symbol: str, action: str, shares: int):
        if symbol not in STARTING_PRICES:
            return False, "找不到這支股票。"
        if not 1 <= shares <= MAX_SHARES_PER_ORDER:
            return False, "每筆交易數量需介於 1 至 100,000 股。"
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            self._refresh_quotes(con)
            quote = con.execute("SELECT price FROM stock_quotes WHERE symbol=?", (symbol,)).fetchone()
            price = int(quote["price"])
            total = price * shares
            if total > MAX_BALANCE:
                con.rollback()
                return False, "交易總額超過系統上限。"
            world = self.world
            world.ensure_user(con, guild_id, user_id)
            wallet = world.wallet(con, guild_id, user_id)
            holding = con.execute(
                "SELECT shares,average_cost FROM stock_holdings WHERE guild_id=? AND user_id=? AND symbol=?",
                (guild_id, user_id, symbol),
            ).fetchone()
            held = int(holding["shares"]) if holding else 0
            avg_cost = int(holding["average_cost"]) if holding else 0

            if action == "buy":
                cost = total
                if not _is_inf(wallet) and int(wallet) < cost:
                    con.rollback()
                    return False, "現金不足，買進需要 NT$ " + format(cost, ",") + "，目前有 " + _money(wallet) + "。"
                new_shares = held + shares
                new_average = ((held * avg_cost) + cost) // new_shares
                if not _is_inf(wallet):
                    world.set_wallet(con, user_id, str(int(wallet) - cost))
                con.execute(
                    "INSERT INTO stock_holdings(guild_id,user_id,symbol,shares,average_cost) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(guild_id,user_id,symbol) DO UPDATE SET shares=excluded.shares,average_cost=excluded.average_cost",
                    (guild_id, user_id, symbol, new_shares, new_average),
                )
                world.ledger(con, guild_id, user_id, "stock_buy", -cost, "股票買進 " + symbol + " x" + str(shares))
            elif action == "sell":
                if held < shares:
                    con.rollback()
                    return False, "持股不足，目前持有 " + format(held, ",") + " 股。"
                if not _is_inf(wallet) and int(wallet) + total > MAX_BALANCE:
                    con.rollback()
                    return False, "賣出後現金會超出系統上限，交易取消。"
                if not _is_inf(wallet):
                    world.set_wallet(con, user_id, str(int(wallet) + total))
                remaining = held - shares
                if remaining:
                    con.execute(
                        "UPDATE stock_holdings SET shares=? WHERE guild_id=? AND user_id=? AND symbol=?",
                        (remaining, guild_id, user_id, symbol),
                    )
                else:
                    con.execute(
                        "DELETE FROM stock_holdings WHERE guild_id=? AND user_id=? AND symbol=?",
                        (guild_id, user_id, symbol),
                    )
                world.ledger(con, guild_id, user_id, "stock_sell", total, "股票賣出 " + symbol + " x" + str(shares))
            else:
                con.rollback()
                return False, "不支援的交易操作。"

            now = int(time.time())
            con.execute(
                "INSERT INTO stock_trades(guild_id,user_id,symbol,action,shares,price,total,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (guild_id, user_id, symbol, action, shares, price, total, now),
            )
            con.commit()
            verb = "買進" if action == "buy" else "賣出"
            return True, verb + "完成：" + STARTING_PRICES[symbol][0] + "（" + symbol + "） x" + format(shares, ",") + " 股\n成交單價：NT$ " + format(price, ",") + "\n成交總額：NT$ " + format(total, ",") + "\n這是伺服器內的模擬股票，不是真實證券交易。"

    def portfolio(self, guild_id: int, user_id: int):
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            self._refresh_quotes(con)
            rows = con.execute(
                "SELECT h.symbol,h.shares,h.average_cost,q.price FROM stock_holdings h JOIN stock_quotes q ON q.symbol=h.symbol WHERE h.guild_id=? AND h.user_id=? AND h.shares>0 ORDER BY h.symbol",
                (guild_id, user_id),
            ).fetchall()
            con.commit()
        return rows

    def history(self, guild_id: int, user_id: int, limit: int = 8):
        with closing(self.connect()) as con:
            return con.execute(
                "SELECT * FROM stock_trades WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT ?",
                (guild_id, user_id, limit),
            ).fetchall()


class StockCog(commands.GroupCog, group_name=app_commands.locale_str("stocks", zh_TW="股票"), group_description="模擬股票交易"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = StockStore()

    async def _guild(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id is not None:
            return True
        await interaction.response.send_message("股票功能只能在伺服器裡使用。", ephemeral=True)
        return False

    async def _reply(self, interaction: discord.Interaction, content: str, public: bool):
        if public:
            await interaction.response.send_message(content, ephemeral=False, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.response.send_message(
                content, ephemeral=True,
                view=PublicizeView(interaction.user.id, content=content),
                allowed_mentions=discord.AllowedMentions.none(),
            )

    @app_commands.command(name=app_commands.locale_str("stock-market", zh_TW="股票市場"), description="查看伺服器模擬股票報價")
    @app_commands.rename(public=app_commands.locale_str("public", zh_TW="公開"))
    @app_commands.describe(public="是否將報價公開發到頻道")
    async def stock_market(self, interaction: discord.Interaction, public: bool = False):
        if not await self._guild(interaction):
            return
        rows = self.store.quotes(interaction.guild_id)
        lines = []
        for row in rows:
            symbol = row["symbol"]
            label = STARTING_PRICES[symbol][0]
            price = int(row["price"])
            previous = int(row["previous_price"])
            change = ((price - previous) / previous * 100) if previous else 0
            sign = "+" if change >= 0 else ""
            lines.append("**" + symbol + "｜" + label + "**　NT$ " + format(price, ",") + "　（" + sign + format(change, ".2f") + "%）")
        await self._reply(interaction, "模擬股票市場\n\n" + "\n".join(lines) + "\n\n每 5 分鐘更新一次模擬價格；漲跌為隨機模擬，並非真實市場行情。", public)

    @app_commands.command(name=app_commands.locale_str("stock-buy", zh_TW="股票買進"), description="使用伺服器現金買進模擬股票")
    @app_commands.choices(symbol=[app_commands.Choice(name=key + "｜" + value[0], value=key) for key, value in STARTING_PRICES.items()])
    @app_commands.rename(symbol=app_commands.locale_str("symbol", zh_TW="股票代號"), shares=app_commands.locale_str("shares", zh_TW="股數"), public=app_commands.locale_str("public", zh_TW="公開"))
    @app_commands.describe(shares="買進股數", public="是否將交易結果公開發到頻道")
    async def stock_buy(self, interaction: discord.Interaction, symbol: app_commands.Choice[str], shares: app_commands.Range[int, 1, MAX_SHARES_PER_ORDER], public: bool = False):
        if not await self._guild(interaction):
            return
        _, message = self.store.trade(interaction.guild_id, interaction.user.id, symbol.value, "buy", shares)
        await self._reply(interaction, message, public)

    @app_commands.command(name=app_commands.locale_str("stock-sell", zh_TW="股票賣出"), description="賣出自己持有的模擬股票")
    @app_commands.choices(symbol=[app_commands.Choice(name=key + "｜" + value[0], value=key) for key, value in STARTING_PRICES.items()])
    @app_commands.rename(symbol=app_commands.locale_str("symbol", zh_TW="股票代號"), shares=app_commands.locale_str("shares", zh_TW="股數"), public=app_commands.locale_str("public", zh_TW="公開"))
    @app_commands.describe(shares="賣出股數", public="是否將交易結果公開發到頻道")
    async def stock_sell(self, interaction: discord.Interaction, symbol: app_commands.Choice[str], shares: app_commands.Range[int, 1, MAX_SHARES_PER_ORDER], public: bool = False):
        if not await self._guild(interaction):
            return
        _, message = self.store.trade(interaction.guild_id, interaction.user.id, symbol.value, "sell", shares)
        await self._reply(interaction, message, public)

    @app_commands.command(name=app_commands.locale_str("stock-portfolio", zh_TW="股票持倉"), description="查看持股、市值與未實現損益")
    @app_commands.rename(public=app_commands.locale_str("public", zh_TW="公開"))
    @app_commands.describe(public="是否將持倉結果公開發到頻道")
    async def stock_portfolio(self, interaction: discord.Interaction, public: bool = False):
        if not await self._guild(interaction):
            return
        rows = self.store.portfolio(interaction.guild_id, interaction.user.id)
        if not rows:
            text = "目前沒有持有任何模擬股票。"
        else:
            lines = []
            total_value = 0
            total_cost = 0
            for row in rows:
                symbol = row["symbol"]
                shares = int(row["shares"])
                price = int(row["price"])
                cost = int(row["average_cost"]) * shares
                value = price * shares
                profit = value - cost
                total_value += value
                total_cost += cost
                sign = "+" if profit >= 0 else ""
                lines.append("**" + symbol + "｜" + STARTING_PRICES[symbol][0] + "**\n持有 " + format(shares, ",") + " 股｜均價 NT$ " + format(int(row["average_cost"]), ",") + "\n現值 NT$ " + format(value, ",") + "｜未實現損益 " + sign + format(profit, ","))
            total_profit = total_value - total_cost
            sign = "+" if total_profit >= 0 else ""
            text = "我的股票持倉\n\n" + "\n\n".join(lines) + "\n\n總市值：NT$ " + format(total_value, ",") + "\n總成本：NT$ " + format(total_cost, ",") + "\n總未實現損益： " + sign + format(total_profit, ",")
        await self._reply(interaction, text, public)

    @app_commands.command(name=app_commands.locale_str("stock-history", zh_TW="股票紀錄"), description="查看最近股票買賣紀錄")
    @app_commands.rename(public=app_commands.locale_str("public", zh_TW="公開"))
    @app_commands.describe(public="是否將紀錄公開發到頻道")
    async def stock_history(self, interaction: discord.Interaction, public: bool = False):
        if not await self._guild(interaction):
            return
        rows = self.store.history(interaction.guild_id, interaction.user.id)
        if not rows:
            text = "目前沒有股票交易紀錄。"
        else:
            lines = []
            for row in rows:
                action = "買進" if row["action"] == "buy" else "賣出"
                lines.append("<t:" + str(row["created_at"]) + ":R>　" + action + " " + row["symbol"] + " x" + format(int(row["shares"]), ",") + " 股｜NT$ " + format(int(row["total"]), ","))
            text = "近期股票交易紀錄\n\n" + "\n".join(lines)
        await self._reply(interaction, text, public)


async def setup(bot: commands.Bot):
    await bot.add_cog(StockCog(bot))
