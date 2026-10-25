from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from collections import deque
from datetime import datetime, timezone

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

import storage

logger = logging.getLogger(__name__)

HISTORY_ALLOWED_USER_ID = 1140900506198351924
HISTORY_LIMIT = 1000
RECENT_LIMIT = 300
EMBED_COLOR = discord.Color(0x2B2D31)


def _as_utc_timestamp(value) -> int:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp())
    return int(float(value or time.time()))


def _format_size(size_bytes: int) -> str:
    return f"{max(0, int(size_bytes)) / (1024 * 1024):.2f}"


def _clean_text(value, limit: int = 3500) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 3] + "..."


async def _archive_attachment(attachment: discord.Attachment) -> str | None:
    """Copy an attachment to GoFile before the original Discord message disappears."""
    temp_path = None
    try:
        timeout = aiohttp.ClientTimeout(total=120, connect=15, sock_read=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(str(attachment.url)) as response:
                response.raise_for_status()
                with tempfile.NamedTemporaryFile(prefix="history-", delete=False) as tmp:
                    temp_path = tmp.name
                    async for chunk in response.content.iter_chunked(1024 * 1024):
                        tmp.write(chunk)

            with open(temp_path, "rb") as file_handle:
                form = aiohttp.FormData()
                form.add_field(
                    "file",
                    file_handle,
                    filename=os.path.basename(attachment.filename) or "attachment",
                    content_type=attachment.content_type or "application/octet-stream",
                )
                async with session.post("https://upload.gofile.io/uploadfile", data=form) as response:
                    response.raise_for_status()
                    payload = await response.json(content_type=None)

        data = payload.get("data") or {}
        if payload.get("status") not in (None, "ok") or not data.get("downloadPage"):
            logger.warning("GoFile upload failed for attachment %s: %s", attachment.filename, payload)
            return None
        return str(data["downloadPage"])
    except Exception:
        logger.exception("附件備份到 GoFile 失敗：%s", attachment.filename)
        return None
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def _initial_attachment_snapshot(message: discord.Message) -> list[dict]:
    """Persist original metadata before any network upload can delay deletion handling."""
    return [
        {
            "filename": attachment.filename,
            "size": int(attachment.size),
            "url": str(attachment.url),
            "discord_url": str(attachment.url),
            "content_type": attachment.content_type,
            "archived": False,
        }
        for attachment in message.attachments
    ]


async def _snapshot_attachments(message: discord.Message) -> list[dict]:
    results = []
    for attachment in message.attachments:
        archived_url = await _archive_attachment(attachment)
        results.append({
            "filename": attachment.filename,
            "size": int(attachment.size),
            "url": archived_url or str(attachment.url),
            "discord_url": str(attachment.url),
            "content_type": attachment.content_type,
            "archived": bool(archived_url),
        })
    return results


class HistoryStore:
    """SQLite persistence plus per-channel/per-user recent deques."""

    def __init__(self) -> None:
        self.channel_recent: dict[int, deque[dict]] = {}
        self.user_recent: dict[int, deque[dict]] = {}
        self._ensure_schema()

    @staticmethod
    def _ensure_schema() -> None:
        with storage.connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS history_message_snapshots (
                    message_id INTEGER PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    channel_name TEXT NOT NULL DEFAULT '',
                    author_id INTEGER NOT NULL,
                    author_name TEXT NOT NULL,
                    author_avatar_url TEXT,
                    content TEXT NOT NULL DEFAULT '',
                    attachments_json TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL,
                    saved_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS history_deleted_messages (
                    message_id INTEGER PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    channel_name TEXT NOT NULL DEFAULT '',
                    author_id INTEGER NOT NULL,
                    author_name TEXT NOT NULL,
                    author_avatar_url TEXT,
                    content TEXT NOT NULL DEFAULT '',
                    attachments_json TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL,
                    deleted_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_history_deleted_guild_time
                    ON history_deleted_messages(guild_id, deleted_at DESC);
                """
            )

    @staticmethod
    def _record_from_row(row) -> dict:
        record = dict(row)
        try:
            record["attachments"] = json.loads(record.pop("attachments_json") or "[]")
        except (TypeError, ValueError):
            record["attachments"] = []
        record["created_at"] = float(record.get("created_at") or 0)
        if "deleted_at" in record:
            record["deleted_at"] = float(record.get("deleted_at") or 0)
        return record

    def remember(self, record: dict) -> None:
        with storage.connect() as con:
            con.execute(
                """
                INSERT INTO history_message_snapshots
                    (message_id, guild_id, channel_id, channel_name, author_id,
                     author_name, author_avatar_url, content, attachments_json,
                     created_at, saved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    guild_id=excluded.guild_id,
                    channel_id=excluded.channel_id,
                    channel_name=excluded.channel_name,
                    author_id=excluded.author_id,
                    author_name=excluded.author_name,
                    author_avatar_url=excluded.author_avatar_url,
                    content=excluded.content,
                    attachments_json=excluded.attachments_json,
                    created_at=excluded.created_at,
                    saved_at=excluded.saved_at
                """,
                (
                    int(record["message_id"]),
                    int(record["guild_id"]),
                    int(record["channel_id"]),
                    str(record.get("channel_name") or "")[:100],
                    int(record["author_id"]),
                    str(record.get("author_name") or "Unknown")[:256],
                    record.get("author_avatar_url"),
                    str(record.get("content") or ""),
                    json.dumps(record.get("attachments") or [], ensure_ascii=False),
                    float(record["created_at"]),
                    time.time(),
                ),
            )
            # Keep the active snapshots bounded to the latest 30 days.
            con.execute(
                "DELETE FROM history_message_snapshots WHERE saved_at < ?",
                (time.time() - 30 * 86400,),
            )

        channel_id = int(record["channel_id"])
        author_id = int(record["author_id"])
        self.channel_recent.setdefault(
            channel_id, deque(maxlen=RECENT_LIMIT)
        ).append(record)
        self.user_recent.setdefault(
            author_id, deque(maxlen=RECENT_LIMIT)
        ).append(record)

    def get_snapshot(self, message_id: int) -> dict | None:
        with storage.connect() as con:
            row = con.execute(
                "SELECT * FROM history_message_snapshots WHERE message_id=?",
                (int(message_id),),
            ).fetchone()
        return self._record_from_row(row) if row else None

    def has_deleted(self, message_id: int) -> bool:
        with storage.connect() as con:
            row = con.execute(
                "SELECT 1 FROM history_deleted_messages WHERE message_id=?",
                (int(message_id),),
            ).fetchone()
        return row is not None

    def update_attachments(self, message_id: int, attachments: list[dict]) -> None:
        """Apply completed archive URLs whether the message is active or already deleted."""
        encoded = json.dumps(attachments or [], ensure_ascii=False)
        with storage.connect() as con:
            con.execute(
                "UPDATE history_message_snapshots SET attachments_json=? WHERE message_id=?",
                (encoded, int(message_id)),
            )
            con.execute(
                "UPDATE history_deleted_messages SET attachments_json=? WHERE message_id=?",
                (encoded, int(message_id)),
            )

        for recent_map in (self.channel_recent, self.user_recent):
            for queue in recent_map.values():
                for record in queue:
                    if int(record.get("message_id", -1)) == int(message_id):
                        record["attachments"] = attachments or []

    def mark_deleted(self, message_id: int, fallback: dict | None = None) -> bool:
        with storage.connect() as con:
            row = con.execute(
                "SELECT * FROM history_message_snapshots WHERE message_id=?",
                (int(message_id),),
            ).fetchone()
            record = self._record_from_row(row) if row else fallback
            if not record:
                return False

            deleted_at = time.time()
            con.execute(
                """
                INSERT OR IGNORE INTO history_deleted_messages
                    (message_id, guild_id, channel_id, channel_name, author_id,
                     author_name, author_avatar_url, content, attachments_json,
                     created_at, deleted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(record["message_id"]),
                    int(record["guild_id"]),
                    int(record["channel_id"]),
                    str(record.get("channel_name") or "")[:100],
                    int(record["author_id"]),
                    str(record.get("author_name") or "Unknown")[:256],
                    record.get("author_avatar_url"),
                    str(record.get("content") or ""),
                    json.dumps(record.get("attachments") or [], ensure_ascii=False),
                    float(record["created_at"]),
                    deleted_at,
                ),
            )
            inserted = con.execute("SELECT changes()").fetchone()[0] > 0
            con.execute(
                "DELETE FROM history_message_snapshots WHERE message_id=?",
                (int(message_id),),
            )
            con.execute(
                """
                DELETE FROM history_deleted_messages
                WHERE message_id IN (
                    SELECT message_id FROM history_deleted_messages
                    ORDER BY deleted_at DESC LIMIT -1 OFFSET ?
                )
                """,
                (HISTORY_LIMIT,),
            )
            return inserted

    def get_deleted(self, guild_id: int | None) -> list[dict]:
        with storage.connect() as con:
            if guild_id is None:
                rows = con.execute(
                    "SELECT * FROM history_deleted_messages ORDER BY deleted_at DESC LIMIT ?",
                    (HISTORY_LIMIT,),
                ).fetchall()
            else:
                rows = con.execute(
                    """
                    SELECT * FROM history_deleted_messages
                    WHERE guild_id=? ORDER BY deleted_at DESC LIMIT ?
                    """,
                    (int(guild_id), HISTORY_LIMIT),
                ).fetchall()
        return [self._record_from_row(row) for row in rows]


def build_history_embed(record: dict) -> discord.Embed:
    author_name = _clean_text(record.get("author_name") or "未知使用者", 256)
    avatar_url = record.get("author_avatar_url")
    embed = discord.Embed(color=EMBED_COLOR)

    if avatar_url:
        embed.set_author(name=author_name, icon_url=avatar_url)
    else:
        embed.set_author(name=author_name)

    parts = ["**訊息內容**"]
    content = _clean_text(record.get("content"), 3000).strip()
    attachments = record.get("attachments") or []

    if content:
        parts.append(content)
    elif not attachments:
        parts.append("（無文字內容）")

    if attachments:
        parts.append("")
        parts.append("**附件**")

    for item in attachments:
        filename = _clean_text(item.get("filename") or "未知檔案", 180)
        size_mb = _format_size(item.get("size", item.get("size_bytes", 0)))
        url = str(item.get("url") or item.get("download_url") or "")
        parts.append(f"📦 **{filename}** ({size_mb} MB)")
        if url.startswith(("https://", "http://")):
            parts.append(f"🔗 下載: {url}")
        else:
            parts.append("🔗 下載: 無可用連結")

    created = _as_utc_timestamp(record.get("created_at"))
    deleted = _as_utc_timestamp(record.get("deleted_at"))
    parts.extend(
        [
            "",
            "**傳送時間**",
            f"<t:{created}:F> (<t:{created}:R>)",
            "",
            "**刪除時間**",
            f"<t:{deleted}:F> (<t:{deleted}:R>)",
        ]
    )
    embed.description = _clean_text("\n".join(parts), 4000)
    channel_id = record.get("channel_id")
    channel_name = str(record.get("channel_name") or "").strip()
    if channel_id:
        channel_label = f"<#{int(channel_id)}>"
        if channel_name:
            channel_label += f"（#{_clean_text(channel_name, 100)}）"
    else:
        channel_label = f"#{_clean_text(channel_name or '未知頻道', 100)}"
    embed.add_field(name="📍 發送頻道", value=channel_label, inline=True)
    embed.set_footer(text=f"作者 ID：{record.get('author_id', '未知')} ・ 訊息 ID：{record.get('message_id', '未知')}")
    return embed


class HistoryPaginator(discord.ui.View):
    def __init__(self, records: list[dict], owner_id: int) -> None:
        super().__init__(timeout=300)
        self.records = records
        self.owner_id = owner_id
        self.index = 0

        self.first_button = discord.ui.Button(
            label="⏮", style=discord.ButtonStyle.secondary, row=0
        )
        self.previous_button = discord.ui.Button(
            label="◀", style=discord.ButtonStyle.secondary, row=0
        )
        self.page_button = discord.ui.Button(
            label="頁數 1/1",
            style=discord.ButtonStyle.secondary,
            disabled=True,
            row=0,
        )
        self.next_button = discord.ui.Button(
            label="▶", style=discord.ButtonStyle.secondary, row=0
        )
        self.last_button = discord.ui.Button(
            label="⏭", style=discord.ButtonStyle.secondary, row=0
        )

        self.first_button.callback = self.go_first
        self.previous_button.callback = self.go_previous
        self.next_button.callback = self.go_next
        self.last_button.callback = self.go_last
        for button in (
            self.first_button,
            self.previous_button,
            self.page_button,
            self.next_button,
            self.last_button,
        ):
            self.add_item(button)
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        self.first_button.disabled = self.index == 0
        self.previous_button.disabled = self.index == 0
        self.next_button.disabled = self.index >= len(self.records) - 1
        self.last_button.disabled = self.index >= len(self.records) - 1
        self.page_button.label = f"頁數 {self.index + 1}/{len(self.records)}"

    def current_embed(self) -> discord.Embed:
        return build_history_embed(self.records[self.index])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "這不是你的歷史查詢介面。", ephemeral=True
                )
            return False
        return True

    async def _move(self, interaction: discord.Interaction, index: int) -> None:
        self.index = max(0, min(index, len(self.records) - 1))
        self._refresh_buttons()
        await interaction.response.edit_message(
            embed=self.current_embed(), view=self
        )

    async def go_first(self, interaction: discord.Interaction) -> None:
        await self._move(interaction, 0)

    async def go_previous(self, interaction: discord.Interaction) -> None:
        await self._move(interaction, self.index - 1)

    async def go_next(self, interaction: discord.Interaction) -> None:
        await self._move(interaction, self.index + 1)

    async def go_last(self, interaction: discord.Interaction) -> None:
        await self._move(interaction, len(self.records) - 1)

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True


class History(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = HistoryStore()
        self._delete_locks: dict[int, asyncio.Lock] = {}
        self._attachment_tasks: set[asyncio.Task] = set()

    async def _archive_and_update_attachments(self, message: discord.Message) -> None:
        try:
            archived_attachments = await _snapshot_attachments(message)
            self.store.update_attachments(message.id, archived_attachments)
        except Exception:
            logger.exception("附件封存背景工作失敗：%s", message.id)

    def _schedule_attachment_archive(self, message: discord.Message) -> None:
        task = asyncio.create_task(self._archive_and_update_attachments(message))
        self._attachment_tasks.add(task)
        task.add_done_callback(self._attachment_tasks.discard)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return

        record = {
            "message_id": message.id,
            "guild_id": message.guild.id,
            "channel_id": message.channel.id,
            "channel_name": getattr(message.channel, "name", ""),
            "author_id": message.author.id,
            "author_name": str(message.author.display_name or message.author.name),
            "author_avatar_url": str(message.author.display_avatar.url),
            "content": message.content or "",
            "attachments": _initial_attachment_snapshot(message),
            "created_at": message.created_at.timestamp(),
        }
        try:
            # Persist before network I/O so a fast deletion cannot beat the snapshot.
            # Attachment downloads/uploads run in the background so message handling stays responsive.
            self.store.remember(record)
            if message.attachments:
                self._schedule_attachment_archive(message)
        except Exception:
            logger.exception("保存訊息歷史失敗：%s", message.id)

    async def _process_delete(
        self,
        message_id: int,
        guild_id: int | None,
        message: discord.Message | None,
    ) -> None:
        if guild_id is None:
            return

        if len(self._delete_locks) >= 1024:
            self._delete_locks = {
                key: lock for key, lock in self._delete_locks.items() if lock.locked()
            }
        lock = self._delete_locks.setdefault(int(message_id), asyncio.Lock())

        async with lock:
            if self.store.has_deleted(message_id):
                return

            # Cached and raw-delete events can both fire for one message.
            if self.store.get_snapshot(message_id):
                self.store.mark_deleted(message_id)
                return

            # If the bot missed the original message event, use the cached message
            # as a best-effort fallback while Discord's attachment URL still works.
            if message is None or message.guild is None or message.author.bot:
                return

            fallback = {
                "message_id": message.id,
                "guild_id": message.guild.id,
                "channel_id": message.channel.id,
                "channel_name": getattr(message.channel, "name", ""),
                "author_id": message.author.id,
                "author_name": str(message.author.display_name or message.author.name),
                "author_avatar_url": str(message.author.display_avatar.url),
                "content": message.content or "",
                "attachments": _initial_attachment_snapshot(message),
                "created_at": message.created_at.timestamp(),
            }
            inserted = self.store.mark_deleted(message_id, fallback)
            if inserted and message.attachments:
                self._schedule_attachment_archive(message)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        try:
            await self._process_delete(message.id, message.guild.id, message)
        except Exception:
            logger.exception("記錄刪除訊息失敗：%s", message.id)

    @commands.Cog.listener()
    async def on_raw_message_delete(
        self, payload: discord.RawMessageDeleteEvent
    ) -> None:
        if payload.guild_id is None:
            return
        try:
            await self._process_delete(
                payload.message_id,
                payload.guild_id,
                payload.cached_message,
            )
        except Exception:
            logger.exception("記錄 Raw 刪除訊息失敗：%s", payload.message_id)

    @app_commands.command(name=app_commands.locale_str("history", zh_TW="訊息紀錄"),
        description="查詢已刪除的訊息歷史紀錄",
    )
    @app_commands.describe(public=app_commands.locale_str("是否公開顯示回覆", zh_TW="是否公開顯示回覆"))
    @app_commands.rename(public=app_commands.locale_str("公開", zh_TW="公開"))
    async def history(self, interaction: discord.Interaction, public: bool = False) -> None:
        if interaction.user.id != HISTORY_ALLOWED_USER_ID:
            await interaction.response.send_message(
                "你沒有使用此指令的權限。", ephemeral=True
            )
            return

        records = self.store.get_deleted(interaction.guild_id)
        if not records:
            await interaction.response.send_message(
                "目前沒有可查詢的已刪除訊息紀錄。", ephemeral=True
            )
            return

        view = HistoryPaginator(records, interaction.user.id)
        await interaction.response.send_message(
            embed=view.current_embed(),
            view=view,
            ephemeral=not public,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(History(bot))
