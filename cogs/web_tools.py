from __future__ import annotations

import asyncio
import io
import os
import re
from urllib.parse import quote, urlparse

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from openai import AsyncOpenAI


class WebToolsCog(commands.Cog):
    """Public web search and optional image generation."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.openai_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.openai_model = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1").strip() or "gpt-image-1"
        self.client = AsyncOpenAI(api_key=self.openai_key) if self.openai_key else None

    @app_commands.command(name="websearch", name_localizations={"zh-TW": "網路搜尋"}, description="搜尋公開網頁並整理結果", description_localizations={"zh-TW": "搜尋公開網頁並整理結果"})
    @app_commands.describe(query="要搜尋的問題或關鍵字")
    async def websearch(self, interaction: discord.Interaction, query: str):
        query = query.strip()
        if not query or len(query) > 400:
            await interaction.response.send_message("請輸入 1 到 400 字的搜尋內容", ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        url = "https://html.duckduckgo.com/html/?q=" + quote(query)
        headers = {"User-Agent": "Mozilla/5.0 (compatible; LogRelayBot/1.0)"}
        timeout = aiohttp.ClientTimeout(total=15)
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(url, allow_redirects=True) as response:
                    if response.status != 200:
                        raise RuntimeError(f"搜尋服務回傳 HTTP {response.status}")
                    html = await response.text(errors="replace")
            from html import unescape
            matches = re.findall(
                r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                html,
                flags=re.I | re.S,
            )
            snippets = re.findall(
                r'<(?:a|div)[^>]*class="result__snippet"[^>]*>(.*?)</(?:a|div)>',
                html,
                flags=re.I | re.S,
            )
            rows = []
            for index, (href, title_html) in enumerate(matches[:6]):
                title = re.sub(r"<[^>]+>", "", unescape(title_html)).strip()
                href = unescape(href)
                parsed = urlparse(href)
                if parsed.scheme not in ("http", "https"):
                    continue
                snippet = ""
                if index < len(snippets):
                    snippet = re.sub(r"<[^>]+>", "", unescape(snippets[index]))
                    snippet = re.sub(r"\s+", " ", snippet).strip()
                rows.append(f"**{title or '網頁結果'}**\n{href}\n{snippet[:260]}")
            if not rows:
                await interaction.followup.send("這次沒有抓到搜尋結果，請換個關鍵字再試。")
                return
            await interaction.followup.send(
                f"搜尋：{query[:200]}\n\n" + "\n\n".join(rows),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as exc:
            print(f"[WebSearch] {type(exc).__name__}: {exc}")
            await interaction.followup.send("目前無法完成網路搜尋，請稍後再試。")

    @app_commands.command(name="generate_image", name_localizations={"zh-TW": "生成圖片"}, description="使用 OpenAI 依文字描述生成圖片", description_localizations={"zh-TW": "使用 OpenAI 依文字描述生成圖片"})
    @app_commands.describe(prompt="想生成的圖片內容")
    async def generate_image(self, interaction: discord.Interaction, prompt: str):
        prompt = prompt.strip()
        if not prompt or len(prompt) > 1000:
            await interaction.response.send_message("請輸入 1 到 1000 字的圖片描述", ephemeral=True)
            return
        if self.client is None:
            await interaction.response.send_message(
                "尚未設定 OPENAI_API_KEY。請在本機 .env 設定金鑰後重啟 Bot；不要把金鑰貼到聊天室。",
                ephemeral=True,
            )
            return
        await interaction.response.defer(thinking=True)
        try:
            result = await asyncio.wait_for(
                self.client.images.generate(
                    model=self.openai_model,
                    prompt=prompt,
                    size="1024x1024",
                ),
                timeout=120,
            )
            item = result.data[0] if result.data else None
            encoded = getattr(item, "b64_json", None) if item else None
            if not encoded:
                await interaction.followup.send("圖片服務沒有回傳可用的圖片資料。")
                return
            import base64
            raw = base64.b64decode(encoded, validate=True)
            if len(raw) > 10 * 1024 * 1024:
                await interaction.followup.send("生成的圖片太大，無法直接上傳。")
                return
            file = discord.File(io.BytesIO(raw), filename="generated-image.png")
            await interaction.followup.send(file=file, allowed_mentions=discord.AllowedMentions.none())
        except asyncio.TimeoutError:
            await interaction.followup.send("圖片生成超時，請稍後再試。")
        except Exception as exc:
            print(f"[ImageGeneration] {type(exc).__name__}")
            await interaction.followup.send(
                "圖片生成失敗。請確認 OpenAI API 金鑰、帳戶額度與圖片模型權限。"
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(WebToolsCog(bot))
