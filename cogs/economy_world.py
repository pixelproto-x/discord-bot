from __future__ import annotations

import math
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

DB_PATH = Path(__file__).resolve().parent.parent / "economy.db"
MAX_BALANCE = int("9" * 2000)
UTC = timezone.utc

GOODS = {
    "coca_cola_600": ("可口可樂 600ml", 35, "台灣超商單瓶參考售價；單位：1 瓶"),
    "uni_noodles_5": ("統一肉燥麵 5 入組", 85, "台灣零售通路參考價；單位：1 組"),
    "ime_puff": ("義美小泡芙 57g", 35, "台灣零售通路參考價；單位：1 包"),
    "ime_soymilk": ("義美豆奶 400ml", 30, "台灣零售通路參考價；單位：1 瓶"),
    "big_mac_meal": ("麥當勞大麥克套餐", 150, "台灣門市參考價；單位：1 套"),
    "scott_tissue": ("舒潔抽取式衛生紙 12 包", 199, "台灣零售通路參考價；單位：1 串"),
    "persil_detergent": ("白蘭洗衣精 1.8kg", 199, "台灣零售通路參考價；單位：1 瓶"),
    "scotch_tape": ("3M Scotch 膠帶", 45, "台灣文具零售參考價；單位：1 卷"),
    "nike_af1": ("Nike Air Force 1 '07", 3600, "台灣官方定價參考；單位：1 雙"),
    "adidas_track_top": ("adidas 運動外套", 2590, "台灣零售參考價；單位：1 件"),
    "airpods4": ("Apple AirPods 4", 4490, "Apple 台灣官方售價；單位：1 組"),
    "iphone17": ("Apple iPhone 17 256GB", 32900, "Apple 台灣官方售價；單位：1 支"),
    "ipad11": ("Apple iPad 11 吋 128GB Wi-Fi", 14900, "Apple 台灣官方售價參考；單位：1 台"),
    "macbook_air": ("Apple MacBook Air 13 吋", 39900, "台灣官方通路參考價；單位：1 台"),
    "ps5_slim": ("Sony PlayStation 5 Slim", 14980, "台灣零售通路參考價；單位：1 台"),
    "switch2": ("Nintendo Switch 2", 14980, "台灣零售通路參考價；單位：1 台"),
    "rtx5070": ("NVIDIA GeForce RTX 5070 顯示卡", 23990, "台灣零售通路參考價；單位：1 張"),
    "asus_rtx5070_laptop": ("ASUS RTX 5070 電競筆電", 56999, "台灣零售通路參考價；單位：1 台"),
    "iphone17_pro": ("Apple iPhone 17 Pro 256GB", 39900, "台灣上市建議售價參考；單位：1 支"),
    "toyota_corolla_cross": ("Toyota Corolla Cross 汽油豪華型", 809000, "台灣 2026 年式建議售價；單位：1 輛"),
}


def _now() -> int:
    return int(time.time())


def _is_inf(value) -> bool:
    return str(value).strip().lower() == "inf"


def _money(value) -> str:
    return "∞（無限）" if _is_inf(value) else f"{int(value):,}"


class WorldEconomy:
    def __init__(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS world_market (
                    guild_id INTEGER NOT NULL,
                    item TEXT NOT NULL,
                    stock INTEGER NOT NULL DEFAULT 1000,
                    demand REAL NOT NULL DEFAULT 1.0,
                    last_price INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, item)
                );
                CREATE TABLE IF NOT EXISTS world_inventory (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    item TEXT NOT NULL,
                    quantity INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id, item)
                );
                CREATE TABLE IF NOT EXISTS world_companies (
                    guild_id INTEGER NOT NULL,
                    owner_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    level INTEGER NOT NULL DEFAULT 1,
                    last_collect INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, owner_id)
                );
                CREATE TABLE IF NOT EXISTS world_loans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    principal INTEGER NOT NULL,
                    balance INTEGER NOT NULL,
                    rate REAL NOT NULL,
                    created_at INTEGER NOT NULL,
                    due_at INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active'
                );
                CREATE TABLE IF NOT EXISTS world_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    detail TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
            """)
            # Replace the old generic catalog completely; remove its market and inventory rows.
            valid_items = tuple(GOODS)
            placeholders = ",".join("?" for _ in valid_items)
            con.execute(f"DELETE FROM world_market WHERE item NOT IN ({placeholders})", valid_items)
            con.execute(f"DELETE FROM world_inventory WHERE item NOT IN ({placeholders})", valid_items)


    def connect(self):
        con = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=10000")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def ensure_user(self, con, guild_id: int, user_id: int):
        con.execute(
            "INSERT OR IGNORE INTO users(user_id,guild_id,wallet,bank) VALUES(?,?, '1000','0')",
            (user_id, guild_id),
        )
        con.execute("UPDATE users SET guild_id=? WHERE user_id=?", (guild_id, user_id))

    def wallet(self, con, guild_id: int, user_id: int) -> str:
        self.ensure_user(con, guild_id, user_id)
        return str(con.execute("SELECT wallet FROM users WHERE user_id=?", (user_id,)).fetchone()["wallet"])

    def set_wallet(self, con, user_id: int, value: str):
        con.execute("UPDATE users SET wallet=? WHERE user_id=?", (value, user_id))

    def ledger(self, con, guild_id: int, user_id: int, kind: str, amount: int, detail: str):
        con.execute(
            "INSERT INTO world_ledger(guild_id,user_id,kind,amount,detail,created_at) VALUES(?,?,?,?,?,?)",
            (guild_id, user_id, kind, amount, detail[:300], _now()),
        )

    def market_rows(self, con, guild_id: int):
        now = _now()
        for key, (_, base, _) in GOODS.items():
            con.execute(
                "INSERT OR IGNORE INTO world_market(guild_id,item,stock,demand,last_price,updated_at) VALUES(?,?,1000,1.0,?,?)",
                (guild_id, key, base, now),
            )
        rows = con.execute("SELECT * FROM world_market WHERE guild_id=?", (guild_id,)).fetchall()
        result = {}
        for row in rows:
            key = row["item"]
            if key not in GOODS:
                continue
            _, base, _ = GOODS[key]
            elapsed_days = max(0, (now - row["updated_at"]) // 86400)
            demand = float(row["demand"])
            # Demand gradually returns toward normal when the market is quiet.
            if elapsed_days:
                demand = 1.0 + (demand - 1.0) * (0.82 ** min(elapsed_days, 30))
            stock = int(row["stock"])
            scarcity = 1.0 + max(-0.55, min(2.0, (1000 - stock) / 1000))
            price = max(1, int(base * max(0.35, min(3.5, demand * scarcity))))
            con.execute(
                "UPDATE world_market SET demand=?,last_price=?,updated_at=? WHERE guild_id=? AND item=?",
                (demand, price, now, guild_id, key),
            )
            result[key] = {"stock": stock, "demand": demand, "price": price, "base": base}
        return result

    def trade(self, guild_id: int, user_id: int, item: str, action: str, amount: int):
        if item not in GOODS or amount < 1 or amount > 100000:
            return False, "商品或數量不正確，每筆交易最多 100,000 件。"
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            self.ensure_user(con, guild_id, user_id)
            market = self.market_rows(con, guild_id)[item]
            wallet = self.wallet(con, guild_id, user_id)
            price = market["price"]
            if action == "buy":
                if market["stock"] < amount:
                    con.rollback()
                    return False, f"庫存不足，目前只剩 {market['stock']:,} 件。"
                total = math.ceil(price * amount * 1.05)
                if not _is_inf(wallet) and int(wallet) < total:
                    con.rollback()
                    return False, f"資金不足，需要 {total:,}，目前現金 {_money(wallet)}。"
                if not _is_inf(wallet):
                    self.set_wallet(con, user_id, str(int(wallet) - total))
                con.execute(
                    "UPDATE world_market SET stock=stock-?,demand=MIN(3.5,demand+?) WHERE guild_id=? AND item=?",
                    (amount, amount / 1000, guild_id, item),
                )
                con.execute(
                    "INSERT INTO world_inventory(guild_id,user_id,item,quantity) VALUES(?,?,?,?) "
                    "ON CONFLICT(guild_id,user_id,item) DO UPDATE SET quantity=quantity+excluded.quantity",
                    (guild_id, user_id, item, amount),
                )
                self.ledger(con, guild_id, user_id, "market_buy", -total, f"買入 {GOODS[item][0]} x{amount}")
                con.commit()
                return True, f"買入 **{GOODS[item][0]} × {amount:,}**，單價 {_money(price)}，總額 {_money(total)}。"
            if action == "sell":
                owned = con.execute(
                    "SELECT quantity FROM world_inventory WHERE guild_id=? AND user_id=? AND item=?",
                    (guild_id, user_id, item),
                ).fetchone()
                if not owned or int(owned["quantity"]) < amount:
                    con.rollback()
                    have = int(owned["quantity"]) if owned else 0
                    return False, f"持有數量不足，你只有 {have:,} 件；不能賣出未持有的商品。"
                proceeds = max(0, int(price * amount * 0.95))
                if not _is_inf(wallet) and int(wallet) + proceeds > MAX_BALANCE:
                    con.rollback()
                    return False, "出售後現金會超出系統上限，交易取消。"
                if not _is_inf(wallet):
                    self.set_wallet(con, user_id, str(int(wallet) + proceeds))
                con.execute(
                    "UPDATE world_market SET stock=MIN(100000000,stock+?),demand=MAX(0.35,demand-?) WHERE guild_id=? AND item=?",
                    (amount, amount / 1500, guild_id, item),
                )
                con.execute(
                    "UPDATE world_inventory SET quantity=quantity-? WHERE guild_id=? AND user_id=? AND item=?",
                    (amount, guild_id, user_id, item),
                )
                self.ledger(con, guild_id, user_id, "market_sell", proceeds, f"賣出 {GOODS[item][0]} x{amount}")
                con.commit()
                return True, f"賣出 **{GOODS[item][0]} × {amount:,}**，單價 {_money(price)}，收入 {_money(proceeds)}。"
            con.rollback()
            return False, "不支援的交易類型。"

    def company(self, guild_id: int, user_id: int, action: str, name: str = ""):
        now = _now()
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            self.ensure_user(con, guild_id, user_id)
            wallet = self.wallet(con, guild_id, user_id)
            row = con.execute(
                "SELECT * FROM world_companies WHERE guild_id=? AND owner_id=?",
                (guild_id, user_id),
            ).fetchone()
            if action == "create":
                if row:
                    con.rollback()
                    return False, "你已經有一家企業；先經營現有企業。"
                if len(name.strip()) < 2 or len(name.strip()) > 40:
                    con.rollback()
                    return False, "企業名稱需為 2 至 40 個字。"
                if not _is_inf(wallet) and int(wallet) < 10000:
                    con.rollback()
                    return False, "成立企業需要 10,000 現金。"
                if not _is_inf(wallet):
                    self.set_wallet(con, user_id, str(int(wallet) - 10000))
                con.execute(
                    "INSERT INTO world_companies(guild_id,owner_id,name,level,last_collect,created_at) VALUES(?,?,?,1,?,?)",
                    (guild_id, user_id, name.strip(), now, now),
                )
                self.ledger(con, guild_id, user_id, "company_create", -10000, f"成立企業：{name.strip()}")
                con.commit()
                return True, f"企業 **{name.strip()}** 已成立。初始營運收入約每小時 200，實際收益受市場景氣影響。"
            if not row:
                con.rollback()
                return False, "你還沒有企業，先建立一家企業。"
            if action == "info":
                hours = min(24, max(0, (now - row["last_collect"]) // 3600))
                income = hours * 200 * int(row["level"])
                con.rollback()
                return True, f"**{row['name']}**\n等級：{row['level']}\n預估可領營收：{income:,}\n收入每小時累積，最多保留 24 小時。"
            if action == "upgrade":
                cost = 5000 * int(row["level"])
                if not _is_inf(wallet) and int(wallet) < cost:
                    con.rollback()
                    return False, f"升級需要 {cost:,} 現金。"
                if not _is_inf(wallet):
                    self.set_wallet(con, user_id, str(int(wallet) - cost))
                con.execute(
                    "UPDATE world_companies SET level=level+1 WHERE guild_id=? AND owner_id=?",
                    (guild_id, user_id),
                )
                self.ledger(con, guild_id, user_id, "company_upgrade", -cost, f"企業升級至 {row['level']+1} 級")
                con.commit()
                return True, f"企業已升級至 **{row['level'] + 1} 級**，每小時基礎營收提高至 {(row['level'] + 1) * 200:,}。"
            if action == "collect":
                hours = min(24, max(0, (now - row["last_collect"]) // 3600))
                if hours < 1:
                    con.rollback()
                    return False, "營收尚未累積滿一小時。"
                # Market conditions modestly affect operating income.
                market = self.market_rows(con, guild_id)
                factor = max(0.7, min(1.3, sum(v["demand"] for v in market.values()) / max(1, len(market))))
                income = int(hours * 200 * int(row["level"]) * factor)
                if not _is_inf(wallet) and int(wallet) + income > MAX_BALANCE:
                    con.rollback()
                    return False, "入帳後現金會超出系統上限。"
                if not _is_inf(wallet):
                    self.set_wallet(con, user_id, str(int(wallet) + income))
                con.execute(
                    "UPDATE world_companies SET last_collect=? WHERE guild_id=? AND owner_id=?",
                    (now - ((now - row["last_collect"]) % 3600), guild_id, user_id),
                )
                self.ledger(con, guild_id, user_id, "company_income", income, f"企業營收 {hours} 小時")
                con.commit()
                return True, f"已入帳 **{income:,}** 企業營收（累積 {hours} 小時，景氣係數 {factor:.2f}）。"
            con.rollback()
            return False, "未知的企業操作。"

    def loan(self, guild_id: int, user_id: int, action: str, amount: int = 0):
        now = _now()
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            self.ensure_user(con, guild_id, user_id)
            wallet = self.wallet(con, guild_id, user_id)
            if action == "status":
                rows = con.execute(
                    "SELECT * FROM world_loans WHERE guild_id=? AND user_id=? AND status='active' ORDER BY id",
                    (guild_id, user_id),
                ).fetchall()
                con.rollback()
                if not rows:
                    return True, "目前沒有未清償貸款。"
                total = sum(int(r["balance"]) + (int(r["balance"]) // 100 if now > r["due_at"] else 0) for r in rows)
                details = "\n".join(
                    f"貸款 #{r['id']}：餘額 {int(r['balance']):,}，年利率模擬值 {r['rate']*100:.1f}% ，到期 <t:{r['due_at']}:R>"
                    for r in rows
                )
                return True, f"{details}\n合計應還約 **{total:,}**。"
            if action == "apply":
                if not 1000 <= amount <= 100000:
                    con.rollback()
                    return False, "貸款金額需介於 1,000 至 100,000。"
                active = con.execute(
                    "SELECT COUNT(*) FROM world_loans WHERE guild_id=? AND user_id=? AND status='active'",
                    (guild_id, user_id),
                ).fetchone()[0]
                if active >= 2:
                    con.rollback()
                    return False, "同時最多兩筆未清償貸款。"
                balance = math.ceil(amount * 1.08)
                if not _is_inf(wallet) and int(wallet) + amount > MAX_BALANCE:
                    con.rollback()
                    return False, "入帳後現金會超出系統上限。"
                if not _is_inf(wallet):
                    self.set_wallet(con, user_id, str(int(wallet) + amount))
                due = now + 7 * 86400
                con.execute(
                    "INSERT INTO world_loans(guild_id,user_id,principal,balance,rate,created_at,due_at,status) VALUES(?,?,?,?,?,?,?,'active')",
                    (guild_id, user_id, amount, balance, 0.08, now, due),
                )
                self.ledger(con, guild_id, user_id, "loan_issue", amount, f"貸款本金 {amount}，應還 {balance}")
                con.commit()
                return True, f"貸款已核准 **{amount:,}**，應還 **{balance:,}**，到期 <t:{due}:F>。逾期會增加費用。"
            if action == "repay":
                rows = con.execute(
                    "SELECT * FROM world_loans WHERE guild_id=? AND user_id=? AND status='active' ORDER BY due_at,id",
                    (guild_id, user_id),
                ).fetchall()
                if not rows:
                    con.rollback()
                    return False, "目前沒有未清償貸款。"
                total_due = sum(int(r["balance"]) + (max(1, int(r["balance"]) // 100) if now > r["due_at"] else 0) for r in rows)
                remaining = min(amount, total_due) if amount > 0 else total_due
                if not _is_inf(wallet) and int(wallet) < remaining:
                    con.rollback()
                    return False, f"現金不足，需要 {remaining:,}。"
                if not _is_inf(wallet):
                    self.set_wallet(con, user_id, str(int(wallet) - remaining))
                due_amount = remaining
                for row in rows:
                    balance = int(row["balance"])
                    if now > row["due_at"]:
                        balance += max(1, balance // 100)
                    paid_from_loan = min(balance, due_amount)
                    due_amount -= paid_from_loan
                    balance -= paid_from_loan
                    if balance == 0:
                        con.execute("UPDATE world_loans SET balance=0,status='paid' WHERE id=?", (row["id"],))
                    else:
                        con.execute("UPDATE world_loans SET balance=? WHERE id=?", (balance, row["id"]))
                    if due_amount <= 0:
                        break
                paid = remaining - max(0, due_amount)
                self.ledger(con, guild_id, user_id, "loan_repay", -paid, f"償還貸款 {paid}")
                con.commit()
                return True, f"已支付 **{paid:,}** 貸款。"
            con.rollback()
            return False, "未知的貸款操作。"


class WorldEconomyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.store = WorldEconomy()

    async def _guild(self, interaction: discord.Interaction) -> bool:
        if interaction.guild:
            return True
        await interaction.response.send_message("這個經濟功能只能在伺服器使用。", ephemeral=True)
        return False

    async def _run(self, interaction: discord.Interaction, result):
        ok, message = result
        color = 0x2ECC71 if ok else 0xE74C3C
        embed = discord.Embed(description=message, color=color, timestamp=datetime.now(UTC))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("market", zh_TW="市場"), description="查看市場價格，或買賣商品")
    @app_commands.choices(action=[
        app_commands.Choice(name="查詢", value="info"),
        app_commands.Choice(name="買入", value="buy"),
        app_commands.Choice(name="賣出", value="sell"),
    ])
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
        app_commands.Choice(name="Apple iPad 11 吋 128GB", value="ipad11"),
        app_commands.Choice(name="Apple MacBook Air 13 吋", value="macbook_air"),
        app_commands.Choice(name="Sony PlayStation 5 Slim", value="ps5_slim"),
        app_commands.Choice(name="Nintendo Switch 2", value="switch2"),
        app_commands.Choice(name="NVIDIA RTX 5070 顯示卡", value="rtx5070"),
        app_commands.Choice(name="ASUS RTX 5070 電競筆電", value="asus_rtx5070_laptop"),
        app_commands.Choice(name="Apple iPhone 17 Pro 256GB", value="iphone17_pro"),
        app_commands.Choice(name="Toyota Corolla Cross 汽油豪華型", value="toyota_corolla_cross"),
    ])
    @app_commands.rename(action=app_commands.locale_str("動作", zh_TW="動作"), item=app_commands.locale_str("商品", zh_TW="商品"), amount=app_commands.locale_str("數量", zh_TW="數量"))
    async def market(self, interaction: discord.Interaction, action: app_commands.Choice[str], item: app_commands.Choice[str], amount: app_commands.Range[int, 1, 100000] = 1):
        if not await self._guild(interaction):
            return
        if action.value != "info" and item is None:
            await interaction.response.send_message("買賣時必須選擇商品。", ephemeral=True)
            return
        if action.value == "info":
            with closing(self.store.connect()) as con:
                con.execute("BEGIN IMMEDIATE")
                rows = self.store.market_rows(con, interaction.guild_id)
                con.commit()
            key = item.value
            data = rows[key]
            label, base, desc = GOODS[key]
            trend = "高於基準" if data["price"] > base else "低於基準" if data["price"] < base else "接近基準"
            embed = discord.Embed(title=f"商品市場｜{label}", description=f"現價：**{data['price']:,}**\n庫存：{data['stock']:,}\n需求指數：{data['demand']:.2f}\n{trend} · {desc}\n買入價差約 5%，賣出折價約 5%。", color=0x3498DB)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        await self._run(interaction, self.store.trade(interaction.guild_id, interaction.user.id, item.value, action.value, amount))

    @app_commands.command(name=app_commands.locale_str("company", zh_TW="企業"), description="建立企業、查看營運或領取營收")
    @app_commands.choices(action=[
        app_commands.Choice(name="成立", value="create"),
        app_commands.Choice(name="查看", value="info"),
        app_commands.Choice(name="領取營收", value="collect"),
        app_commands.Choice(name="升級", value="upgrade"),
    ])
    @app_commands.rename(action=app_commands.locale_str("動作", zh_TW="動作"), name=app_commands.locale_str("名稱", zh_TW="名稱"))
    async def company(self, interaction: discord.Interaction, action: app_commands.Choice[str], name: str = ""):
        if not await self._guild(interaction):
            return
        await self._run(interaction, self.store.company(interaction.guild_id, interaction.user.id, action.value, name))

    @app_commands.command(name=app_commands.locale_str("loan", zh_TW="貸款"), description="申請、查看或償還模擬銀行貸款")
    @app_commands.choices(action=[
        app_commands.Choice(name="申請", value="apply"),
        app_commands.Choice(name="查看", value="status"),
        app_commands.Choice(name="償還", value="repay"),
    ])
    @app_commands.rename(action=app_commands.locale_str("動作", zh_TW="動作"), amount=app_commands.locale_str("金額", zh_TW="金額"))
    async def loan(self, interaction: discord.Interaction, action: app_commands.Choice[str], amount: app_commands.Range[int, 0, 100000] = 0):
        if not await self._guild(interaction):
            return
        await self._run(interaction, self.store.loan(interaction.guild_id, interaction.user.id, action.value, amount))

    @app_commands.command(name=app_commands.locale_str("economy", zh_TW="經濟概況"), description="查看伺服器市場與企業經濟指標")
    async def economy(self, interaction: discord.Interaction):
        if not await self._guild(interaction):
            return
        with closing(self.store.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            market = self.store.market_rows(con, interaction.guild_id)
            companies = con.execute("SELECT COUNT(*) FROM world_companies WHERE guild_id=?", (interaction.guild_id,)).fetchone()[0]
            loans = con.execute("SELECT COUNT(*),COALESCE(SUM(balance),0) FROM world_loans WHERE guild_id=? AND status='active'", (interaction.guild_id,)).fetchone()
            con.commit()
        average = sum(row["price"] / row["base"] for row in market.values()) / max(1, len(market))
        inflation = (average - 1) * 100
        embed = discord.Embed(title="伺服器經濟概況", color=0x5865F2, timestamp=datetime.now(UTC))
        embed.add_field(name="價格指數", value=f"{average:.2f}（基準 1.00）", inline=True)
        embed.add_field(name="模擬通膨", value=f"{inflation:+.1f}%", inline=True)
        embed.add_field(name="企業數", value=f"{companies:,}", inline=True)
        embed.add_field(name="未償貸款", value=f"{loans[0]:,} 筆 / {loans[1]:,} 金幣", inline=False)
        embed.set_footer(text="價格指數只反映本伺服器的模擬商品市場，並非真實金融數據")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("inventory", zh_TW="資產清單"), description="查看自己持有的市場商品")
    async def inventory(self, interaction: discord.Interaction):
        if not await self._guild(interaction):
            return
        with closing(self.store.connect()) as con:
            rows = con.execute(
                "SELECT item,quantity FROM world_inventory WHERE guild_id=? AND user_id=? AND quantity>0 ORDER BY item",
                (interaction.guild_id, interaction.user.id),
            ).fetchall()
        if not rows:
            await interaction.response.send_message("你的商品庫存是空的。", ephemeral=True)
            return
        lines = [f"**{GOODS[row['item']][0]}**：{int(row['quantity']):,} 件" for row in rows if row["item"] in GOODS]
        await interaction.response.send_message(embed=discord.Embed(title="我的商品庫存", description="\\n".join(lines), color=0x1ABC9C), ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("transactions", zh_TW="交易紀錄"), description="查看自己的近期市場、企業與貸款紀錄")
    @app_commands.rename(limit=app_commands.locale_str("筆數", zh_TW="筆數"))
    async def transactions(self, interaction: discord.Interaction, limit: app_commands.Range[int, 1, 10] = 5):
        if not await self._guild(interaction):
            return
        with closing(self.store.connect()) as con:
            rows = con.execute(
                "SELECT kind,amount,detail,created_at FROM world_ledger WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT ?",
                (interaction.guild_id, interaction.user.id, limit),
            ).fetchall()
        if not rows:
            await interaction.response.send_message("目前沒有經濟系統交易紀錄。", ephemeral=True)
            return
        lines = []
        for row in rows:
            stamp = f"<t:{row['created_at']}:R>"
            lines.append(f"{stamp} · **{row['detail']}** · {int(row['amount']):+,}")
        embed = discord.Embed(title="近期經濟交易紀錄", description="\n".join(lines), color=0x95A5A6)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(WorldEconomyCog(bot))
