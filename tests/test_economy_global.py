import sqlite3

import cogs.economy as economy


def _seed_scoped_database(path):
    with sqlite3.connect(path) as con:
        con.executescript("""
            CREATE TABLE users (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                wallet TEXT NOT NULL DEFAULT '1000',
                bank TEXT NOT NULL DEFAULT '0',
                last_daily TEXT, last_work TEXT, last_fish TEXT, last_rob TEXT,
                PRIMARY KEY (guild_id, user_id)
            );
            CREATE TABLE economy_inventory (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                item_key TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id, item_key)
            );
            CREATE TABLE economy_effects (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                effect_key TEXT NOT NULL,
                charges INTEGER NOT NULL DEFAULT 0,
                expires_at TEXT,
                PRIMARY KEY (guild_id, user_id, effect_key)
            );
            INSERT INTO users VALUES
                (10, 123, '100', '20', '2026-10-01T00:00:00+00:00', NULL, NULL, NULL),
                (20, 123, '300', '40', '2026-10-02T00:00:00+00:00', NULL, NULL, NULL),
                (10, 456, 'inf', '0', NULL, NULL, NULL, NULL);
            INSERT INTO economy_inventory VALUES
                (10, 123, 'lucky_charm', 2),
                (20, 123, 'lucky_charm', 3);
            INSERT INTO economy_effects VALUES
                (10, 123, 'work_ticket', 1, NULL),
                (20, 123, 'work_ticket', 2, NULL);
        """)


def test_migration_merges_accounts_and_preserves_backup(tmp_path, monkeypatch):
    db_path = tmp_path / "economy.db"
    _seed_scoped_database(db_path)
    monkeypatch.setattr(economy, "DB_PATH", db_path)

    store = economy.EconomyStore()

    with sqlite3.connect(db_path) as con:
        rows = con.execute("SELECT guild_id,user_id,wallet,bank,last_daily FROM users ORDER BY user_id").fetchall()
        inventory = con.execute(
            "SELECT guild_id,user_id,item_key,quantity FROM economy_inventory"
        ).fetchall()
        effects = con.execute(
            "SELECT guild_id,user_id,effect_key,charges FROM economy_effects"
        ).fetchall()

    assert rows == [
        (0, 123, "400", "60", "2026-10-02T00:00:00+00:00"),
        (0, 456, "inf", "0", None),
    ]
    assert inventory == [(0, 123, "lucky_charm", 5)]
    assert effects == [(0, 123, "work_ticket", 3)]
    assert db_path.with_name("economy.db.pre-global-backup").is_file()
    assert store.get(10, 123)["wallet"] == "400"
    assert store.get(20, 123)["wallet"] == "400"


def test_new_accounts_are_shared_between_guilds(tmp_path, monkeypatch):
    db_path = tmp_path / "economy.db"
    monkeypatch.setattr(economy, "DB_PATH", db_path)

    store = economy.EconomyStore()
    first = store.get(100, 999)
    second = store.get(200, 999)

    assert first["wallet"] == second["wallet"] == "1000"
    with sqlite3.connect(db_path) as con:
        rows = con.execute("SELECT guild_id,user_id FROM users WHERE user_id=999").fetchall()
    assert rows == [(0, 999)]
