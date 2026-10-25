import unittest

import discord

from cogs.hacks import (
    _convert_backup_setting,
    _ordinary_backup_channels,
    is_backup_apply_confirmed,
    validate_backup_payload,
)


class BackupOrdinaryChannelSelectionTests(unittest.TestCase):
    def test_announcement_channels_are_included_in_restore_queue(self):
        channels = [
            {"type": "category", "name": "category"},
            {"type": "text", "name": "general"},
            {"type": "announcement", "name": "news"},
            {"type": "voice", "name": "voice"},
            {"type": "stage", "name": "stage"},
            {"type": "forum", "name": "forum"},
            {"type": "unsupported", "name": "unknown"},
        ]

        restored = _ordinary_backup_channels(channels)

        self.assertEqual(
            [item["type"] for item in restored],
            ["text", "announcement", "voice", "stage", "forum"],
        )


class BackupApplyConfirmationTests(unittest.TestCase):
    def test_requires_exact_confirmation_phrase(self):
        for value in (None, "", "restore", "YES", "RESTORE NOW", "false", 1):
            with self.subTest(value=value):
                self.assertFalse(is_backup_apply_confirmed(value))

    def test_accepts_explicit_confirmation_phrase(self):
        self.assertTrue(is_backup_apply_confirmed("RESTORE"))
        self.assertTrue(is_backup_apply_confirmed(" RESTORE "))

class BackupPayloadValidationTests(unittest.TestCase):
    def test_rejects_malformed_nested_items_before_restore(self):
        payloads = (
            {"roles": ["not-a-role"], "channels": []},
            {"roles": [], "channels": [None]},
            {"roles": [], "channels": [{"type": "text", "name": "general", "position": None}]},
            {"roles": [], "channels": [{"type": "text", "name": "general", "overwrites": ["bad"]}]},
            {"roles": [], "channels": [], "emojis": {"not": "a list"}},
            {"roles": [], "channels": [], "guild_settings": {"preferred_locale": "not-a-locale"}},
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                self.assertIsNotNone(validate_backup_payload(payload))

    def test_valid_payload_normalizes_numeric_fields_and_locale(self):
        payload = {
            "roles": [{"name": "Member", "permissions": "0", "color": "0", "position": "1"}],
            "channels": [{"type": "text", "name": "general", "position": "2",
                          "slowmode_delay": "0", "overwrites": [], "available_tags": []}],
            "emojis": [],
            "stickers": [],
            "guild_settings": {"preferred_locale": "zh-TW", "verification_level": "1"},
        }
        self.assertIsNone(validate_backup_payload(payload))
        self.assertEqual(payload["roles"][0]["position"], 1)
        self.assertEqual(payload["channels"][0]["position"], 2)
        self.assertEqual(payload["guild_settings"]["preferred_locale"], discord.Locale("zh-TW"))
        self.assertEqual(_convert_backup_setting("preferred_locale", "zh-TW"), discord.Locale("zh-TW"))
        self.assertEqual(_convert_backup_setting("verification_level", "1"), discord.VerificationLevel(1))

if __name__ == "__main__":
    unittest.main()
