import asyncio
import json
import re
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands


OWNER_ID = 1140900506198351924
BACKUP_PATH = Path(__file__).resolve().parent.parent / "backup_last.json"


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

    @app_commands.command(name="backup", description="備份伺服器身分組、分類、文字及語音頻道")
    @app_commands.guild_only()
    async def backup(self, interaction: discord.Interaction):
        if not await self._authorize(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("這個指令只能在伺服器內使用。", ephemeral=True)
            return
        try:
            payload = {
                "schema_version": 1,
                "guild_id": guild.id,
                "guild_name": guild.name,
                "created_at": discord.utils.utcnow().isoformat(),
                "roles": [
                    self._serialize_role(role)
                    for role in sorted(guild.roles, key=lambda item: item.position)
                    if not role.is_default() and not role.managed
                ],
                "channels": [
                    item for channel in sorted(guild.channels, key=lambda item: item.position)
                    if (item := self._serialize_channel(channel)) is not None
                ],
            }
            BACKUP_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            file = discord.File(str(BACKUP_PATH), filename="backup_last.json")
            await interaction.followup.send(
                f"備份完成：已保存 {len(payload['roles'])} 個身分組、{len(payload['channels'])} 個頻道。",
                file=file,
                ephemeral=True,
            )
        except (OSError, TypeError, ValueError, discord.HTTPException) as exc:
            await interaction.followup.send(f"備份失敗：{exc}", ephemeral=True)

    @app_commands.command(name="restore", description="從 Bot 本機的 backup_last.json 還原伺服器結構")
    @app_commands.guild_only()
    async def restore(self, interaction: discord.Interaction):
        if not await self._authorize(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("這個指令只能在伺服器內使用。", ephemeral=True)
            return
        if not BACKUP_PATH.is_file():
            await interaction.followup.send(
                "找不到 backup_last.json。請先執行 /backup；備份檔必須存在於 Bot 執行環境。",
                ephemeral=True,
            )
            return
        try:
            payload = json.loads(BACKUP_PATH.read_text(encoding="utf-8"))
            roles_data = payload.get("roles", [])
            channels_data = payload.get("channels", [])
            role_map = {role.id: role for role in guild.roles}
            role_name_map = {role.name: role for role in guild.roles}
            created_roles = 0
            created_channels = 0

            for data in sorted(roles_data, key=lambda item: item.get("position", 0)):
                existing = role_map.get(data.get("id")) or role_name_map.get(data.get("name"))
                if existing is None:
                    try:
                        existing = await guild.create_role(
                            name=str(data["name"])[:100],
                            permissions=discord.Permissions(int(data.get("permissions", 0))),
                            colour=discord.Colour(int(data.get("color", 0))),
                            hoist=bool(data.get("hoist", False)),
                            mentionable=bool(data.get("mentionable", False)),
                            reason="Restore from backup_last.json",
                        )
                        created_roles += 1
                        await asyncio.sleep(0.5)
                    except (discord.HTTPException, discord.Forbidden, KeyError, ValueError):
                        continue
                role_map[data.get("id")] = existing
                role_name_map[existing.name] = existing

            def resolve_overwrites(items):
                overwrites = {}
                for entry in items or []:
                    target = None
                    if entry.get("target_type") == "role":
                        target = role_map.get(entry.get("target_id")) or role_name_map.get(entry.get("target_name"))
                    elif entry.get("target_type") == "member":
                        target = guild.get_member(int(entry.get("target_id", 0)))
                    if target is None:
                        continue
                    try:
                        allow = discord.Permissions(int(entry.get("allow", 0)))
                        deny = discord.Permissions(int(entry.get("deny", 0)))
                        overwrites[target] = discord.PermissionOverwrite.from_pair(allow, deny)
                    except (TypeError, ValueError):
                        continue
                return overwrites

            for data in sorted(
                (item for item in channels_data if item.get("type") == "category"),
                key=lambda item: item.get("position", 0),
            ):
                if discord.utils.get(guild.categories, name=data.get("name")) is None:
                    try:
                        await guild.create_category(
                            str(data["name"])[:100],
                            overwrites=resolve_overwrites(data.get("overwrites")),
                            reason="Restore from backup_last.json",
                        )
                        created_channels += 1
                        await asyncio.sleep(0.5)
                    except (discord.HTTPException, discord.Forbidden, KeyError, TypeError, ValueError):
                        pass

            category_map = {category.name: category for category in guild.categories}
            old_category_to_name = {
                item.get("id"): item.get("name")
                for item in channels_data if item.get("type") == "category"
            }

            ordinary_channels = [
                item for item in channels_data if item.get("type") in {"text", "voice"}
            ]
            ordinary_channels.sort(key=lambda item: (item.get("category_id") is not None, item.get("position", 0)))

            for data in ordinary_channels:
                channel_type = data.get("type")
                if channel_type == "text":
                    existing = discord.utils.get(guild.text_channels, name=data.get("name"))
                else:
                    existing = discord.utils.get(guild.voice_channels, name=data.get("name"))
                if existing is not None:
                    continue
                category_name = old_category_to_name.get(data.get("category_id"))
                category = category_map.get(category_name) if category_name else None
                kwargs = {
                    "name": str(data.get("name", "restored-channel"))[:100],
                    "category": category,
                    "overwrites": resolve_overwrites(data.get("overwrites")),
                    "position": max(0, int(data.get("position", 0))),
                    "reason": "Restore from backup_last.json",
                }
                try:
                    if channel_type == "text":
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
                    await asyncio.sleep(0.5)
                except (discord.HTTPException, discord.Forbidden, TypeError, ValueError, KeyError):
                    continue

            await interaction.followup.send(
                f"還原流程完成。新增身分組：{created_roles} 個；新增頻道／分類：{created_channels} 個。"
                "已存在的同名頻道會略過；受 Discord 權限、階層或伺服器限制的項目可能無法還原。",
                ephemeral=True,
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            await interaction.followup.send(f"還原失敗：備份檔格式或內容有問題。{exc}", ephemeral=True)

    @app_commands.command(name="sendlong", description="將長文字切成多段私密訊息發送")
    @app_commands.guild_only()
    @app_commands.describe(text="要發送的文字，會以每段最多 2000 字切割")
    async def sendlong(self, interaction: discord.Interaction, text: str):
        if not await self._authorize(interaction):
            return
        if not text.strip():
            await interaction.response.send_message("文字不能是空的。", ephemeral=True)
            return
        chunks = [text[index:index + 2000] for index in range(0, len(text), 2000)]
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
