import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import storage
from cogs.history import (History, HistoryPaginator, MAX_HISTORY_ATTACHMENT_BYTES, _archive_attachment, build_history_embed)


class HistoryFeatureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = storage.DB_PATH
        storage.DB_PATH = Path(self.temp_dir.name) / "history-test.sqlite3"
        self.history = History(None)
        self.store = self.history.store

    async def asyncTearDown(self):
        storage.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def make_message(self, message_id=500, *, with_attachment=False):
        attachments = []
        if with_attachment:
            attachments.append(
                SimpleNamespace(
                    filename="fixture.txt",
                    size=4,
                    url="https://cdn.discordapp.com/attachments/1/2/fixture.txt",
                    content_type="text/plain",
                )
            )
        author = SimpleNamespace(
            id=42,
            bot=False,
            display_name="顯示名稱",
            name="account_name",
            display_avatar=SimpleNamespace(url="https://example.com/avatar.png"),
        )
        return SimpleNamespace(
            id=message_id,
            guild=SimpleNamespace(id=10),
            channel=SimpleNamespace(id=20, name="general"),
            author=author,
            content="這是一則測試訊息",
            created_at=datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc),
            attachments=attachments,
        )

    def archived_attachment(self):
        return [{
            "filename": "fixture.txt",
            "size": 4,
            "url": "https://gofile.io/d/history-test",
            "discord_url": "https://cdn.discordapp.com/attachments/1/2/fixture.txt",
            "content_type": "text/plain",
            "archived": True,
        }]


    async def test_oversized_attachment_is_not_downloaded_or_uploaded(self):
        attachment = SimpleNamespace(
            filename="huge.bin",
            size=MAX_HISTORY_ATTACHMENT_BYTES + 1,
            url="https://cdn.discordapp.com/attachments/1/2/huge.bin",
            content_type="application/octet-stream",
        )
        with patch("cogs.history.aiohttp.ClientSession") as session:
            result = await _archive_attachment(attachment)
        self.assertIsNone(result)
        session.assert_not_called()

    async def test_attachment_archive_finishing_after_delete_updates_deleted_record(self):
        message = self.make_message(with_attachment=True)

        async def delayed_archive(_message):
            # Simulate deletion arriving while the GoFile upload is still in flight.
            await self.history.on_message_delete(message)
            return self.archived_attachment()

        with patch("cogs.history._snapshot_attachments", new=AsyncMock(side_effect=delayed_archive)) as archive:
            await self.history.on_message(message)
            await asyncio.gather(*list(self.history._attachment_tasks))

        self.assertEqual(archive.await_count, 1)
        self.assertTrue(self.store.has_deleted(message.id))
        self.assertIsNone(self.store.get_snapshot(message.id))
        deleted = self.store.get_deleted(message.guild.id)[0]
        self.assertEqual(deleted["attachments"][0]["url"], "https://gofile.io/d/history-test")
        self.assertTrue(deleted["attachments"][0]["archived"])

    async def test_cached_and_raw_delete_events_do_not_upload_attachments_twice(self):
        message = self.make_message(with_attachment=True)
        with patch(
            "cogs.history._snapshot_attachments",
            new=AsyncMock(return_value=self.archived_attachment()),
        ) as archive:
            await self.history.on_message(message)
            await self.history.on_message_delete(message)
            await self.history.on_raw_message_delete(
                SimpleNamespace(
                    guild_id=message.guild.id,
                    message_id=message.id,
                    cached_message=message,
                )
            )
            await asyncio.gather(*list(self.history._attachment_tasks))

        self.assertEqual(archive.await_count, 1)
        self.assertTrue(self.store.has_deleted(message.id))
        self.assertIsNone(self.store.get_snapshot(message.id))

    async def test_cached_message_is_used_when_no_original_snapshot_exists(self):
        message = self.make_message(with_attachment=True)
        with patch(
            "cogs.history._snapshot_attachments",
            new=AsyncMock(return_value=self.archived_attachment()),
        ) as archive:
            await self.history.on_message_delete(message)
            await asyncio.gather(*list(self.history._attachment_tasks))

        archive.assert_awaited_once_with(message)
        deleted = self.store.get_deleted(message.guild.id)
        self.assertEqual(len(deleted), 1)
        self.assertEqual(deleted[0]["author_name"], "顯示名稱")
        self.assertEqual(deleted[0]["attachments"][0]["url"], "https://gofile.io/d/history-test")

    async def test_deleted_message_embed_contains_requested_sections_and_original_author(self):
        record = {
            "message_id": 500,
            "guild_id": 10,
            "channel_id": 20,
            "channel_name": "general",
            "author_id": 42,
            "author_name": "原作者顯示名稱",
            "author_avatar_url": "https://example.com/avatar.png",
            "content": "這是一則測試訊息",
            "attachments": self.archived_attachment(),
            "created_at": datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc).timestamp(),
            "deleted_at": datetime(2026, 10, 20, 12, 5, tzinfo=timezone.utc).timestamp(),
        }
        embed = build_history_embed(record)

        self.assertEqual(embed.author.name, "原作者顯示名稱")
        self.assertIn("**訊息內容**", embed.description)
        self.assertIn("**附件**", embed.description)
        self.assertIn("fixture.txt", embed.description)
        self.assertIn("https://gofile.io/d/history-test", embed.description)
        self.assertIn("**傳送時間**", embed.description)
        self.assertIn("**刪除時間**", embed.description)
        self.assertIn("作者 ID：42", embed.footer.text)

    async def test_paginator_renders_five_buttons_in_requested_order(self):
        record = {
            "message_id": 500,
            "guild_id": 10,
            "channel_id": 20,
            "channel_name": "general",
            "author_id": 42,
            "author_name": "原作者",
            "content": "測試",
            "attachments": [],
            "created_at": 1_792_478_400,
            "deleted_at": 1_792_478_700,
        }
        view = HistoryPaginator([record, dict(record, message_id=501)], owner_id=42)
        self.assertEqual(len(view.children), 5)
        self.assertEqual(
            [item.label for item in view.children],
            ["⏮", "◀", "頁數 1/2", "▶", "⏭"],
        )
        self.assertTrue(view.first_button.disabled)
        self.assertTrue(view.previous_button.disabled)
        self.assertFalse(view.next_button.disabled)
        view.stop()


if __name__ == "__main__":
    unittest.main()
