import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import economy_editor_names as editor
from economy_editor_names import _parse_balance


class EconomyEditorBalanceValidationTests(unittest.TestCase):
    def test_accepts_zero_and_positive_balances(self):
        self.assertEqual(_parse_balance("0"), "0")
        self.assertEqual(_parse_balance("00123"), "123")

    def test_accepts_positive_and_negative_infinity(self):
        self.assertEqual(_parse_balance("inf"), "inf")
        self.assertEqual(_parse_balance(" INF "), "inf")
        self.assertEqual(_parse_balance("-inf"), "-inf")

    def test_accepts_negative_balances(self):
        self.assertEqual(_parse_balance("-1"), "-1")
        self.assertEqual(_parse_balance("-0"), "0")
        self.assertEqual(_parse_balance("-" + "9" * 20), "-" + "9" * 20)

    def test_evaluates_integer_arithmetic_before_saving(self):
        self.assertEqual(_parse_balance("1+1"), "2")
        self.assertEqual(_parse_balance("(10 + 5) * 2"), "30")
        self.assertEqual(_parse_balance("2 ** 10"), "1024")
        self.assertEqual(_parse_balance("-5 + 2"), "-3")
        self.assertEqual(_parse_balance("8 / 2"), "4")

    def test_rejects_unsafe_or_non_integer_expressions(self):
        for value in ("", "1.5", "abc", "__import__('os')", "1 / 2", "1 / 0", "2 ** 999999"):
            with self.subTest(value=value):
                self.assertIsNone(_parse_balance(value))
        self.assertIsNone(_parse_balance("9" * 2001))


class EconomyEditorSaveTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "economy.db"
        with sqlite3.connect(self.db_path) as con:
            con.execute("""CREATE TABLE users (
                guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                wallet TEXT NOT NULL, bank TEXT NOT NULL,
                last_daily TEXT, last_work TEXT, last_fish TEXT, last_rob TEXT,
                PRIMARY KEY(guild_id,user_id)
            )""")
            con.execute(
                "INSERT INTO users VALUES(0,123,'100','200',NULL,NULL,NULL,NULL)"
            )
        self.db_patch = patch.object(editor, "DB_PATH", self.db_path)
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_saves_negative_and_infinite_balances_as_text(self):
        times = {key: None for key in ("last_daily", "last_work", "last_fish", "last_rob")}
        self.assertTrue(editor._save_user_balances(123, "-500", "-inf", times))
        with sqlite3.connect(self.db_path) as con:
            row = con.execute(
                "SELECT wallet,bank FROM users WHERE guild_id=0 AND user_id=123"
            ).fetchone()
        self.assertEqual(row, ("-500", "-inf"))

    def test_returns_false_for_missing_player(self):
        times = {key: None for key in ("last_daily", "last_work", "last_fish", "last_rob")}
        self.assertFalse(editor._save_user_balances(999, "2", "3", times))

    def test_retries_transient_sqlite_writer_lock(self):
        times = {key: None for key in ("last_daily", "last_work", "last_fish", "last_rob")}
        real_connect = sqlite3.connect
        attempts = {"count": 0}

        def flaky_connect(*args, **kwargs):
            attempts["count"] += 1
            if attempts["count"] <= 2:
                raise sqlite3.OperationalError("database is locked")
            con = real_connect(self.db_path, timeout=10)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA busy_timeout=10000")
            return con

        with patch.object(editor, "connect", side_effect=flaky_connect), patch.object(editor.time, "sleep"):
            self.assertTrue(editor._save_user_balances(123, "7", "8", times))
        self.assertEqual(attempts["count"], 3)
        with real_connect(self.db_path) as con:
            row = con.execute("SELECT wallet,bank FROM users WHERE user_id=123").fetchone()
        self.assertEqual(row, ("7", "8"))


if __name__ == "__main__":
    unittest.main()
