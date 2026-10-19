import unittest

from cogs.ai_persona import (
    CHAT_PERSONA_CONTRACT,
    is_repetitive_reply,
    is_valid_chat_reply,
    normalize_chat_reply,
)


class ChatPersonaTests(unittest.TestCase):
    def test_contract_explicitly_locks_all_providers(self):
        self.assertIn("FreeMind AI、Gemini 或 Groq", CHAT_PERSONA_CONTRACT)
        self.assertIn("不得因模型切換", CHAT_PERSONA_CONTRACT)
        self.assertIn("台灣繁體中文口語", CHAT_PERSONA_CONTRACT)

    def test_preserves_emoji_and_removes_bot_prefix(self):
        self.assertEqual(
            normalize_chat_reply("LogRelay：欸這真的很扯 😂"),
            "欸這真的很扯 😂",
        )

    def test_truncates_fabricated_multi_speaker_dialogue(self):
        self.assertEqual(
            normalize_chat_reply("這真的很扯。小明：我也覺得。小華：笑死。"),
            "這真的很扯",
        )

    def test_keeps_a_natural_single_reply(self):
        answer = "先別整個重寫，貼錯誤訊息來，我們先抓是哪裡出問題。"
        normalized = normalize_chat_reply(answer)
        self.assertEqual(normalized, "先別整個重寫貼錯誤訊息來我們先抓是哪裡出問題")
        self.assertTrue(is_valid_chat_reply(normalized))

    def test_detects_near_duplicate_replies(self):
        self.assertTrue(is_repetitive_reply(
            "這個真的很扯我們先看看是哪裡出問題",
            ["這個真的很扯我們先看看是哪裡出問題"],
        ))

    def test_allows_short_reactions_and_different_replies(self):
        self.assertFalse(is_repetitive_reply("笑死", ["笑死"]))
        self.assertFalse(is_repetitive_reply(
            "先貼一下錯誤訊息我幫你定位問題",
            ["你先把專案整份重寫就好了這樣會比較乾淨"],
        ))

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
