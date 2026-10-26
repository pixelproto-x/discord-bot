import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cogs import economy_classes


class EconomyClassStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "economy.db"
        self.path_patch = patch.object(economy_classes, "DB_PATH", self.db_path)
        self.path_patch.start()
        with sqlite3.connect(self.db_path) as con:
            con.execute(
                "CREATE TABLE users (user_id INTEGER PRIMARY KEY, guild_id INTEGER, "
                "wallet TEXT NOT NULL DEFAULT '1000', bank TEXT NOT NULL DEFAULT '0')"
            )
        self.store = economy_classes.EconomyClassStore()

    def tearDown(self):
        self.path_patch.stop()
        self.tempdir.cleanup()

    def wallet(self, user_id):
        with sqlite3.connect(self.db_path) as con:
            return con.execute("SELECT wallet FROM users WHERE user_id=?", (user_id,)).fetchone()[0]

    def set_wallet(self, user_id, amount):
        with sqlite3.connect(self.db_path) as con:
            con.execute("UPDATE users SET wallet=? WHERE user_id=?", (str(amount), user_id))

    def test_first_class_is_free_and_duplicate_class_is_rejected(self):
        ok, message = self.store.choose_class(10, 1, "mage", now=1000)
        self.assertTrue(ok)
        self.assertIn("首次選職業免費", message)
        ok, message = self.store.choose_class(10, 1, "mage", now=1001)
        self.assertFalse(ok)
        self.assertIn("已經是法師", message)

    def test_class_change_has_cost_and_seven_day_cooldown(self):
        self.set_wallet(1, 50_000)
        self.store.choose_class(10, 1, "warrior", now=1000)
        ok, message = self.store.choose_class(10, 1, "merchant", now=1001)
        self.assertFalse(ok)
        self.assertIn("冷卻中", message)

        ok, message = self.store.choose_class(
            10, 1, "merchant", now=1000 + economy_classes.CLASS_CHANGE_COOLDOWN
        )
        self.assertTrue(ok)
        self.assertIn("轉職費用", message)
        self.assertEqual(self.wallet(1), "40000")

    def test_adventure_awards_class_bonus_and_levels_up(self):
        self.store.choose_class(10, 1, "mage", now=1000)
        ok, message = self.store.adventure(10, 1, now=2000, roll=0.1, reward=100, xp_gain=100)
        self.assertTrue(ok)
        self.assertIn("獲得金幣：100", message)
        self.assertIn("獲得經驗：130 XP", message)
        self.assertIn("升級：Lv.1 → Lv.2", message)
        profile = self.store.profile(10, 1)
        self.assertEqual(profile["level"], 2)
        self.assertEqual(profile["xp"], 30)
        self.assertEqual(profile["adventures"], 1)
        self.assertEqual(profile["wins"], 1)
        self.assertEqual(self.wallet(1), "1600")

    def test_adventure_requires_class_and_enforces_cooldown(self):
        ok, message = self.store.adventure(10, 1, now=1000, roll=0.1, reward=100, xp_gain=25)
        self.assertFalse(ok)
        self.assertIn("還沒有職業", message)
        self.store.choose_class(10, 1, "warrior", now=1000)
        ok, _ = self.store.adventure(10, 1, now=2000, roll=0.1, reward=100, xp_gain=25)
        self.assertTrue(ok)
        ok, message = self.store.adventure(10, 1, now=2020, roll=0.1, reward=100, xp_gain=25)
        self.assertFalse(ok)
        self.assertIn("冷卻", message)

    def test_profile_and_leaderboard_are_guild_scoped(self):
        self.store.choose_class(10, 1, "ranger", now=1000)
        self.store.choose_class(20, 2, "warrior", now=1000)
        self.assertEqual(self.store.profile(10, 1)["class_key"], "ranger")
        self.assertEqual(self.store.profile(20, 2)["class_key"], "warrior")
        self.assertEqual([row["user_id"] for row in self.store.leaderboard(10)], [1])
        self.assertEqual([row["user_id"] for row in self.store.leaderboard(20)], [2])


if __name__ == "__main__":
    unittest.main()
