import asyncio
import os
import re
from collections import defaultdict, deque

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands


DEFAULT_SYSTEM_PROMPT = (
    "你是台灣 Discord 群裡的朋友，專門自然聊天、接梗、講幹話、吐槽，不是客服、老師或笑話解說員。"
    "只能用自然的繁體中文和台灣口語，像朋友秒回訊息，語氣隨意、有個性，偶爾反串、嘴砲、裝傻或突然歪樓。"
    "回答越短越好，通常一小句就夠，最多兩句；目標約 5 到 25 個中文字。"
    "絕對禁止使用任何 emoji、表情符號或顏文字，只能用純文字。"
    "不要寫開場白、不要重述問題、不要列點、不要解釋梗、不要補充笑話的意思、不要每次都硬講笑話。"
    "先看對方說什麼再接話；可以回嗆但要像熟朋友鬧著玩，不要每句都用同一種句型。"
    "例如：對方說「好無聊」→「那你跟天花板比誰先睡著。」；說「你很吵」→「你先叫我的欸哈哈。」；說「笑死」→「先別死，等等還要你接梗。」"
    "普通聊天就自然接話；真的在問正事才簡短回答。不要自稱 AI。不要羞辱身分、外貌、疾病或脆弱處，也不要鼓勵危險行為。"
)


def _strip_emojis(text: str) -> str:
    return re.sub(
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


class LocalBanterCog(commands.Cog):
    """Local Ollama-powered banter chat. No cloud AI API key is required."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        self.model = os.getenv("OLLAMA_MODEL", "qwen2.5:3b").strip() or "qwen2.5:3b"
        self.timeout = max(10, int(os.getenv("OLLAMA_TIMEOUT", "90")))
        self.max_tokens = max(50, min(500, int(os.getenv("OLLAMA_MAX_TOKENS", "90"))))
        self.system_prompt = (
            (os.getenv("OLLAMA_SYSTEM_PROMPT", "").strip() or DEFAULT_SYSTEM_PROMPT)
            + "\\n回答禁止使用任何 emoji、表情符號或顏文字，只能輸出純文字。"
        )
        self.allowed_channel_ids = self._parse_ids(os.getenv("OLLAMA_BANTER_CHANNEL_IDS", ""))
        self.history = defaultdict(lambda: deque(maxlen=6))
        self.channel_locks = defaultdict(asyncio.Lock)

    @staticmethod
    def _parse_ids(raw: str) -> set[int]:
        result = set()
        for item in raw.split(","):
            item = item.strip()
            if item.isdecimal() and int(item) > 0:
                result.add(int(item))
        return result

    def _is_triggered(self, message: discord.Message) -> bool:
        if not self.bot.user:
            return False
        if isinstance(message.channel, discord.DMChannel):
            return True
        if self.allowed_channel_ids and message.channel.id not in self.allowed_channel_ids:
            return False
        if self.bot.user in message.mentions:
            return True
        reference = message.reference
        if reference and reference.message_id:
            resolved = reference.resolved
            if isinstance(resolved, discord.Message):
                return resolved.author.id == self.bot.user.id
            return reference.cached_message is not None and reference.cached_message.author.id == self.bot.user.id
        return False

    def _clean_message(self, message: discord.Message) -> str:
        content = message.content or ""
        if self.bot.user:
            content = re.sub(rf"<@!?{self.bot.user.id}>", "", content)
        return content.strip()

    async def _generate(self, channel_id: int, username: str, prompt: str) -> str:
        messages = [{"role": "system", "content": self.system_prompt}]
        for old_user, old_prompt, old_answer in self.history[channel_id]:
            messages.append({"role": "user", "content": f"{old_user}：{old_prompt}"})
            messages.append({"role": "assistant", "content": old_answer})
        messages.append({"role": "user", "content": f"{username}：{prompt}"})

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": 1.0,
                "top_p": 0.9,
                "repeat_penalty": 1.12,
                "num_ctx": 4096,
                "num_predict": self.max_tokens,
            },
        }
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f"{self.base_url}/api/chat", json=payload) as response:
                if response.status != 200:
                    detail = (await response.text())[:300]
                    raise RuntimeError(f"Ollama HTTP {response.status}: {detail}")
                data = await response.json()

        answer = _strip_emojis(str(data.get("message", {}).get("content", "")).strip())
        if not answer:
            raise RuntimeError("Ollama 回傳了空白回答")
        self.history[channel_id].append((username, prompt, answer))
        return answer[:1900]

    async def _reply(self, message: discord.Message, prompt: str):
        if not prompt:
            prompt = "有人叫你出來，隨口回一句有梗的幹話。"
        async with self.channel_locks[message.channel.id]:
            try:
                async with message.channel.typing():
                    answer = await self._generate(
                        message.channel.id,
                        message.author.display_name,
                        prompt[:1500],
                    )
                await message.reply(
                    answer,
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except asyncio.TimeoutError:
                await message.reply(
                    "我腦袋轉太久當機了，等一下再叫我啦。",
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (aiohttp.ClientError, RuntimeError, ValueError) as exc:
                print(f"[LocalBanter] {type(exc).__name__}: {exc}")
                await message.reply(
                    "我的本機 AI 還沒連上啦。先在跑 Bot 的電腦啟動 Ollama，並下載設定的模型。",
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not self._is_triggered(message):
            return
        content = self._clean_message(message)
        if content.startswith("!"):
            return
        await self._reply(message, content)

    @app_commands.command(name="localai", description="測試本機幹話 AI 是否正常")
    @app_commands.describe(message="想讓 AI 接的話")
    async def localai(self, interaction: discord.Interaction, message: str = "講一句有梗的幹話"):
        await interaction.response.defer(thinking=True)
        try:
            answer = await self._generate(
                interaction.channel_id or interaction.user.id,
                interaction.user.display_name,
                message[:1500],
            )
            await interaction.followup.send(
                answer,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except asyncio.TimeoutError:
            await interaction.followup.send("我腦袋轉太久當機了，等一下再試。")
        except (aiohttp.ClientError, RuntimeError, ValueError) as exc:
            print(f"[LocalBanter] {type(exc).__name__}: {exc}")
            await interaction.followup.send(
                "本機 Ollama 沒連上。請確認 Ollama 正在執行，而且已下載設定的模型。"
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(LocalBanterCog(bot))
