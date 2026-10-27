import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from cogs.economy_progress import _member_display_name


class EconomyLeaderboardNameTests(unittest.IsolatedAsyncioTestCase):
    async def test_cached_member_uses_display_name(self):
        guild = SimpleNamespace(
            get_member=lambda user_id: SimpleNamespace(display_name="伺服器暱稱"),
            fetch_member=AsyncMock(),
        )

        name = await _member_display_name(guild, 123456789)

        self.assertEqual(name, "伺服器暱稱")
        guild.fetch_member.assert_not_awaited()

    async def test_fetches_member_when_not_cached(self):
        member = SimpleNamespace(display_name="抓取到的暱稱")
        guild = SimpleNamespace(
            get_member=lambda user_id: None,
            fetch_member=AsyncMock(return_value=member),
        )

        name = await _member_display_name(guild, 123456789)

        self.assertEqual(name, "抓取到的暱稱")
        guild.fetch_member.assert_awaited_once_with(123456789)

    async def test_missing_member_does_not_expose_raw_user_id(self):
        guild = SimpleNamespace(
            get_member=lambda user_id: None,
            fetch_member=AsyncMock(return_value=None),
        )

        name = await _member_display_name(guild, 123456789)

        self.assertEqual(name, "無法取得名稱的成員")
        self.assertNotIn("123456789", name)


if __name__ == "__main__":
    unittest.main()
