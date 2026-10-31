import tempfile
from contextlib import closing
import unittest
from pathlib import Path
from unittest.mock import patch

import cogs.economy as economy
from cogs.economy_games import MAX_BET, MiniGameStore


class EconomyMiniGameStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_patch = patch.object(
            economy, "DB_PATH", Path(self.temp_dir.name) / "economy.db"
        )
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.store = MiniGameStore()

    def test_rejects_invalid_bet_or_payout_without_changing_balance(self):
        self.store.economy.get(123, 456)
        self.assertFalse(self.store.play(123, 456, 0, 0, "test", "test")[0])
        self.assertFalse(self.store.play(123, 456, 10, -1, "test", "test")[0])
        self.assertEqual(self.store.economy.get(123, 456)["wallet"], "1000")

    def test_rejects_bet_above_wallet_without_changing_balance(self):
        self.store.economy.get(123, 456)
        ok, _ = self.store.play(123, 456, MAX_BET, 0, "test", "test")
        self.assertFalse(ok)
        self.assertEqual(self.store.economy.get(123, 456)["wallet"], "1000")

    def test_rejects_bet_when_wallet_is_zero_or_negative(self):
        for balance in ("0", "-10000"):
            with self.subTest(balance=balance):
                self.store.economy.get(123, 456)
                with closing(self.store.economy.connect()) as con:
                    con.execute(
                        "UPDATE users SET wallet=? WHERE guild_id=? AND user_id=?",
                        (balance, 0, 456),
                    )
                ok, _ = self.store.play(123, 456, 1, 4, "test", "test")
                self.assertFalse(ok)
                self.assertEqual(self.store.economy.get(123, 456)["wallet"], balance)

    def test_valid_settlement_applies_exact_payout(self):
        self.store.economy.get(123, 456)
        ok, _ = self.store.play(123, 456, 100, 200, "test", "test")
        self.assertTrue(ok)
        self.assertEqual(self.store.economy.get(123, 456)["wallet"], "1100")


if __name__ == "__main__":
    unittest.main()
