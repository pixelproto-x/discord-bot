import unittest

from cogs.hacks import is_backup_apply_confirmed


class BackupApplyConfirmationTests(unittest.TestCase):
    def test_requires_exact_confirmation_phrase(self):
        for value in (None, "", "restore", "YES", "RESTORE NOW", "false", 1):
            with self.subTest(value=value):
                self.assertFalse(is_backup_apply_confirmed(value))

    def test_accepts_explicit_confirmation_phrase(self):
        self.assertTrue(is_backup_apply_confirmed("RESTORE"))
        self.assertTrue(is_backup_apply_confirmed(" RESTORE "))


if __name__ == "__main__":
    unittest.main()
