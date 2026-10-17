import sqlite3
import tempfile
import time
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import storage
from cogs.games import GameCog
from cogs.moderation import ModerationCog, count_message_mentions
from cogs.settings import autorole_is_safe


class StorageConnectionTests(unittest.TestCase):
    def test_context_managed_connections_are_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(storage, "DB_PATH", Path(directory) / "bot.sqlite3"):
                storage.init_db()
                with storage.connect() as connection:
                    connection.execute("CREATE TABLE connection_test (id INTEGER)")
                    connection.execute("INSERT INTO connection_test (id) VALUES (1)")
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute("SELECT 1")


class ModerationHardeningTests(unittest.TestCase):
    def test_everyone_and_here_count_toward_mention_limit(self):
        self.assertEqual(count_message_mentions([10, 11, 11], [20], False), 3)
        self.assertEqual(count_message_mentions([10], [20], True), 3)

    def test_old_antispam_buckets_are_removed(self):
        cog = object.__new__(ModerationCog)
        cog.message_times = {
            (1, 2): deque([10.0, 11.0]),
            (1, 3): deque([99.0]),
        }
        cog._last_message_times_prune = 0.0

        cog._prune_message_times(now=100.0, force=True)

        self.assertNotIn((1, 2), cog.message_times)
        self.assertEqual(list(cog.message_times[(1, 3)]), [99.0])


class GameSessionCleanupTests(unittest.TestCase):
    def test_stale_game_sessions_are_removed(self):
        cog = object.__new__(GameCog)
        cog.guess_sessions = {(1, 1): {"target": 10, "started": 1.0}}
        cog.wordle_sessions = {(1, 2): {"word": "apple", "started": 1.0}}
        cog.hangman_sessions = {(1, 3): {"word": "python", "started": 99.0}}
        cog.higher_sessions = {(1, 4): {"value": 20, "expires": 99.0}}

        GameCog._prune_expired_sessions(cog, now=4000.0)

        self.assertEqual(cog.guess_sessions, {})
        self.assertEqual(cog.wordle_sessions, {})
        self.assertEqual(cog.hangman_sessions, {})
        self.assertEqual(cog.higher_sessions, {})


class AutoroleSafetyTests(unittest.TestCase):
    def test_autorole_requires_safe_permissions_and_lower_hierarchy(self):
        class Role:
            def __init__(self, position, *, default=False, dangerous=False):
                self.position = position
                self.default = default
                self.permissions = SimpleNamespace(**{
                    name: dangerous for name in (
                        "administrator", "manage_guild", "manage_roles", "manage_channels",
                        "manage_messages", "moderate_members", "kick_members", "ban_members",
                        "manage_webhooks", "manage_expressions", "manage_emojis_and_stickers",
                        "manage_events", "manage_threads", "manage_nicknames", "view_audit_log",
                        "mention_everyone", "mute_members", "deafen_members", "move_members",
                    )
                })

            def is_default(self):
                return self.default

            def __lt__(self, other):
                return self.position < other.position

        bot_member = SimpleNamespace(
            guild_permissions=SimpleNamespace(manage_roles=True),
            top_role=Role(10),
        )
        self.assertTrue(autorole_is_safe(Role(5), bot_member))
        self.assertFalse(autorole_is_safe(Role(10), bot_member))
        self.assertFalse(autorole_is_safe(Role(5, dangerous=True), bot_member))
        self.assertFalse(autorole_is_safe(Role(5), SimpleNamespace(
            guild_permissions=SimpleNamespace(manage_roles=False),
            top_role=Role(10),
        )))


if __name__ == "__main__":
    unittest.main()
