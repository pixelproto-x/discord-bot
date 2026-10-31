import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cogs.economy_sqlite import connect_economy_database


class FakeCursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, wal_error=None, delete_error=None):
        self.wal_error = wal_error
        self.delete_error = delete_error
        self.calls = []
        self.closed = False
        self.row_factory = None

    def execute(self, sql):
        self.calls.append(sql)
        if "journal_mode=WAL" in sql and self.wal_error:
            raise self.wal_error
        if "journal_mode=DELETE" in sql and self.delete_error:
            raise self.delete_error
        if "journal_mode=WAL" in sql:
            return FakeCursor(("wal",))
        if "journal_mode=DELETE" in sql:
            return FakeCursor(("delete",))
        return FakeCursor(None)

    def close(self):
        self.closed = True


class EconomySQLiteConnectionTests(unittest.TestCase):
    def test_prefers_wal_when_supported(self):
        connection = FakeConnection()
        with tempfile.TemporaryDirectory() as temp, patch(
            "cogs.economy_sqlite.sqlite3.connect", return_value=connection
        ):
            result = connect_economy_database(Path(temp) / "economy.db")
        self.assertIs(result, connection)
        self.assertFalse(connection.closed)
        self.assertIn("PRAGMA journal_mode=WAL", connection.calls)
        self.assertNotIn("PRAGMA journal_mode=DELETE", connection.calls)
        self.assertIs(connection.row_factory, sqlite3.Row)

    def test_falls_back_to_delete_when_wal_fails(self):
        connection = FakeConnection(wal_error=sqlite3.OperationalError("disk I/O error"))
        with tempfile.TemporaryDirectory() as temp, patch(
            "cogs.economy_sqlite.sqlite3.connect", return_value=connection
        ), patch("builtins.print") as output:
            result = connect_economy_database(Path(temp) / "economy.db", label="Test")
        self.assertIs(result, connection)
        self.assertFalse(connection.closed)
        self.assertIn("PRAGMA journal_mode=DELETE", connection.calls)
        output.assert_called_once()
        self.assertIn("DELETE", output.call_args.args[0])

    def test_closes_connection_and_reports_both_errors_when_fallback_fails(self):
        connection = FakeConnection(
            wal_error=sqlite3.OperationalError("WAL denied"),
            delete_error=sqlite3.OperationalError("DELETE denied"),
        )
        with tempfile.TemporaryDirectory() as temp, patch(
            "cogs.economy_sqlite.sqlite3.connect", return_value=connection
        ):
            with self.assertRaisesRegex(sqlite3.OperationalError, "WAL denied.*DELETE denied"):
                connect_economy_database(Path(temp) / "economy.db")
        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
