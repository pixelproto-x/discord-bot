import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from cogs.voice import VoiceCog


class TemporaryVoiceCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_delete_removes_persistent_tracking(self):
        channel = SimpleNamespace(id=123, delete=AsyncMock())
        cog = SimpleNamespace(temp_channels={123})

        with patch("cogs.voice.storage.remove_temp_channel") as remove_record:
            deleted = await VoiceCog._delete_temp_channel(cog, channel, reason="test")

        self.assertTrue(deleted)
        self.assertNotIn(123, cog.temp_channels)
        remove_record.assert_called_once_with(123)

    async def test_missing_channel_clears_stale_tracking(self):
        response = Mock(status=404, reason="Not Found", text="")
        channel = SimpleNamespace(
            id=456,
            delete=AsyncMock(side_effect=discord.NotFound(response, "already deleted")),
        )
        cog = SimpleNamespace(temp_channels={456})

        with patch("cogs.voice.storage.remove_temp_channel") as remove_record:
            deleted = await VoiceCog._delete_temp_channel(cog, channel, reason="test")

        self.assertTrue(deleted)
        self.assertNotIn(456, cog.temp_channels)
        remove_record.assert_called_once_with(456)

    async def test_failed_delete_keeps_tracking_for_retry(self):
        response = Mock(status=403, reason="Forbidden", text="")
        channel = SimpleNamespace(
            id=789,
            delete=AsyncMock(side_effect=discord.Forbidden(response, "cannot delete")),
        )
        cog = SimpleNamespace(temp_channels={789})

        with patch("cogs.voice.storage.remove_temp_channel") as remove_record:
            deleted = await VoiceCog._delete_temp_channel(cog, channel, reason="test")

        self.assertFalse(deleted)
        self.assertIn(789, cog.temp_channels)
        remove_record.assert_not_called()


if __name__ == "__main__":
    unittest.main()
