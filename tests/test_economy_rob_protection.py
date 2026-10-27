import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import cogs.economy as economy


class PermanentRobberyProtectionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_patch = patch.object(
            economy, "DB_PATH", Path(self.temp_dir.name) / "economy.db"
        )
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.store = economy.EconomyStore()

    def test_configured_user_cannot_be_robbed_even_on_success_roll(self):
        guild_id = 123
        robber_id = 456
        protected_id = 1338493569781989407

        result = self.store.rob(
            guild_id, robber_id, protected_id,
            success=True, fraction=0.30, fine_fraction=0.20,
        )

        self.assertEqual(result, ("protected_permanent", 0))
        with closing(self.store.connect()) as con:
            self.assertIsNone(
                con.execute(
                    "SELECT 1 FROM users WHERE guild_id=? AND user_id=?",
                    (guild_id, protected_id),
                ).fetchone()
            )

    def test_other_users_keep_existing_robbery_flow(self):
        guild_id = 123
        robber_id = 456
        target_id = 789
        self.store.get(guild_id, robber_id)
        self.store.get(guild_id, target_id)

        result = self.store.rob(
            guild_id, robber_id, target_id,
            success=False, fraction=0.30, fine_fraction=0.20,
        )

        self.assertEqual(result[0], "failed")


if __name__ == "__main__":
    unittest.main()
