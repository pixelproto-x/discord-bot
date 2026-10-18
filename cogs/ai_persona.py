"""Shared, provider-independent personality rules and reply normalization for LogRelay."""

from __future__ import annotations

import re


# Keep this contract identical for FreeMind AI, Gemini, and Groq. Provider-specific
# prompts may add task instructions, but must not redefine LogRelay's personality.
CHAT_PERSONA_CONTRACT = """
【固定人格一致性契約｜所有 AI 供應商共用】
不論目前由 FreeMind AI、Gemini 或 Groq 生成，LogRelay 都是同一個角色；不得因模型切換、額度不足、逾時或備援而改變人格，也不要向使用者提及內部模型切換。
固定語氣：台灣繁體中文口語、像熟悉的 Discord 群友；自然、直接、會接梗和適度吐槽，但不刻意裝熱情、不每句都開玩笑、不用客服腔。
閒聊通常 1 到 4 句，簡單就簡短；認真問題就清楚、可靠地回答。不要為了維持搞笑而忽略真正嚴肅或需要幫助的內容。
禁止 emoji、顏文字、客服套話、固定開場與多餘結尾。每次只輸出一則直接回覆目前發言者的訊息，不寫對話劇本、不替其他人補台詞。
語氣校準範例（只參考回覆方式，不要照抄或每次套用）：
- 群友說「笑死」：可以自然接梗，例如「你笑屁，剛剛那個真的很扯。」
- 群友說「我這程式又壞了」：可以像熟人一樣直接幫忙，例如「先別整個重寫，貼錯誤訊息來，我們先抓是哪裡出問題。」
- 群友提出認真的技術問題：先給正確、可執行的答案，不要為了搞笑而迴避問題。
以上是穩定的角色規格，不是要求固定句型；依照當下上下文自然表達。
""".strip()


_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"
    "\U0001FC00-\U0001FFFF"
    "\u2300-\u23FF"
    "\u2600-\u27BF"
    "\u2B00-\u2BFF"
    "\uFE0E\uFE0F\u200D\u20E3"
    "]"
)
_SPEAKER_LABEL_RE = re.compile(
    r"(?:^|(?<=[。！？!?])\s*)[^\s：:\n]{1,12}\s*[：:]\s*",
    re.MULTILINE,
)
_LEADING_BOT_LABEL_RE = re.compile(
    r"^\s*(?:LogRelay|assistant|bot)\s*[：:]\s*",
    re.IGNORECASE,
)


def normalize_chat_reply(text: str, *, max_chars: int = 900) -> str:
    """Normalize chat replies consistently, regardless of which provider produced them."""
    answer = _EMOJI_RE.sub("", (text or "").strip())
    labels = list(_SPEAKER_LABEL_RE.finditer(answer))
    if len(labels) >= 2:
        answer = answer[: labels[1].start()].rstrip()
        answer = _SPEAKER_LABEL_RE.sub("", answer, count=1)
    answer = _LEADING_BOT_LABEL_RE.sub("", answer)
    answer = re.sub(r"\n{3,}", "\n\n", answer)
    return answer[:max_chars].strip()


def is_valid_chat_reply(text: str, *, max_chars: int = 900) -> bool:
    """Reject empty or malformed output after normalization; do not judge the opinion/content."""
    answer = (text or "").strip()
    if not answer or len(answer) > max_chars:
        return False
    if not re.search(r"[\w\u3400-\u9fff]", answer, flags=re.UNICODE):
        return False
    return True
