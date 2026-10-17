import hashlib
import hmac
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "bot.sqlite3"
MAX_ACTIVE_REMINDERS_PER_USER = 50
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

class ClosingConnection(sqlite3.Connection):
    """SQLite connection that commits/rolls back and then always closes in a with block."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect():
    con = sqlite3.connect(DB_PATH, timeout=10, factory=ClosingConnection)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=10000")
    return con


def init_db():
    with connect() as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            due_at REAL NOT NULL,
            done INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS autoreplies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            trigger TEXT NOT NULL,
            response TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'contains',
            channel_id INTEGER,
            enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS profiles (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            xp INTEGER NOT NULL DEFAULT 0,
            level INTEGER NOT NULL DEFAULT 1,
            msg_count INTEGER NOT NULL DEFAULT 0,
            voice_seconds INTEGER NOT NULL DEFAULT 0,
            games_won INTEGER NOT NULL DEFAULT 0,
            games_played INTEGER NOT NULL DEFAULT 0,
            last_xp_at REAL NOT NULL DEFAULT 0,
            PRIMARY KEY(guild_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            moderator_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
            guild_id INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY(guild_id, key)
        );
        CREATE TABLE IF NOT EXISTS role_panels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            mode TEXT NOT NULL,
            role_ids TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS polls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            question TEXT NOT NULL,
            options TEXT NOT NULL,
            anonymous INTEGER NOT NULL DEFAULT 0,
            multiple INTEGER NOT NULL DEFAULT 0,
            ends_at REAL,
            closed INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS poll_votes (
            poll_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            option_index INTEGER NOT NULL,
            PRIMARY KEY(poll_id, user_id, option_index)
        );
        CREATE TABLE IF NOT EXISTS giveaways (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            prize TEXT NOT NULL,
            winners INTEGER NOT NULL DEFAULT 1,
            ends_at REAL NOT NULL,
            ended INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS giveaway_entries (
            giveaway_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            PRIMARY KEY(giveaway_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS giveaway_winners (
            giveaway_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            round_no INTEGER NOT NULL,
            PRIMARY KEY(giveaway_id, user_id, round_no)
        );
        CREATE TABLE IF NOT EXISTS temp_channels (
            guild_id INTEGER NOT NULL,
            channel_id INTEGER PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS voice_sessions (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            started_at REAL NOT NULL,
            PRIMARY KEY(guild_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL
        );
        """)
        columns = {row["name"] for row in con.execute("PRAGMA table_info(polls)").fetchall()}
        if "multiple" not in columns:
            con.execute("ALTER TABLE polls ADD COLUMN multiple INTEGER NOT NULL DEFAULT 0")

        anonymous_rows = con.execute(
            "SELECT id FROM polls WHERE anonymous=1"
        ).fetchall()
        secret = os.getenv("POLL_ANON_SECRET") or os.getenv("DISCORD_TOKEN", "change-me")
        for poll in anonymous_rows:
            vote_rows = con.execute(
                "SELECT poll_id, user_id, option_index FROM poll_votes WHERE poll_id=? AND user_id>0",
                (poll["id"],),
            ).fetchall()
            for vote in vote_rows:
                raw = f"{poll['id']}:{vote['user_id']}".encode()
                digest = hmac.new(secret.encode(), raw, hashlib.sha256).digest()
                anonymous_id = -((int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF) + 1)
                con.execute(
                    "UPDATE poll_votes SET user_id=? WHERE poll_id=? AND user_id=? AND option_index=?",
                    (anonymous_id, vote["poll_id"], vote["user_id"], vote["option_index"]),
                )
        
def set_setting(guild_id: int, key: str, value: Any):
    with connect() as con:
        con.execute(
            "INSERT INTO settings(guild_id,key,value) VALUES(?,?,?) "
            "ON CONFLICT(guild_id,key) DO UPDATE SET value=excluded.value",
            (guild_id, key, json.dumps(value, ensure_ascii=False)),
        )

def get_setting(guild_id: int, key: str, default=None):
    with connect() as con:
        row = con.execute("SELECT value FROM settings WHERE guild_id=? AND key=?", (guild_id, key)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except Exception:
        return default

def add_autoreply(guild_id: int, trigger: str, response: str, mode: str="contains", channel_id: int|None=None):
    with connect() as con:
        cur = con.execute(
            "INSERT INTO autoreplies(guild_id,trigger,response,mode,channel_id) VALUES(?,?,?,?,?)",
            (guild_id, trigger, response, mode, channel_id),
        )
        return cur.lastrowid

def update_autoreply(guild_id: int, rule_id: int, trigger=None, response=None, mode=None, channel_id=None, enabled=None):
    fields, values = [], []
    for name, value in (("trigger", trigger), ("response", response), ("mode", mode), ("channel_id", channel_id), ("enabled", enabled)):
        if value is not None:
            fields.append(f"{name}=?")
            values.append(value)
    if not fields:
        return False
    values.extend([guild_id, rule_id])
    with connect() as con:
        cur = con.execute(f"UPDATE autoreplies SET {', '.join(fields)} WHERE guild_id=? AND id=?", values)
        return cur.rowcount > 0

def delete_autoreply(guild_id: int, rule_id: int):
    with connect() as con:
        cur = con.execute("DELETE FROM autoreplies WHERE guild_id=? AND id=?", (guild_id, rule_id))
        return cur.rowcount > 0

def list_autoreplies(guild_id: int):
    with connect() as con:
        return con.execute("SELECT * FROM autoreplies WHERE guild_id=? ORDER BY id", (guild_id,)).fetchall()

def find_autoreplies(guild_id: int, content: str, channel_id: int):
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM autoreplies WHERE guild_id=? AND enabled=1 ORDER BY LENGTH(trigger) DESC",
            (guild_id,),
        ).fetchall()
    result = []
    lowered = content.casefold()
    for row in rows:
        if row["channel_id"] not in (None, channel_id):
            continue
        trigger = row["trigger"].casefold()
        matched = content.casefold() == trigger if row["mode"] == "exact" else trigger in lowered
        if matched:
            result.append(row)
    return result

def add_reminder(guild_id, user_id, channel_id, message, due_at):
    """Create a reminder unless this member already has the maximum active count."""
    with connect() as con:
        # Lock before counting so concurrent slash commands cannot race past the cap.
        con.execute("BEGIN IMMEDIATE")
        active_count = con.execute(
            "SELECT COUNT(*) FROM reminders WHERE guild_id=? AND user_id=? AND done=0",
            (guild_id, user_id),
        ).fetchone()[0]
        if active_count >= MAX_ACTIVE_REMINDERS_PER_USER:
            return None

        cur = con.execute(
            "INSERT INTO reminders(guild_id,user_id,channel_id,message,due_at) VALUES(?,?,?,?,?)",
            (guild_id, user_id, channel_id, message, due_at),
        )
        return cur.lastrowid

def list_reminders(guild_id, user_id):
    with connect() as con:
        return con.execute(
            "SELECT * FROM reminders WHERE guild_id=? AND user_id=? AND done=0 ORDER BY due_at",
            (guild_id, user_id),
        ).fetchall()

def cancel_reminder(guild_id, user_id, reminder_id):
    with connect() as con:
        cur = con.execute(
            "UPDATE reminders SET done=1 WHERE id=? AND guild_id=? AND user_id=? AND done=0",
            (reminder_id, guild_id, user_id),
        )
        return cur.rowcount > 0

def due_reminders(now=None):
    now = now or time.time()
    with connect() as con:
        return con.execute(
            "SELECT * FROM reminders WHERE done=0 AND due_at<=? ORDER BY due_at",
            (now,),
        ).fetchall()


def complete_reminder(reminder_id: int):
    with connect() as con:
        con.execute("UPDATE reminders SET done=1 WHERE id=? AND done=0", (reminder_id,))

def ensure_profile(guild_id, user_id):
    with connect() as con:
        con.execute(
            "INSERT INTO profiles(guild_id,user_id) VALUES(?,?) "
            "ON CONFLICT(guild_id,user_id) DO NOTHING",
            (guild_id, user_id),
        )

def add_message_xp(guild_id, user_id, amount=8, cooldown=45):
    now = time.time()
    with connect() as con:
        row = con.execute(
            "SELECT * FROM profiles WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        ).fetchone()
        if not row:
            con.execute("INSERT INTO profiles(guild_id,user_id) VALUES(?,?)", (guild_id, user_id))
            row = con.execute(
                "SELECT * FROM profiles WHERE guild_id=? AND user_id=?",
                (guild_id, user_id),
            ).fetchone()
        level = row["level"]
        xp = row["xp"]
        msg_count = row["msg_count"] + 1
        gained = 0
        new_level = level
        if now - row["last_xp_at"] >= cooldown:
            gained = amount
            xp += amount
            while xp >= new_level * 500:
                xp -= new_level * 500
                new_level += 1
        last_xp_at = now if gained else row["last_xp_at"]
        con.execute(
            "UPDATE profiles SET xp=?, level=?, msg_count=?, last_xp_at=? WHERE guild_id=? AND user_id=?",
            (xp, new_level, msg_count, last_xp_at, guild_id, user_id),
        )
        return {"xp_gained": gained, "xp": xp, "level": new_level, "old_level": level, "msg_count": msg_count}

def add_voice_seconds(guild_id, user_id, seconds):
    ensure_profile(guild_id, user_id)
    with connect() as con:
        con.execute(
            "UPDATE profiles SET voice_seconds=voice_seconds+? WHERE guild_id=? AND user_id=?",
            (max(0, int(seconds)), guild_id, user_id),
        )

def record_game(guild_id, user_id, won=False):
    ensure_profile(guild_id, user_id)
    with connect() as con:
        con.execute(
            "UPDATE profiles SET games_played=games_played+1, games_won=games_won+? WHERE guild_id=? AND user_id=?",
            (1 if won else 0, guild_id, user_id),
        )

def get_profile(guild_id, user_id):
    ensure_profile(guild_id, user_id)
    with connect() as con:
        return con.execute("SELECT * FROM profiles WHERE guild_id=? AND user_id=?", (guild_id, user_id)).fetchone()

def leaderboard(guild_id, field="xp", limit=10):
    allowed = {"xp","msg_count","voice_seconds","games_won","games_played"}
    field = field if field in allowed else "xp"
    with connect() as con:
        return con.execute(
            f"SELECT * FROM profiles WHERE guild_id=? ORDER BY {field} DESC LIMIT ?",
            (guild_id, limit),
        ).fetchall()

def add_warning(guild_id, user_id, moderator_id, reason):
    with connect() as con:
        cur = con.execute(
            "INSERT INTO warnings(guild_id,user_id,moderator_id,reason,created_at) VALUES(?,?,?,?,?)",
            (guild_id, user_id, moderator_id, reason, time.time()),
        )
        return cur.lastrowid

def get_warnings(guild_id, user_id):
    with connect() as con:
        return con.execute(
            "SELECT * FROM warnings WHERE guild_id=? AND user_id=? ORDER BY created_at DESC",
            (guild_id, user_id),
        ).fetchall()

def save_role_panel(guild_id, channel_id, message_id, title, description, mode, role_ids):
    with connect() as con:
        cur = con.execute(
            "INSERT INTO role_panels(guild_id,channel_id,message_id,title,description,mode,role_ids) VALUES(?,?,?,?,?,?,?)",
            (guild_id, channel_id, message_id, title, description, mode, json.dumps(role_ids)),
        )
        return cur.lastrowid

def get_role_panels(guild_id=None):
    with connect() as con:
        if guild_id is None:
            return con.execute("SELECT * FROM role_panels ORDER BY id").fetchall()
        return con.execute("SELECT * FROM role_panels WHERE guild_id=? ORDER BY id", (guild_id,)).fetchall()

def delete_role_panel(guild_id, panel_id):
    with connect() as con:
        cur = con.execute("DELETE FROM role_panels WHERE guild_id=? AND id=?", (guild_id, panel_id))
        return cur.rowcount > 0

def save_poll(guild_id, channel_id, message_id, question, options, anonymous=False, multiple=False, ends_at=None):
    with connect() as con:
        cur = con.execute(
            "INSERT INTO polls(guild_id,channel_id,message_id,question,options,anonymous,multiple,ends_at) VALUES(?,?,?,?,?,?,?,?)",
            (guild_id, channel_id, message_id, question, json.dumps(options, ensure_ascii=False), int(anonymous), int(multiple), ends_at),
        )
        return cur.lastrowid

def get_poll(poll_id):
    with connect() as con:
        return con.execute("SELECT * FROM polls WHERE id=?", (poll_id,)).fetchone()

def list_active_polls():
    with connect() as con:
        return con.execute("SELECT * FROM polls WHERE closed=0").fetchall()

def close_poll(poll_id):
    with connect() as con:
        con.execute("UPDATE polls SET closed=1 WHERE id=?", (poll_id,))


def delete_poll(poll_id):
    with connect() as con:
        con.execute("DELETE FROM poll_votes WHERE poll_id=?", (poll_id,))
        con.execute("DELETE FROM polls WHERE id=?", (poll_id,))


def poll_voter_key(poll_id, user_id, con=None):
    close_after = False
    if con is None:
        con = connect()
        close_after = True
    try:
        row = con.execute("SELECT anonymous FROM polls WHERE id=?", (poll_id,)).fetchone()
        if not row or not row["anonymous"]:
            return int(user_id)
        secret = os.getenv("POLL_ANON_SECRET") or os.getenv("DISCORD_TOKEN", "change-me")
        raw = f"{poll_id}:{user_id}".encode()
        digest = hmac.new(secret.encode(), raw, hashlib.sha256).digest()
        return -((int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF) + 1)
    finally:
        if close_after:
            con.close()


def set_poll_vote(poll_id, user_id, option_index, multiple=False):
    with connect() as con:
        voter_id = poll_voter_key(poll_id, user_id, con)
        if not multiple:
            con.execute("DELETE FROM poll_votes WHERE poll_id=? AND user_id=?", (poll_id, voter_id))
        else:
            con.execute("DELETE FROM poll_votes WHERE poll_id=? AND user_id=? AND option_index=?", (poll_id, voter_id, option_index))
        con.execute(
            "INSERT OR IGNORE INTO poll_votes(poll_id,user_id,option_index) VALUES(?,?,?)",
            (poll_id, voter_id, option_index),
        )

def replace_poll_votes(poll_id, user_id, option_indexes):
    clean = sorted(set(int(x) for x in option_indexes))
    with connect() as con:
        voter_id = poll_voter_key(poll_id, user_id, con)
        con.execute("DELETE FROM poll_votes WHERE poll_id=? AND user_id=?", (poll_id, voter_id))
        con.executemany(
            "INSERT INTO poll_votes(poll_id,user_id,option_index) VALUES(?,?,?)",
            [(poll_id, voter_id, idx) for idx in clean],
        )

def get_poll_counts(poll_id):
    with connect() as con:
        rows = con.execute(
            "SELECT option_index, COUNT(*) AS total FROM poll_votes WHERE poll_id=? GROUP BY option_index",
            (poll_id,),
        ).fetchall()
    return {row["option_index"]: row["total"] for row in rows}

def save_giveaway(guild_id, channel_id, message_id, prize, winners, ends_at):
    with connect() as con:
        cur = con.execute(
            "INSERT INTO giveaways(guild_id,channel_id,message_id,prize,winners,ends_at) VALUES(?,?,?,?,?,?)",
            (guild_id, channel_id, message_id, prize, winners, ends_at),
        )
        return cur.lastrowid

def get_giveaway(giveaway_id):
    with connect() as con:
        return con.execute("SELECT * FROM giveaways WHERE id=?", (giveaway_id,)).fetchone()

def list_active_giveaways():
    with connect() as con:
        return con.execute("SELECT * FROM giveaways WHERE ended=0").fetchall()

def end_giveaway(giveaway_id):
    with connect() as con:
        con.execute("UPDATE giveaways SET ended=1 WHERE id=?", (giveaway_id,))


def delete_giveaway(giveaway_id):
    with connect() as con:
        con.execute("DELETE FROM giveaway_winners WHERE giveaway_id=?", (giveaway_id,))
        con.execute("DELETE FROM giveaway_entries WHERE giveaway_id=?", (giveaway_id,))
        con.execute("DELETE FROM giveaways WHERE id=?", (giveaway_id,))


def record_giveaway_winners(giveaway_id, user_ids):
    clean = sorted(set(int(x) for x in user_ids))
    if not clean:
        return
    with connect() as con:
        row = con.execute(
            "SELECT COALESCE(MAX(round_no), 0) AS n FROM giveaway_winners WHERE giveaway_id=?",
            (giveaway_id,),
        ).fetchone()
        round_no = int(row["n"]) + 1
        con.executemany(
            "INSERT OR IGNORE INTO giveaway_winners(giveaway_id,user_id,round_no) VALUES(?,?,?)",
            [(giveaway_id, uid, round_no) for uid in clean],
        )


def get_giveaway_previous_winners(giveaway_id):
    with connect() as con:
        return {
            row["user_id"]
            for row in con.execute(
                "SELECT DISTINCT user_id FROM giveaway_winners WHERE giveaway_id=?",
                (giveaway_id,),
            ).fetchall()
        }


def enter_giveaway(giveaway_id, user_id):
    with connect() as con:
        con.execute(
            "INSERT OR IGNORE INTO giveaway_entries(giveaway_id,user_id) VALUES(?,?)",
            (giveaway_id, user_id),
        )

def get_giveaway_entries(giveaway_id):
    with connect() as con:
        return [row["user_id"] for row in con.execute(
            "SELECT user_id FROM giveaway_entries WHERE giveaway_id=?",
            (giveaway_id,),
        ).fetchall()]

def add_note(guild_id, user_id, title, content):
    with connect() as con:
        cur = con.execute(
            "INSERT INTO notes(guild_id,user_id,title,content) VALUES(?,?,?,?)",
            (guild_id, user_id, title, content),
        )
        return cur.lastrowid

def list_notes(guild_id, user_id):
    with connect() as con:
        return con.execute(
            "SELECT * FROM notes WHERE guild_id=? AND user_id=? ORDER BY id DESC",
            (guild_id, user_id),
        ).fetchall()

def get_note(guild_id, user_id, note_id):
    with connect() as con:
        return con.execute(
            "SELECT * FROM notes WHERE guild_id=? AND user_id=? AND id=?",
            (guild_id, user_id, note_id),
        ).fetchone()

def delete_note(guild_id, user_id, note_id):
    with connect() as con:
        cur = con.execute(
            "DELETE FROM notes WHERE guild_id=? AND user_id=? AND id=?",
            (guild_id, user_id, note_id),
        )
        return cur.rowcount > 0



def add_temp_channel(guild_id, channel_id):
    with connect() as con:
        con.execute(
            "INSERT OR IGNORE INTO temp_channels(guild_id,channel_id) VALUES(?,?)",
            (guild_id, channel_id),
        )


def remove_temp_channel(channel_id):
    with connect() as con:
        con.execute("DELETE FROM temp_channels WHERE channel_id=?", (channel_id,))


def list_temp_channels(guild_id=None):
    with connect() as con:
        if guild_id is None:
            return con.execute("SELECT * FROM temp_channels ORDER BY channel_id").fetchall()
        return con.execute(
            "SELECT * FROM temp_channels WHERE guild_id=? ORDER BY channel_id",
            (guild_id,),
        ).fetchall()



def start_voice_session(guild_id, user_id, channel_id, started_at=None):
    started_at = started_at or time.time()
    with connect() as con:
        con.execute(
            "INSERT INTO voice_sessions(guild_id,user_id,channel_id,started_at) VALUES(?,?,?,?) "
            "ON CONFLICT(guild_id,user_id) DO UPDATE SET channel_id=excluded.channel_id, started_at=excluded.started_at",
            (guild_id, user_id, channel_id, started_at),
        )


def get_voice_session(guild_id, user_id):
    with connect() as con:
        return con.execute(
            "SELECT * FROM voice_sessions WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        ).fetchone()


def list_voice_sessions(guild_id=None):
    with connect() as con:
        if guild_id is None:
            return con.execute("SELECT * FROM voice_sessions ORDER BY guild_id,user_id").fetchall()
        return con.execute(
            "SELECT * FROM voice_sessions WHERE guild_id=? ORDER BY user_id",
            (guild_id,),
        ).fetchall()


def finish_voice_session(guild_id, user_id):
    with connect() as con:
        row = con.execute(
            "SELECT * FROM voice_sessions WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        ).fetchone()
        if row:
            con.execute(
                "DELETE FROM voice_sessions WHERE guild_id=? AND user_id=?",
                (guild_id, user_id),
            )
        return row
