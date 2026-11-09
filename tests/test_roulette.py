import unittest
from types import SimpleNamespace

from cogs.games import eligible_roulette_members


class RouletteEligibilityTests(unittest.TestCase):
    def test_only_opted_in_members_bot_can_timeout_are_eligible(self):
        bot_member = SimpleNamespace(id=99, top_role=10)
        owner = SimpleNamespace(id=1, bot=False, top_role=1)
        bot = SimpleNamespace(id=99, bot=True, top_role=1)
        eligible = SimpleNamespace(id=2, bot=False, top_role=3)
        too_high = SimpleNamespace(id=3, bot=False, top_role=10)
        members = {1: owner, 2: eligible, 3: too_high, 99: bot}
        guild = SimpleNamespace(owner_id=1, get_member=lambda member_id: members.get(member_id))

        result = eligible_roulette_members([1, 2, 3, 99, 404], guild, bot_member)

        self.assertEqual(result, [eligible])


if __name__ == "__main__":
    unittest.main()
