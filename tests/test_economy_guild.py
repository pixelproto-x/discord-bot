import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from cogs import economy_guild


class GuildEconomyStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "economy.db"
        self.path_patch = patch.object(economy_guild, "DB_PATH", self.db_path)
        self.path_patch.start()
        with closing(sqlite3.connect(self.db_path)) as con, con:
            con.execute(
                "CREATE TABLE users (user_id INTEGER PRIMARY KEY, guild_id INTEGER, "
                "wallet TEXT NOT NULL DEFAULT '1000', bank TEXT NOT NULL DEFAULT '0')"
            )
        self.store = economy_guild.GuildEconomyStore()

    def tearDown(self):
        self.path_patch.stop()
        self.tempdir.cleanup()

    def seed_user(self, user_id, guild_id=10, wallet="500"):
        with closing(sqlite3.connect(self.db_path)) as con, con:
            con.execute(
                "INSERT INTO users(user_id,guild_id,wallet,bank) VALUES(?,?,?,'0')",
                (user_id, guild_id, wallet),
            )

    def set_treasury(self, guild_id, balance):
        with closing(self.store.connect()) as con, con:
            con.execute(
                "INSERT INTO guild_treasuries(guild_id,balance,updated_at) VALUES(?,?,0) "
                "ON CONFLICT(guild_id) DO UPDATE SET balance=excluded.balance",
                (guild_id, str(balance)),
            )

    def treasury_balance(self, guild_id):
        row, _ = self.store.treasury(guild_id)
        return int(row["balance"])

    def wallet_balance(self, user_id):
        with closing(sqlite3.connect(self.db_path)) as con, con:
            row = con.execute("SELECT wallet FROM users WHERE user_id=?", (user_id,)).fetchone()
        return int(row[0])

    def test_deposit_and_withdraw_are_atomic_and_recorded(self):
        self.seed_user(1)
        ok, _ = self.store.transfer(10, 1, 120, True)
        self.assertTrue(ok)
        self.assertEqual(self.wallet_balance(1), 380)
        self.assertEqual(self.treasury_balance(10), 120)

        ok, _ = self.store.transfer(10, 1, 20, False)
        self.assertTrue(ok)
        self.assertEqual(self.wallet_balance(1), 400)
        self.assertEqual(self.treasury_balance(10), 100)

        ok, message = self.store.transfer(10, 1, 1000, True)
        self.assertFalse(ok)
        self.assertIn("現金不足", message)
        self.assertEqual(self.wallet_balance(1), 400)
        self.assertEqual(self.treasury_balance(10), 100)

        with closing(self.store.connect()) as con, con:
            actions = [row["action"] for row in con.execute(
                "SELECT action FROM guild_treasury_ledger WHERE guild_id=10 ORDER BY id"
            )]
        self.assertEqual(actions, ["deposit", "withdraw"])

    def test_treasuries_are_isolated_by_guild(self):
        self.seed_user(1)
        ok, _ = self.store.transfer(10, 1, 100, True)
        self.assertTrue(ok)
        self.assertEqual(self.treasury_balance(10), 100)
        self.assertEqual(self.treasury_balance(20), 0)

    def test_event_reserves_rewards_and_each_contributor_can_claim_once(self):
        self.seed_user(1, wallet="500")
        self.seed_user(2, wallet="500")
        self.set_treasury(10, 1000)

        ok, _ = self.store.create_event(10, 99, "共同任務", "測試共同目標", 100, 101, 24)
        self.assertTrue(ok)
        self.assertEqual(self.treasury_balance(10), 899)

        ok, _ = self.store.contribute(10, 1, 60)
        self.assertTrue(ok)
        ok, _ = self.store.contribute(10, 2, 40)
        self.assertTrue(ok)
        event = self.store.event_info(10)
        self.assertEqual(event["status"], "completed")
        self.assertEqual(event["progress"], 100)

        ok, _ = self.store.claim_reward(10, 1)
        self.assertTrue(ok)
        after_first_claim = self.wallet_balance(1)
        ok, message = self.store.claim_reward(10, 1)
        self.assertFalse(ok)
        self.assertIn("領過", message)
        self.assertEqual(self.wallet_balance(1), after_first_claim)

        ok, _ = self.store.claim_reward(10, 2)
        self.assertTrue(ok)
        self.assertEqual(self.wallet_balance(1), 490)
        self.assertEqual(self.wallet_balance(2), 511)
        event = self.store.event_info(10)
        self.assertEqual(event["claimed"], 2)
        self.assertEqual(self.treasury_balance(10), 999)

    def test_expired_active_event_refunds_reserved_pool_only_once(self):
        self.set_treasury(10, 500)
        ok, _ = self.store.create_event(10, 99, "限時任務", "到期退款", 100, 200, 24)
        self.assertTrue(ok)
        self.assertEqual(self.treasury_balance(10), 300)

        with closing(self.store.connect()) as con, con:
            con.execute(
                "UPDATE guild_economy_events SET ends_at=1 WHERE guild_id=10"
            )
        event = self.store.event_info(10)
        self.assertEqual(event["status"], "expired")
        self.assertEqual(self.treasury_balance(10), 500)

        self.store.event_info(10)
        self.assertEqual(self.treasury_balance(10), 500)
        with closing(self.store.connect()) as con, con:
            refunds = con.execute(
                "SELECT COUNT(*) FROM guild_treasury_ledger "
                "WHERE guild_id=10 AND action='event_expired_refund'"
            ).fetchone()[0]
        self.assertEqual(refunds, 1)

    def test_event_creation_rejects_unfunded_reward_pool(self):
        self.set_treasury(10, 50)
        ok, message = self.store.create_event(10, 99, "資金不足", "不應建立", 100, 100, 24)
        self.assertFalse(ok)
        self.assertIn("金庫不足", message)
        with closing(self.store.connect()) as con, con:
            count = con.execute("SELECT COUNT(*) FROM guild_economy_events").fetchone()[0]
        self.assertEqual(count, 0)
        self.assertEqual(self.treasury_balance(10), 50)


if __name__ == "__main__":
    unittest.main()
