import asyncio
import base64
import json
import io
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
            "category_id": getattr(channel, "category_id", None),
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
        elif isinstance(channel, getattr(discord, "ForumChannel", ())):
            data.update({
                "type": "forum",
                "topic": getattr(channel, "topic", None),
                "slowmode_delay": getattr(channel, "slowmode_delay", 0),
                "nsfw": getattr(channel, "nsfw", False),
                "default_auto_archive_duration": getattr(channel, "default_auto_archive_duration", 1440),
                "default_thread_slowmode_delay": getattr(channel, "default_thread_slowmode_delay", 0),
                "default_sort_order": getattr(getattr(channel, "default_sort_order", None), "value", None),
                "default_layout": getattr(getattr(channel, "default_layout", None), "value", None),
                "available_tags": [
                    {
                        "name": tag.name,
                        "moderated": tag.moderated,
                        "emoji_name": getattr(tag.emoji, "name", None),
                        "emoji_id": getattr(tag.emoji, "id", None),
                    }
                    for tag in getattr(channel, "available_tags", [])
                ],
            })
        elif isinstance(channel, getattr(discord, "StageChannel", ())):
            data.update({
                "type": "stage",
                "bitrate": channel.bitrate,
                "user_limit": channel.user_limit,
                "rtc_region": str(channel.rtc_region) if channel.rtc_region else None,
                "video_quality_mode": int(channel.video_quality_mode) if channel.video_quality_mode else None,
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
    async def _asset_base64(asset):
        if asset is None:
            return None
        try:
            return base64.b64encode(await asset.read()).decode("ascii")
        except (discord.HTTPException, OSError, TypeError, ValueError):
            return None

    @staticmethod
    async def _serialize_emoji(emoji):
        try:
            return {
                "name": emoji.name,
                "image_base64": base64.b64encode(await emoji.url.read()).decode("ascii"),
                "roles": [role.id for role in emoji.roles],
                "available": emoji.available,
            }
        except (discord.HTTPException, OSError, TypeError, ValueError):
            return None

    @staticmethod
    async def _serialize_sticker(sticker):
        try:
            return {
                "name": sticker.name,
                "description": sticker.description or "",
                "emoji": sticker.emoji or "🙂",
                "format": int(sticker.format),
                "image_base64": base64.b64encode(await sticker.url.read()).decode("ascii"),
            }
        except (discord.HTTPException, OSError, TypeError, ValueError):
            return None

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
            icon_base64 = await self._asset_base64(guild.icon)
            assets = {
                "icon": icon_base64,
                "banner": await self._asset_base64(guild.banner),
                "splash": await self._asset_base64(guild.splash),
                "discovery_splash": await self._asset_base64(guild.discovery_splash),
            }
            guild_settings = {
                "description": guild.description,
                "verification_level": guild.verification_level.value,
                "default_notifications": guild.default_notifications.value,
                "explicit_content_filter": guild.explicit_content_filter.value,
                "afk_timeout": guild.afk_timeout,
                "afk_channel_id": guild.afk_channel.id if guild.afk_channel else None,
                "system_channel_id": guild.system_channel.id if guild.system_channel else None,
                "rules_channel_id": guild.rules_channel.id if guild.rules_channel else None,
                "public_updates_channel_id": guild.public_updates_channel.id if guild.public_updates_channel else None,
                "preferred_locale": str(guild.preferred_locale),
                "premium_progress_bar_enabled": guild.premium_progress_bar_enabled,
                "default_role_permissions": guild.default_role.permissions.value,
            }
            emoji_data = [
                item for emoji in guild.emojis
                if (item := await self._serialize_emoji(emoji)) is not None
            ]
            sticker_data = [
                item for sticker in guild.stickers
                if (item := await self._serialize_sticker(sticker)) is not None
            ]

            payload = {
                "schema_version": 3,
                "server_name": guild.name,
                "icon_base64": icon_base64,
                "server_assets": assets,
                "guild_settings": guild_settings,
                "emojis": emoji_data,
                "stickers": sticker_data,
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
        except (OSError, TypeError, ValueError, json.JSONDecodeError, discord.HTTPException) as exc:
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

            # Restore the name first, then each asset independently so one unsupported
            # asset (for example a banner on a non-boosted server) cannot block the others.
            server_name = payload.get("server_name")
            server_assets = payload.get("server_assets") or {}
            if not isinstance(server_assets, dict):
                server_assets = {}
            icon_data = payload.get("icon_base64") or server_assets.get("icon")
            if server_name:
                try:
                    await guild.edit(name=str(server_name)[:100], reason=f"Apply server backup key {backup_key}")
                except (discord.Forbidden, discord.HTTPException, TypeError, ValueError):
                    pass
            for asset_key in ("icon", "banner", "splash", "discovery_splash"):
                encoded = icon_data if asset_key == "icon" else server_assets.get(asset_key)
                if not encoded:
                    continue
                try:
                    asset_bytes = base64.b64decode(encoded, validate=True)
                    await guild.edit(
                        **{asset_key: asset_bytes},
                        reason=f"Restore server {asset_key} from backup {backup_key}",
                    )
                except (discord.Forbidden, discord.HTTPException, TypeError, ValueError):
                    continue

            bot_member = guild.me
            if bot_member is None:
                await interaction.followup.send("無法取得機器人在此伺服器的成員資料。", ephemeral=True)
                return

            # Clear channels (non-categories first, categories last) before rebuilding.
            deleted_channels = 0
            existing_channels = list(guild.channels)
            channels_first = [channel for channel in existing_channels if not isinstance(channel, discord.CategoryChannel)]
            categories_last = [channel for channel in existing_channels if isinstance(channel, discord.CategoryChannel)]
            for channel in channels_first + categories_last:
                try:
                    await channel.delete(reason=f"Apply server backup key {backup_key}")
                    deleted_channels += 1
                    await asyncio.sleep(0.3)
                except (discord.Forbidden, discord.HTTPException):
                    continue

            # Remove manageable custom roles before creating any replacement roles.
            deleted_roles = 0
            for role in sorted(list(guild.roles), key=lambda item: item.position, reverse=True):
                if role.is_default() or role.managed or role >= bot_member.top_role:
                    continue
                try:
                    await role.delete(reason=f"Apply server backup key {backup_key}")
                    deleted_roles += 1
                    await asyncio.sleep(0.3)
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
            channel_map = {}
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
                    channel_map[data.get("id")] = category
                    created_channels += 1
                    await asyncio.sleep(CREATE_DELAY)
                except (discord.HTTPException, discord.Forbidden, KeyError, TypeError, ValueError):
                    continue

            ordinary_channels = [
                item for item in channels_data if item.get("type") in {"text", "voice", "stage", "forum"}
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
                    created = None
                    if data.get("type") == "text":
                        kwargs.update({
                            "topic": data.get("topic"),
                            "slowmode_delay": max(0, min(21600, int(data.get("slowmode_delay", 0)))),
                            "nsfw": bool(data.get("nsfw", False)),
                        })
                        created = await guild.create_text_channel(**kwargs)
                    elif data.get("type") == "forum" and hasattr(guild, "create_forum"):
                        kwargs.update({
                            "topic": data.get("topic"),
                            "slowmode_delay": max(0, min(21600, int(data.get("slowmode_delay", 0)))),
                            "nsfw": bool(data.get("nsfw", False)),
                            "default_auto_archive_duration": int(data.get("default_auto_archive_duration", 1440)),
                            "default_thread_slowmode_delay": max(0, int(data.get("default_thread_slowmode_delay", 0))),
                        })
                        tags = []
                        for tag_data in data.get("available_tags", []):
                            try:
                                tags.append(discord.ForumTag(
                                    name=str(tag_data.get("name", "tag"))[:20],
                                    moderated=bool(tag_data.get("moderated", False)),
                                ))
                            except (TypeError, ValueError):
                                continue
                        if tags:
                            kwargs["available_tags"] = tags[:20]
                        sort_value = data.get("default_sort_order")
                        if sort_value is not None:
                            try:
                                kwargs["default_sort_order"] = discord.ForumOrderType(int(sort_value))
                            except (AttributeError, TypeError, ValueError):
                                pass
                        layout_value = data.get("default_layout")
                        if layout_value is not None:
                            try:
                                kwargs["default_layout"] = discord.ForumLayoutType(int(layout_value))
                            except (AttributeError, TypeError, ValueError):
                                pass
                        created = await guild.create_forum(**kwargs)
                    else:
                        kwargs.update({
                            "bitrate": max(8000, min(guild.bitrate_limit, int(data.get("bitrate", 64000)))),
                            "user_limit": max(0, min(99, int(data.get("user_limit", 0)))),
                        })
                        if data.get("rtc_region"):
                            kwargs["rtc_region"] = data["rtc_region"]
                        if data.get("video_quality_mode") is not None:
                            kwargs["video_quality_mode"] = discord.VideoQualityMode(int(data["video_quality_mode"]))
                        if data.get("type") == "stage" and hasattr(guild, "create_stage_channel"):
                            created = await guild.create_stage_channel(**kwargs)
                        else:
                            created = await guild.create_voice_channel(**kwargs)
                    if created is not None:
                        channel_map[data.get("id")] = created
                    created_channels += 1
                    await asyncio.sleep(CREATE_DELAY)
                except (discord.HTTPException, discord.Forbidden, TypeError, ValueError, KeyError):
                    continue

            # Restore guild settings that reference recreated channels after the channel map exists.
            guild_settings = payload.get("guild_settings") or {}
            if isinstance(guild_settings, dict):
                if "default_role_permissions" in guild_settings:
                    try:
                        await guild.default_role.edit(
                            permissions=discord.Permissions(int(guild_settings["default_role_permissions"])),
                            reason=f"Restore server backup key {backup_key}",
                        )
                    except (discord.Forbidden, discord.HTTPException, TypeError, ValueError):
                        pass
                setting_converters = {
                    "verification_level": discord.VerificationLevel,
                    "default_notifications": discord.NotificationLevel,
                    "explicit_content_filter": discord.ContentFilter,
                    "preferred_locale": discord.Locale,
                }
                for setting_name, converter in setting_converters.items():
                    if setting_name not in guild_settings:
                        continue
                    try:
                        await guild.edit(
                            **{setting_name: converter(int(guild_settings[setting_name]))},
                            reason=f"Restore server backup key {backup_key}",
                        )
                    except (discord.Forbidden, discord.HTTPException, TypeError, ValueError):
                        pass
                scalar_settings = (
                    "description", "afk_timeout", "premium_progress_bar_enabled"
                )
                for setting_name in scalar_settings:
                    if setting_name not in guild_settings:
                        continue
                    value = guild_settings[setting_name]
                    try:
                        await guild.edit(
                            **{setting_name: value},
                            reason=f"Restore server backup key {backup_key}",
                        )
                    except (discord.Forbidden, discord.HTTPException, TypeError, ValueError):
                        pass
                channel_settings = (
                    ("afk_channel_id", "afk_channel"),
                    ("system_channel_id", "system_channel"),
                    ("rules_channel_id", "rules_channel"),
                    ("public_updates_channel_id", "public_updates_channel"),
                )
                for source_key, target_key in channel_settings:
                    source_id = guild_settings.get(source_key)
                    if source_key not in guild_settings:
                        continue
                    target_channel = channel_map.get(source_id) if source_id is not None else None
                    try:
                        await guild.edit(
                            **{target_key: target_channel},
                            reason=f"Restore server backup key {backup_key}",
                        )
                    except (discord.Forbidden, discord.HTTPException, TypeError, ValueError):
                        pass

            # Recreate custom emojis from embedded image bytes.
            restored_emojis = 0
            for emoji_data in payload.get("emojis", []):
                try:
                    emoji_name = str(emoji_data.get("name", ""))[:32]
                    encoded = emoji_data.get("image_base64")
                    if not emoji_name or not encoded or discord.utils.get(guild.emojis, name=emoji_name):
                        continue
                    image_bytes = base64.b64decode(encoded, validate=True)
                    emoji_roles = [
                        role_map.get(role_id)
                        for role_id in emoji_data.get("roles", [])
                        if role_map.get(role_id) is not None
                    ]
                    await guild.create_custom_emoji(
                        name=emoji_name,
                        image=image_bytes,
                        roles=emoji_roles,
                        reason=f"Restore server backup key {backup_key}",
                    )
                    restored_emojis += 1
                    await asyncio.sleep(1.0)
                except (discord.Forbidden, discord.HTTPException, TypeError, ValueError):
                    continue

            # Recreate supported static/animated stickers when the target server allows it.
            restored_stickers = 0
            for sticker_data in payload.get("stickers", []):
                try:
                    sticker_name = str(sticker_data.get("name", ""))[:30]
                    encoded = sticker_data.get("image_base64")
                    if not sticker_name or not encoded or discord.utils.get(guild.stickers, name=sticker_name):
                        continue
                    image_bytes = base64.b64decode(encoded, validate=True)
                    fmt = int(sticker_data.get("format", 1))
                    extension = {1: "png", 2: "apng", 3: "json", 4: "gif"}.get(fmt, "png")
                    sticker_file = discord.File(io.BytesIO(image_bytes), filename=f"{sticker_name}.{extension}")
                    await guild.create_sticker(
                        name=sticker_name,
                        description=str(sticker_data.get("description") or "")[:100],
                        emoji=str(sticker_data.get("emoji") or "🙂")[:100],
                        file=sticker_file,
                        reason=f"Restore server backup key {backup_key}",
                    )
                    restored_stickers += 1
                    await asyncio.sleep(1.0)
                except (discord.Forbidden, discord.HTTPException, TypeError, ValueError):
                    continue

            await interaction.followup.send(
                f"套用完成（Key：{backup_key}）。已刪除頻道／分類 {deleted_channels} 個、可管理身分組 {deleted_roles} 個；"
                f"重建身分組 {created_roles} 個、頻道／分類 {created_channels} 個。"
                f"已還原表情 {restored_emojis} 個、貼圖 {restored_stickers} 個。"
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
        if not re.fullmatch(r"<a?:[A-Za-z0-9_]{2,32}:\d{15,22}>", value):
            await interaction.response.send_message(
                "格式不正確。請貼上 Discord 自訂表情符號的完整格式，例如 <:name:123456789012345678> 或 <a:name:123456789012345678>。",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(value, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Hacks(bot))
