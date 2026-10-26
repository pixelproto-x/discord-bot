import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import cogs.economy as economy


class EconomyWalletDisplayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_patch = patch.object(
            economy, "DB_PATH", Path(self.temp_dir.name) / "economy.db"
        )
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.cog = economy.EconomyCog(None)

    async def test_wallet_formats_text_balances_and_total_as_integers(self):
        guild_id = 123
        user_id = 456
        self.cog.store.get(guild_id, user_id)
        with closing(self.cog.store.connect()) as con:
            con.execute(
                "UPDATE users SET wallet=?, bank=? WHERE guild_id=? AND user_id=?",
                ("1234567", "2345", guild_id, user_id),
            )

        user = SimpleNamespace(
            id=user_id,
            display_name="Test User",
            display_avatar=SimpleNamespace(url="https://example.com/avatar.png"),
        )
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=guild_id),
            author=user,
            reply=AsyncMock(),
        )

        await self.cog._wallet(ctx)

        ctx.reply.assert_awaited_once()
        embed = ctx.reply.await_args.kwargs["embed"]
        self.assertEqual(
            [field.value for field in embed.fields],
            ["1,234,567 金幣", "2,345 金幣", "1,236,912 金幣"],
        )

    def test_format_coins_accepts_text_database_values(self):
        self.assertEqual(economy.format_coins("1000"), "1,000")
        self.assertEqual(economy.format_coins("12345678901234567890"), "12,345,678,901,234,567,890")


    def test_deposit_rejects_more_than_wallet_balance(self):
        guild_id, user_id = 123, 456
        self.cog.store.get(guild_id, user_id)
        self.assertFalse(self.cog.store.move(guild_id, user_id, 1001, True))
        row = self.cog.store.get(guild_id, user_id)
        self.assertEqual(row["wallet"], "1000")
        self.assertEqual(row["bank"], "0")

    def test_withdraw_rejects_more_than_bank_balance(self):
        guild_id, user_id = 123, 456
        self.cog.store.get(guild_id, user_id)
        self.assertFalse(self.cog.store.move(guild_id, user_id, 1, False))
        row = self.cog.store.get(guild_id, user_id)
        self.assertEqual(row["wallet"], "1000")
        self.assertEqual(row["bank"], "0")

    def test_transfer_rejects_insufficient_funds_and_self_transfer(self):
        guild_id, sender, receiver = 123, 456, 789
        self.cog.store.get(guild_id, sender)
        self.cog.store.get(guild_id, receiver)
        self.assertFalse(self.cog.store.transfer(guild_id, sender, receiver, 1001))
        self.assertFalse(self.cog.store.transfer(guild_id, sender, sender, 1))
        self.assertEqual(self.cog.store.get(guild_id, sender)["wallet"], "1000")
        self.assertEqual(self.cog.store.get(guild_id, receiver)["wallet"], "1000")

    def test_dice_rejects_bet_larger_than_wallet(self):
        guild_id, user_id = 123, 456
        self.cog.store.get(guild_id, user_id)
        self.assertFalse(self.cog.store.dice(guild_id, user_id, 1001, 2000))
        self.assertEqual(self.cog.store.get(guild_id, user_id)["wallet"], "1000")

    def test_transactions_reject_invalid_amounts(self):
        guild_id, user_id = 123, 456
        self.cog.store.get(guild_id, user_id)
        self.assertFalse(self.cog.store.move(guild_id, user_id, 0, True))
        self.assertFalse(self.cog.store.transfer(guild_id, user_id, 789, -1))
        self.assertFalse(self.cog.store.dice(guild_id, user_id, 1, -1))
        self.assertEqual(self.cog.store.get(guild_id, user_id)["wallet"], "1000")


    def test_same_user_has_independent_wallets_per_guild(self):
        user_id = 456
        self.cog.store.get(123, user_id)
        with closing(self.cog.store.connect()) as con:
            con.execute(
                "UPDATE users SET wallet=? WHERE guild_id=? AND user_id=?",
                ("4321", 123, user_id),
            )
        other_guild = self.cog.store.get(789, user_id)
        original_guild = self.cog.store.get(123, user_id)
        self.assertEqual(other_guild["wallet"], "1000")
        self.assertEqual(original_guild["wallet"], "4321")

    def test_transfer_cannot_cross_guild_account_boundaries(self):
        user_id, receiver = 456, 789
        self.cog.store.get(123, user_id)
        self.cog.store.get(789, receiver)
        self.assertFalse(self.cog.store.transfer(123, user_id, receiver, 1))
        self.assertEqual(self.cog.store.get(123, user_id)["wallet"], "1000")
        self.assertEqual(self.cog.store.get(789, receiver)["wallet"], "1000")


class LegacyEconomyMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "economy.db"
        self.db_patch = patch.object(economy, "DB_PATH", self.db_path)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)

    def test_migrates_legacy_user_primary_key_without_losing_balance(self):
        with closing(economy.sqlite3.connect(self.db_path)) as con:
            con.execute("""
                CREATE TABLE users (
                    user_id INTEGER PRIMARY KEY,
                    guild_id INTEGER,
                    wallet INTEGER NOT NULL DEFAULT 1000,
                    bank INTEGER NOT NULL DEFAULT 0,
                    last_daily TEXT, last_work TEXT, last_fish TEXT, last_rob TEXT
                )
            """)
            con.execute(
                "INSERT INTO users(user_id,guild_id,wallet,bank) VALUES(?,?,?,?)",
                (456, 123, 9876, 321),
            )
            con.commit()

        store = economy.EconomyStore()
        original = store.get(123, 456)
        isolated = store.get(789, 456)
        self.assertEqual((original["wallet"], original["bank"]), ("9876", "321"))
        self.assertEqual((isolated["wallet"], isolated["bank"]), ("1000", "0"))
        with closing(store.connect()) as con:
            pk = {row["name"]: row["pk"] for row in con.execute("PRAGMA table_info(users)")}
        self.assertEqual(pk["guild_id"], 1)
        self.assertEqual(pk["user_id"], 2)

if __name__ == "__main__":
    unittest.main()
