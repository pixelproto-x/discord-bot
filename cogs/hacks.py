import asyncio
import json
import re
import secrets
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands


OWNER_ID = 1140900506198351924
BACKUP_PATH = Path(__file__).resolve().parent.parent / "server_backups.json"
DELETE_DELAY = 0.4
CREATE_DELAY = 0.4


class Hacks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _authorize(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == OWNER_ID:
            return True
        message = "你沒有使用這個指令的權限。"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass
        return False

    @staticmethod
    def _read_database():
        BACKUP_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not BACKUP_PATH.exists():
            BACKUP_PATH.write_text(
                json.dumps({"servers": {}, "last_backup_key": None}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        data = json.loads(BACKUP_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("中央備份資料庫格式不正確。")
        data.setdefault("servers", {})
        data.setdefault("last_backup_key", None)
        if not isinstance(data["servers"], dict):
            raise ValueError("中央備份資料庫的 servers 必須是物件。")
        return data

    @staticmethod
    def _write_database(data):
        BACKUP_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp_path = BACKUP_PATH.with_suffix(".tmp")
        temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(BACKUP_PATH)

    @staticmethod
    def _overwrite_to_json(overwrites):
        result = []
        for target, overwrite in overwrites.items():
            allow, deny = overwrite.pair()
            result.append({
                "target_type": "role" if isinstance(target, discord.Role) else "member",
                "target_id": target.id,
                "target_name": getattr(target, "name", None),
                "allow": allow.value,
                "deny": deny.value,
            })
        return result

    @classmethod
    def _serialize_channel(cls, channel):
        data = {
            "id": channel.id,
            "name": channel.name,
            "position": channel.position,
            "category_id": channel.category_id,
            "overwrites": cls._overwrite_to_json(channel.overwrites),
        }
        if isinstance(channel, discord.CategoryChannel):
            data["type"] = "category"
        elif isinstance(channel, discord.TextChannel):
            data.update({
                "type": "text",
                "topic": channel.topic,
                "slowmode_delay": channel.slowmode_delay,
                "nsfw": channel.nsfw,
                "default_auto_archive_duration": getattr(channel, "default_auto_archive_duration", 1440),
                "default_thread_slowmode_delay": getattr(channel, "default_thread_slowmode_delay", 0),
            })
        elif isinstance(channel, discord.VoiceChannel):
            data.update({
                "type": "voice",
                "bitrate": channel.bitrate,
                "user_limit": channel.user_limit,
                "rtc_region": str(channel.rtc_region) if channel.rtc_region else None,
                "video_quality_mode": int(channel.video_quality_mode) if channel.video_quality_mode else None,
            })
        else:
            return None
        return data

    @staticmethod
    def _serialize_role(role):
        return {
            "id": role.id,
            "name": role.name,
            "color": role.color.value,
            "permissions": role.permissions.value,
            "position": role.position,
            "hoist": role.hoist,
            "mentionable": role.mentionable,
        }

    @app_commands.command(name="sync", description="將伺服器身分組、分類、文字及語音頻道備份至中央資料庫")
    @app_commands.guild_only()
    async def sync(self, interaction: discord.Interaction):
        if not await self._authorize(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("這個指令只能在伺服器內使用。", ephemeral=True)
            return
        try:
            payload = {
                "schema_version": 2,
                "guild_name": guild.name,
                "created_at": discord.utils.utcnow().isoformat(),
                "roles": [
                    self._serialize_role(role)
                    for role in sorted(guild.roles, key=lambda item: item.position)
                    if not role.is_default() and not role.managed
                ],
                "channels": [
                    item for channel in sorted(guild.channels, key=lambda item: (item.position, item.id))
                    if (item := self._serialize_channel(channel)) is not None
                ],
            }
            database = self._read_database()
            key = secrets.token_hex(4)
            while key in database["servers"]:
                key = secrets.token_hex(4)
            database["servers"][key] = payload
            database["last_backup_key"] = key
            self._write_database(database)
            await interaction.followup.send(
                f"{guild.name}：（{key}）",
                ephemeral=True,
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            await interaction.followup.send(f"備份失敗：{exc}", ephemeral=True)

    @app_commands.command(name="apply", description="清空目前伺服器頻道及可管理身分組，再套用指定備份")
    @app_commands.guild_only()
    @app_commands.describe(key="8 碼備份 Key；留空則使用最近一次備份")
    async def apply(self, interaction: discord.Interaction, key: str | None = None):
        if not await self._authorize(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("這個指令只能在伺服器內使用。", ephemeral=True)
            return

        try:
            database = self._read_database()
            backup_key = key.strip() if key else database.get("last_backup_key")
            if not backup_key:
                await interaction.followup.send("找不到最近一次備份，請先執行 /sync。", ephemeral=True)
                return
            payload = database["servers"].get(backup_key)
            if not isinstance(payload, dict):
                await interaction.followup.send(f"找不到備份 Key：{backup_key}", ephemeral=True)
                return
            roles_data = payload.get("roles", [])
            channels_data = payload.get("channels", [])
            if not isinstance(roles_data, list) or not isinstance(channels_data, list):
                await interaction.followup.send("備份資料格式不正確，未刪除任何伺服器內容。", ephemeral=True)
                return

            bot_member = guild.me
            if bot_member is None:
                await interaction.followup.send("無法取得機器人在此伺服器的成員資料。", ephemeral=True)
                return

            # Clear channels first so category deletion and role overwrite cleanup are less likely to fail.
            deleted_channels = 0
            for channel in list(guild.channels):
                try:
                    await channel.delete(reason=f"Apply server backup key {backup_key}")
                    deleted_channels += 1
                    await asyncio.sleep(DELETE_DELAY)
                except (discord.Forbidden, discord.HTTPException):
                    continue

            deleted_roles = 0
            for role in sorted(list(guild.roles), key=lambda item: item.position, reverse=True):
                if role.is_default() or role.managed or role >= bot_member.top_role:
                    continue
                try:
                    await role.delete(reason=f"Apply server backup key {backup_key}")
                    deleted_roles += 1
                    await asyncio.sleep(DELETE_DELAY)
                except (discord.Forbidden, discord.HTTPException):
                    continue

            role_map = {}
            role_name_map = {}
            created_roles = 0
            created_channels = 0

            for data in sorted(roles_data, key=lambda item: item.get("position", 0)):
                try:
                    role = await guild.create_role(
                        name=str(data["name"])[:100],
                        permissions=discord.Permissions(int(data.get("permissions", 0))),
                        colour=discord.Colour(int(data.get("color", 0))),
                        hoist=bool(data.get("hoist", False)),
                        mentionable=bool(data.get("mentionable", False)),
                        reason=f"Restore server backup key {backup_key}",
                    )
                    role_map[data.get("id")] = role
                    role_name_map[role.name] = role
                    created_roles += 1
                    await asyncio.sleep(CREATE_DELAY)
                except (discord.HTTPException, discord.Forbidden, KeyError, TypeError, ValueError):
                    continue

            def resolve_overwrites(items):
                overwrites = {}
                for entry in items or []:
                    target = None
                    if entry.get("target_type") == "role":
                        target = role_map.get(entry.get("target_id")) or role_name_map.get(entry.get("target_name"))
                    elif entry.get("target_type") == "member":
                        try:
                            target = guild.get_member(int(entry.get("target_id", 0)))
                        except (TypeError, ValueError):
                            target = None
                    if target is None:
                        continue
                    try:
                        allow = discord.Permissions(int(entry.get("allow", 0)))
                        deny = discord.Permissions(int(entry.get("deny", 0)))
                        overwrites[target] = discord.PermissionOverwrite.from_pair(allow, deny)
                    except (TypeError, ValueError):
                        continue
                return overwrites

            category_map = {}
            category_data = sorted(
                (item for item in channels_data if item.get("type") == "category"),
                key=lambda item: item.get("position", 0),
            )
            for data in category_data:
                try:
                    category = await guild.create_category(
                        str(data["name"])[:100],
                        overwrites=resolve_overwrites(data.get("overwrites")),
                        reason=f"Restore server backup key {backup_key}",
                    )
                    category_map[data.get("id")] = category
                    created_channels += 1
                    await asyncio.sleep(CREATE_DELAY)
                except (discord.HTTPException, discord.Forbidden, KeyError, TypeError, ValueError):
                    continue

            ordinary_channels = [
                item for item in channels_data if item.get("type") in {"text", "voice"}
            ]
            ordinary_channels.sort(
                key=lambda item: (item.get("category_id") is not None, item.get("position", 0))
            )
            for data in ordinary_channels:
                category = category_map.get(data.get("category_id"))
                kwargs = {
                    "name": str(data.get("name", "restored-channel"))[:100],
                    "category": category,
                    "overwrites": resolve_overwrites(data.get("overwrites")),
                    "position": max(0, int(data.get("position", 0))),
                    "reason": f"Restore server backup key {backup_key}",
                }
                try:
                    if data.get("type") == "text":
                        kwargs.update({
                            "topic": data.get("topic"),
                            "slowmode_delay": max(0, min(21600, int(data.get("slowmode_delay", 0)))),
                            "nsfw": bool(data.get("nsfw", False)),
                        })
                        await guild.create_text_channel(**kwargs)
                    else:
                        kwargs.update({
                            "bitrate": max(8000, min(guild.bitrate_limit, int(data.get("bitrate", 64000)))),
                            "user_limit": max(0, min(99, int(data.get("user_limit", 0)))),
                        })
                        if data.get("rtc_region"):
                            kwargs["rtc_region"] = data["rtc_region"]
                        if data.get("video_quality_mode") is not None:
                            kwargs["video_quality_mode"] = discord.VideoQualityMode(int(data["video_quality_mode"]))
                        await guild.create_voice_channel(**kwargs)
                    created_channels += 1
                    await asyncio.sleep(CREATE_DELAY)
                except (discord.HTTPException, discord.Forbidden, TypeError, ValueError, KeyError):
                    continue

            await interaction.followup.send(
                f"套用完成（Key：{backup_key}）。已刪除頻道／分類 {deleted_channels} 個、可管理身分組 {deleted_roles} 個；"
                f"重建身分組 {created_roles} 個、頻道／分類 {created_channels} 個。"
                "若有高於機器人位階或受 Discord 權限限制的項目，可能無法刪除或重建。",
                ephemeral=True,
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            await interaction.followup.send(f"套用失敗：備份資料庫格式或內容有問題。{exc}", ephemeral=True)

    @app_commands.command(name="sendlong", description="將長文字切成多段私密訊息發送")
    @app_commands.guild_only()
    @app_commands.describe(text="要發送的文字，會以每段最多 2000 字切割")
    async def sendlong(self, interaction: discord.Interaction, text: str):
        if not await self._authorize(interaction):
            return
        if not text.strip():
            await interaction.response.send_message("文字不能是空的。", ephemeral=True)
            return
        chunks = [text[index:index + 1900] for index in range(0, len(text), 1900)]
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            for index, chunk in enumerate(chunks):
                await interaction.followup.send(f"（{index + 1}/{len(chunks)}）\n{chunk}", ephemeral=True)
            await interaction.followup.send(f"已完成：共發送 {len(chunks)} 段私密訊息。", ephemeral=True)
        except discord.HTTPException as exc:
            try:
                await interaction.followup.send(f"發送中斷：{exc}", ephemeral=True)
            except discord.HTTPException:
                pass

    @app_commands.command(name="sendemoji", description="檢查並私密回覆自訂 Discord 表情符號")
    @app_commands.guild_only()
    @app_commands.describe(emoji="貼上自訂表情符號，例如 <:name:123456789012345678>")
    async def sendemoji(self, interaction: discord.Interaction, emoji: str):
        if not await self._authorize(interaction):
            return
        value = emoji.strip()
        if not re.fullmatch(r"<a?:[A-Za-z0-9_]{2,32}:\\d{15,22}>", value):
            await interaction.response.send_message(
                "格式不正確。請貼上 Discord 自訂表情符號的完整格式，例如 <:name:123456789012345678> 或 <a:name:123456789012345678>。",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(value, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Hacks(bot))
