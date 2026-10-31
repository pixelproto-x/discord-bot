"""Shared SQLite connection setup for the economy database.

WAL is preferred, but some synced or restricted Windows folders cannot create
SQLite's -wal/-shm sidecar files. In that case use the rollback journal so the
bot can still open the existing database without recreating it.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def connect_economy_database(db_path: str | Path, *, label: str = "Economy") -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row

    try:
        con.execute("PRAGMA busy_timeout=10000")
        mode_row = con.execute("PRAGMA journal_mode=WAL").fetchone()
        mode = str(mode_row[0]).strip().lower() if mode_row else ""
        if mode == "wal":
            return con
        wal_error = sqlite3.OperationalError(
            f"SQLite returned journal mode {mode or 'no result'!r} instead of WAL"
        )
    except sqlite3.OperationalError as exc:
        wal_error = exc

    try:
        mode_row = con.execute("PRAGMA journal_mode=DELETE").fetchone()
        mode = str(mode_row[0]).strip().lower() if mode_row else ""
        if mode and mode != "delete":
            raise sqlite3.OperationalError(
                f"SQLite returned journal mode {mode!r} instead of DELETE"
            )
    except sqlite3.OperationalError as fallback_error:
        con.close()
        raise sqlite3.OperationalError(
            f"{label} 資料庫無法啟用 WAL，且回退 DELETE 模式也失敗。"
            f" WAL 錯誤: {wal_error}; DELETE 錯誤: {fallback_error}"
        ) from fallback_error

    print(
        f"[{label}] WAL 模式無法啟用，已改用 DELETE journal mode：{wal_error}"
    )
    return con
