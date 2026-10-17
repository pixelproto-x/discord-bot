import asyncio
import io
import os
import re
import time
import urllib.parse
from pathlib import Path

import aiohttp
import discord
import yt_dlp
from PIL import Image, ImageStat
from discord import app_commands
from discord.ext import commands
from dotenv import dotenv_values, load_dotenv

# Load the project-root .env here as well, so this cog works even when imported
# by an entry point that did not load dotenv first.
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(dotenv_path=ENV_FILE, override=True)

from google import genai
from openai import AsyncOpenAI


MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000


def _strip_emojis(text: str) -> str:
    """Remove emoji and presentation/sequence characters from Gemini output."""
    text = re.sub(
        "["
        "\\U0001F000-\\U0001FAFF"
        "\\U0001FC00-\\U0001FFFF"
        "\\u2300-\\u23FF"
        "\\u2600-\\u27BF"
        "\\u2B00-\\u2BFF"
        "\\uFE0E\\uFE0F\\u200D\\u20E3"
        "]",
        "",
        text,
    )
    return text


class SearchCog(commands.Cog):
    search = app_commands.Group(name="search", description="在 YouTube、Wikipedia、GitHub 找東西")
    image = app_commands.Group(name="image", description="簡單的圖片處理工具")
    ai = app_commands.Group(name="ai", description="聊天、問答、翻譯與摘要工具")

    def __init__(self, bot):
        self.bot = bot
        self.ai_cooldowns = {}
        self.ocr_cooldowns = {}
        self.ai_cooldown_seconds = 8
        self.ocr_cooldown_seconds = 8
        self._last_cooldown_prune = 0.0
        self.chat_cooldowns = {}
        self.chat_history = {}
        self.chat_cooldown_seconds = 4
        self.chat_history_limit = 8
        self.gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip() or "gemini-3.8-flash"
        self.gemini_client = genai.Client(api_key=self.gemini_key) if self.gemini_key else None
        self.groq_key = os.getenv("GROQ_API_KEY", "").strip()
        self.groq_model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip() or "openai/gpt-oss-120b"
        self.groq_client = (
            AsyncOpenAI(
                api_key=self.groq_key,
                base_url="https://api.groq.com/openai/v1",
            )
            if self.groq_key
            else None
        )
        if self.groq_client:
            print(f"[Groq] GROQ_API_KEY loaded; model={self.groq_model}")
        else:
            print("[Groq] GROQ_API_KEY is missing; /ai ask needs a Groq API key in the project-root .env.")

        if self.gemini_client:
            print(f"[Gemini] GEMINI_API_KEY loaded; model={self.gemini_model}")
        else:
            file_values = dotenv_values(ENV_FILE) if ENV_FILE.is_file() else {}
            key_in_file = bool(str(file_values.get("GEMINI_API_KEY") or "").strip())
            print(
                "[Gemini] GEMINI_API_KEY is missing. "
                f".env exists={ENV_FILE.is_file()}, key parsed from .env={key_in_file}. "
                "Check that GEMINI_API_KEY=YOUR_KEY is on its own line in .env."
            )

    async def _check_cooldown(self, interaction, bucket, seconds):
        key = (interaction.guild.id if interaction.guild else 0, interaction.user.id)
        now = time.monotonic()
        if now - self._last_cooldown_prune >= 60:
            for cache in (self.ai_cooldowns, self.ocr_cooldowns):
                for stale_key, timestamp in list(cache.items()):
                    if now - timestamp >= max(self.ai_cooldown_seconds, self.ocr_cooldown_seconds):
                        cache.pop(stale_key, None)
            self._last_cooldown_prune = now
        last = bucket.get(key, 0)
        remaining = seconds - (now - last)
        if remaining > 0:
            await interaction.response.send_message(
                f"先等等 {remaining:.1f} 秒，再用一次。",
                ephemeral=True,
            )
            return False
        bucket[key] = now
        return True

    async def _check_ai_limit(self, interaction):
        return await self._check_cooldown(
            interaction,
            self.ai_cooldowns,
            self.ai_cooldown_seconds,
        )

    @staticmethod
    def _api_status_code(exc: Exception):
        """Extract HTTP status codes from common SDK exception shapes."""
        candidates = (
            getattr(exc, "status_code", None),
            getattr(getattr(exc, "response", None), "status_code", None),
            getattr(exc, "code", None),
        )
        for candidate in candidates:
            candidate = getattr(candidate, "value", candidate)
            if isinstance(candidate, int) and 100 <= candidate <= 599:
                return candidate
            if candidate is not None:
                match = re.search(r"\b([1-5][0-9]{2})\b", str(candidate))
                if match:
                    return int(match.group(1))
        return None

    @classmethod
    def _friendly_ai_error(cls, provider: str, exc: Exception) -> str:
        """Turn provider exceptions into useful user-facing messages without exposing raw errors."""
        status = cls._api_status_code(exc)
        error_type = type(exc).__name__.lower()
        details = str(exc).lower()
        provider_name = {"openai": "OpenAI", "groq": "Groq"}.get(provider.lower(), "Gemini")

        if isinstance(exc, asyncio.TimeoutError) or "timeout" in error_type:
            return f"⏱️ {provider_name} 回覆逾時。可以縮短問題後再試一次。"

        if any(marker in details for marker in (
            "credit_balance_exhausted",
            "insufficient_quota",
            "no credits remaining",
            "billing hard limit",
            "exceeded your current quota",
        )):
            return (
                f"💳 {provider_name} API 額度或帳戶餘額不足，暫時無法回答。"
                "需要由機器人管理者確認 API 用量／免費額度，或改用其他 AI 服務。"
            )

        if status == 401 or "authenticationerror" in error_type:
            return (
                f"🔐 {provider_name} 驗證失敗，API 憑證可能無效或已撤銷。"
                "需要由機器人管理者更新設定。"
            )

        if status == 403 or "permissiondenied" in error_type:
            return (
                f"⛔ {provider_name} 目前沒有權限使用這項服務或模型。"
                "請由機器人管理者確認帳戶、專案權限與模型設定。"
            )

        if status == 404 or "model_not_found" in details or "model not found" in details:
            return (
                f"🧩 {provider_name} 找不到目前設定的模型，或此帳戶無法使用該模型。"
                "需要由機器人管理者確認模型名稱與可用權限。"
            )

        if status == 429 or "ratelimit" in error_type or "resource_exhausted" in details:
            if provider_name == "Gemini" and (
                "quota" in details or "resource_exhausted" in details
            ):
                return (
                    "📊 Gemini 已達目前模型或帳戶的配額上限。"
                    "請等配額重置後再試，或暫時改用其他 AI 服務。"
                )
            return (
                f"🐢 {provider_name} 目前達到請求頻率或用量上限。"
                "請稍後再試；如果帳戶額度已用完，需要先恢復可用額度。"
            )

        if "connection" in error_type or "connecterror" in error_type:
            return f"🌐 目前連不上 {provider_name} 服務，請確認機器人主機的網路後再試。"

        if (
            (status is not None and status >= 500)
            or "servererror" in error_type
            or "internalservererror" in error_type
            or "serviceunavailable" in error_type
        ):
            return f"🛠️ {provider_name} 服務暫時異常，請過一會兒再試。"

        if status == 400:
            return (
                f"📝 {provider_name} 無法接受這個請求。請縮短問題或換個方式描述；"
                "若每個問題都失敗，可能是模型設定不相容。"
            )

        status_note = f"（HTTP {status}）" if status is not None else ""
        return (
            f"⚠️ {provider_name} 這次沒有完成回覆{status_note}。"
            "請稍後再試，或改用其他 AI 指令。"
        )

    @staticmethod
    def _log_ai_error(label: str, exc: Exception):
        """Log useful diagnostic metadata without dumping prompts, response bodies, or secrets."""
        status = SearchCog._api_status_code(exc)
        fields = [f"error={type(exc).__name__}"]
        if status is not None:
            fields.append(f"http_status={status}")

        error_code = getattr(exc, "code", None)
        if error_code is not None:
            safe_code = str(getattr(error_code, "name", None) or error_code)
            if re.fullmatch(r"[A-Za-z0-9_.-]{1,48}", safe_code):
                fields.append(f"error_code={safe_code}")

        response_headers = getattr(getattr(exc, "response", None), "headers", None) or {}
        request_id = (
            getattr(exc, "request_id", None)
            or getattr(exc, "requestId", None)
            or response_headers.get("x-request-id")
        )
        if request_id is not None and re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", str(request_id)):
            fields.append(f"request_id={request_id}")

        print(f"[{label}] " + " ".join(fields))

    @staticmethod
    def _gemini_response_stats(response):
        """Return finish reason and token usage; never log generated text or the prompt."""
        candidates = getattr(response, "candidates", None) or []
        finish_reason = "NO_CANDIDATE"
        if candidates:
            raw_reason = getattr(candidates[0], "finish_reason", None)
            finish_reason = str(getattr(raw_reason, "name", None) or raw_reason or "UNKNOWN").upper()

        if not candidates:
            feedback = getattr(response, "prompt_feedback", None)
            block_reason = getattr(feedback, "block_reason", None)
            if block_reason:
                finish_reason = "PROMPT_BLOCK_" + str(
                    getattr(block_reason, "name", None) or block_reason
                ).upper()

        usage = getattr(response, "usage_metadata", None)
        input_tokens = getattr(usage, "prompt_token_count", None) or 0
        output_tokens = getattr(usage, "candidates_token_count", None) or 0
        thinking_tokens = getattr(usage, "thoughts_token_count", None) or 0
        return finish_reason, input_tokens, output_tokens, thinking_tokens

    async def _send_ai_output(self, interaction, answer: str, filename: str, empty_message: str):
        answer = (answer or "").strip()
        if not answer:
            await interaction.followup.send(empty_message)
            return

        if len(answer) <= 1900:
            await interaction.followup.send(answer)
            return

        chunks = [answer[i:i + 1900] for i in range(0, len(answer), 1900)]
        for chunk in chunks[:10]:
            await interaction.followup.send(chunk)

        if len(chunks) > 10:
            data = io.BytesIO(answer.encode("utf-8"))
            await interaction.followup.send(
                "內容較長，完整結果也附在文字檔。",
                file=discord.File(data, filename=filename),
            )

    async def _generate_groq_text(
        self,
        prompt: str,
        system_instruction: str,
        *,
        label: str = "Groq fallback",
        max_tokens: int = 768,
        temperature: float = 0.7,
    ) -> str:
        """Generate a fallback answer through Groq using the same task instructions."""
        if not self.groq_client:
            print(f"[{label}] skipped; GROQ_API_KEY is not configured")
            return ""

        request = (
            f"{system_instruction}\n\n"
            "回答一律禁止使用 emoji、表情符號、顏文字或用符號拼成的表情；只輸出純文字。"
        )
        try:
            response = await asyncio.wait_for(
                self.groq_client.chat.completions.create(
                    model=self.groq_model,
                    messages=[
                        {"role": "system", "content": request},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=temperature,
                    max_completion_tokens=max_tokens,
                ),
                timeout=25,
            )
            choices = getattr(response, "choices", None) or []
            choice = choices[0] if choices else None
            answer = ((getattr(getattr(choice, "message", None), "content", None) or "") if choice else "").strip()
            finish_reason = getattr(choice, "finish_reason", None) or "UNKNOWN"
            usage = getattr(response, "usage", None)
            input_tokens = getattr(usage, "prompt_tokens", None) or 0
            output_tokens = getattr(usage, "completion_tokens", None) or 0
            print(
                f"[{label}] model={self.groq_model} finish_reason={finish_reason} "
                f"chars={len(answer)} input_tokens={input_tokens} output_tokens={output_tokens}"
            )
            return _strip_emojis(answer)
        except Exception as exc:
            self._log_ai_error(label, exc)
            return ""

    async def _generate_gemini_chat(self, prompt: str) -> str:
        """Generate a casual Gemini reply, retry transient failures, then fail over to Groq."""
        system_instruction = (
            "你是 Discord 群組裡很熟的朋友，嘴快、會接梗、敢吐槽，絕對不是客服、老師或禮貌型助理。"
            "全程用自然的繁體中文口語，像台灣朋友在 Discord 打字；可以用幹話、反諷、吐槽、裝傻、直接反駁，別刻意裝熱情或裝客氣。"
            "禁止客服腔和制式安慰，例如「很高興能幫助你」「我理解你的感受」「這是一個很好的問題」「如果你還有其他問題」；除非真的符合情境，否則不要說。"
            "朋友講幹話就先接梗，不要突然認真分析；朋友抱怨時可以自然吐槽或站在朋友這邊，不要每次都變成心理諮商。"
            "可以互嗆、嘴砲、回得有點欠揍，但要像熟人鬧著玩，不要針對外貌、身分、創傷或其他脆弱點羞辱人。"
            "不要每次都附和；覺得對方講得不合理可以直接講，別為了討好而假裝同意。"
            "通常回 1 到 4 句，長短看情境；梗就短接，值得聊的才多講幾句。不要硬湊字、不要寫成作文。"
            "少用反問句，不要每句最後都問問題；能直接接話就直接接。避免重複開場、總結和多餘的客套結尾。"
            "不要說「作為 AI」，不要提自己在模仿朋友，也不要解釋你正在使用什麼聊天風格。"
            "遇到真正嚴肅、危險或求助的問題時，先認真幫忙，不要硬開玩笑。"
            "回答一律禁止使用 emoji、表情符號、顏文字或用符號拼成的表情；只用純文字。"
        )

        if not self.gemini_client:
            return await self._generate_groq_text(
                prompt, system_instruction, label="Groq chat (Gemini unavailable)",
                max_tokens=1024, temperature=1.0,
            )

        base_contents = (
            f"{system_instruction}\n\n對方說：{prompt}\n"
            "直接接話，不要解釋你在做什麼。"
        )
        failure_reason = "gemini_failed"

        for attempt in range(2):
            contents = base_contents
            if attempt == 1 and failure_reason == "short_response":
                contents += (
                    "\n請重新回答：上一個回覆太短。請用至少一個完整自然句子接話，"
                    "不要只回兩三個詞；仍要像朋友聊天，不要寫成正式文章。"
                )

            try:
                response = await asyncio.wait_for(
                    self.gemini_client.aio.models.generate_content(
                        model=self.gemini_model,
                        contents=contents,
                        config={
                            "max_output_tokens": 1536,
                            "thinking_config": {"thinking_level": "low"},
                        },
                    ),
                    timeout=22,
                )
            except Exception as exc:
                failure_reason = "gemini_error"
                self._log_ai_error(f"Gemini chat attempt {attempt + 1}/2", exc)
                status = self._api_status_code(exc)
                error_type = type(exc).__name__.lower()
                # Retry one transient failure, but do not waste time retrying bad auth/model requests.
                fatal = status in (400, 401, 403, 404) or any(
                    marker in error_type for marker in ("authentication", "permissiondenied", "notfound")
                )
                if attempt == 0 and not fatal:
                    await asyncio.sleep(0.8)
                    continue
                break

            try:
                answer = (getattr(response, "text", "") or "").strip()
            except Exception:
                answer = ""

            finish_reason, input_tokens, output_tokens, thinking_tokens = self._gemini_response_stats(response)
            print(
                f"[Gemini chat] model={self.gemini_model} finish_reason={finish_reason} "
                f"chars={len(answer)} input_tokens={input_tokens} output_tokens={output_tokens} thinking_tokens={thinking_tokens}"
            )

            # Don't try to route around a safety block; report it as an empty result instead.
            if any(marker in finish_reason for marker in ("SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "PROMPT_BLOCK")):
                return ""

            if "MAX_TOKENS" in finish_reason:
                failure_reason = "max_tokens"
                print("[Gemini chat] output stopped at the token limit; trying Groq fallback")
                break

            if not answer:
                failure_reason = "empty_response"
                if attempt == 0:
                    continue
                break

            if len(answer) < 8 and len(prompt) >= 8:
                failure_reason = "short_response"
                if attempt == 0:
                    print("[Gemini chat] reply unusually short; requesting one fuller reply")
                    continue
                print("[Gemini chat] retry was still unusually short; trying Groq fallback")
                break

            return _strip_emojis(answer)

        return await self._generate_groq_text(
            prompt,
            system_instruction,
            label=f"Groq chat fallback ({failure_reason})",
            max_tokens=1024,
            temperature=1.0,
        )

    async def _generate_ai(self, prompt: str, system_instruction: str) -> str:
        request = (
            f"{system_instruction}\n\n"
            "回答一律禁止使用 emoji、表情符號、顏文字或用符號拼成的表情；只輸出純文字。\n\n"
            f"{prompt}"
        )

        if not self.gemini_client:
            return await self._generate_groq_text(
                prompt, system_instruction, label="Groq fallback (Gemini unavailable)"
            )

        last_failure = "gemini_failed"
        for attempt in range(3):
            try:
                response = await asyncio.wait_for(
                    self.gemini_client.aio.models.generate_content(
                        model=self.gemini_model,
                        contents=request,
                        config={"max_output_tokens": 2048},
                    ),
                    timeout=35,
                )
                try:
                    answer = (getattr(response, "text", "") or "").strip()
                except Exception:
                    answer = ""
                finish_reason, input_tokens, output_tokens, thinking_tokens = self._gemini_response_stats(response)
                print(
                    f"[Gemini API] model={self.gemini_model} finish_reason={finish_reason} "
                    f"chars={len(answer)} input_tokens={input_tokens} output_tokens={output_tokens} thinking_tokens={thinking_tokens}"
                )
                if any(marker in finish_reason for marker in ("SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "PROMPT_BLOCK")):
                    return ""
                if "MAX_TOKENS" in finish_reason:
                    last_failure = "max_tokens"
                    print("[Gemini API] output reached the token limit; switching to Groq fallback")
                    break
                if answer:
                    return _strip_emojis(answer)
                last_failure = "empty_response"
            except Exception as exc:
                last_failure = "gemini_error"
                self._log_ai_error(f"Gemini API attempt {attempt + 1}/3", exc)
                status = self._api_status_code(exc)
                error_type = type(exc).__name__.lower()
                fatal = status in (400, 401, 403, 404) or any(
                    marker in error_type
                    for marker in ("authentication", "permissiondenied", "notfound", "badrequest")
                )
                if fatal:
                    print("[Gemini API] non-retryable request/authentication error; switching to fallback")
                    break
                if attempt < 2:
                    await asyncio.sleep(1.0 * (attempt + 1))

        return await self._generate_groq_text(
            prompt,
            system_instruction,
            label=f"Groq fallback ({last_failure})",
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Reply to direct mentions/replies like a casual server friend, without interrupting every chat."""
        if message.author.bot or message.guild is None or self.bot.user is None:
            return

        bot_id = self.bot.user.id
        is_mentioned = any(user.id == bot_id for user in message.mentions)
        is_reply_to_bot = False
        reference = message.reference
        if reference and reference.message_id:
            referenced = reference.resolved
            if referenced is None:
                try:
                    referenced = await message.channel.fetch_message(reference.message_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    referenced = None
            is_reply_to_bot = bool(
                referenced and getattr(getattr(referenced, "author", None), "id", None) == bot_id
            )

        if not (is_mentioned or is_reply_to_bot):
            return

        now = time.monotonic()
        cooldown_key = (message.guild.id, message.channel.id, message.author.id)
        last = self.chat_cooldowns.get(cooldown_key, 0.0)
        if now - last < self.chat_cooldown_seconds:
            return
        self.chat_cooldowns[cooldown_key] = now

        # Keep cooldown/history caches bounded over long-running bot sessions.
        if len(self.chat_cooldowns) > 2000:
            cutoff = now - self.chat_cooldown_seconds
            self.chat_cooldowns = {
                key: stamp for key, stamp in self.chat_cooldowns.items()
                if stamp >= cutoff
            }
        if len(self.chat_history) > 500:
            self.chat_history = dict(list(self.chat_history.items())[-300:])

        user_prompt = re.sub(rf"<@!?{bot_id}>", "", message.content).strip()
        user_prompt = user_prompt or "有人叫了你一聲，接一句自然又有梗的話。"
        if len(user_prompt) > 3000:
            user_prompt = user_prompt[:3000]

        prompt = user_prompt
        history_key = (message.guild.id, message.channel.id)
        history = self.chat_history.get(history_key, [])
        if history:
            context = "\n".join(
                f"{'朋友' if role == 'user' else '你'}：{text}"
                for role, text in history[-self.chat_history_limit:]
            )
            prompt = f"最近的聊天上下文（用來接話，不要重複摘要）：\n{context}\n\n這次朋友說：{user_prompt}"

        try:
            async with message.channel.typing():
                answer = await self._generate_gemini_chat(prompt)
            if not answer:
                await message.reply(
                    "我剛剛卡了一下，等我一下再嘴你。",
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return

            answer = answer.strip()

            # Record only successful, non-empty turns so the next reply can follow the thread.
            history = self.chat_history.setdefault(history_key, [])
            history.extend([
                ("user", user_prompt[-3000:]),
                ("assistant", answer[-3000:]),
            ])
            self.chat_history[history_key] = history[-self.chat_history_limit:]

            chunks = [answer[i:i + 1900] for i in range(0, len(answer), 1900)]
            for index, chunk in enumerate(chunks):
                if index == 0:
                    await message.reply(
                        chunk,
                        mention_author=False,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                else:
                    await message.channel.send(
                        chunk,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
        except (discord.Forbidden, discord.HTTPException) as exc:
            self._log_ai_error("Discord casual chat send", exc)
        except Exception as exc:
            self._log_ai_error("Discord casual chat", exc)

    @search.command(name="youtube", description="搜尋 YouTube 並列出幾個結果")
    @app_commands.describe(query="關鍵字")
    async def youtube(self, interaction, query: str):
        query = query.strip()
        if not query:
            await interaction.response.send_message("請輸入搜尋關鍵字。", ephemeral=True)
            return

        await interaction.response.defer()
        try:
            with yt_dlp.YoutubeDL({
                "quiet": True,
                "skip_download": True,
                "extract_flat": True,
                "default_search": "ytsearch5",
            }) as ydl:
                data = await asyncio.wait_for(
                    self.bot.loop.run_in_executor(
                        None,
                        lambda: ydl.extract_info(f"ytsearch5:{query}", download=False),
                    ),
                    timeout=20,
                )

            entries = (data or {}).get("entries", [])[:5]
            if not entries:
                await interaction.followup.send("沒找到東西。")
                return

            lines = []
            for i, entry in enumerate(entries, 1):
                video_id = entry.get("id")
                url = entry.get("webpage_url") or (
                    f"https://www.youtube.com/watch?v={video_id}" if video_id else ""
                )
                title = entry.get("title", "未知")
                lines.append(f"{i}. **{title}**\n{url}")

            await interaction.followup.send("🔎 YouTube 搜尋結果：\n" + "\n".join(lines))
        except Exception as exc:
            print(f"YouTube search error: {exc}")
            await interaction.followup.send("YouTube 搜尋這次沒回來。")

    @search.command(name="wikipedia", description="搜尋 Wikipedia")
    @app_commands.describe(query="想查的東西")
    async def wikipedia(self, interaction, query: str):
        query = query.strip()
        if not query:
            await interaction.response.send_message("請輸入搜尋內容。", ephemeral=True)
            return

        await interaction.response.defer()
        url = "https://zh.wikipedia.org/w/api.php?" + urllib.parse.urlencode({
            "action": "query",
            "format": "json",
            "list": "search",
            "srsearch": query,
            "srlimit": 5,
        })

        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    data = await resp.json()

            items = data.get("query", {}).get("search", [])
            if not items:
                await interaction.followup.send("Wikipedia 沒找到。")
                return

            lines = [
                f"{i + 1}. **{item['title']}**\nhttps://zh.wikipedia.org/wiki/{urllib.parse.quote(item['title'])}"
                for i, item in enumerate(items)
            ]
            await interaction.followup.send("📖 找到這些：\n" + "\n".join(lines))
        except Exception as exc:
            print(f"Wikipedia search error: {exc}")
            await interaction.followup.send("Wikipedia 現在沒回應。")

    @search.command(name="github", description="搜尋 GitHub 公開專案")
    @app_commands.describe(query="關鍵字")
    async def github(self, interaction, query: str):
        query = query.strip()
        if not query:
            await interaction.response.send_message("請輸入搜尋關鍵字。", ephemeral=True)
            return

        await interaction.response.defer()
        url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode({
            "q": query,
            "per_page": 5,
        })

        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    url,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "discord-bot",
                    },
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json()

            items = data.get("items", [])
            if not items:
                await interaction.followup.send("GitHub 沒找到。")
                return

            lines = [
                f"{i + 1}. **{item['full_name']}** ⭐ {item.get('stargazers_count', 0)}\n{item['html_url']}"
                for i, item in enumerate(items)
            ]
            await interaction.followup.send("💻 GitHub 搜尋結果：\n" + "\n".join(lines))
        except Exception as exc:
            print(f"GitHub search error: {exc}")
            await interaction.followup.send("GitHub 搜尋這次失敗了。")

    @image.command(name="resize", description="把圖片縮到指定寬度")
    @app_commands.describe(image="要處理的圖片", width="寬度 64 到 2048")
    async def resize(
        self,
        interaction,
        image: discord.Attachment,
        width: app_commands.Range[int, 64, 2048],
    ):
        if not image.content_type or not image.content_type.startswith("image/"):
            await interaction.response.send_message("請丟圖片給我。", ephemeral=True)
            return
        if image.size > MAX_IMAGE_BYTES:
            await interaction.response.send_message("圖片太大，請使用 8 MB 以下的圖片。", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        try:
            data = await image.read()
            with Image.open(io.BytesIO(data)) as source:
                if source.width * source.height > MAX_IMAGE_PIXELS:
                    await interaction.followup.send("圖片解析度太大，請先縮小或裁切再試。")
                    return
                pic = source.convert("RGBA")
            ratio = width / pic.width
            height = max(1, int(pic.height * ratio))
            if height > 20000 or width * height > MAX_IMAGE_PIXELS:
                await interaction.followup.send("縮放後的圖片比例過長，請先裁切再試。")
                return
            pic = pic.resize((width, height), Image.Resampling.LANCZOS)

            out = io.BytesIO()
            pic.save(out, format="PNG")
            out.seek(0)

            await interaction.followup.send(
                f"好了，縮成 {width} × {height}。",
                file=discord.File(out, "resized.png"),
            )
        except Exception as exc:
            print(f"Image resize error: {exc}")
            await interaction.followup.send("這張圖處理失敗了。")

    @image.command(name="color", description="抓一張圖片的平均顏色")
    @app_commands.describe(image="要分析的圖片")
    async def color(self, interaction, image: discord.Attachment):
        if not image.content_type or not image.content_type.startswith("image/"):
            await interaction.response.send_message("請丟圖片給我。", ephemeral=True)
            return
        if image.size > MAX_IMAGE_BYTES:
            await interaction.response.send_message("圖片太大，請使用 8 MB 以下的圖片。", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        try:
            data = await image.read()
            with Image.open(io.BytesIO(data)) as source:
                if source.width * source.height > MAX_IMAGE_PIXELS:
                    await interaction.followup.send("圖片解析度太大，請先縮小或裁切再試。")
                    return
                pic = source.convert("RGB").resize((1, 1))
            r, g, b = ImageStat.Stat(pic).mean
            hex_color = f"#{int(r):02X}{int(g):02X}{int(b):02X}"
            await interaction.followup.send(
                f"這張圖抓到的平均色大概是 **{hex_color}**。"
            )
        except Exception as exc:
            print(f"Image color error: {exc}")
            await interaction.followup.send("這張圖分析失敗了。")

    @image.command(name="ocr", description="從圖片抓文字，需要設定 OCR_SPACE_API_KEY")
    @app_commands.describe(image="有文字的圖片")
    async def ocr(self, interaction, image: discord.Attachment):
        if not image.content_type or not image.content_type.startswith("image/"):
            await interaction.response.send_message("請丟圖片給我。", ephemeral=True)
            return
        if image.size > MAX_IMAGE_BYTES:
            await interaction.response.send_message("圖片太大，請使用 8 MB 以下的圖片。", ephemeral=True)
            return
        key = os.getenv("OCR_SPACE_API_KEY", "").strip()
        if not key:
            await interaction.response.send_message(
                "OCR 功能還沒設定 API Key，其他圖片工具可以直接用。",
                ephemeral=True,
            )
            return

        if not await self._check_cooldown(
            interaction,
            self.ocr_cooldowns,
            self.ocr_cooldown_seconds,
        ):
            return

        await interaction.response.defer()

        try:
            data = await image.read()
            form = aiohttp.FormData()
            form.add_field(
                "file",
                data,
                filename="image.png",
                content_type=image.content_type or "image/png",
            )
            form.add_field("language", "cht")
            form.add_field("isOverlayRequired", "false")
            form.add_field("OCREngine", "2")

            timeout = aiohttp.ClientTimeout(total=25)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    "https://api.ocr.space/parse/image",
                    data=form,
                    headers={"apikey": key},
                ) as resp:
                    resp.raise_for_status()
                    result = await resp.json(content_type=None)

            texts = [
                item.get("ParsedText", "")
                for item in result.get("ParsedResults", [])
            ]
            extracted = "\n".join(
                text.strip() for text in texts if text.strip()
            )[:1800]

            await interaction.followup.send(
                ("抓到的文字：\n" + extracted)
                if extracted
                else "這張圖沒抓到文字。"
            )
        except Exception as exc:
            print(f"OCR error: {exc}")
            await interaction.followup.send("OCR 這次失敗了，再試一次。")

    @ai.command(name="chat", description="和 Gemini 幹話聊天、吐槽與接梗")
    @app_commands.describe(prompt="想聊的內容")
    async def ask(self, interaction, prompt: str):
        prompt = prompt.strip()

        if not self.gemini_client and not self.groq_client:
            await interaction.response.send_message(
                "Gemini 和 Groq 都還沒設定 API Key。請在專案根目錄的 .env 設定 GEMINI_API_KEY 或 GROQ_API_KEY，再重新啟動 Bot。",
                ephemeral=True,
            )
            return

        if not prompt:
            await interaction.response.send_message(
                "請輸入你想問的內容。",
                ephemeral=True,
            )
            return

        if len(prompt) > 6000:
            await interaction.response.send_message(
                "問題太長了，請控制在 6000 字內。",
                ephemeral=True,
            )
            return

        if not await self._check_ai_limit(interaction):
            return

        await interaction.response.defer()

        answer = await self._generate_gemini_chat(prompt)
        if not answer:
            await interaction.followup.send("AI 這次沒有產生正常回覆，Gemini 與 Groq 備援都沒接上。請稍後再試。")
            return

        await self._send_ai_output(
            interaction,
            answer,
            "ai-chat.txt",
            "Gemini 這次沒接到梗，再試一次。",
        )

    @ai.command(name="translate", description="讓 Gemini 幫你翻譯並順一下語氣")
    @app_commands.describe(text="原文", target="目標語言")
    async def ai_translate(
        self,
        interaction,
        text: str,
        target: str = "繁體中文",
    ):
        text = text.strip()
        target = target.strip() or "繁體中文"

        if not self.gemini_client and not self.groq_client:
            await interaction.response.send_message(
                "Gemini 和 Groq 都還沒設定 API Key。請在專案根目錄的 .env 設定 GEMINI_API_KEY 或 GROQ_API_KEY，再重新啟動 Bot。",
                ephemeral=True,
            )
            return

        if not text:
            await interaction.response.send_message(
                "請輸入原文。",
                ephemeral=True,
            )
            return

        if len(text) > 5000:
            await interaction.response.send_message(
                "文字太長了，請控制在 5000 字內。",
                ephemeral=True,
            )
            return

        if not await self._check_ai_limit(interaction):
            return

        await interaction.response.defer()

        answer = await self._generate_ai(
            f"請翻譯成{target}：\n{text}",
            "你是翻譯助手。只輸出翻譯結果，保留原意，語氣自然。",
        )

        await self._send_ai_output(
            interaction,
            answer,
            "ai-translate.txt",
            "Gemini 翻譯這次沒回來。",
        )

    @ai.command(name="summarize", description="讓 Gemini 幫你整理重點")
    @app_commands.describe(text="要整理的內容")
    async def summarize(self, interaction, text: str):
        text = text.strip()

        if not self.gemini_client and not self.groq_client:
            await interaction.response.send_message(
                "Gemini 和 Groq 都還沒設定 API Key。請在專案根目錄的 .env 設定 GEMINI_API_KEY 或 GROQ_API_KEY，再重新啟動 Bot。",
                ephemeral=True,
            )
            return

        if not text:
            await interaction.response.send_message(
                "請輸入要整理的內容。",
                ephemeral=True,
            )
            return

        if len(text) > 12000:
            await interaction.response.send_message(
                "文字太長了，請控制在 12000 字內。",
                ephemeral=True,
            )
            return

        if not await self._check_ai_limit(interaction):
            return

        await interaction.response.defer()

        answer = await self._generate_ai(
            text,
            "請把內容整理成自然的繁體中文重點摘要。保留重要資訊，不要加入原文沒有的內容。",
        )

        await self._send_ai_output(
            interaction,
            answer,
            "ai-summary.txt",
            "Gemini 整理這次沒回來。",
        )



    @ai.command(name="ask", description="使用 Groq AI 認真回答問題、協助解題與除錯")
    @app_commands.describe(question="想詢問或需要協助解決的問題")
    async def ai_ask(self, interaction: discord.Interaction, question: str):
        question = question.strip()
        if not self.groq_client:
            await interaction.response.send_message(
                "Groq AI 還沒設定 GROQ_API_KEY。請在專案根目錄的 .env 加入金鑰，然後重新啟動 Bot。",
                ephemeral=True,
            )
            return
        if not question:
            await interaction.response.send_message("請輸入問題。", ephemeral=True)
            return
        if len(question) > 12000:
            await interaction.response.send_message("問題太長了，請控制在 12000 字內。", ephemeral=True)
            return
        if not await self._check_ai_limit(interaction):
            return
        await interaction.response.defer(thinking=True)
        try:
            response = await asyncio.wait_for(
                self.groq_client.chat.completions.create(
                    model=self.groq_model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "你是 Discord 伺服器裡認真、可靠的繁體中文 AI 助手。"
                                "優先協助使用者解決問題、解釋知識、寫程式與除錯。"
                                "回答清楚、具體、可執行；不確定時要坦白，不要捏造。"
                                "使用者以其他語言提問時，跟隨使用者的語言。"
                            ),
                        },
                        {"role": "user", "content": question},
                    ],
                ),
                timeout=40,
            )
            answer = (response.choices[0].message.content or "").strip()
            await self._send_ai_output(
                interaction,
                answer,
                "groq-answer.txt",
                "Groq 這次沒有產生文字回覆。",
            )
        except asyncio.TimeoutError as exc:
            self._log_ai_error("Groq ask", exc)
            await interaction.followup.send(self._friendly_ai_error("Groq", exc))
        except Exception as exc:
            self._log_ai_error("Groq ask", exc)
            await interaction.followup.send(self._friendly_ai_error("Groq", exc))

    @ai.command(name="groq-test", description="實際測試 Groq API 與模型是否可用")
    async def ai_groq_test(self, interaction: discord.Interaction):
        if not self.groq_client:
            file_values = dotenv_values(ENV_FILE) if ENV_FILE.is_file() else {}
            key_in_file = bool(str(file_values.get("GROQ_API_KEY") or "").strip())
            await interaction.response.send_message(
                "❌ Groq Client 沒有建立。\n"
                f".env 檔案存在：{'是' if ENV_FILE.is_file() else '否'}\n"
                f".env 能讀到 GROQ_API_KEY：{'是' if key_in_file else '否'}\n"
                "不會顯示金鑰內容。請確認 GROQ_API_KEY 在專案根目錄的 .env 中，然後重啟 Bot。",
                ephemeral=True,
            )
            return
        if not await self._check_ai_limit(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            response = await asyncio.wait_for(
                self.groq_client.chat.completions.create(
                    model=self.groq_model,
                    messages=[{"role": "user", "content": "請只回覆 OK。"}],
                ),
                timeout=25,
            )
            answer = (response.choices[0].message.content or "").strip()
            await interaction.followup.send(
                f"✅ Groq API 測試成功。\n模型：`{self.groq_model}`\n測試回覆：{answer[:150] or '(空白)'}",
                ephemeral=True,
            )
        except asyncio.TimeoutError as exc:
            self._log_ai_error("Groq test", exc)
            await interaction.followup.send(
                self._friendly_ai_error("Groq", exc),
                ephemeral=True,
            )
        except Exception as exc:
            self._log_ai_error("Groq test", exc)
            await interaction.followup.send(
                self._friendly_ai_error("Groq", exc),
                ephemeral=True,
            )

    @ai.command(name="test", description="實際測試 Gemini API 與模型是否可用")
    async def ai_test(self, interaction: discord.Interaction):
        if not self.gemini_client:
            file_values = dotenv_values(ENV_FILE) if ENV_FILE.is_file() else {}
            key_in_file = bool(str(file_values.get("GEMINI_API_KEY") or "").strip())
            await interaction.response.send_message(
                "❌ Gemini Client 沒有建立。\n"
                f".env 檔案存在：{'是' if ENV_FILE.is_file() else '否'}\n"
                f".env 能讀到 GEMINI_API_KEY：{'是' if key_in_file else '否'}\n"
                "不會顯示金鑰內容。請確認是從含有 bot.py 的 discord-bot 資料夾啟動，並重新啟動 Bot。",
                ephemeral=True,
            )
            return

        if not await self._check_ai_limit(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            response = await asyncio.wait_for(
                self.gemini_client.aio.models.generate_content(
                    model=self.gemini_model,
                    contents="請只回覆 OK。",
                ),
                timeout=25,
            )
            answer = (getattr(response, "text", "") or "").strip()
            if answer:
                await interaction.followup.send(
                    f"✅ Gemini API 測試成功。\n模型：`{self.gemini_model}`\n測試回覆：{answer[:150]}",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    f"⚠️ Gemini API 有回應，但沒有文字輸出。模型：`{self.gemini_model}`",
                    ephemeral=True,
                )
        except asyncio.TimeoutError as exc:
            self._log_ai_error("Gemini test", exc)
            await interaction.followup.send(
                self._friendly_ai_error("Gemini", exc),
                ephemeral=True,
            )
        except Exception as exc:
            self._log_ai_error("Gemini test", exc)
            await interaction.followup.send(
                self._friendly_ai_error("Gemini", exc),
                ephemeral=True,
            )


async def setup(bot):
    await bot.add_cog(SearchCog(bot))
