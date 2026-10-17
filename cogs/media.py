import asyncio
import io
import os
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


class SearchCog(commands.Cog):
    search = app_commands.Group(name="search", description="在 YouTube、Wikipedia、GitHub 找東西")
    image = app_commands.Group(name="image", description="簡單的圖片處理工具")
    ai = app_commands.Group(name="ai", description="Gemini AI 工具")

    def __init__(self, bot):
        self.bot = bot
        self.ai_cooldowns = {}
        self.ocr_cooldowns = {}
        self.ai_cooldown_seconds = 8
        self.ocr_cooldown_seconds = 8
        self.gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip() or "gemini-3.8-flash"
        self.gemini_client = genai.Client(api_key=self.gemini_key) if self.gemini_key else None
        self.openai_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
        self.openai_client = AsyncOpenAI(api_key=self.openai_key) if self.openai_key else None

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

    async def _generate_ai(self, prompt: str, system_instruction: str) -> str:
        if not self.gemini_client:
            return ""

        request = f"{system_instruction}\n\n{prompt}"

        for attempt in range(3):
            try:
                response = await asyncio.wait_for(
                    self.gemini_client.aio.models.generate_content(
                        model=self.gemini_model,
                        contents=request,
                    ),
                    timeout=35,
                )
                return (getattr(response, "text", "") or "").strip()
            except Exception as exc:
                detail = str(exc)
                if self.gemini_key:
                    detail = detail.replace(self.gemini_key, "[REDACTED_API_KEY]")
                print(f"Gemini API error (attempt {attempt + 1}/3): {type(exc).__name__}: {detail[:600]}")
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))

        return ""

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

    @ai.command(name="ask", description="詢問 Gemini AI")
    @app_commands.describe(prompt="想問的內容")
    async def ask(self, interaction, prompt: str):
        prompt = prompt.strip()

        if not self.gemini_client:
            await interaction.response.send_message(
                "Gemini AI 還沒設定 GEMINI_API_KEY。",
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

        answer = await self._generate_ai(
            prompt,
            "你是在朋友群裡幫忙的 Discord Bot。回答自然、清楚、直接。使用繁體中文。",
        )

        await self._send_ai_output(
            interaction,
            answer,
            "ai-answer.txt",
            "Gemini 這次沒回來，再試一次。",
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

        if not self.gemini_client:
            await interaction.response.send_message(
                "Gemini AI 還沒設定 GEMINI_API_KEY。",
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

        if not self.gemini_client:
            await interaction.response.send_message(
                "Gemini AI 還沒設定 GEMINI_API_KEY。",
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



    @ai.command(name="ask", description="使用 OpenAI 認真回答問題、協助解題與除錯")
    @app_commands.describe(question="想詢問或需要協助解決的問題")
    async def ai_ask(self, interaction: discord.Interaction, question: str):
        question = question.strip()
        if not self.openai_client:
            await interaction.response.send_message(
                "OpenAI 尚未設定 OPENAI_API_KEY。請確認專案根目錄的 .env，然後重新啟動 Bot。",
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
                self.openai_client.chat.completions.create(
                    model=self.openai_model,
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
                "openai-answer.txt",
                "OpenAI 這次沒有產生文字回覆。",
            )
        except asyncio.TimeoutError:
            print(f"[OpenAI ask] TimeoutError: model={self.openai_model}")
            await interaction.followup.send("OpenAI 回覆逾時，請稍後再試。")
        except Exception as exc:
            detail = str(exc)
            if self.openai_key:
                detail = detail.replace(self.openai_key, "[REDACTED_API_KEY]")
            print(f"[OpenAI ask] {type(exc).__name__}: {detail[:800]}")
            await interaction.followup.send(
                f"OpenAI 回覆失敗（{type(exc).__name__}）。請查看 Bot 終端機的診斷訊息；不會顯示 API Key。"
            )

    @ai.command(name="openai-test", description="實際測試 OpenAI API 與模型是否可用")
    async def ai_openai_test(self, interaction: discord.Interaction):
        if not self.openai_client:
            file_values = dotenv_values(ENV_FILE) if ENV_FILE.is_file() else {}
            key_in_file = bool(str(file_values.get("OPENAI_API_KEY") or "").strip())
            await interaction.response.send_message(
                "❌ OpenAI Client 沒有建立。\n"
                f".env 檔案存在：{'是' if ENV_FILE.is_file() else '否'}\n"
                f".env 能讀到 OPENAI_API_KEY：{'是' if key_in_file else '否'}\n"
                "不會顯示金鑰內容。請確認 OPENAI_API_KEY 在專案根目錄的 .env 中，然後重啟 Bot。",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            response = await asyncio.wait_for(
                self.openai_client.chat.completions.create(
                    model=self.openai_model,
                    messages=[{"role": "user", "content": "請只回覆 OK。"}],
                ),
                timeout=25,
            )
            answer = (response.choices[0].message.content or "").strip()
            await interaction.followup.send(
                f"✅ OpenAI API 測試成功。\n模型：`{self.openai_model}`\n測試回覆：{answer[:150] or '(空白)'}",
                ephemeral=True,
            )
        except asyncio.TimeoutError:
            print(f"[OpenAI test] TimeoutError: model={self.openai_model}")
            await interaction.followup.send("❌ OpenAI API 測試逾時（25 秒）。", ephemeral=True)
        except Exception as exc:
            detail = str(exc)
            if self.openai_key:
                detail = detail.replace(self.openai_key, "[REDACTED_API_KEY]")
            print(f"[OpenAI test] {type(exc).__name__}: {detail[:800]}")
            await interaction.followup.send(
                f"❌ OpenAI API 測試失敗。錯誤類型：`{type(exc).__name__}`。請查看 Bot 終端機的診斷訊息；金鑰不會顯示。",
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
        except asyncio.TimeoutError:
            print(f"[Gemini test] TimeoutError: model={self.gemini_model}")
            await interaction.followup.send(
                "❌ Gemini API 測試逾時（25 秒）。請檢查網路或稍後再試。",
                ephemeral=True,
            )
        except Exception as exc:
            detail = str(exc)
            if self.gemini_key:
                detail = detail.replace(self.gemini_key, "[REDACTED_API_KEY]")
            print(f"[Gemini test] {type(exc).__name__}: {detail[:800]}")
            await interaction.followup.send(
                "❌ Gemini API 測試失敗。\n"
                f"錯誤類型：`{type(exc).__name__}`\n"
                "請查看啟動 Bot 的終端機中 Gemini test 診斷行；金鑰不會顯示在 Discord。",
                ephemeral=True,
            )


async def setup(bot):
    await bot.add_cog(SearchCog(bot))
