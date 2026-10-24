import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs.games import GameCog


class GuessCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_submitted_number_is_checked_immediately(self):
        cog = object.__new__(GameCog)
        cog.guess_sessions = {}
        cog.finish_game = AsyncMock()

        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=123),
            user=SimpleNamespace(id=456),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        with patch("cogs.games.random.randint", return_value=42):
            await GameCog.guess.callback(cog, interaction, number=30)

        self.assertEqual(cog.guess_sessions[(123, 456)]["target"], 42)
        interaction.response.send_message.assert_awaited_once_with("太小了。", ephemeral=False)
        cog.finish_game.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
