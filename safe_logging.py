"""Redact common credentials before application output is persisted to logs."""

import re


_SECRET_ASSIGNMENT = re.compile(
    r"""(?i)\b(discord_token|openai_api_key|gemini_api_key|google_api_key|groq_api_key|api_key|access_token|refresh_token|client_secret|session_secret|dashboard_session|poll_anon_secret)\b(\s*[:=]\s*)(["']?)[^\s,;}\]"']+"""
)
_AUTHORIZATION = re.compile(
    r"(?i)\b(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")
_DISCORD_WEBHOOK = re.compile(
    r"https?://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9._-]+",
    re.IGNORECASE,
)
_CREDENTIAL_FORMATS = (
    re.compile(r"\b(?:mfa\.[A-Za-z0-9_-]{20,}|[A-Za-z0-9_-]{23,28}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{25,})\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{30,}\b"),
    re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b"),
)


def redact_sensitive_text(value: object) -> str:
    """Mask likely credentials in log text while preserving surrounding context."""
    text = str(value)
    text = _SECRET_ASSIGNMENT.sub(lambda match: match.group(1) + match.group(2) + "[REDACTED]", text)
    text = _AUTHORIZATION.sub(lambda match: match.group(1) + "[REDACTED]", text)
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _DISCORD_WEBHOOK.sub("[REDACTED_DISCORD_WEBHOOK_URL]", text)
    for pattern in _CREDENTIAL_FORMATS:
        text = pattern.sub("[REDACTED_CREDENTIAL]", text)
    return text
