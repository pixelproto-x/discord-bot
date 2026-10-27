import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import cogs.economy as economy
import cogs.economy_world as world_module
import cogs.economy_property as property_module
import cogs.economy_stocks as stocks_module
import cogs.economy_player_market as market_module


class EconomyWorldWalletScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "economy.db"
        self.patches = []
        for module in (economy, world_module, property_module, stocks_module, market_module):
            p = patch.object(module, "DB_PATH", self.db_path)
            p.start()
            self.patches.append(p)
            self.addCleanup(p.stop)

        self.accounts = economy.EconomyStore()
        self.world = world_module.WorldEconomy()
        self.properties = property_module.PropertyStore()
        self.stocks = stocks_module.StockStore()
        self.market = market_module.PlayerMarketStore()
        self.guild_id = 123
        self.user_id = 456
        self.accounts.get(self.guild_id, self.user_id)
        self.set_wallet(self.user_id, 2_000_000)

    def set_wallet(self, user_id, amount, guild_id=None):
        guild_id = self.guild_id if guild_id is None else guild_id
        self.accounts.get(guild_id, user_id)
        with closing(self.accounts.connect()) as con:
            con.execute(
                "UPDATE users SET wallet=? WHERE guild_id=? AND user_id=?",
                (str(amount), guild_id, user_id),
            )

    def wallet(self, user_id, guild_id=None):
        guild_id = self.guild_id if guild_id is None else guild_id
        return int(self.accounts.get(guild_id, user_id)["wallet"])

    def test_loan_rejects_negative_repayment_instead_of_treating_it_as_all(self):
        before = self.wallet(self.user_id)
        ok, _ = self.world.loan(self.guild_id, self.user_id, "apply", 1000)
        self.assertTrue(ok)
        after_loan = self.wallet(self.user_id)
        self.assertEqual(after_loan, before + 1000)

        ok, message = self.world.loan(self.guild_id, self.user_id, "repay", -1)
        self.assertFalse(ok)
        self.assertIn("不可為負數", message)
        self.assertEqual(self.wallet(self.user_id), after_loan)
        with closing(self.world.connect()) as con:
            row = con.execute(
                "SELECT status,balance FROM world_loans WHERE guild_id=? AND user_id=? AND status='active'",
                (self.guild_id, self.user_id),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "active")
        self.assertEqual(int(row["balance"]), 1080)

    def test_world_market_company_and_loan_wallet_paths(self):
        start = self.wallet(self.user_id)
        ok, _ = self.world.trade(self.guild_id, self.user_id, "coca_cola_600", "buy", 1)
        self.assertTrue(ok)
        after_buy = self.wallet(self.user_id)
        self.assertLess(after_buy, start)
        ok, _ = self.world.trade(self.guild_id, self.user_id, "coca_cola_600", "sell", 1)
        self.assertTrue(ok)
        self.assertGreater(self.wallet(self.user_id), after_buy)

        ok, _ = self.world.company(self.guild_id, self.user_id, "create", "Test Company")
        self.assertTrue(ok)
        ok, _ = self.world.company(self.guild_id, self.user_id, "upgrade")
        self.assertTrue(ok)
        with closing(self.world.connect()) as con:
            con.execute(
                "UPDATE world_companies SET last_collect=? WHERE guild_id=? AND owner_id=?",
                (int(time.time()) - 7200, self.guild_id, self.user_id),
            )
        ok, _ = self.world.company(self.guild_id, self.user_id, "collect")
        self.assertTrue(ok)

        ok, _ = self.world.loan(self.guild_id, self.user_id, "apply", 1000)
        self.assertTrue(ok)
        ok, _ = self.world.loan(self.guild_id, self.user_id, "repay", 0)
        self.assertTrue(ok)

    def test_property_buy_collect_upgrade_and_sell_wallet_paths(self):
        ok, _ = self.properties.buy(self.guild_id, self.user_id, "office")
        self.assertTrue(ok)
        with closing(self.properties.connect()) as con:
            row = con.execute(
                "SELECT id FROM world_properties WHERE guild_id=? AND owner_id=?",
                (self.guild_id, self.user_id),
            ).fetchone()
            property_id = int(row["id"])
            con.execute(
                "UPDATE world_properties SET last_collect=? WHERE id=?",
                (int(time.time()) - 3 * 86400, property_id),
            )
        ok, _ = self.properties.collect(self.guild_id, self.user_id, property_id)
        self.assertTrue(ok)
        ok, _ = self.properties.upgrade(self.guild_id, self.user_id, property_id)
        self.assertTrue(ok)
        ok, _ = self.properties.sell(self.guild_id, self.user_id, property_id)
        self.assertTrue(ok)

    def test_stock_buy_and_sell_wallet_paths(self):
        ok, _ = self.stocks.trade(self.guild_id, self.user_id, "TSMC", "buy", 1)
        self.assertTrue(ok)
        ok, _ = self.stocks.trade(self.guild_id, self.user_id, "TSMC", "sell", 1)
        self.assertTrue(ok)

    def test_player_market_buyer_and_seller_wallet_paths(self):
        seller_id, buyer_id = 456, 789
        self.set_wallet(seller_id, 1000)
        self.set_wallet(buyer_id, 1000)
        with closing(self.world.connect()) as con:
            con.execute(
                "INSERT INTO world_inventory(guild_id,user_id,item,quantity) VALUES(?,?,?,?)",
                (self.guild_id, seller_id, "coca_cola_600", 3),
            )
        ok, _ = self.market.create_listing(self.guild_id, seller_id, "coca_cola_600", 3, 100)
        self.assertTrue(ok)
        with closing(self.market.connect()) as con:
            listing_id = con.execute(
                "SELECT id FROM player_market_listings WHERE guild_id=? AND seller_id=?",
                (self.guild_id, seller_id),
            ).fetchone()["id"]
        ok, _ = self.market.buy_listing(self.guild_id, buyer_id, int(listing_id), 2)
        self.assertTrue(ok)
        self.assertEqual(self.wallet(buyer_id), 800)
        self.assertEqual(self.wallet(seller_id), 1200)


if __name__ == "__main__":
    unittest.main()
