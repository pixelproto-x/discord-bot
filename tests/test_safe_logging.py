import unittest

from safe_logging import redact_sensitive_text, sanitize_log_text


class SensitiveLogRedactionTests(unittest.TestCase):
    def test_redacts_secret_environment_assignments(self):
        value = "OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuv DISCORD_TOKEN=topsecret"
        redacted = redact_sensitive_text(value)
        self.assertNotIn("sk-proj-abcdefghijklmnopqrstuv", redacted)
        self.assertNotIn("topsecret", redacted)
        self.assertIn("OPENAI_API_KEY=[REDACTED]", redacted)
        self.assertIn("DISCORD_TOKEN=[REDACTED]", redacted)

    def test_redacts_authorization_and_bearer_values(self):
        value = "Authorization: Bearer abc.def.ghi"
        redacted = redact_sensitive_text(value)
        self.assertNotIn("abc.def.ghi", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_redacts_discord_webhook_urls(self):
        value = "request failed https://discord.com/api/webhooks/123456789012345678/verySecretWebhookToken"
        redacted = redact_sensitive_text(value)
        self.assertNotIn("verySecretWebhookToken", redacted)
        self.assertIn("[REDACTED_DISCORD_WEBHOOK_URL]", redacted)

    def test_redacts_common_provider_key_formats(self):
        for secret in (
            "sk-abcdefghijklmnopqrstuv",
            "AIza12345678901234567890123456789012",
            "gsk_abcdefghijklmnopqrstuvwxyz123456",
        ):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, redact_sensitive_text(f"provider error: {secret}"))

    def test_escapes_newlines_to_prevent_forged_log_entries(self):
        value = "failed login\\n[OK] administrator logged in\\rnext"
        sanitized = sanitize_log_text(value)
        self.assertEqual(sanitized, r"failed login\\n[OK] administrator logged in\\rnext")
        self.assertEqual(len(sanitized.splitlines()), 1)

    def test_truncates_oversized_untrusted_log_values(self):
        sanitized = sanitize_log_text("x" * 5000)
        self.assertEqual(len(sanitized), 4013)
        self.assertTrue(sanitized.endswith("...[TRUNCATED]"))

    def test_redaction_also_sanitizes_newlines(self):
        value = "Authorization: Bearer abc.def.ghi\\nOPENAI_API_KEY=topsecret"
        redacted = redact_sensitive_text(value)
        self.assertNotIn("abc.def.ghi", redacted)
        self.assertNotIn("topsecret", redacted)
        self.assertEqual(len(redacted.splitlines()), 1)

    def test_keeps_ordinary_diagnostic_text(self):
        value = "Slash command error: database is temporarily unavailable"
        self.assertEqual(redact_sensitive_text(value), value)


if __name__ == "__main__":
    unittest.main()
