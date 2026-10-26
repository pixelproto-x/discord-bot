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
                "UPDATE users SET wallet=?, bank=? WHERE user_id=?",
                ("1234567", "2345", user_id),
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

if __name__ == "__main__":
    unittest.main()
