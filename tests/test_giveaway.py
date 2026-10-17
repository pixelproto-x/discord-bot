import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cogs.giveaway import GiveawayCog


class GiveawayFinishTests(unittest.TestCase):
    def setUp(self):
        self.cog = object.__new__(GiveawayCog)
        self.cog.finish_locks = {}
        self.guild = SimpleNamespace(id=123, get_channel=lambda _channel_id: None)

    def test_finish_returns_false_for_missing_giveaway(self):
        with patch("cogs.giveaway.storage.get_giveaway", return_value=None), \
             patch("cogs.giveaway.storage.end_giveaway") as end_giveaway:
            result = asyncio.run(self.cog.finish(456, self.guild, manual=True))

        self.assertFalse(result)
        end_giveaway.assert_not_called()

    def test_finish_records_winners_and_closes_before_network_work(self):
        row = {
            "id": 456,
            "guild_id": 123,
            "ended": 0,
            "winners": 1,
            "channel_id": 789,
            "message_id": 0,
            "ends_at": 2_000_000_000,
            "prize": "test prize",
        }
        events = []

        with patch("cogs.giveaway.storage.get_giveaway", return_value=row), \
             patch("cogs.giveaway.storage.get_giveaway_entries", return_value=[111, 222]), \
             patch("cogs.giveaway.random.sample", return_value=[111]), \
             patch("cogs.giveaway.storage.record_giveaway_winners", side_effect=lambda *_: events.append("winners")), \
             patch("cogs.giveaway.storage.end_giveaway", side_effect=lambda *_: events.append("ended")):
            result = asyncio.run(self.cog.finish(456, self.guild, manual=True))

        self.assertTrue(result)
        self.assertEqual(events, ["winners", "ended"])


if __name__ == "__main__":
    unittest.main()
