import unittest

from cogs.ai_persona import (
    CHAT_PERSONA_CONTRACT,
    is_valid_chat_reply,
    normalize_chat_reply,
)


class ChatPersonaTests(unittest.TestCase):
    def test_contract_explicitly_locks_all_providers(self):
        self.assertIn("FreeMind AI、Gemini 或 Groq", CHAT_PERSONA_CONTRACT)
        self.assertIn("不得因模型切換", CHAT_PERSONA_CONTRACT)
        self.assertIn("台灣繁體中文口語", CHAT_PERSONA_CONTRACT)

    def test_removes_emoji_and_bot_prefix(self):
        self.assertEqual(
            normalize_chat_reply("LogRelay：欸這真的很扯 😂"),
            "欸這真的很扯",
        )

    def test_truncates_fabricated_multi_speaker_dialogue(self):
        self.assertEqual(
            normalize_chat_reply("這真的很扯。小明：我也覺得。小華：笑死。"),
            "這真的很扯。",
        )

    def test_keeps_a_natural_single_reply(self):
        answer = "先別整個重寫，貼錯誤訊息來，我們先抓是哪裡出問題。"
        self.assertEqual(normalize_chat_reply(answer), answer)
        self.assertTrue(is_valid_chat_reply(answer))

    def test_rejects_empty_or_punctuation_only_reply(self):
        self.assertFalse(is_valid_chat_reply(""))
        self.assertFalse(is_valid_chat_reply("..."))
        self.assertFalse(is_valid_chat_reply("   "))

    def test_normalizes_blank_and_length(self):
        self.assertEqual(normalize_chat_reply("  "), "")
        self.assertEqual(len(normalize_chat_reply("中" * 1000)), 900)
        self.assertFalse(is_valid_chat_reply("中" * 901))


if __name__ == "__main__":
    unittest.main()
