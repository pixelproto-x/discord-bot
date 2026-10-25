import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from cogs.banter import LocalBanterCog


class LocalBanterDuplicateReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_automatic_local_reply_is_disabled(self):
        cog = LocalBanterCog(SimpleNamespace())
        cog._reply = AsyncMock()
        message = SimpleNamespace(author=SimpleNamespace(bot=False))

        await cog.on_message(message)

        cog._reply.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
