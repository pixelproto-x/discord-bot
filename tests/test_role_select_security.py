import unittest
from types import SimpleNamespace

from cogs.role_select import is_safe_game_role


SENSITIVE_PERMISSIONS = (
    "administrator",
    "manage_guild",
    "manage_roles",
    "manage_channels",
    "manage_messages",
    "moderate_members",
    "kick_members",
    "ban_members",
    "manage_webhooks",
    "manage_expressions",
    "manage_emojis_and_stickers",
    "manage_events",
    "manage_threads",
    "manage_nicknames",
    "view_audit_log",
    "mention_everyone",
    "mute_members",
    "deafen_members",
    "move_members",
)


class Role:
    def __init__(self, position, *, dangerous=False, default=False, managed=False):
        self.position = position
        self.managed = managed
        self.permissions = SimpleNamespace(**{
            name: dangerous for name in SENSITIVE_PERMISSIONS
        })
        self._default = default

    def is_default(self):
        return self._default

    def __lt__(self, other):
        return self.position < other.position


def bot_member(*, manage_roles=True, top_position=10):
    return SimpleNamespace(
        guild_permissions=SimpleNamespace(manage_roles=manage_roles),
        top_role=Role(top_position),
    )


class GameRoleSecurityTests(unittest.TestCase):
    def test_safe_low_privilege_role_can_be_self_assigned(self):
        self.assertTrue(is_safe_game_role(Role(5), bot_member()))

    def test_administrator_or_other_sensitive_role_is_rejected(self):
        self.assertFalse(is_safe_game_role(Role(5, dangerous=True), bot_member()))

    def test_default_managed_and_too_high_roles_are_rejected(self):
        member = bot_member()
        self.assertFalse(is_safe_game_role(Role(1, default=True), member))
        self.assertFalse(is_safe_game_role(Role(5, managed=True), member))
        self.assertFalse(is_safe_game_role(Role(10), member))
        self.assertFalse(is_safe_game_role(Role(11), member))

    def test_requires_bot_manage_roles_permission(self):
        self.assertFalse(is_safe_game_role(Role(5), bot_member(manage_roles=False)))

    def test_missing_role_or_bot_member_is_rejected(self):
        self.assertFalse(is_safe_game_role(None, bot_member()))
        self.assertFalse(is_safe_game_role(Role(5), None))


if __name__ == "__main__":
    unittest.main()
