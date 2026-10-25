import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
import unittest
from pathlib import Path
from unittest.mock import patch

from cogs.hacks import Hacks


class BackupStoreTests(unittest.TestCase):
    def test_store_preserves_existing_backups_when_another_server_is_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server_backups.json"
            with patch("cogs.hacks.BACKUP_PATH", path):
                first_key, first_saved = Hacks._store_backup_payload({
                    "server_id": "100",
                    "server_name": "first",
                })
                second_key, second_saved = Hacks._store_backup_payload({
                    "server_id": "200",
                    "server_name": "second",
                })

                self.assertTrue(first_saved)
                self.assertTrue(second_saved)
                self.assertNotEqual(first_key, second_key)
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(set(data["servers"]), {first_key, second_key})
                self.assertEqual(data["last_backup_key"], second_key)

    def test_store_does_not_duplicate_a_server_if_another_sync_finished_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server_backups.json"
            with patch("cogs.hacks.BACKUP_PATH", path):
                key, saved = Hacks._store_backup_payload({
                    "server_id": "100",
                    "server_name": "first",
                })
                duplicate_key, duplicate_saved = Hacks._store_backup_payload({
                    "server_id": "100",
                    "server_name": "first",
                })

                self.assertTrue(saved)
                self.assertFalse(duplicate_saved)
                self.assertEqual(duplicate_key, key)
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(len(data["servers"]), 1)


    def test_concurrent_stores_preserve_every_server_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server_backups.json"
            payloads = [
                {"server_id": str(server_id), "server_name": f"server-{server_id}"}
                for server_id in range(100, 124)
            ]
            with patch("cogs.hacks.BACKUP_PATH", path):
                with ThreadPoolExecutor(max_workers=12) as executor:
                    results = list(executor.map(Hacks._store_backup_payload, payloads))

                self.assertTrue(all(saved for _, saved in results))
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(len(data["servers"]), len(payloads))
                self.assertEqual(
                    {item["server_id"] for item in data["servers"].values()},
                    {item["server_id"] for item in payloads},
                )
                self.assertIn(data["last_backup_key"], {key for key, _ in results})
                self.assertFalse(list(Path(tmp).glob("*.tmp")))
                self.assertFalse(list(Path(tmp).glob(".*.tmp")))


if __name__ == "__main__":
    unittest.main()
