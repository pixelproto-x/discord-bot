import asyncio
import os
import re
import traceback
import time
from pathlib import Path

from dotenv import load_dotenv
from web_dashboard import start_dashboard
from localization import TraditionalChineseTranslator

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
    browser_mic,
    marriage,
)


OWNER_ID = int(os.getenv("OWNER_ID", "1140900506198351924"))
ALLOWED_ROLE_ID = int(os.getenv("ALLOWED_ROLE_ID", "1509577038443319416"))
MAX_COUNT = 100
MAX_TOTAL_SPAM_MESSAGES = 100

MAX_CONTENT_LEN = 2000


ORIGINAL_INTERACTION_SEND_MESSAGE = None
ORIGINAL_WEBHOOK_SEND = None


class ChooseVisibilityView(discord.ui.View):
    def __init__(self, owner_id: int, channel_id: int | None, content, send_kwargs: dict):
        super().__init__(timeout=900)
        self.owner_id = owner_id
        self.channel_id = channel_id
        self.content = content
        self.send_kwargs = dict(send_kwargs)
        self.send_kwargs.pop("ephemeral", None)
        self.send_kwargs.pop("thinking", None)
        self.send_kwargs.pop("wait", None)
        self.send_kwargs.pop("delete_after", None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await ORIGINAL_INTERACTION_SEND_MESSAGE(
            interaction.response,
            "只有發起這則指令的人可以選擇訊息公開方式。",
            ephemeral=True,
        )
        return False

    @discord.ui.button(label="公開發送", style=discord.ButtonStyle.primary, emoji="📢")
    async def send_public(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.guild.get_channel(self.channel_id) if interaction.guild and self.channel_id else None
        if channel is None and interaction.channel is not None and interaction.channel.id == self.channel_id:
            channel = interaction.channel
        if channel is None or not hasattr(channel, "send"):
            await ORIGINAL_INTERACTION_SEND_MESSAGE(
                interaction.response, "找不到原本的頻道，無法公開發送。", ephemeral=True
            )
            return
        try:
            await channel.send(**self.send_kwargs)
            await ORIGINAL_INTERACTION_SEND_MESSAGE(
                interaction.response, "已公開發送。", ephemeral=True
            )
            self.stop()
        except (discord.Forbidden, discord.HTTPException, TypeError, ValueError):
            await ORIGINAL_INTERACTION_SEND_MESSAGE(
                interaction.response,
                "公開發送失敗，請確認機器人有在原頻道發送訊息的權限。",
                ephemeral=True,
            )

    @discord.ui.button(label="僅自己可見", style=discord.ButtonStyle.secondary)
    async def send_private(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            private_kwargs = dict(self.send_kwargs)
            private_kwargs["ephemeral"] = True
            await ORIGINAL_INTERACTION_SEND_MESSAGE(
                interaction.response,
                **private_kwargs,
            )
            self.stop()
        except (discord.Forbidden, discord.HTTPException, TypeError, ValueError):
            await ORIGINAL_INTERACTION_SEND_MESSAGE(
                interaction.response, "私密發送失敗，請重新執行指令。", ephemeral=True
            )


def install_ephemeral_publish_buttons():
    # Ask for visibility before the command's actual response content is sent.
    global ORIGINAL_INTERACTION_SEND_MESSAGE, ORIGINAL_WEBHOOK_SEND
    response_cls = discord.InteractionResponse
    if not getattr(response_cls, "_visibility_choice_wrapped", False):
        ORIGINAL_INTERACTION_SEND_MESSAGE = response_cls.send_message

        async def send_message_with_visibility_choice(self, *args, **kwargs):
            if kwargs.get("ephemeral", False) and not kwargs.get("_skip_visibility_choice", False):
                content = kwargs.get("content", args[0] if args else None)
                parent = getattr(self, "_parent", None)
                original_kwargs = dict(kwargs)
                original_kwargs.pop("ephemeral", None)
                original_kwargs.pop("_skip_visibility_choice", None)
                if args and "content" not in original_kwargs:
                    args_content = args[0]
                    original_kwargs["content"] = args_content
                view = ChooseVisibilityView(
                    getattr(getattr(parent, "user", None), "id", 0),
                    getattr(getattr(parent, "channel", None), "id", None),
                    content,
                    original_kwargs,
                )
                return await ORIGINAL_INTERACTION_SEND_MESSAGE(
                    self,
                    "這則回覆尚未發送。請先選擇要公開發送，還是僅自己可見。",
                    ephemeral=True,
                    view=view,
                )
            return await ORIGINAL_INTERACTION_SEND_MESSAGE(self, *args, **kwargs)

        response_cls.send_message = send_message_with_visibility_choice
        response_cls._visibility_choice_wrapped = True

    webhook_cls = discord.Webhook
    if not getattr(webhook_cls, "_visibility_choice_wrapped", False):
        ORIGINAL_WEBHOOK_SEND = webhook_cls.send

        async def webhook_send_with_visibility_choice(self, *args, **kwargs):
            if kwargs.get("ephemeral", False):
                content = kwargs.get("content", args[0] if args else None)
                parent = getattr(self, "_parent", None)
                original_kwargs = dict(kwargs)
                original_kwargs.pop("ephemeral", None)
                if args and "content" not in original_kwargs:
                    original_kwargs["content"] = args[0]
                user = getattr(parent, "user", None)
                channel = getattr(parent, "channel", None)
                view = ChooseVisibilityView(
                    getattr(user, "id", 0),
                    getattr(channel, "id", None),
                    content,
                    original_kwargs,
                )
                return await ORIGINAL_WEBHOOK_SEND(
                    self,
                    "這則回覆尚未發送。請先選擇要公開發送，還是僅自己可見。",
                    ephemeral=True,
                    view=view,
                )
            return await ORIGINAL_WEBHOOK_SEND(self, *args, **kwargs)

        webhook_cls.send = webhook_send_with_visibility_choice
        webhook_cls._visibility_choice_wrapped = True


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
        # 只要 Bot 已加入該伺服器，就允許使用斜線指令。
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
        install_ephemeral_publish_buttons()
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
            browser_mic,
            marriage,
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

        await self.tree.set_translator(TraditionalChineseTranslator())

        for attempt in range(3):
            try:
                # 清除舊版本可能留下的伺服器專屬指令，避免與全域指令重複。
                for guild_id in sorted(ALLOWED_GUILD_IDS):
                    guild = discord.Object(id=guild_id)
                    self.tree.clear_commands(guild=guild)
                    await self.tree.sync(guild=guild)
                synced = await self.tree.sync()
                print(f"Synced {len(synced)} global slash commands (available in every joined guild)")
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
            print(f"[Guild] {guild.name} | ID={guild.id} | SLASH COMMANDS ENABLED (global)")
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
            guild_label = guild.name if guild else "未選擇"
            channel_label = f"#{channel.name}" if isinstance(channel, (discord.TextChannel, discord.Thread)) else "未選擇"
            return (
                f"CORE STATUS: {'ONLINE' if self.is_ready() else 'CONNECTING'}\n"
                f"Bot: {self.user}\n伺服器: {guild_label}\n頻道: {channel_label}\n"
                f"已連線伺服器數: {len(self.guilds)}"
            )

        if cmd == "/use":
            args = raw.strip().split(maxsplit=1)
            if len(args) == 1:
                return "可用伺服器：\n" + "\n".join(
                    f"{g.name} | ID: {g.id}" for g in self.guilds
                ) + "\n用法：/use 伺服器名稱或 ID"
            query = args[1].strip().strip('"').casefold()
            matches = [g for g in self.guilds if str(g.id) == query or g.name.casefold() == query]
            if not matches:
                matches = [g for g in self.guilds if query in g.name.casefold()]
            if len(matches) != 1:
                if not matches:
                    return "找不到符合的伺服器。輸入 /servers 查看完整列表。"
                return "名稱符合多個伺服器，請輸入完整名稱或 ID：\n" + "\n".join(
                    f"{g.name} | ID: {g.id}" for g in matches[:20]
                )
            guild = matches[0]
            self._terminal_guild_id = guild.id
            self._terminal_channel_id = None
            return f"已選擇伺服器：{guild.name}\n接著輸入 /channels，再用 /channel 頻道名稱選擇頻道。"

        if cmd == "/channels":
            args = raw.strip().split(maxsplit=1)
            guild = self.get_guild(self._terminal_guild_id) if self._terminal_guild_id else None
            if len(args) > 1:
                query = args[1].strip().strip('"')
                guild = self.get_guild(int(query)) if query.isdecimal() else next(
                    (g for g in self.guilds if g.name.casefold() == query.casefold()), None
                )
                if guild is None and not query.isdecimal():
                    candidates = [g for g in self.guilds if query.casefold() in g.name.casefold()]
                    guild = candidates[0] if len(candidates) == 1 else None
                if guild is None:
                    return "找不到伺服器。輸入 /servers 查看列表。"
                self._terminal_guild_id = guild.id
            if guild is None:
                return "先輸入 /use 伺服器名稱選擇伺服器，或輸入 /channels 伺服器名稱。"
            rows = [
                f"{'#' if isinstance(channel, (discord.TextChannel, discord.Thread)) else '🔊'} {channel.name}"
                for channel in guild.channels
                if isinstance(channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel, discord.StageChannel, discord.ForumChannel))
            ]
            return f"{guild.name} 的頻道：\n" + "\n".join(rows[:100]) + "\n用 /channel 頻道名稱選擇文字頻道。"

        if cmd == "/channel":
            args = raw.strip().split(maxsplit=1)
            if len(args) < 2:
                channel = self.get_channel(self._terminal_channel_id) if self._terminal_channel_id else None
                return f"目前頻道：#{channel.name}" if isinstance(channel, (discord.TextChannel, discord.Thread)) else "用法：/channel 頻道名稱"
            guild = self.get_guild(self._terminal_guild_id) if self._terminal_guild_id else None
            if guild is None:
                return "請先輸入 /use 伺服器名稱選擇伺服器。"
            query = args[1].strip().lstrip("#").strip('"').casefold()
            candidates = [
                c for c in guild.channels
                if isinstance(c, (discord.TextChannel, discord.Thread)) and c.name.casefold() == query
            ]
            if not candidates:
                candidates = [
                    c for c in guild.channels
                    if isinstance(c, (discord.TextChannel, discord.Thread)) and query in c.name.casefold()
                ]
            if len(candidates) != 1:
                if not candidates:
                    return "找不到文字頻道。輸入 /channels 查看可用頻道。"
                return "頻道名稱符合多個結果，請輸入完整名稱：\n" + "\n".join(f"#{c.name}" for c in candidates[:20])
            channel = candidates[0]
            me = bot_member(guild)
            perms = channel.permissions_for(me) if me else None
            if not perms or not perms.view_channel or not perms.send_messages:
                return "Bot 沒有查看或傳送訊息到這個頻道的權限。"
            self._terminal_channel_id = channel.id
            return f"已選擇頻道：{guild.name} / #{channel.name}\n現在直接輸入文字即可傳送。"

        if cmd == "/roles":
            args = raw.strip().split(maxsplit=1)
            if len(args) < 2:
                return "用法：/roles 伺服器ID"
            guild, err = await get_guild(args[1])
            if err:
                return err
            rows = [
                f"{role.name} | ID: {role.id} | 階級: {role.position}"
                for role in sorted(guild.roles, key=lambda r: r.position, reverse=True)[:100]
            ]
            return "身分組列表：\n" + "\n".join(rows)

        if cmd == "/members":
            args = raw.strip().split(maxsplit=2)
            if len(args) < 2:
                return "用法：/members 伺服器ID [搜尋文字]"
            guild, err = await get_guild(args[1])
            if err:
                return err
            query = args[2].casefold() if len(args) > 2 else ""
            members = [
                member for member in guild.members
                if not query or query in member.display_name.casefold() or query in str(member.id)
            ]
            rows = [f"{m.display_name} (@{m.name}) | ID: {m.id}" for m in members[:80]]
            suffix = "\n（只列出 Bot 目前快取到的成員；若要完整名單，需在 Developer Portal 啟用 Server Members Intent。）"
            return ("成員列表：\n" + "\n".join(rows) if rows else "沒有符合的快取成員。") + suffix

        async def send_terminal_message(channel, message, target_member=None):
            message = message.strip()
            if not message or len(message) > 2000:
                return "訊息不能空白，且一次最多 2000 個字。"
            if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                return "請先選擇文字頻道：/use 伺服器名稱，再輸入 /channel 頻道名稱。"
            me = bot_member(channel.guild)
            perms = channel.permissions_for(me) if me else None
            can_send = bool(perms and (
                perms.send_messages or (isinstance(channel, discord.Thread) and perms.send_messages_in_threads)
            ))
            if not perms or not perms.view_channel or not can_send:
                return no_perm("查看頻道／傳送訊息")
            if target_member:
                message = f"{target_member.mention} {message}"
            try:
                explicit_mass_mention = ("@everyone" in message or "@here" in message) and bool(perms.mention_everyone)
                await channel.send(
                    message,
                    allowed_mentions=discord.AllowedMentions(
                        everyone=explicit_mass_mention,
                        users=True,
                        roles=False,
                        replied_user=False,
                    ),
                )
                return f"已由 LogRelay 傳送到 {channel.guild.name} / #{channel.name}。"
            except discord.HTTPException as exc:
                return f"傳送失敗：{exc}"

        if cmd in ("/say", "/reply", "/mention"):
            args = raw.strip().split(maxsplit=2)
            channel = self.get_channel(self._terminal_channel_id) if self._terminal_channel_id else None
            target_member = None
            if cmd in ("/reply", "/mention"):
                match = re.match(r'^/(?:reply|mention)\s+(@(?:"[^"]+"|\S+))\s+(.+)$', raw.strip(), re.DOTALL)
                if not match:
                    return '用法：/reply @成員 文字（名稱含空格請用 @"顯示名稱" 文字）'
                guild = self.get_guild(self._terminal_guild_id) if self._terminal_guild_id else None
                if guild is None:
                    return "請先用 /use 伺服器名稱選擇伺服器。"
                target_query = match.group(1).strip()
                if target_query.startswith("@"):
                    target_query = target_query[1:]
                target_query = target_query.strip('"').casefold()
                matches = [
                    m for m in guild.members
                    if m.name.casefold() == target_query
                    or m.display_name.casefold() == target_query
                    or str(m.id) == target_query
                ]
                if not matches:
                    matches = [
                        m for m in guild.members
                        if target_query in m.name.casefold() or target_query in m.display_name.casefold()
                    ]
                if len(matches) != 1:
                    if not matches:
                        return "找不到這位成員。請確認 Bot 已啟用 Server Members Intent，或改用成員 ID。"
                    return "成員名稱符合多個結果，請使用完整名稱或 ID：\n" + "\n".join(
                        f"{m.display_name} (@{m.name}) | ID: {m.id}" for m in matches[:20]
                    )
                target_member = matches[0]
                message = match.group(2).strip()
            elif len(args) >= 3 and args[1] == "--current":
                message = args[2]
            elif len(args) >= 3 and args[1].isdecimal():
                try:
                    legacy_channel = self.get_channel(int(args[1])) or await self.fetch_channel(int(args[1]))
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    return "找不到頻道，或 Bot 無權讀取。"
                if not isinstance(legacy_channel, (discord.TextChannel, discord.Thread)):
                    return "目前只支援文字頻道或討論串。"
                channel = legacy_channel
                message = args[2]
            else:
                message = raw.strip()[len(args[0]):].strip()
                if not message:
                    return "用法：/say 文字（需先選擇頻道）。"
            if channel is None and self._terminal_channel_id:
                try:
                    channel = await self.fetch_channel(self._terminal_channel_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    channel = None
            return await send_terminal_message(channel, message, target_member)

        if cmd == "/dm":
            args = raw.strip().split(maxsplit=2)
            if len(args) < 3:
                return "用法：/dm 使用者ID 要傳送的私訊"
            try:
                user_id = int(args[1])
            except ValueError:
                return "使用者 ID 必須是數字。"
            message = args[2].strip()
            if not message or len(message) > 2000:
                return "私訊不能空白，且一次最多 2000 個字。"
            try:
                user = self.get_user(user_id) or await self.fetch_user(user_id)
                await user.send(message, allowed_mentions=discord.AllowedMentions.none())
                return f"已送出私訊給 {user}；若對方封鎖 Bot 私訊，則會失敗。"
            except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
                return f"私訊沒有送出：{exc}"

        if cmd == "/nick":
            args = raw.strip().split(maxsplit=3)
            if len(args) < 4:
                return "用法：/nick 伺服器ID 成員ID 新暱稱（要清空暱稱請用 -）"
            guild, err = await get_guild(args[1])
            if err:
                return err
            member, err = await get_member(guild, args[2])
            if err:
                return err
            me = bot_member(guild)
            if not me or not me.guild_permissions.manage_nicknames or not hierarchy_ok(guild, member):
                return no_perm("管理暱稱")
            try:
                await member.edit(nick=None if args[3] == "-" else args[3], reason="由本機終端控制")
                return f"已更新 {member} 的伺服器暱稱。"
            except discord.HTTPException as exc:
                return f"更改暱稱失敗：{exc}"

        if cmd == "/role":
            args = raw.strip().split()
            if len(args) != 5 or args[1].casefold() not in ("add", "remove"):
                return "用法：/role add|remove 伺服器ID 成員ID 身分組ID"
            action, guild_id, member_id, role_id = args[1:]
            guild, err = await get_guild(guild_id)
            if err:
                return err
            member, err = await get_member(guild, member_id)
            if err:
                return err
            try:
                role = guild.get_role(int(role_id))
            except ValueError:
                role = None
            me = bot_member(guild)
            if not role:
                return "找不到這個身分組。"
            if not me or not me.guild_permissions.manage_roles or not hierarchy_ok(guild, member) or me.top_role <= role:
                return no_perm("管理身分組")
            try:
                if action.casefold() == "add":
                    await member.add_roles(role, reason="由本機終端控制")
                    return f"已把身分組「{role.name}」加給 {member}。"
                await member.remove_roles(role, reason="由本機終端控制")
                return f"已移除 {member} 的身分組「{role.name}」。"
            except discord.HTTPException as exc:
                return f"身分組操作失敗：{exc}"

        if cmd in ("/timeout", "/kick", "/ban"):
            args = raw.strip().split(maxsplit=4)
            if cmd == "/timeout":
                if len(args) < 4:
                    return "用法：/timeout 伺服器ID 成員ID 分鐘 [原因]"
                guild, err = await get_guild(args[1])
                if err:
                    return err
                member, err = await get_member(guild, args[2])
                if err:
                    return err
                try:
                    minutes = int(args[3])
                except ValueError:
                    return "分鐘必須是數字。"
                if not 1 <= minutes <= 40320:
                    return "分鐘必須介於 1 至 40320（最多 28 天）。"
                me = bot_member(guild)
                if not me or not me.guild_permissions.moderate_members or not hierarchy_ok(guild, member):
                    return no_perm("暫停成員")
                reason = args[4] if len(args) > 4 else "由本機終端控制"
                try:
                    from datetime import timedelta
                    await member.timeout(timedelta(minutes=minutes), reason=reason[:512])
                    return f"已暫停 {member} {minutes} 分鐘。"
                except discord.HTTPException as exc:
                    return f"暫停成員失敗：{exc}"
            if len(args) < 3:
                return f"用法：{cmd} 伺服器ID 成員ID [原因]"
            guild, err = await get_guild(args[1])
            if err:
                return err
            member, err = await get_member(guild, args[2])
            if err:
                return err
            me = bot_member(guild)
            if not me or not hierarchy_ok(guild, member):
                return "Bot 無法操作伺服器擁有者或身分組階級高於／等於 Bot 的成員。"
            reason = args[3] if len(args) > 3 else "由本機終端控制"
            try:
                if cmd == "/kick":
                    if not me.guild_permissions.kick_members:
                        return no_perm("踢出成員")
                    await member.kick(reason=reason[:512])
                    return f"已踢出成員 {member}。"
                if not me.guild_permissions.ban_members:
                    return no_perm("封鎖成員")
                await member.ban(reason=reason[:512])
                return f"已封鎖成員 {member}。"
            except discord.HTTPException as exc:
                return f"{cmd} 操作失敗：{exc}"

        if cmd == "/unban":
            args = raw.strip().split(maxsplit=3)
            if len(args) < 3:
                return "用法：/unban 伺服器ID 使用者ID [原因]"
            guild, err = await get_guild(args[1])
            if err:
                return err
            me = bot_member(guild)
            if not me or not me.guild_permissions.ban_members:
                return no_perm("解除封鎖")
            try:
                await guild.unban(discord.Object(id=int(args[2])), reason=args[3] if len(args) > 3 else "由本機終端控制")
                return f"已送出解除封鎖請求：{args[2]}。"
            except (ValueError, discord.HTTPException) as exc:
                return f"解除封鎖失敗：{exc}"

        if cmd == "/purge":
            args = raw.strip().split(maxsplit=2)
            if len(args) < 3:
                return "用法：/purge 頻道ID 數量（1 到 100）"
            try:
                channel_id, count = int(args[1]), int(args[2])
            except ValueError:
                return "頻道 ID 和數量都必須是數字。"
            if not 1 <= count <= 100:
                return "一次只能清理 1 到 100 則訊息。"
            channel = self.get_channel(channel_id)
            if channel is None:
                return "找不到頻道；先用 /channels 查詢。"
            if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                return "目前只支援文字頻道或討論串。"
            me = bot_member(channel.guild)
            perms = channel.permissions_for(me) if me else None
            if not perms or not perms.manage_messages or not perms.read_message_history:
                return no_perm("管理訊息／讀取歷史訊息")
            try:
                deleted = await channel.purge(limit=count)
                return f"已清理 #{channel.name} 的 {len(deleted)} 則訊息。"
            except discord.HTTPException as exc:
                return f"清理訊息失敗：{exc}"

        return "未知的 Discord 操作指令。輸入 /help 查看用法。"


    async def close(self):
        reminder_worker.cancel()
        for job_key in list(active_spam):
            active_spam[job_key]["running"] = False
        await super().close()


bot = Bot()
bot._relay_suppressed_delete_log_ids = set()

# Server-wide message relay control: only the configured owner can toggle it.
# When enabled for a guild, human members' messages are relayed with their own names/avatars.
_RELAY_CONTROL_OWNER_ID = 1140900506198351924
_relay_webhooks: dict[int, discord.Webhook] = {}
_relay_webhook_locks: dict[int, asyncio.Lock] = {}
_relay_enabled_cache: dict[int, bool] = {}
_relay_warm_tasks: dict[int, asyncio.Task] = {}


async def _get_relay_webhook(channel) -> discord.Webhook | None:
    # Threads use a webhook belonging to their parent text channel.
    webhook_channel = channel.parent if isinstance(channel, discord.Thread) else channel
    if (
        webhook_channel is None
        or not hasattr(webhook_channel, "webhooks")
        or not hasattr(webhook_channel, "create_webhook")
    ):
        return None

    cached = _relay_webhooks.get(webhook_channel.id)
    if cached is not None:
        return cached

    lock = _relay_webhook_locks.setdefault(webhook_channel.id, asyncio.Lock())
    async with lock:
        cached = _relay_webhooks.get(webhook_channel.id)
        if cached is not None:
            return cached
        hooks = await webhook_channel.webhooks()
        webhook = next(
            (hook for hook in hooks if hook.name == "Transparent Message Relay"),
            None,
        )
        if webhook is None:
            webhook = await webhook_channel.create_webhook(name="Transparent Message Relay")
        _relay_webhooks[webhook_channel.id] = webhook
        return webhook


async def _warm_relay_guild(guild: discord.Guild, exclude_channel_id: int | None = None):
    semaphore = asyncio.Semaphore(3)

    async def prepare(channel: discord.TextChannel):
        if channel.id == exclude_channel_id or channel.id in _relay_webhooks:
            return
        me = guild.me
        perms = channel.permissions_for(me) if me else None
        if (
            not perms
            or not perms.view_channel
            or not perms.send_messages
            or not perms.manage_webhooks
            or not perms.manage_messages
        ):
            return
        try:
            async with semaphore:
                await _get_relay_webhook(channel)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            return

    await asyncio.gather(
        *(prepare(channel) for channel in guild.text_channels),
        return_exceptions=True,
    )


def _schedule_relay_warm(guild: discord.Guild, exclude_channel_id: int | None = None):
    current = _relay_warm_tasks.get(guild.id)
    if current is not None and not current.done():
        return
    task = asyncio.create_task(_warm_relay_guild(guild, exclude_channel_id))
    _relay_warm_tasks[guild.id] = task
    task.add_done_callback(
        lambda completed, guild_id=guild.id:
            _relay_warm_tasks.pop(guild_id, None)
            if _relay_warm_tasks.get(guild_id) is completed else None
    )


@bot.tree.command(name=app_commands.locale_str("relay", zh_TW="訊息轉發"), description=app_commands.locale_str("開啟或關閉伺服器訊息模仿功能", zh_TW="開啟或關閉伺服器訊息模仿功能"))
@app_commands.describe(action="選擇開啟或關閉")
@app_commands.choices(action=[
    app_commands.Choice(name="開啟", value="on"),
    app_commands.Choice(name="關閉", value="off"),
])
async def relay_slash(interaction: discord.Interaction, action: app_commands.Choice[str]):
    # Slash-command responses are ephemeral: visible only to the person who ran it,
    # while the actual relayed messages remain in their original server channels.
    if interaction.user.id != _RELAY_CONTROL_OWNER_ID:
        await interaction.response.send_message("你沒有權限控制這項功能。", ephemeral=True)
        return
    if interaction.guild is None or not isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
        await interaction.response.send_message("請在伺服器文字頻道使用這個指令。", ephemeral=True)
        return

    enabled = action.value == "on"
    if enabled:
        me = interaction.guild.me
        perms = interaction.channel.permissions_for(me) if me else None
        if (
            not perms
            or not perms.view_channel
            or not perms.send_messages
            or not perms.manage_webhooks
            or not perms.manage_messages
            or not perms.read_message_history
        ):
            await interaction.response.send_message(
                "無法開啟：目前頻道需要查看頻道、傳送訊息、管理 Webhook、管理訊息及讀取訊息歷史權限。",
                ephemeral=True,
            )
            return
        try:
            webhook = await _get_relay_webhook(interaction.channel)
            if webhook is None:
                await interaction.response.send_message("無法建立轉發 Webhook，請確認頻道類型及 Bot 權限。", ephemeral=True)
                return
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            print(f"[Relay control] webhook setup failed in channel {interaction.channel.id}: {type(exc).__name__}: {exc}")
            await interaction.response.send_message(
                f"無法準備轉發服務（{type(exc).__name__}），請確認 Bot 有管理 Webhook 權限。",
                ephemeral=True,
            )
            return

    try:
        await asyncio.to_thread(
            storage.set_setting, interaction.guild.id, "owner_message_relay_enabled", enabled
        )
        _relay_enabled_cache[interaction.guild.id] = enabled
        if enabled:
            _schedule_relay_warm(interaction.guild, exclude_channel_id=interaction.channel.id)
    except Exception as exc:
        print(f"[Relay control] setting save failed for guild {interaction.guild.id}: {type(exc).__name__}: {exc}")
        await interaction.response.send_message("設定儲存失敗，請查看 Bot 控制台錯誤紀錄。", ephemeral=True)
        return

    await interaction.response.send_message(
        "已在此伺服器開啟訊息模仿功能。轉發訊息會留在各自原本的頻道。" if enabled
        else "已在此伺服器關閉訊息模仿功能。",
        ephemeral=True,
    )


@bot.command(name="bot", hidden=True)
async def server_message_relay(ctx: commands.Context, action: str = ""):
    """Legacy prefix command is intentionally silent; use /relay for private status."""
    try:
        await ctx.message.delete()
    except discord.NotFound:
        return
    except (discord.Forbidden, discord.HTTPException) as exc:
        print(f"[Relay control] could not delete !bot command message: {type(exc).__name__}: {exc}")
        return
    # Prefix commands cannot produce ephemeral responses. Do not DM the owner:
    # /relay is the supported control and returns a server-only ephemeral response.


@bot.listen("on_reaction_add")
async def mirror_member_reaction(reaction: discord.Reaction, user: discord.User | discord.Member):
    """Mirror a member's emoji reaction on the same message once."""
    if user.bot or reaction.message.guild is None:
        return
    try:
        await reaction.message.add_reaction(reaction.emoji)
        print(
            f"[Reaction mirror] guild={reaction.message.guild.id} "
            f"channel={reaction.message.channel.id} message={reaction.message.id}"
        )
    except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
        print(
            f"[Reaction mirror failed] guild={reaction.message.guild.id} "
            f"channel={reaction.message.channel.id} message={reaction.message.id}: "
            f"{type(exc).__name__}: {exc}"
        )


@bot.listen("on_message")
async def block_sticker_messages(message: discord.Message):
    """Delete every human message containing any Discord sticker, including server stickers."""
    if message.guild is None or message.author.bot or message.webhook_id or not message.stickers:
        return
    try:
        await message.delete()
        print(
            f"[Sticker blocked] guild={message.guild.id} channel={message.channel.id} "
            f"user={message.author.id} stickers={len(message.stickers)}"
        )
    except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
        print(
            f"[Sticker block failed] guild={message.guild.id} channel={message.channel.id} "
            f"message={message.id}: {type(exc).__name__}: {exc}"
        )


@bot.listen("on_message")
async def transparent_message_relay(message: discord.Message):
    if message.guild is None or message.author.bot or message.webhook_id or message.stickers:
        return
    # Relay messages from all human members in this guild. Never relay commands,
    # bots, or webhook messages, so command handling and the relay cannot loop.
    if message.content.lstrip().startswith("!"):
        return
    guild_id = message.guild.id
    enabled = _relay_enabled_cache.get(guild_id)
    if enabled is None:
        try:
            enabled = await asyncio.to_thread(
                storage.get_setting, guild_id, "owner_message_relay_enabled", False
            )
            _relay_enabled_cache[guild_id] = bool(enabled)
        except Exception:
            traceback.print_exc()
            return
    if not enabled:
        return

    # Keep a backend registry of human users encountered while message mimic is enabled.
    # The database enforces one record per Discord user ID across all servers.
    try:
        await asyncio.to_thread(
            storage.record_relay_seen_user,
            message.author.id,
            message.author.name,
            message.author.display_name,
            guild_id,
        )
    except Exception as exc:
        print(
            f"[Relay user registry] failed for user={message.author.id}: "
            f"{type(exc).__name__}: {exc}"
        )

    channel = message.channel
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        print(f"[Relay skipped] unsupported channel type: guild={guild_id} channel={getattr(channel, 'id', None)} type={type(channel).__name__}")
        return
    perms = channel.permissions_for(message.guild.me) if message.guild.me else None
    if (
        not perms
        or not perms.view_channel
        or not perms.send_messages
        or not perms.manage_webhooks
        or not perms.manage_messages
    ):
        print(
            f"[Relay blocked] missing permissions: guild={guild_id} channel={channel.id} "
            f"view={bool(perms and perms.view_channel)} send={bool(perms and perms.send_messages)} "
            f"manage_webhooks={bool(perms and perms.manage_webhooks)} "
            f"manage_messages={bool(perms and perms.manage_messages)}"
        )
        return
    webhook_channel = channel.parent if isinstance(channel, discord.Thread) else channel
    if (
        webhook_channel is None
        or not hasattr(webhook_channel, "webhooks")
        or not hasattr(webhook_channel, "create_webhook")
    ):
        return

    relay_started = time.perf_counter()
    try:
        # Webhooks are cached and warmed ahead of time. Threads share their parent's webhook.
        webhook_channel = channel.parent if isinstance(channel, discord.Thread) else channel
        webhook = _relay_webhooks.get(webhook_channel.id) if webhook_channel else None
        if webhook is None:
            webhook = await _get_relay_webhook(channel)
        if webhook is None:
            print(f"[Relay failed] no webhook available: guild={guild_id} channel={channel.id}")
            return

        parts = [message.content] if message.content else []
        relay_files = []
        try:
            # Download media before deleting the source message so the repost contains real files.
            for attachment in message.attachments[:10]:
                try:
                    relay_files.append(await attachment.to_file(use_cached=True))
                except (discord.HTTPException, discord.NotFound, discord.Forbidden, OSError) as media_exc:
                    parts.append(f"附件下載失敗：{attachment.filename}")
                    print(f"[Relay media] failed to download {attachment.filename}: {type(media_exc).__name__}")
            if not parts and not relay_files:
                parts.append("（此訊息沒有可轉發的文字或附件內容）")

            payload = "\n\n".join(parts)
            if len(payload) > 2000:
                payload = payload[:1900] + "\n…（內容過長，部分內容省略）"

            # Confirm the mimic message was posted before deleting the original.
            send_kwargs = {
                "content": payload or None,
                "files": relay_files,
                "username": message.author.display_name,
                "avatar_url": message.author.display_avatar.url,
                "allowed_mentions": discord.AllowedMentions.none(),
                 **({"thread": channel} if isinstance(channel, discord.Thread) else {}),
                "wait": True,
            }
            sent = False
            sent_message = None
            referenced_id = getattr(message.reference, "message_id", None) if message.reference else None
            resolved_reference = getattr(message.reference, "resolved", None) if message.reference else None
            # Keep every relayed message under the original author's webhook identity.
            # Webhooks cannot create Discord-native replies, so preserve reply context
            # as visible text instead of switching the sender to the bot account.
            if referenced_id and (
                referenced_id in _relay_message_map.values()
                or getattr(resolved_reference, "webhook_id", None)
            ):
                relayed_target_id = referenced_id
            else:
                relayed_target_id = _relay_message_map.get(referenced_id) if referenced_id else None

            if relayed_target_id:
                target_author = getattr(resolved_reference, "author", None)
                target_name = getattr(target_author, "display_name", None) or getattr(target_author, "name", None)
                reply_label = f"↪ 回覆「{target_name}」：" if target_name else "↪ 回覆上一則模仿訊息："
                send_kwargs["content"] = f"{reply_label}\\n{payload}" if payload else reply_label

            if not sent:
                try:
                    sent_message = await webhook.send(**send_kwargs)
                    sent = True
                except (discord.Forbidden, discord.NotFound, discord.HTTPException) as first_exc:
                    _relay_webhooks.pop(webhook_channel.id if webhook_channel else channel.id, None)
                    print(
                        f"Webhook relay attempt failed in channel {channel.id}: "
                        f"{type(first_exc).__name__}; refreshing webhook and retrying"
                    )
                    try:
                        webhook = await _get_relay_webhook(channel)
                        if webhook is not None:
                            for relay_file in relay_files:
                                try:
                                    relay_file.fp.seek(0)
                                except (AttributeError, OSError):
                                    pass
                            sent_message = await webhook.send(**send_kwargs)
                            sent = True
                    except (discord.Forbidden, discord.NotFound, discord.HTTPException) as retry_exc:
                        _relay_webhooks.pop(webhook_channel.id if webhook_channel else channel.id, None)
                        print(
                            f"Webhook relay retry failed in channel {channel.id}: "
                            f"{type(retry_exc).__name__}; original message kept"
                        )

            if not sent:
                print(f"[Relay failed] webhook send failed; original preserved: guild={guild_id} channel={channel.id} message={message.id}")
                return

            if sent_message is not None:
                _relay_message_map[message.id] = sent_message.id
                while len(_relay_message_map) > _RELAY_MESSAGE_MAP_LIMIT:
                    _relay_message_map.pop(next(iter(_relay_message_map)))

            delete_started = time.perf_counter()
            suppressed_ids = getattr(bot, "_relay_suppressed_delete_log_ids", None)
            if suppressed_ids is not None:
                suppressed_ids.add(message.id)
                while len(suppressed_ids) > 5000:
                    suppressed_ids.pop()
            try:
                await message.delete()
                delete_ms = (time.perf_counter() - delete_started) * 1000
                total_ms = (time.perf_counter() - relay_started) * 1000
            except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
                if suppressed_ids is not None:
                    suppressed_ids.discard(message.id)
                print(
                    f"Relay posted but original message could not be deleted in channel {channel.id}: "
                    f"{type(exc).__name__}"
                )
        finally:
            for relay_file in relay_files:
                try:
                    relay_file.close()
                except Exception:
                    pass
    except (discord.Forbidden, discord.NotFound) as exc:
        _relay_webhooks.pop(webhook_channel.id if webhook_channel else channel.id, None)
        print(f"Transparent relay unavailable in channel {channel.id}: {exc}")
    except discord.HTTPException as exc:
        print(f"[Relay HTTP error] guild={guild_id} channel={channel.id}: {type(exc).__name__} status={getattr(exc, 'status', None)} code={getattr(exc, 'code', None)} message={exc}")
    except Exception:
        print(f"[Relay unexpected error] guild={guild_id} channel={getattr(channel, 'id', None)}")
        traceback.print_exc()


async def run_spam(job_key: tuple[int, int], notify_channel, target_channels: list, content: str, count: int):
    user_id = job_key[1]
    sent_count = 0
    failed_channels = set()

    async def notify(msg: str):
        if notify_channel:
            try:
                await notify_channel.send(f"<@{user_id}> {msg}")
            except Exception:
                pass

    try:
        for _ in range(count):
            if not active_spam.get(job_key, {}).get("running", False):
                await notify(f"好，停掉了。剛剛大概送了 {sent_count} 則。")
                return

            for ch in target_channels:
                if ch.id in failed_channels:
                    continue
                while True:
                    if not active_spam.get(job_key, {}).get("running", False):
                        await notify(f"好，停掉了。剛剛大概送了 {sent_count} 則。")
                        return
                    try:
                        await ch.send(content, allowed_mentions=discord.AllowedMentions.none())
                        sent_count += 1
                        
                        await asyncio.sleep(0.2)
                        break
                    except discord.HTTPException as exc:
                        if exc.status == 429:
                            await asyncio.sleep(min(float(getattr(exc, "retry_after", 2.0)), 10.0))
                        else:
                            failed_channels.add(ch.id)
                            await notify(f"{ch.mention} 發不出去，我先跳過這個頻道。")
                            break
            await asyncio.sleep(0.01)

        await notify(f"好了，總共送了 {sent_count} 則。")
    except asyncio.CancelledError:
        raise
    except Exception:
        await notify(f"這次中間出了點問題，已停止。剛剛送了 {sent_count} 則。")
    finally:
        active_spam.pop(job_key, None)


def can_spam(interaction: discord.Interaction) -> bool:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return False
    member = interaction.user
    return (
        member.guild_permissions.administrator
        or member.id == OWNER_ID
        or any(role.id == ALLOWED_ROLE_ID for role in member.roles)
    )


@bot.tree.command(name=app_commands.locale_str("spam", zh_TW="多頻道發送"), description=app_commands.locale_str("在多個頻道快速發送同一句訊息", zh_TW="在多個頻道快速發送同一句訊息"))
@app_commands.describe(content="要發的內容", count="發送次數")
async def spam(interaction: discord.Interaction, content: str, count: int):
    if not interaction.guild:
        await interaction.response.send_message("這個只能在伺服器裡用。", ephemeral=True)
        return
    if not can_spam(interaction):
        await interaction.response.send_message("你沒有這個功能的權限。", ephemeral=True)
        return
    job_key = (interaction.guild.id, interaction.user.id)
    if job_key in active_spam:
        await interaction.response.send_message("你已經有一個正在跑的，先停掉再開新的。", ephemeral=True)
        return
    if not 1 <= count <= MAX_COUNT:
        await interaction.response.send_message(f"次數要在 1 到 {MAX_COUNT} 之間。", ephemeral=True)
        return
    if not content or len(content) > MAX_CONTENT_LEN:
        await interaction.response.send_message("內容太長，或是根本沒內容。", ephemeral=True)
        return

    view = ChannelSelectView()
    active_spam[job_key] = {"running": False, "task": None, "cancelled": False, "view": view}
    await interaction.response.send_message("要發去哪幾個頻道？選完我再開始。", view=view, ephemeral=True)
    await view.wait()

    if active_spam.get(job_key, {}).get("cancelled"):
        active_spam.pop(job_key, None)
        await interaction.followup.send("選擇頻道時已取消這次工作。", ephemeral=True)
        return

    if not view.selected_channels:
        active_spam.pop(job_key, None)
        await interaction.followup.send("算了，這次沒有選頻道。", ephemeral=True)
        return

    guild_me = interaction.guild.get_member(bot.user.id)
    if not guild_me:
        active_spam.pop(job_key, None)
        await interaction.followup.send("我找不到自己的成員資料，重試一下。", ephemeral=True)
        return

    valid_channels = []
    skipped = []
    for selected in view.selected_channels:
        resolved = selected.resolve() or interaction.guild.get_channel(selected.id)
        if resolved and isinstance(resolved, (discord.TextChannel, discord.Thread)):
            perms = resolved.permissions_for(guild_me)
            send_allowed = perms.send_messages or getattr(perms, "send_messages_in_threads", False)
            if perms.view_channel and send_allowed:
                valid_channels.append(resolved)
            else:
                skipped.append(resolved.name)
        else:
            skipped.append(str(selected.id))
    if not valid_channels:
        active_spam.pop(job_key, None)
        await interaction.followup.send("選的頻道我都沒有足夠權限。", ephemeral=True)
        return

    total_messages = count * len(valid_channels)
    if total_messages > MAX_TOTAL_SPAM_MESSAGES:
        active_spam.pop(job_key, None)
        max_rounds = max(1, MAX_TOTAL_SPAM_MESSAGES // len(valid_channels))
        await interaction.followup.send(
            f"單次工作最多送出 {MAX_TOTAL_SPAM_MESSAGES} 則訊息。"
            f"你選了 {len(valid_channels)} 個頻道，請把次數調到 {max_rounds} 次以下，或減少頻道數。",
            ephemeral=True,
        )
        return

    if active_spam.get(job_key, {}).get("cancelled"):
        active_spam.pop(job_key, None)
        await interaction.followup.send("選擇頻道時已取消這次工作。", ephemeral=True)
        return

    active_spam[job_key] = {"running": True, "task": None, "cancelled": False, "view": view}
    embed = discord.Embed(
        title="好，開始了",
        description=f"次數：{count}\n頻道：{', '.join(c.mention for c in valid_channels)}",
        color=0x57F287,
    )
    if skipped:
        embed.add_field(name="我跳過了", value=", ".join(skipped), inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)

    if active_spam.get(job_key, {}).get("cancelled"):
        active_spam.pop(job_key, None)
        return

    task = asyncio.create_task(
        run_spam(job_key, interaction.channel, valid_channels, content, count)
    )
    active_spam[job_key]["task"] = task


@bot.tree.command(name=app_commands.locale_str("stopspam", zh_TW="停止發送"), description=app_commands.locale_str("停止自己或有權限時停止別人的發送工作", zh_TW="停止自己或有權限時停止別人的發送工作"))
@app_commands.describe(member="要停止誰，不填就是自己")
async def stopspam(interaction: discord.Interaction, member: discord.Member | None = None):
    target = member or interaction.user
    if not interaction.guild:
        await interaction.response.send_message("這個只能在伺服器裡用。", ephemeral=True)
        return
    invoker = interaction.guild.get_member(interaction.user.id)
    if not invoker:
        await interaction.response.send_message("我找不到你的成員資料。", ephemeral=True)
        return
    privileged = (
        invoker.guild_permissions.administrator
        or invoker.id == OWNER_ID
        or any(role.id == ALLOWED_ROLE_ID for role in invoker.roles)
    )
    if target.id != interaction.user.id and not privileged:
        await interaction.response.send_message("你不能停別人的工作。", ephemeral=True)
        return

    target_key = (interaction.guild.id, target.id)
    if target_key in active_spam:
        state = active_spam[target_key]
        state["running"] = False
        state["cancelled"] = True
        view = state.get("view")
        if view and not view.is_finished():
            view.stop()
        task = state.get("task")
        if task and not task.done():
            task.cancel()
        await interaction.response.send_message(f"好，{target.mention} 的工作停掉了。", ephemeral=True)
    else:
        await interaction.response.send_message("他現在沒有正在跑的發送工作。", ephemeral=True)


@bot.tree.command(name=app_commands.locale_str("history_cleanup", zh_TW="清理歷史訊息"), description=app_commands.locale_str("搜尋並清掉歷史訊息", zh_TW="搜尋並清掉歷史訊息"))
@app_commands.describe(
    count="搜尋範圍 1 到 1000",
    member="只處理這位成員",
    content="只處理包含這段文字的訊息",
    ch1="頻道 1",
    ch2="頻道 2",
    ch3="頻道 3",
)
async def history_cmd(
    interaction: discord.Interaction,
    count: app_commands.Range[int, 1, 1000],
    ch1: discord.TextChannel | None = None,
    ch2: discord.TextChannel | None = None,
    ch3: discord.TextChannel | None = None,
    member: discord.Member | None = None,
    content: str | None = None,
):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("這個只能在伺服器裡用。", ephemeral=True)
        return
    invoker = interaction.user
    if not (
        invoker.guild_permissions.administrator
        or invoker.id == OWNER_ID
        or any(role.id == ALLOWED_ROLE_ID for role in invoker.roles)
    ):
        await interaction.response.send_message("你沒有清理歷史訊息的權限。", ephemeral=True)
        return

    channels = [c for c in [ch1, ch2, ch3] if c] or [interaction.channel]
    guild_me = interaction.guild.get_member(bot.user.id)
    if not guild_me:
        await interaction.response.send_message("我找不到自己的成員資料。", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    total = 0
    skipped = []
    for ch in channels:
        if not isinstance(ch, discord.TextChannel):
            continue
        perms = ch.permissions_for(guild_me)
        if not perms.manage_messages or not perms.read_message_history:
            skipped.append(ch.mention)
            continue

        def check(msg):
            if member and msg.author.id != member.id:
                return False
            return not content or content.casefold() in msg.content.casefold()

        try:
            deleted = await ch.purge(limit=count, check=check)
            total += len(deleted)
        except discord.HTTPException:
            skipped.append(ch.mention)

    msg = f"處理完了，共清掉 **{total}** 則。"
    if skipped:
        msg += "\n這些頻道我沒辦法處理：" + " ".join(skipped)
    await interaction.followup.send(msg, ephemeral=True)


@bot.tree.error
async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    print("Slash command error:", repr(error))
    msg = "剛剛那一下沒成功，你再試一次看看。"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass


@tasks.loop(seconds=15)
async def reminder_worker():
    try:
        for row in await asyncio.to_thread(storage.due_reminders):
            reminder_text = f"<@{row['user_id']}> ⏰ 你之前叫我提醒你的：{row['message']}"
            try:
                channel = bot.get_channel(row['channel_id'])
                if channel is None:
                    channel = await bot.fetch_channel(row['channel_id'])
                await channel.send(
                    reminder_text,
                    allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=int(row['user_id']))]),
                )
                await asyncio.to_thread(storage.complete_reminder, row['id'])
            except (discord.NotFound, discord.Forbidden):
                
                
                try:
                    user = bot.get_user(row['user_id']) or await bot.fetch_user(row['user_id'])
                    await user.send(f"⏰ 你之前叫我提醒你的：{row['message']}\n（原本的提醒頻道目前無法使用。）")
                except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                    pass
                await asyncio.to_thread(storage.complete_reminder, row['id'])
            except discord.HTTPException as exc:
                print(f"Reminder delivery failed for {row['id']}: {exc}")
                continue
            except Exception:
                traceback.print_exc()
                continue
        now = __import__("time").time()
        for row in await asyncio.to_thread(storage.list_active_polls):
            if row["ends_at"] and row["ends_at"] <= now:
                await asyncio.to_thread(storage.close_poll, row["id"])
                channel = bot.get_channel(row["channel_id"])
                if channel and row["message_id"]:
                    try:
                        msg = await channel.fetch_message(row["message_id"])
                        counts = await asyncio.to_thread(storage.get_poll_counts, row["id"])
                        embed = social.PollCog.build_embed(row, counts)
                        embed.set_footer(text="投票結束")
                        await msg.edit(embed=embed, view=None)
                    except Exception:
                        pass

        for row in await asyncio.to_thread(storage.list_active_giveaways):
            if row["ends_at"] <= now:
                guild = bot.get_guild(row["guild_id"])
                if guild:
                    cog = bot.get_cog("GiveawayCog")
                    if cog:
                        await cog.finish(row["id"], guild)
    except Exception:
        traceback.print_exc()


@reminder_worker.before_loop
async def before_reminder_worker():
    await bot.wait_until_ready()


def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN 未設定。")
    bot.run(token)


if __name__ == "__main__":
    main()