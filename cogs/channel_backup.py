from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from html import escape

import discord
from discord import app_commands
from discord.ext import commands


def serialize_message(message: discord.Message) -> dict:
    author = message.author
    reference = message.reference
    return {
        "message_id": str(message.id),
        "channel_id": str(message.channel.id),
        "channel_name": getattr(message.channel, "name", ""),
        "guild_id": str(message.guild.id) if message.guild else None,
        "author_id": str(author.id),
        "author_name": str(getattr(author, "display_name", None) or author.name),
        "author_is_bot": bool(author.bot),
        "created_at": message.created_at.astimezone(timezone.utc).isoformat(),
        "edited_at": message.edited_at.astimezone(timezone.utc).isoformat() if message.edited_at else None,
        "content": message.content or "",
        "pinned": bool(message.pinned),
        "jump_url": str(message.jump_url),
        "reply_to_message_id": str(reference.message_id) if reference and reference.message_id else None,
        "attachments": [
            {
                "filename": item.filename,
                "size": int(item.size),
                "content_type": item.content_type,
                "url": str(item.url),
            }
            for item in message.attachments
        ],
        "embeds": [
            {
                "title": item.title,
                "description": item.description,
                "url": item.url,
                "type": item.type,
            }
            for item in message.embeds
        ],
        "stickers": [{"id": str(item.id), "name": item.name} for item in message.stickers],
        "reactions": [{"emoji": str(item.emoji), "count": item.count} for item in message.reactions],
    }


def build_archive_zip(channel: discord.TextChannel, records: list[dict], exported_at: datetime) -> bytes:
    metadata = {
        "format": "discord-channel-archive-v1",
        "exported_at": exported_at.astimezone(timezone.utc).isoformat(),
        "guild_id": str(channel.guild.id),
        "guild_name": channel.guild.name,
        "channel_id": str(channel.id),
        "channel_name": channel.name,
        "message_count": len(records),
        "note": "Attachment files are represented by their URLs and metadata; they are not embedded in this archive.",
        "messages": records,
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("messages.json", json.dumps(metadata, ensure_ascii=False, indent=2))
        lines = []
        for record in records:
            stamp = escape(record["created_at"])
            author = escape(record["author_name"])
            content = escape(record["content"]).replace("\n", "<br>")
            attachments = "".join(
                f'<li><a href="{escape(item["url"], quote=True)}">{escape(item["filename"])}</a></li>'
                for item in record["attachments"]
            )
            if attachments:
                attachments = f"<ul>{attachments}</ul>"
            lines.append(
                f'<article id="m-{escape(record["message_id"])}">'
                f'<header><strong>{author}</strong> · <time>{stamp}</time> · '
                f'<a href="{escape(record["jump_url"], quote=True)}">Discord 原訊息</a></header>'
                f'<div class="content">{content or "<em>(無文字內容)</em>"}</div>{attachments}</article>'
            )
        html_doc = (
            "<!doctype html><html lang=\"zh-Hant\"><meta charset=\"utf-8\">"
            f"<title>{escape(channel.guild.name)} / #{escape(channel.name)} 訊息備份</title>"
            "<style>body{font:16px system-ui;max-width:1000px;margin:2rem auto;padding:0 1rem;background:#313338;color:#dbdee1}"
            "article{padding:1rem;margin:1rem 0;background:#2b2d31;border-radius:8px;overflow-wrap:anywhere}"
            "header{color:#b5bac1;margin-bottom:.7rem}.content{white-space:normal}a{color:#00a8fc}</style>"
            f"<h1>{escape(channel.guild.name)} / #{escape(channel.name)}</h1>"
            f"<p>共 {len(records)} 則訊息；依時間由舊到新排列。</p>"
            + "".join(lines) + "</html>"
        )
        archive.writestr("messages.html", html_doc)
        archive.writestr("README.txt", (
            "Discord 頻道訊息備份\n"
            "messages.html：可直接用瀏覽器閱讀。\n"
            "messages.json：完整結構化資料，適合搜尋或轉換。\n"
            "附件只保存檔名、大小、類型和 Discord URL，沒有把檔案本體打包；Discord 附件網址可能失效。\n"
            "只包含匯出時機器人有權限讀取且 Discord 仍保留的訊息。"
        ))
    return output.getvalue()


class ChannelBackup(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="channel_backup", description="匯出指定文字頻道目前可讀取的全部歷史訊息")
    @app_commands.describe(channel="要備份的文字頻道")
    @app_commands.default_permissions(manage_guild=True)
    async def channel_backup(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        if interaction.guild is None or channel.guild.id != interaction.guild.id:
            await interaction.response.send_message("只能備份目前伺服器的文字頻道。", ephemeral=True)
            return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("你需要「管理伺服器」權限才能匯出頻道訊息。", ephemeral=True)
            return

        bot_member = interaction.guild.me
        permissions = channel.permissions_for(bot_member)
        if not permissions.view_channel or not permissions.read_message_history:
            await interaction.response.send_message(
                "我沒有這個頻道的「查看頻道」或「讀取訊息歷史」權限，請先授權再試。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        records = []
        try:
            async for message in channel.history(limit=None, oldest_first=True):
                records.append(serialize_message(message))
                if len(records) % 1000 == 0:
                    await interaction.edit_original_response(
                        content=f"正在讀取歷史訊息……已處理 {len(records):,} 則。"
                    )
            payload = build_archive_zip(channel, records, datetime.now(timezone.utc))
            filename = f"channel-{channel.id}-backup.zip"
            if len(payload) > 24 * 1024 * 1024:
                await interaction.edit_original_response(
                    content=(
                        f"已讀取 {len(records):,} 則訊息，但備份壓縮檔約 {len(payload) / (1024 * 1024):.1f} MB，"
                        "超過安全上傳大小，Discord 無法可靠地把整包傳回來。請先縮小匯出範圍，或改用本機檔案匯出方式。"
                    )
                )
                return
            await interaction.edit_original_response(
                content=f"備份完成：#{channel.name}，共 {len(records):,} 則訊息。ZIP 內含可閱讀 HTML 與完整 JSON；附件目前保存連結和資訊。",
                attachments=[discord.File(io.BytesIO(payload), filename=filename)],
            )
        except discord.Forbidden:
            await interaction.edit_original_response(
                content="Discord 拒絕讀取此頻道的歷史訊息，請確認機器人權限與伺服器設定。"
            )
        except discord.HTTPException as exc:
            await interaction.edit_original_response(
                content=f"Discord API 回應錯誤，備份未完成：{exc}"
            )
        except Exception:
            await interaction.edit_original_response(
                content="備份失敗，請查看機器人終端機的錯誤紀錄。"
            )
            raise


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ChannelBackup(bot))
