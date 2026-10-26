import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import cogs.economy as economy


class EconomyWalletDisplayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_patch = patch.object(
            economy, "DB_PATH", Path(self.temp_dir.name) / "economy.db"
        )
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.cog = economy.EconomyCog(None)

    async def test_wallet_formats_text_balances_and_total_as_integers(self):
        guild_id = 123
        user_id = 456
        self.cog.store.get(guild_id, user_id)
        with closing(self.cog.store.connect()) as con:
            con.execute(
                "UPDATE users SET wallet=?, bank=? WHERE user_id=?",
                ("1234567", "2345", user_id),
            )

        user = SimpleNamespace(
            id=user_id,
            display_name="Test User",
            display_avatar=SimpleNamespace(url="https://example.com/avatar.png"),
        )
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=guild_id),
            author=user,
            reply=AsyncMock(),
        )

        await self.cog._wallet(ctx)

        ctx.reply.assert_awaited_once()
        embed = ctx.reply.await_args.kwargs["embed"]
        self.assertEqual(
            [field.value for field in embed.fields],
            ["1,234,567 金幣", "2,345 金幣", "1,236,912 金幣"],
        )

    def test_format_coins_accepts_text_database_values(self):
        self.assertEqual(economy.format_coins("1000"), "1,000")
        self.assertEqual(economy.format_coins("12345678901234567890"), "12,345,678,901,234,567,890")


if __name__ == "__main__":
    unittest.main()
