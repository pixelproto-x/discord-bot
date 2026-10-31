import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

import storage
from cogs.history import HISTORY_ALLOWED_USER_ID, History, HistoryPaginator, build_history_embed


class HistoryEndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = storage.DB_PATH
        storage.DB_PATH = Path(self.temp_dir.name) / "history-e2e.sqlite3"
        self.cog = History(None)
        self.store = self.cog.store

    async def asyncTearDown(self):
        storage.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def make_message(self, message_id=501, attachment=False):
        author = SimpleNamespace(
            id=902,
            bot=False,
            name="account",
            display_name="Original Display Name",
            display_avatar=SimpleNamespace(url="https://example.com/avatar.png"),
        )
        attachments = []
        if attachment:
            attachments = [SimpleNamespace(
                filename="fixture.txt", size=12,
                url="https://cdn.discordapp.com/attachments/1/2/fixture.txt",
                content_type="text/plain",
            )]
        return SimpleNamespace(
            id=message_id,
            guild=SimpleNamespace(id=100),
            channel=SimpleNamespace(id=200, name="test-channel"),
            author=author,
            content="end-to-end history test",
            created_at=datetime(2026, 10, 20, 1, 0, tzinfo=timezone.utc),
            attachments=attachments,
        )

    async def test_message_to_deleted_record_to_private_command_and_pagination(self):
        message = self.make_message(attachment=True)
        gofile = [{
            "filename": "fixture.txt", "size": 12,
            "url": "https://gofile.io/d/e2e-fixture",
            "discord_url": str(message.attachments[0].url),
            "content_type": "text/plain", "archived": True,
        }]
        with patch("cogs.history._snapshot_attachments", new=AsyncMock(return_value=gofile)) as upload:
            await self.cog.on_message(message)
            await asyncio.gather(*list(self.cog._attachment_tasks))
            snapshot = self.store.get_snapshot(message.id)
            self.assertIsNotNone(snapshot, "Message snapshot should be committed before deletion")
            self.assertEqual(snapshot["author_name"], "Original Display Name")
            self.assertEqual(snapshot["attachments"][0]["archived"], True)

            await self.cog.on_message_delete(message)
            await self.cog.on_raw_message_delete(SimpleNamespace(
                guild_id=message.guild.id, message_id=message.id, cached_message=message
            ))

        self.assertEqual(upload.await_count, 1, "Cached/raw delete events must not duplicate uploads")
        self.assertIsNone(self.store.get_snapshot(message.id))
        self.assertTrue(self.store.has_deleted(message.id))
        deleted = self.store.get_deleted(message.guild.id)
        self.assertEqual(len(deleted), 1)
        self.assertEqual(deleted[0]["author_name"], "Original Display Name")
        self.assertEqual(deleted[0]["attachments"][0]["url"], "https://gofile.io/d/e2e-fixture")

        embed = build_history_embed(deleted[0])
        self.assertEqual(embed.author.name, "Original Display Name")
        for text in ("**訊息內容**", "**附件**", "fixture.txt", "https://gofile.io/d/e2e-fixture", "**傳送時間**", "**刪除時間**"):
            self.assertIn(text, embed.description)

        allowed_interaction = SimpleNamespace(
            user=SimpleNamespace(id=HISTORY_ALLOWED_USER_ID),
            guild_id=message.guild.id,
            response=SimpleNamespace(
                send_message=AsyncMock(),
                is_done=lambda: False,
            ),
        )
        with patch.object(self.cog, "store", self.store):
            await self.cog.history.callback(self.cog, allowed_interaction)

        allowed_interaction.response.send_message.assert_awaited_once()
        args, kwargs = allowed_interaction.response.send_message.await_args
        self.assertTrue(kwargs.get("ephemeral"))
        self.assertIsInstance(kwargs.get("embed"), discord.Embed)
        view = kwargs.get("view")
        self.assertIsInstance(view, HistoryPaginator)
        self.assertEqual(len(view.children), 5)
        self.assertEqual(view.records[0]["author_name"], "Original Display Name")
        view.stop()

    async def test_bot_authored_message_is_included_in_history(self):
        message = self.make_message(502)
        message.author.bot = True
        await self.cog.on_message(message)
        self.assertIsNotNone(self.store.get_snapshot(message.id))
        await self.cog.on_message_delete(message)
        self.assertTrue(self.store.has_deleted(message.id))
        deleted = self.store.get_deleted(message.guild.id)
        self.assertEqual(deleted[0]["author_name"], "Original Display Name")

    async def test_unauthorized_command_is_denied_ephemerally(self):
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=123456789),
            guild_id=100,
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        await self.cog.history.callback(self.cog, interaction)
        interaction.response.send_message.assert_awaited_once_with(
            "你沒有使用此指令的權限。", ephemeral=True
        )

    async def test_empty_history_returns_private_message(self):
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=HISTORY_ALLOWED_USER_ID),
            guild_id=100,
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        await self.cog.history.callback(self.cog, interaction)
        interaction.response.send_message.assert_awaited_once_with(
            "目前沒有可查詢的已刪除訊息紀錄。", ephemeral=True
        )


if __name__ == "__main__":
    unittest.main()
