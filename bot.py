import asyncio
import os
import re
import traceback
import time
from pathlib import Path

from dotenv import load_dotenv
from web_dashboard import start_dashboard

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=False)

import discord
from discord import app_commands
from discord.ext import commands, tasks

import storage
from cogs import (
    utility,
    social,
    games,
    music,
    moderation,
    profile,
    voice,
    giveaway,
    media,
    settings,
    banter,
    speech_control,
    help as help_cog,
    web_tools,
    hacks,
    history,
)


OWNER_ID = int(os.getenv("OWNER_ID", "1140900506198351924"))
ALLOWED_ROLE_ID = int(os.getenv("ALLOWED_ROLE_ID", "1509577038443319416"))
MAX_COUNT = 100
MAX_TOTAL_SPAM_MESSAGES = 100
MAX_CONTENT_LEN = 2000

def parse_guild_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    for value in raw.split(','):
        value = value.strip()
        if not value:
            continue
        if not value.isdecimal():
            raise RuntimeError('GUILD_IDS 格式錯誤：請使用逗號分隔的 Discord 伺服器 ID。')
        guild_id = int(value)
        if guild_id <= 0:
            raise RuntimeError('GUILD_IDS 格式錯誤：伺服器 ID 必須大於 0。')
        ids.add(guild_id)
    return ids


ALLOWED_GUILD_IDS = parse_guild_ids(os.getenv('GUILD_IDS', ''))

active_spam: dict[tuple[int, int], dict] = {}

# Map deleted source messages to their visible relay copies so replies can target the copy.
_relay_message_map: dict[int, int] = {}
_RELAY_MESSAGE_MAP_LIMIT = 5000


class ChannelSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.selected_channels = []
        self.select_menu = discord.ui.ChannelSelect(
            channel_types=[discord.ChannelType.text, discord.ChannelType.public_thread],
            min_values=1,
            max_values=25,
        )
        self.select_menu.callback = self.select_callback
        self.add_item(self.select_menu)

    async def select_callback(self, interaction: discord.Interaction):
        self.selected_channels = self.select_menu.values
        self.stop()
        try:
            await interaction.response.edit_message(content="好，頻道收到了，我現在檢查權限。", view=None)
        except Exception:
            pass


class GuildRestrictedCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if ALLOWED_GUILD_IDS and (
            interaction.guild_id is None or interaction.guild_id not in ALLOWED_GUILD_IDS
        ):
            message = '這個 Bot 目前只允許在管理者設定的伺服器中使用。'
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(message, ephemeral=True)
                else:
                    await interaction.response.send_message(message, ephemeral=True)
            except discord.HTTPException:
                pass
            return False
        return True


class Bot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.guilds = True
        intents.message_content = True
        intents.voice_states = True
        if hasattr(intents, "auto_moderation_execution"):
            intents.auto_moderation_execution = True
        if hasattr(intents, "auto_moderation_configuration"):
            intents.auto_moderation_configuration = True
        super().__init__(command_prefix="!", intents=intents, help_command=None, tree_cls=GuildRestrictedCommandTree)
        self._terminal_guild_id: int | None = None
        self._terminal_channel_id: int | None = None

    async def setup_hook(self):
        storage.init_db()
        modules = [
            utility,
            social,
            games,
            music,
            moderation,
            profile,
            voice,
            giveaway,
            media,
            settings,
            banter,
            speech_control,
            help_cog,
            web_tools,
            hacks,
            history,
        ]
        failed_modules = []
        for module in modules:
            try:
                await module.setup(self)
            except Exception:
                failed_modules.append(module.__name__)
                print(f"Failed to load {module.__name__}")
                traceback.print_exc()

        if failed_modules:
            raise RuntimeError("Cog 載入失敗: " + ", ".join(failed_modules))

        global_commands = list(self.tree.get_commands())
        for attempt in range(3):
            try:
                if ALLOWED_GUILD_IDS:
                    guilds = [discord.Object(id=guild_id) for guild_id in sorted(ALLOWED_GUILD_IDS)]
                    for guild in guilds:
                        self.tree.clear_commands(guild=guild)
                        self.tree.copy_global_to(guild=guild)
                    self.tree.clear_commands(guild=None)
                    try:
                        await self.tree.sync()
                    finally:
                        for command in global_commands:
                            self.tree.add_command(command)
                    synced_total = 0
                    for guild in guilds:
                        synced = await self.tree.sync(guild=guild)
                        synced_total += len(synced)
                    print(
                        f"Synced {synced_total} guild slash commands "
                        f"across {len(ALLOWED_GUILD_IDS)} allowlisted guild(s)"
                    )
                else:
                    synced = await self.tree.sync()
                    print(f"Synced {len(synced)} global slash commands")
                break
            except Exception as exc:
                print(f"Slash command sync failed (attempt {attempt + 1}/3): {exc!r}")
                if attempt < 2:
                    await asyncio.sleep(2 * (attempt + 1))
                else:
                    traceback.print_exc()
    async def on_guild_join(self, guild: discord.Guild):
        print(f"Joined guild {guild.id} ({guild.name})")

    async def on_ready(self):
        if not getattr(self, "_web_dashboard_started", False):
            self._web_dashboard_started = True
            await start_dashboard(self)
        print(f"Logged in as {self.user} ({self.user.id})")
        print(f"Connected guilds: {len(self.guilds)}")
        for relay_guild in self.guilds:
            if relay_guild.id not in _relay_enabled_cache:
                try:
                    stored_enabled = await asyncio.to_thread(
                        storage.get_setting,
                        relay_guild.id,
                        "owner_message_relay_enabled",
                        False,
                    )
                    _relay_enabled_cache[relay_guild.id] = bool(stored_enabled)
                except Exception:
                    traceback.print_exc()
                    _relay_enabled_cache[relay_guild.id] = False
            if _relay_enabled_cache.get(relay_guild.id, False):
                _schedule_relay_warm(relay_guild)
        for guild in sorted(self.guilds, key=lambda item: item.name.casefold()):
            allowed = not ALLOWED_GUILD_IDS or guild.id in ALLOWED_GUILD_IDS
            status = "SLASH COMMANDS ENABLED" if allowed else "NOT ALLOWLISTED — slash commands will not appear here"
            print(f"[Guild] {guild.name} | ID={guild.id} | {status}")
        if ALLOWED_GUILD_IDS:
            connected_ids = {guild.id for guild in self.guilds}
            configured_but_missing = ALLOWED_GUILD_IDS - connected_ids
            if configured_but_missing:
                print(
                    "[Slash commands] GUILD_IDS contains server IDs this bot is not currently in: "
                    + ", ".join(str(guild_id) for guild_id in sorted(configured_but_missing))
                )
            blocked = [guild for guild in self.guilds if guild.id not in ALLOWED_GUILD_IDS]
            if blocked:
                print(
                    "[Slash commands] To enable commands in a listed server, add its ID to GUILD_IDS "
                    "in your local .env, then restart the bot. Current excluded servers: "
                    + ", ".join(f"{guild.name} ({guild.id})" for guild in blocked)
                )
        if not reminder_worker.is_running():
            reminder_worker.start()
        if not getattr(self, "_terminal_speech_started", False):
            self._terminal_speech_started = True
            self._terminal_speech_task = asyncio.create_task(self._terminal_speech_console())


    async def _terminal_speech_console(self):
        green = "\033[92m"
        cyan = "\033[96m"
        purple = "\033[95m"
        dim = "\033[90m"
        reset = "\033[0m"
        print(f"\n{purple}╔══════════════════════════════════════════════╗")
        print("║             LOGRELAY // CORE                ║")
        print(f"╚══════════════════════════════════════════════╝{reset}")
        print(f"{green}● SYSTEM ONLINE{reset}  {cyan}● DISCORD GATEWAY CONNECTED{reset}")
        print("直接輸入文字即可傳到已選頻道；/speak 文字則使用語音朗讀。")
        print(f"{dim}快速操作：/servers  /use 伺服器名稱  /channels  /channel 頻道名稱")
        print("/say 文字  /reply @成員 文字  /status  /help  /exit")
        print(f"管理指令仍可使用，舊版帶 ID 的指令也保留。{reset}")
        print("────────────────────────────────────────────────")
        while not self.is_closed():
            try:
                raw = await asyncio.to_thread(input, f"{green}LOGRELAY {purple}» {reset}")
            except (EOFError, KeyboardInterrupt):
                print("\n終端輸入已結束；Bot 仍會繼續運作。")
                return
            text = raw.strip()
            if not text:
                continue
            command = text.casefold()
            speech = self.get_cog("SpeechControlCog")
            if command in ("/help", "help", "?"):
                print("一般文字=傳送到目前頻道；/say 文字=傳送；/reply @成員 文字=提及並回覆；/speak 文字=語音朗讀。")
                print("/servers；/use 伺服器名稱或 ID；/channels；/channel 頻道名稱；/status。")
                print("/dm、/nick、/role、/timeout、/kick、/ban、/unban、/purge 等管理指令仍可用。")
                print("/join、/mic、/stop、/leave 控制語音；/exit 關閉控制介面。")
            elif command == "/exit":
                print("終端控制介面已關閉，Bot 會繼續運作。要關閉整個 Bot 請按 Ctrl+C。")
                return
            elif command == "/join":
                guild, member, channel = await speech._find_owner_voice() if speech else (None, None, None)
                if not channel:
                    print("請先用你的 Discord 帳號加入一個語音頻道。")
                    continue
                try:
                    vc = guild.voice_client
                    if not vc or not vc.is_connected():
                        await channel.connect()
                    elif vc.channel != channel:
                        await vc.move_to(channel)
                    print(f"LogRelay 已加入：{guild.name} / {channel.name}")
                except Exception as exc:
                    print(f"連接語音失敗：{type(exc).__name__}: {exc}")
            elif command == "/mic":
                print(await speech.start_mic_from_terminal() if speech else "語音模組尚未載入。")
            elif command == "/stop":
                print(await speech.stop_from_terminal() if speech else "語音模組尚未載入。")
            elif command == "/leave":
                print(await speech.leave_from_terminal() if speech else "語音模組尚未載入。")
            elif command == "/servers" or text.startswith((
                "/use", "/status", "/channels", "/channel", "/say", "/reply", "/mention",
                "/dm ", "/members ", "/roles ", "/nick ", "/role ", "/timeout ",
                "/kick ", "/ban ", "/unban ", "/purge "
            )):
                print(await self._terminal_discord_command(text))
            elif text.startswith("/speak "):
                print(await speech.speak_from_terminal(text[7:].strip()) if speech else "語音模組尚未載入。")
            elif text.startswith("/"):
                print("未知指令。輸入 /help 查看。")
            else:
                print(await self._terminal_discord_command("/say --current " + text))


    async def _terminal_discord_command(self, raw: str) -> str:
        command_parts = raw.strip().split(maxsplit=1)
        if not command_parts:
            return "請輸入指令。"
        cmd = command_parts[0].casefold()

        async def get_guild(raw_id):
            try:
                guild_id = int(raw_id)
            except ValueError:
                return None, "伺服器 ID 必須是數字。"
            guild = self.get_guild(guild_id)
            return (guild, None) if guild else (None, "找不到伺服器，或 Bot 尚未加入。")

        async def get_member(guild, raw_id):
            try:
                member_id = int(raw_id)
            except ValueError:
                return None, "成員 ID 必須是數字。"
            member = guild.get_member(member_id)
            if member is None:
                try:
                    member = await guild.fetch_member(member_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    member = None
            return (member, None) if member else (None, "找不到這位成員，或 Bot 無法讀取其資料。")

        def bot_member(guild):
            return guild.me or (guild.get_member(self.user.id) if self.user else None)

        def hierarchy_ok(guild, target):
            me = bot_member(guild)
            return bool(me and target.id != guild.owner_id and me.top_role > target.top_role)

        def no_perm(action):
            return f"操作失敗：Bot 缺少「{action}」權限，或目標的身分組階級不低於 Bot。"

        if cmd == "/servers":
            return "Bot 已加入的伺服器：\n" + "\n".join(
                f"{g.name} | ID: {g.id}" for g in self.guilds
            ) if self.guilds else "Bot 目前不在任何伺服器。"

        if cmd == "/status":
            guild = self.get_guild(self._terminal_guild_id) if self._terminal_guild_id else None
            channel = self.get_channel(self._terminal_channel_id) if self._terminal_channel_id else None
