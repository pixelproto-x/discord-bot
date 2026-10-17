import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import storage


class ActiveReminderLimitTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path_patcher = patch.object(storage, "DB_PATH", Path(self.tempdir.name) / "bot.sqlite3")
        self.db_path_patcher.start()
        storage.init_db()

    def tearDown(self):
        self.db_path_patcher.stop()
        self.tempdir.cleanup()

    def test_active_reminders_are_capped_per_member_and_guild(self):
        due_at = 4_000_000_000
        created = [
            storage.add_reminder(100, 200, 300, f"reminder {index}", due_at)
            for index in range(storage.MAX_ACTIVE_REMINDERS_PER_USER)
        ]

        self.assertTrue(all(reminder_id is not None for reminder_id in created))
        self.assertIsNone(storage.add_reminder(100, 200, 300, "one too many", due_at))
        self.assertEqual(len(storage.list_reminders(100, 200)), storage.MAX_ACTIVE_REMINDERS_PER_USER)

    def test_completing_a_reminder_frees_capacity(self):
        due_at = 4_000_000_000
        reminder_ids = [
            storage.add_reminder(100, 200, 300, f"reminder {index}", due_at)
            for index in range(storage.MAX_ACTIVE_REMINDERS_PER_USER)
        ]
        storage.complete_reminder(reminder_ids[0])

        self.assertIsNotNone(storage.add_reminder(100, 200, 300, "replacement", due_at))
        self.assertEqual(len(storage.list_reminders(100, 200)), storage.MAX_ACTIVE_REMINDERS_PER_USER)

    def test_limit_is_independent_between_members_and_guilds(self):
        due_at = 4_000_000_000
        for index in range(storage.MAX_ACTIVE_REMINDERS_PER_USER):
            self.assertIsNotNone(storage.add_reminder(100, 200, 300, f"member A {index}", due_at))

        self.assertIsNotNone(storage.add_reminder(100, 201, 300, "member B", due_at))
        self.assertIsNotNone(storage.add_reminder(101, 200, 301, "another guild", due_at))


if __name__ == "__main__":
    unittest.main()
