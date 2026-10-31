import unittest
from types import SimpleNamespace

from cogs.media import is_interaction_response_message


class InteractionResponseDetectionTests(unittest.TestCase):
    def test_detects_application_id_on_public_slash_command_followup(self):
        message = SimpleNamespace(
            application_id=123456789,
            interaction_metadata=None,
            interaction=None,
        )
        self.assertTrue(is_interaction_response_message(message))

    def test_detects_new_interaction_metadata(self):
        message = SimpleNamespace(
            application_id=None,
            interaction_metadata=object(),
            interaction=None,
        )
        self.assertTrue(is_interaction_response_message(message))

    def test_detects_legacy_interaction_metadata(self):
        message = SimpleNamespace(
            application_id=None,
            interaction_metadata=None,
            interaction=object(),
        )
        self.assertTrue(is_interaction_response_message(message))

    def test_does_not_classify_ordinary_bot_message_as_command_output(self):
        message = SimpleNamespace(
            application_id=None,
            interaction_metadata=None,
            interaction=None,
        )
        self.assertFalse(is_interaction_response_message(message))


if __name__ == "__main__":
    unittest.main()
