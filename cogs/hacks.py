import asyncio
import base64
import json
import io
import re
import subprocess
import sys
import secrets
import threading
import aiohttp
import os
import tempfile
import time
import edge_tts
import imageio_ffmpeg
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands


OWNER_ID = 1140900506198351924
BACKUP_PATH = Path(__file__).resolve().parent.parent / "server_backups.json"
_BACKUP_STORE_LOCK = threading.Lock()
DELETE_DELAY = 0.4
CREATE_DELAY = 0.4


def is_backup_apply_confirmed(value: str | None) -> bool:
    """Require an explicit confirmation phrase before destructive server restore."""
    return isinstance(value, str) and value.strip() == "RESTORE"


class Hacks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._tts_last_spoken: dict[tuple[int, int], float] = {}
        self._tts_locks: dict[int, asyncio.Lock] = {}

    TTS_SETTINGS_PATH = Path(__file__).resolve().parent.parent / "hacks_tts_settings.json"

    @classmethod
    def _read_tts_settings(cls):
        if not cls.TTS_SETTINGS_PATH.exists():
            return {}
        try:
            data = json.loads(cls.TTS_SETTINGS_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    @classmethod
    def _write_tts_settings(cls, data):
        cls.TTS_SETTINGS_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @app_commands.command(name=app_commands.locale_str("tts_setup", zh_TW="設定文字朗讀"), description=app_commands.locale_str("指定文字頻道與要朗讀的成員", zh_TW="指定文字頻道與要朗讀的成員"))
    @app_commands.guild_only()
    @app_commands.describe(channel="要監聽的文字頻道", member="發言時要朗讀的成員", public=app_commands.locale_str("是否公開顯示設定結果", zh_TW="是否公開顯示設定結果"))
    @app_commands.rename(public=app_commands.locale_str("公開", zh_TW="公開"))
    async def tts_setup(self, interaction: discord.Interaction, channel: discord.TextChannel, member: discord.Member, public: bool = True):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("請在伺服器內使用。", ephemeral=True)
            return
        if not (interaction.user.guild_permissions.manage_guild or interaction.user.id == OWNER_ID):
            await interaction.response.send_message("需要管理伺服器權限才能設定。", ephemeral=True)
            return
        settings = self._read_tts_settings()
        settings[str(interaction.guild.id)] = {
            "channel_id": channel.id,
            "member_id": member.id,
            "enabled": True,
        }
        self._write_tts_settings(settings)
        await interaction.response.send_message(
            f"已設定文字朗讀：只監聽 {channel.mention} 中 {member.mention} 的訊息。"
            "Bot 會在該成員所在的語音頻道朗讀，單次最多 50 字。",
            ephemeral=not public,
        )

    @app_commands.command(name=app_commands.locale_str("tts_off", zh_TW="關閉文字朗讀"), description=app_commands.locale_str("關閉本伺服器的文字朗讀", zh_TW="關閉本伺服器的文字朗讀"))
    @app_commands.guild_only()
    @app_commands.describe(public=app_commands.locale_str("是否公開顯示設定結果", zh_TW="是否公開顯示設定結果"))
    @app_commands.rename(public=app_commands.locale_str("公開", zh_TW="公開"))
    async def tts_off(self, interaction: discord.Interaction, public: bool = True):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("請在伺服器內使用。", ephemeral=True)
            return
        if not (interaction.user.guild_permissions.manage_guild or interaction.user.id == OWNER_ID):
            await interaction.response.send_message("需要管理伺服器權限才能設定。", ephemeral=True)
            return
        settings = self._read_tts_settings()
        settings.pop(str(interaction.guild.id), None)
        self._write_tts_settings(settings)
        await interaction.response.send_message("已關閉本伺服器的文字朗讀。", ephemeral=not public)

    async def _generate_tts_audio(self, text: str, audio_path: str):
        """Prefer Google Cloud TTS when configured; keep Edge TTS as a safe fallback."""
        api_key = os.getenv("GOOGLE_TTS_API_KEY", "").strip()
        if api_key:
            # Holly was the original US-English Google Assistant voice. Google does not
            # expose Holly itself as a guaranteed Cloud TTS voice, so use a WaveNet
            # approximation for English and a native Taiwan Mandarin WaveNet voice for Chinese.
            has_chinese = bool(re.search(r"[\u3400-\u9fff]", text))
            language_code = "cmn-TW" if has_chinese else "en-US"
            voice_name = (
                os.getenv("GOOGLE_TTS_ZH_VOICE", "cmn-TW-Wavenet-A").strip()
                if has_chinese
                else os.getenv("GOOGLE_TTS_EN_VOICE", "en-US-Wavenet-F").strip()
            )
            payload = {
                "input": {"text": text},
                "voice": {"languageCode": language_code, "name": voice_name},
                "audioConfig": {
                    "audioEncoding": "MP3",
                    "speakingRate": 0.96,
                    "pitch": -1.0 if not has_chinese else 0.0,
                },
            }
            try:
                timeout = aiohttp.ClientTimeout(total=20)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(
                        "https://texttospeech.googleapis.com/v1/text:synthesize",
                        params={"key": api_key},
                        json=payload,
                    ) as response:
                        result = await response.json(content_type=None)
                        if response.status >= 400:
                            raise RuntimeError(
                                f"Google Cloud TTS HTTP {response.status}: "
                                f"{result.get('error', {}).get('message', 'unknown error')}"
                            )
                audio_content = result.get("audioContent")
                if not audio_content:
                    raise RuntimeError("Google Cloud TTS response did not contain audioContent")
                with open(audio_path, "wb") as audio_file:
                    audio_file.write(base64.b64decode(audio_content))
                return
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError, RuntimeError) as exc:
                print(f"[TTS] Google Cloud TTS failed; falling back to Edge TTS: {type(exc).__name__}: {exc}")

        communicate = edge_tts.Communicate(text, "zh-TW-HsiaoChenNeural")
        await communicate.save(audio_path)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot or message.webhook_id:
            return
        settings = self._read_tts_settings().get(str(message.guild.id))
        if not isinstance(settings, dict) or not settings.get("enabled"):
            return
        if message.channel.id != settings.get("channel_id") or message.author.id != settings.get("member_id"):
            return

        # Remove URLs before reading; cap each message and enforce a per-user cooldown.
        text = re.sub(r"https?://\S+|www\.\S+", " ", message.content, flags=re.IGNORECASE)
        text = re.sub(r"<a?:[A-Za-z0-9_]{2,32}:\d{15,22}>", " 表情 ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return
        text = text[:50]
        now = time.monotonic()
        cooldown_key = (message.guild.id, message.author.id)
        if now - self._tts_last_spoken.get(cooldown_key, 0.0) < 3.0:
            return
        self._tts_last_spoken[cooldown_key] = now

        voice_state = getattr(message.author, "voice", None)
        voice_channel = getattr(voice_state, "channel", None)
        if not voice_channel:
            return
        lock = self._tts_locks.setdefault(message.guild.id, asyncio.Lock())
        if lock.locked():
            return

        async with lock:
            audio_path = None
            voice_client = None
            speech_cog = self.bot.get_cog("SpeechControlCog")
            handoff_claimed = False
            try:
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_file:
                    audio_path = temp_file.name
                await self._generate_tts_audio(text, audio_path)

                if speech_cog and hasattr(speech_cog, "prepare_local_audio"):
                    # Use the same coordinator as slash/dashboard/terminal TTS so
                    # this legacy automatic reader can coexist with Lavalink music.
                    voice_client = await speech_cog.prepare_local_audio(voice_channel)
                    handoff_claimed = True
                else:
                    voice_client = message.guild.voice_client
                    try:
                        import wavelink
                        if isinstance(voice_client, wavelink.Player):
                            raise RuntimeError("語音朗讀模組尚未載入，不能安全切換 Lavalink 音樂連線。")
                    except ImportError:
                        pass
                    if voice_client and voice_client.is_connected():
                        if voice_client.channel.id != voice_channel.id:
                            await voice_client.move_to(voice_channel)
                    else:
                        voice_client = await voice_channel.connect()
                    if voice_client.is_playing() or voice_client.is_paused():
                        return

                source = discord.FFmpegPCMAudio(audio_path, executable=imageio_ffmpeg.get_ffmpeg_exe())
                done = asyncio.Event()
                voice_client.play(source, after=lambda error: self.bot.loop.call_soon_threadsafe(done.set))
                if handoff_claimed:
                    speech_cog.local_audio_started(message.guild.id)
                try:
                    await asyncio.wait_for(done.wait(), timeout=20)
                except asyncio.TimeoutError:
                    if voice_client.is_playing() or voice_client.is_paused():
                        voice_client.stop()
                    try:
                        await asyncio.wait_for(done.wait(), timeout=2)
                    except asyncio.TimeoutError:
                        pass
            except Exception as exc:
                print(f"[TTS] guild={message.guild.id} channel={message.channel.id}: {type(exc).__name__}: {exc}")
            finally:
                if handoff_claimed and speech_cog:
                    try:
                        await speech_cog.finish_local_audio(message.guild.id)
                    except Exception as exc:
                        print(f"[TTS] voice handoff restore failed: {type(exc).__name__}")
                if audio_path:
                    try:
                        os.unlink(audio_path)
                    except OSError:
                        pass


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
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=BACKUP_PATH.parent,
                prefix=f".{BACKUP_PATH.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
                temp_file.write(json.dumps(data, ensure_ascii=False, indent=2))
                temp_file.flush()
                os.fsync(temp_file.fileno())
            temp_path.replace(BACKUP_PATH)
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _store_backup_payload(payload):
        """Persist one backup without losing updates from concurrent calls in this process."""
        with _BACKUP_STORE_LOCK:
            database = Hacks._read_database()
            server_id = str(payload.get("server_id", ""))
            server_name = payload.get("server_name")
            for saved_key, saved_payload in database["servers"].items():
                if not isinstance(saved_payload, dict):
                    continue
                if (
                    (server_id and str(saved_payload.get("server_id", "")) == server_id)
                    or (
                        not saved_payload.get("server_id")
                        and saved_payload.get("server_name") == server_name
                    )
                ):
                    return saved_key, False

            key = secrets.token_hex(4)
            while key in database["servers"]:
                key = secrets.token_hex(4)
            database["servers"][key] = payload
            database["last_backup_key"] = key
            Hacks._write_database(database)
            return key, True

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
                "type": "announcement" if getattr(channel, "is_news", lambda: False)() else "text",
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
    async def _download_asset_base64(url):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(str(url), timeout=aiohttp.ClientTimeout(total=20)) as response:
                    response.raise_for_status()
                    return base64.b64encode(await response.read()).decode("ascii")
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, TypeError, ValueError):
            return None

    @classmethod
    async def _serialize_emoji(cls, emoji):
        image_base64 = await cls._download_asset_base64(emoji.url)
        if not image_base64:
            return None
        return {
            "name": emoji.name,
            "image_base64": image_base64,
            "roles": [role.id for role in emoji.roles],
            "available": emoji.available,
        }

    @classmethod
    async def _serialize_sticker(cls, sticker):
        image_base64 = await cls._download_asset_base64(sticker.url)
        if not image_base64:
            return None
        return {
            "name": sticker.name,
            "description": sticker.description or "",
            "emoji": sticker.emoji or "🙂",
            "format": sticker.format.value,
            "image_base64": image_base64,
        }

    @classmethod
    async def _serialize_role(cls, role, creation_order):
        return {
            "id": role.id,
            "name": role.name,
            "color": role.color.value,
            "permissions": role.permissions.value,
            "position": role.position,
            "creation_order": creation_order,
            "created_at": role.created_at.isoformat() if role.created_at else None,
            "hoist": role.hoist,
            "mentionable": role.mentionable,
            "unicode_emoji": getattr(role, "unicode_emoji", None),
            "icon_base64": await cls._asset_base64(getattr(role, "icon", None)),
        }

    @commands.Cog.listener()
    async def on_message_emoji_alias(self, message: discord.Message):
        """Show a clearly bot-attributed preview for custom emoji aliases."""
        if not message.guild or message.author.bot or message.webhook_id or not message.content:
            return
        matches = list(dict.fromkeys(re.findall(r"(?<!\w):([A-Za-z0-9_]{2,32}):(?!\w)", message.content)))
        if not matches:
            return
        guild_emojis = {emoji.name.casefold(): emoji for emoji in message.guild.emojis}
        found = []
        for name in matches[:3]:
            emoji = guild_emojis.get(name.casefold())
            if emoji:
                found.append(emoji)
        if not found:
            return
        # This is intentionally a visible Bot preview: no user impersonation and no deletion.
        embeds = []
        for emoji in found:
            embed = discord.Embed(
                title=f"表情預覽：:{emoji.name}:",
                description=f"由 {message.author.mention} 的訊息觸發",
                color=discord.Color.blurple(),
            )
            embed.set_image(url=str(emoji.url))
            embeds.append(embed)
        try:
            await message.channel.send(
                embeds=embeds,
                allowed_mentions=discord.AllowedMentions.none(),
                reference=message,
                mention_author=False,
            )
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"[Emoji alias preview] channel={message.channel.id}: {type(exc).__name__}: {exc}")

    @app_commands.command(name=app_commands.locale_str("backup-keys", zh_TW="查看備份密鑰"), description=app_commands.locale_str("查看已保存的伺服器備份密鑰", zh_TW="查看已保存的伺服器備份密鑰"))
    @app_commands.guild_only()
    @app_commands.describe(public=app_commands.locale_str("是否公開顯示備份密鑰（可能讓其他人看到）", zh_TW="是否公開顯示備份密鑰（可能讓其他人看到）"))
    @app_commands.rename(public=app_commands.locale_str("公開", zh_TW="公開"))
    async def backup_keys(self, interaction: discord.Interaction, public: bool = False):
        if not await self._authorize(interaction):
            return
        try:
            database = self._read_database()
            servers = database.get("servers", {})
            if not servers:
                await interaction.response.send_message("目前沒有任何備份密鑰，請先執行 /sync。", ephemeral=True)
                return
            lines = []
            for backup_key, payload in sorted(
                servers.items(),
                key=lambda item: str(item[1].get("created_at", "")) if isinstance(item[1], dict) else "",
                reverse=True,
            ):
                if not isinstance(payload, dict):
                    continue
                name = str(payload.get("server_name") or "未知伺服器")
                created_at = str(payload.get("created_at") or "未知時間")
                lines.append(f"伺服器：{name}｜密鑰：\x60{backup_key}\x60｜時間：{created_at}")
            if not lines:
                await interaction.response.send_message("備份資料庫內沒有有效的備份。", ephemeral=True)
                return
            chunks = []
            current = "已保存的伺服器備份密鑰（僅你可見）：\n"
            for line in lines:
                if len(current) + len(line) + 1 > 1850:
                    chunks.append(current)
                    current = ""
                current += line + "\n"
            if current:
                chunks.append(current)
            await interaction.response.send_message(chunks[0], ephemeral=not public)
            for chunk in chunks[1:]:
                await interaction.followup.send(chunk, ephemeral=not public)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            await interaction.response.send_message(f"讀取備份密鑰失敗：{exc}", ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("delete-backup-key", zh_TW="刪除備份密鑰"), description=app_commands.locale_str("永久刪除指定的已保存備份密鑰", zh_TW="永久刪除指定的已保存備份密鑰"))
    @app_commands.guild_only()
    @app_commands.describe(key="要刪除的備份密鑰")
    async def delete_backup_key(self, interaction: discord.Interaction, key: str):
        if not await self._authorize(interaction):
            return
        key = key.strip()
        try:
            database = self._read_database()
            servers = database["servers"]
            if key not in servers:
                await interaction.response.send_message(f"找不到備份密鑰：{key}", ephemeral=True)
                return
            removed = servers.pop(key)
            if database.get("last_backup_key") == key:
                remaining = sorted(
                    (
                        (saved_key, payload)
                        for saved_key, payload in servers.items()
                        if isinstance(payload, dict)
                    ),
                    key=lambda item: str(item[1].get("created_at", "")),
                    reverse=True,
                )
                database["last_backup_key"] = remaining[0][0] if remaining else None
            self._write_database(database)
            server_name = removed.get("server_name", "未知伺服器") if isinstance(removed, dict) else "未知伺服器"
            await interaction.response.send_message(
                f"已刪除伺服器「{server_name}」的備份密鑰 {key}",
                ephemeral=True,
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            await interaction.response.send_message(f"刪除備份密鑰失敗：{exc}", ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("sync", zh_TW="同步伺服器"), description=app_commands.locale_str("將伺服器身分組、分類、文字及語音頻道備份至中央資料庫", zh_TW="將伺服器身分組、分類、文字及語音頻道備份至中央資料庫"))
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
                "system_channel_flags": getattr(guild, "system_channel_flags", None).value if getattr(guild, "system_channel_flags", None) is not None else None,
                "widget_enabled": getattr(guild, "widget_enabled", None),
            }
            emoji_data = [
                item for emoji in guild.emojis
                if (item := await self._serialize_emoji(emoji)) is not None
            ]
            sticker_data = [
                item for sticker in guild.stickers
                if (item := await self._serialize_sticker(sticker)) is not None
            ]

            database = self._read_database()
            existing_backup = next(
                (
                    (saved_key, saved_payload)
                    for saved_key, saved_payload in database["servers"].items()
                    if isinstance(saved_payload, dict)
                    and (
                        str(saved_payload.get("server_id", "")) == str(guild.id)
                        or (
                            not saved_payload.get("server_id")
                            and saved_payload.get("server_name") == guild.name
                        )
                    )
                ),
                None,
            )
            if existing_backup:
                saved_key, saved_payload = existing_backup
                await interaction.followup.send(
                    f"這個伺服器已經備份過了，為避免重複備份不會再次儲存。現有密鑰：\n```{saved_key}```",
                    ephemeral=True,
                )
                return

            payload = {
                "schema_version": 3,
                "server_id": str(guild.id),
                "server_name": guild.name,
                "icon_base64": icon_base64,
                "server_assets": assets,
                "guild_settings": guild_settings,
                "emojis": emoji_data,
                "stickers": sticker_data,
                "created_at": discord.utils.utcnow().isoformat(),
                "roles": [],
                "role_order_note": "creation_order follows Discord role creation timestamps; position stores visible hierarchy order.",
                "channels": [
                    item for channel in sorted(guild.channels, key=lambda item: (item.position, item.id))
                    if (item := self._serialize_channel(channel)) is not None
                ],
            }
            eligible_roles = [
                role for role in guild.roles
                if not role.is_default() and not role.managed
            ]
            eligible_roles.sort(key=lambda item: (item.created_at.timestamp() if item.created_at else 0, item.id))
            payload["roles"] = [
                await self._serialize_role(role, index)
                for index, role in enumerate(eligible_roles)
            ]
            key, stored = self._store_backup_payload(payload)
            if not stored:
                await interaction.followup.send(
                    f"這個伺服器已經備份過了，為避免重複備份不會再次儲存。現有密鑰：\n```{key}```",
                    ephemeral=True,
                )
                return
            await interaction.followup.send(
                f"{guild.name}：\n備份密鑰（長按即可複製）：\n```{key}```",
                ephemeral=True,
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError, discord.HTTPException) as exc:
            await interaction.followup.send(f"備份失敗：{exc}", ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("apply", zh_TW="套用伺服器備份"), description=app_commands.locale_str("先確認再清空目前伺服器頻道及可管理身分組，並套用指定備份", zh_TW="先確認再清空目前伺服器頻道及可管理身分組，並套用指定備份"))
    @app_commands.guild_only()
    @app_commands.describe(
        key="8 碼備份 Key；留空則使用最近一次備份",
        confirm_text="必須輸入 RESTORE 才會執行破壞性還原",
    )
    async def apply(
        self,
        interaction: discord.Interaction,
        key: str | None = None,
        confirm_text: str | None = None,
    ):
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

            if not is_backup_apply_confirmed(confirm_text):
                manageable_roles = sum(
                    1 for role in guild.roles
                    if not role.is_default() and not role.managed and guild.me and role < guild.me.top_role
                )
                await interaction.followup.send(
                    f"已找到備份「{str(payload.get('server_name') or '未命名伺服器')[:100]}」。"
                    f"目前伺服器有 {len(guild.channels)} 個頻道，約 {manageable_roles} 個可管理身分組；"
                    f"確認後會嘗試刪除這些項目，再從備份重建。\n"
                    "這個操作可能無法完全復原。若確定要繼續，請重新執行指令並將 confirm_text 設為 RESTORE。"
                    "未輸入正確確認文字，因此目前沒有修改伺服器。",
                    ephemeral=True,
                )
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

            # Recreate roles by original creation timestamp, then restore their visible positions.
            created_role_positions = []
            for data in sorted(
                roles_data,
                key=lambda item: (item.get("creation_order", item.get("position", 0)), item.get("position", 0)),
            ):
                try:
                    role_kwargs = {
                        "name": str(data["name"])[:100],
                        "permissions": discord.Permissions(int(data.get("permissions", 0))),
                        "colour": discord.Colour(int(data.get("color", 0))),
                        "hoist": bool(data.get("hoist", False)),
                        "mentionable": bool(data.get("mentionable", False)),
                        "reason": f"Restore server backup key {backup_key}",
                    }
                    if data.get("unicode_emoji"):
                        role_kwargs["unicode_emoji"] = data["unicode_emoji"]
                    if data.get("icon_base64"):
                        try:
                            role_kwargs["icon"] = base64.b64decode(data["icon_base64"], validate=True)
                        except (TypeError, ValueError):
                            pass
                    role = await guild.create_role(**role_kwargs)
                    role_map[data.get("id")] = role
                    role_name_map[role.name] = role
                    created_role_positions.append((role, max(1, int(data.get("position", 1)))))
                    created_roles += 1
                    await asyncio.sleep(CREATE_DELAY)
                except (discord.HTTPException, discord.Forbidden, KeyError, TypeError, ValueError):
                    continue

            if created_role_positions:
                try:
                    position_map = {
                        role: position for role, position in created_role_positions
                        if role < bot_member.top_role
                    }
                    if position_map:
                        await guild.edit_role_positions(
                            positions=position_map,
                            reason=f"Restore role hierarchy from backup {backup_key}",
                        )
                except (discord.Forbidden, discord.HTTPException, TypeError, ValueError):
                    pass

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
                    if data.get("type") in {"text", "announcement"}:
                        kwargs.update({
                            "topic": data.get("topic"),
                            "slowmode_delay": max(0, min(21600, int(data.get("slowmode_delay", 0)))),
                            "nsfw": bool(data.get("nsfw", False)),
                        })
                        if data.get("type") == "announcement":
                            kwargs["news"] = True
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
                    "description", "afk_timeout", "premium_progress_bar_enabled", "widget_enabled"
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
                if guild_settings.get("system_channel_flags") is not None:
                    try:
                        await guild.edit(
                            system_channel_flags=discord.SystemChannelFlags._from_value(int(guild_settings["system_channel_flags"])),
                            reason=f"Restore server settings from backup {backup_key}",
                        )
                    except (discord.Forbidden, discord.HTTPException, AttributeError, TypeError, ValueError):
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

    @app_commands.command(name=app_commands.locale_str("sendlong", zh_TW="發送長訊息"), description=app_commands.locale_str("將長文字切成多段私密訊息發送", zh_TW="將長文字切成多段私密訊息發送"))
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

    @app_commands.command(name=app_commands.locale_str("sendemoji", zh_TW="發送伺服器表情"), description=app_commands.locale_str("檢查並私密回覆自訂 Discord 表情符號", zh_TW="檢查並私密回覆自訂 Discord 表情符號"))
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
