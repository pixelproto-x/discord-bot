import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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


    def test_gemini_chat_uses_low_thinking_and_a_larger_output_budget(self):
        response = SimpleNamespace(
            text="我剛剛在忙一點事情，現在才看到你的訊息。",
            candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="STOP"))],
            usage_metadata=SimpleNamespace(
                prompt_token_count=20,
                candidates_token_count=18,
                thoughts_token_count=5,
            ),
        )
        generate_content = AsyncMock(return_value=response)
        cog = object.__new__(SearchCog)
        cog.gemini_model = "gemini-3.8-flash"
        cog.gemini_client = SimpleNamespace(
            aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
        )
        cog.groq_client = None
        cog.groq_model = "openai/gpt-oss-120b"

        with patch("builtins.print"):
            answer = asyncio.run(cog._generate_gemini_chat("我剛剛在忙，你呢？"))

        self.assertTrue(answer)
        config = generate_content.await_args.kwargs["config"]
        self.assertEqual(config["thinking_config"]["thinking_level"], "low")
        self.assertEqual(config["max_output_tokens"], 1536)
        self.assertNotIn("temperature", config)


    def test_gemini_short_chat_reply_is_retried_before_accepting(self):
        def response(text):
            return SimpleNamespace(
                text=text,
                candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="STOP"))],
                usage_metadata=SimpleNamespace(
                    prompt_token_count=18,
                    candidates_token_count=10,
                    thoughts_token_count=4,
                ),
            )

        generate_content = AsyncMock(
            side_effect=[
                response("還好啊"),
                response("今天事情有點多，現在總算忙完了，終於能休息一下。"),
            ]
        )
        cog = object.__new__(SearchCog)
        cog.gemini_model = "gemini-3.8-flash"
        cog.gemini_client = SimpleNamespace(
            aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
        )
        cog.groq_client = None
        cog.groq_model = "openai/gpt-oss-120b"

        with patch("builtins.print"):
            answer = asyncio.run(cog._generate_gemini_chat("我剛剛在忙，你呢？"))

        self.assertIn("今天事情有點多", answer)
        self.assertEqual(generate_content.await_count, 2)
        self.assertIn("上一個回覆太短", generate_content.await_args_list[1].kwargs["contents"])


    def test_ai_provider_diagnostic_commands_respect_shared_cooldown(self):
        async def exercise(command, client_attr, client_value, model_attr, model_value):
            cog = object.__new__(SearchCog)
            setattr(cog, client_attr, client_value)
            setattr(cog, model_attr, model_value)
            cog._check_ai_limit = AsyncMock(return_value=False)
            interaction = SimpleNamespace(
                response=SimpleNamespace(send_message=AsyncMock()),
                followup=SimpleNamespace(send=AsyncMock()),
            )
            await command.callback(cog, interaction)
            cog._check_ai_limit.assert_awaited_once_with(interaction)
            interaction.response.defer.assert_not_awaited() if hasattr(interaction.response, "defer") else None

        asyncio.run(exercise(
            SearchCog.ai_groq_test,
            "groq_client", object(), "groq_model", "openai/gpt-oss-120b"
        ))
        asyncio.run(exercise(
            SearchCog.ai_test,
            "gemini_client", object(), "gemini_model", "gemini-3.8-flash"
        ))


    def test_generic_gemini_generation_falls_back_instead_of_repeating_max_tokens(self):
        response = SimpleNamespace(
            text="",
            candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="MAX_TOKENS"))],
            usage_metadata=SimpleNamespace(
                prompt_token_count=20,
                candidates_token_count=0,
                thoughts_token_count=18,
            ),
        )
        generate_content = AsyncMock(return_value=response)
        cog = object.__new__(SearchCog)
        cog.gemini_model = "gemini-3.8-flash"
        cog.gemini_client = SimpleNamespace(
            aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
        )
        cog._generate_groq_text = AsyncMock(return_value="備援完成")

        with patch("builtins.print"):
            answer = asyncio.run(cog._generate_ai("整理這段內容", "摘要助手"))

        self.assertEqual(answer, "備援完成")
        self.assertEqual(generate_content.await_count, 1)
        cog._generate_groq_text.assert_awaited_once()

    def test_generic_gemini_generation_does_not_retry_fatal_400_errors(self):
        class SyntheticBadRequest(Exception):
            status_code = 400

        generate_content = AsyncMock(side_effect=SyntheticBadRequest("bad request"))
        cog = object.__new__(SearchCog)
        cog.gemini_model = "gemini-3.8-flash"
        cog.gemini_client = SimpleNamespace(
            aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
        )
        cog._generate_groq_text = AsyncMock(return_value="備援完成")

        with patch("builtins.print"):
            answer = asyncio.run(cog._generate_ai("整理這段內容", "摘要助手"))

        self.assertEqual(answer, "備援完成")
        self.assertEqual(generate_content.await_count, 1)
        cog._generate_groq_text.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
