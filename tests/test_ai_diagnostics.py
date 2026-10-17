import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cogs.media import SearchCog


class SyntheticServerError(Exception):
    """Mimic an SDK server exception without relying on a live provider."""

    status_code = 503
    code = None
    response = SimpleNamespace(headers=None)


class AiDiagnosticsTests(unittest.TestCase):
    def test_http_status_is_extracted_from_exception(self):
        self.assertEqual(SearchCog._api_status_code(SyntheticServerError()), 503)

    def test_server_error_gets_actionable_friendly_message(self):
        message = SearchCog._friendly_ai_error("Gemini", SyntheticServerError())
        self.assertIn("服務暫時異常", message)

    def test_gemini_finish_reason_and_token_usage_are_reported(self):
        response = SimpleNamespace(
            candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="MAX_TOKENS"))],
            usage_metadata=SimpleNamespace(
                prompt_token_count=31,
                candidates_token_count=7,
                thoughts_token_count=19,
            ),
        )
        self.assertEqual(
            SearchCog._gemini_response_stats(response),
            ("MAX_TOKENS", 31, 7, 19),
        )

    def test_prompt_block_is_detected_even_when_there_are_no_candidates(self):
        response = SimpleNamespace(
            candidates=[],
            prompt_feedback=SimpleNamespace(block_reason=SimpleNamespace(name="SAFETY")),
            usage_metadata=None,
        )
        stats = SearchCog._gemini_response_stats(response)
        self.assertEqual(stats[0], "PROMPT_BLOCK_SAFETY")

    def test_error_logging_handles_missing_response_headers_without_leaking_body(self):
        error = SyntheticServerError("sensitive-response-body")
        with patch("builtins.print") as printer:
            SearchCog._log_ai_error("Gemini test", error)
        log_line = " ".join(str(call.args[0]) for call in printer.call_args_list)
        self.assertIn("http_status=503", log_line)
        self.assertNotIn("sensitive-response-body", log_line)


if __name__ == "__main__":
    unittest.main()
