import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from web_dashboard import _find_message_in_readable_channels


class DashboardDeleteLookupTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_channel_is_checked_before_other_channels(self):
        permissions = SimpleNamespace(
            view_channel=True,
            read_message_history=True,
        )
        guild = SimpleNamespace(me=object(), text_channels=[], threads=[])
        target_message = SimpleNamespace(id=123, author=SimpleNamespace(id=456))
        selected = SimpleNamespace(
            id=789,
            guild=guild,
            permissions_for=lambda member: permissions,
            fetch_message=AsyncMock(return_value=target_message),
        )

        bot = SimpleNamespace(guilds=[])

        found_channel, found_message = await _find_message_in_readable_channels(
            bot, selected, target_message.id
        )

        self.assertIs(found_channel, selected)
        self.assertIs(found_message, target_message)
        selected.fetch_message.assert_awaited_once_with(target_message.id)


if __name__ == "__main__":
    unittest.main()
