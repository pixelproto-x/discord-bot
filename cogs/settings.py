import json

import discord
from discord import app_commands
from discord.ext import commands

import storage
from cogs.social import is_safe_self_assignable_role



def autorole_is_safe(role, bot_member) -> bool:
    return bool(
        role
        and bot_member
        and bot_member.guild_permissions.manage_roles
        and is_safe_self_assignable_role(role)
        and role < bot_member.top_role
    )


SETTINGS_CATEGORIES = [
    ("security", "安全防護", "短時間刷屏、邀請連結、大量大寫與標記上限"),
    ("notifications", "紀錄／語音", "管理紀錄頻道及語音進出通知"),
    ("message_logs", "訊息刪除／編輯", "把成員刪除或編輯的訊息記錄到指定頻道"),
    ("welcome", "歡迎／離開", "新人歡迎、成員離開通知與訊息"),
    ("roles", "新人身分組", "設定新成員自動取得的身分組"),
]


class SettingsPanelView(discord.ui.View):
    def __init__(self, bot, guild_id, page="security"):
        super().__init__(timeout=900)
        self.bot = bot
        self.guild_id = guild_id
        self.page = page if page in {item[0] for item in SETTINGS_CATEGORIES} else "security"
        self.add_item(SettingsCategorySelect(self))
        if self.page == "security":
            flood = int(storage.get_setting(self.guild_id, "flood_limit", 0) or 0)
            invites = bool(storage.get_setting(self.guild_id, "block_invites", False))
            caps = bool(storage.get_setting(self.guild_id, "block_caps", False))
            self.add_item(SettingsActionButton(
                self, "🛡️ 短時間防刷：" + ("開" if flood else "關"),
                "toggle_flood", 1, discord.ButtonStyle.success if flood else discord.ButtonStyle.secondary,
            ))
            self.add_item(SettingsActionButton(
                self, "🔗 邀請連結攔截：" + ("開" if invites else "關"),
                "toggle_invites", 1, discord.ButtonStyle.success if invites else discord.ButtonStyle.secondary,
            ))
            self.add_item(SettingsActionButton(
                self, "🔠 大量大寫攔截：" + ("開" if caps else "關"),
                "toggle_caps", 1, discord.ButtonStyle.success if caps else discord.ButtonStyle.secondary,
            ))
            self.add_item(SettingsFloodLimitSelect(self))
            self.add_item(SettingsMentionLimitSelect(self))
        elif self.page == "notifications":
            self.add_item(SettingsChannelSelect(self, "log_channel_id", "選擇管理紀錄頻道", 1))
            self.add_item(SettingsActionButton(
                self, "關閉管理紀錄", "clear_log", 2, discord.ButtonStyle.danger,
            ))
            self.add_item(SettingsChannelSelect(self, "voice_notice_channel_id", "選擇語音通知頻道", 3))
            self.add_item(SettingsActionButton(
                self, "關閉語音通知", "clear_voice", 4, discord.ButtonStyle.danger,
            ))
        elif self.page == "message_logs":
            self.add_item(SettingsChannelSelect(self, "message_log_channel_id", "選擇訊息刪除／編輯紀錄頻道", 1))
            self.add_item(SettingsActionButton(
                self, "關閉訊息刪除／編輯紀錄", "clear_message_logs", 2, discord.ButtonStyle.danger,
            ))
        elif self.page == "welcome":
            self.add_item(SettingsChannelSelect(self, "welcome_channel_id", "選擇新人歡迎頻道", 1))
            self.add_item(SettingsActionButton(
                self, "關閉歡迎", "clear_welcome", 2, discord.ButtonStyle.danger,
            ))
            self.add_item(SettingsActionButton(
                self, "編輯歡迎訊息", "edit_welcome", 2, discord.ButtonStyle.primary,
            ))
            self.add_item(SettingsChannelSelect(self, "goodbye_channel_id", "選擇成員離開通知頻道", 3))
            self.add_item(SettingsActionButton(
                self, "關閉離開通知", "clear_goodbye", 4, discord.ButtonStyle.danger,
            ))
            self.add_item(SettingsActionButton(
                self, "編輯離開訊息", "edit_goodbye", 4, discord.ButtonStyle.primary,
            ))
        elif self.page == "roles":
            self.add_item(SettingsRoleSelect(self))
            self.add_item(SettingsActionButton(
                self, "關閉新人身分組", "clear_autorole", 2, discord.ButtonStyle.danger,
            ))

    @classmethod
    def for_guild(cls, bot, guild, page="security"):
        return cls(bot, guild.id, page)

    def authorized(self, interaction):
        member = interaction.user
        return bool(
            interaction.guild
            and interaction.guild.id == self.guild_id
            and isinstance(member, discord.Member)
            and (member.guild_permissions.administrator or member.guild_permissions.manage_guild)
        )

    async def require_admin(self, interaction):
        if self.authorized(interaction):
            return True
        try:
            await interaction.response.send_message(
                "只有具備管理伺服器或管理員權限的成員能操作設定面板。",
                ephemeral=True,
            )
        except discord.InteractionResponded:
            pass
        return False

    def make_embed(self, guild):
        embed = discord.Embed(
            title=f"⚙️ {guild.name}｜伺服器設定中心",
            description="使用上方選單切換分類；按鈕或選單變更會立即儲存至 Bot 資料庫。只有具備管理伺服器／管理員權限的成員可以操作。",
            color=0x5865F2,
        )
        if self.page == "security":
            flood = int(storage.get_setting(guild.id, "flood_limit", 0) or 0)
            invites = bool(storage.get_setting(guild.id, "block_invites", False))
            caps = bool(storage.get_setting(guild.id, "block_caps", False))
            mention_limit = int(storage.get_setting(guild.id, "mention_limit", 5) or 0)
            flood_text = f"開啟（6 秒內 {flood} 則）" if flood else "關閉"
            mention_text = "關閉" if mention_limit <= 0 else f"{mention_limit} 個"
            embed.add_field(
                name="安全防護狀態",
                value=(
                    f"短時間防刷：**{flood_text}**\n"
                    f"邀請連結攔截：**{'開啟' if invites else '關閉'}**\n"
                    f"大量大寫攔截：**{'開啟' if caps else '關閉'}**\n"
                    f"單則訊息標記上限：**{mention_text}**"
                ),
                inline=False,
            )
            embed.add_field(
                name="操作提示",
                value="點擊前三個按鈕切換防護；下拉選單可以調整刷屏門檻及 @ 標記上限。設定標記上限時也會嘗試同步 Discord AutoMod。",
                inline=False,
            )
        elif self.page == "notifications":
            embed.add_field(name="管理紀錄", value=self._channel_mention(guild, "log_channel_id"), inline=False)
            embed.add_field(name="語音進出通知", value=self._channel_mention(guild, "voice_notice_channel_id"), inline=False)
            embed.add_field(
                name="說明",
                value="選擇文字頻道會立即套用；若選單中沒有頻道，請確認 Bot 能查看並在該頻道發訊息。",
                inline=False,
            )
        elif self.page == "message_logs":
            embed.add_field(
                name="訊息紀錄頻道",
                value=self._channel_mention(guild, "message_log_channel_id"),
                inline=False,
            )
            embed.add_field(
                name="紀錄內容",
                value="有人刪除訊息時會記錄原有文字、作者、頻道及訊息 ID；有人編輯訊息時會記錄修改前與修改後的文字。選擇頻道即啟用，按「關閉」即可停用。",
                inline=False,
            )
            embed.add_field(
                name="注意",
                value="Bot 只能記錄它實際收到或快取到的內容；無法還原 Bot 加入前或未取得的已刪除訊息。",
                inline=False,
            )
        elif self.page == "welcome":
            welcome_message = str(storage.get_setting(guild.id, "welcome_message", "歡迎 {user} 來到 {server}！"))
            goodbye_message = str(storage.get_setting(guild.id, "goodbye_message", "{user} 離開了，掰。"))
            embed.add_field(name="新人歡迎頻道", value=self._channel_mention(guild, "welcome_channel_id"), inline=False)
            embed.add_field(name="目前歡迎訊息", value=self._preview(welcome_message), inline=False)
            embed.add_field(name="成員離開通知頻道", value=self._channel_mention(guild, "goodbye_channel_id"), inline=False)
            embed.add_field(name="目前離開訊息", value=self._preview(goodbye_message), inline=False)
            embed.add_field(name="可用變數", value="{user} 代表成員，{server} 代表伺服器名稱。", inline=False)
        elif self.page == "roles":
            role_id = storage.get_setting(guild.id, "autorole_id", 0)
            role = guild.get_role(int(role_id)) if role_id else None
            embed.add_field(name="新人自動身分組", value=role.mention if role else "關閉", inline=False)
            embed.add_field(
                name="安全限制",
                value="面板只允許選擇可安全自動發放、且位階低於 Bot 最高身分組的身分組。",
                inline=False,
            )
        embed.set_footer(text="設定會存入 SQLite，Bot 重啟後仍會保留。")
        return embed

    def _channel_mention(self, guild, key):
        channel_id = storage.get_setting(guild.id, key)
        channel = None
        if channel_id:
            try:
                channel = guild.get_channel(int(channel_id))
            except (TypeError, ValueError):
                channel = None
        return channel.mention if channel else "關閉／尚未設定"

    @staticmethod
    def _preview(value):
        value = value.strip() or "（空白訊息）"
        return value[:900] + ("…" if len(value) > 900 else "")

    async def refresh(self, interaction):
        view = SettingsPanelView.for_guild(self.bot, interaction.guild, self.page)
        await interaction.response.edit_message(embed=view.make_embed(interaction.guild), view=view)


class SettingsCategorySelect(discord.ui.Select):
    def __init__(self, panel):
        self.panel = panel
        options = [
            discord.SelectOption(label=label, value=key, description=description, default=key == panel.page)
            for key, label, description in SETTINGS_CATEGORIES
        ]
        super().__init__(
            placeholder="選擇設定分類",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="server_settings:category",
            row=0,
        )

    async def callback(self, interaction):
        if not await self.panel.require_admin(interaction):
            return
        view = SettingsPanelView.for_guild(self.panel.bot, interaction.guild, self.values[0])
        await interaction.response.edit_message(embed=view.make_embed(interaction.guild), view=view)


class SettingsActionButton(discord.ui.Button):
    def __init__(self, panel, label, action, row, style=discord.ButtonStyle.secondary):
        self.panel = panel
        self.action = action
        super().__init__(label=label, style=style, row=row, custom_id=f"server_settings:{action}")

    async def callback(self, interaction):
        if not await self.panel.require_admin(interaction):
            return
        guild_id = interaction.guild.id
        if self.action == "toggle_flood":
            current = int(storage.get_setting(guild_id, "flood_limit", 0) or 0)
            storage.set_setting(guild_id, "flood_limit", 0 if current else 8)
        elif self.action == "toggle_invites":
            storage.set_setting(guild_id, "block_invites", not bool(storage.get_setting(guild_id, "block_invites", False)))
        elif self.action == "toggle_caps":
            storage.set_setting(guild_id, "block_caps", not bool(storage.get_setting(guild_id, "block_caps", False)))
        elif self.action == "clear_log":
            storage.set_setting(guild_id, "log_channel_id", None)
        elif self.action == "clear_message_logs":
            storage.set_setting(guild_id, "message_log_channel_id", None)
        elif self.action == "clear_voice":
            storage.set_setting(guild_id, "voice_notice_channel_id", 0)
        elif self.action == "clear_welcome":
            storage.set_setting(guild_id, "welcome_channel_id", 0)
        elif self.action == "clear_goodbye":
            storage.set_setting(guild_id, "goodbye_channel_id", 0)
        elif self.action == "clear_autorole":
            storage.set_setting(guild_id, "autorole_id", 0)
        elif self.action in {"edit_welcome", "edit_goodbye"}:
            key = "welcome_message" if self.action == "edit_welcome" else "goodbye_message"
            title = "編輯新人歡迎訊息" if self.action == "edit_welcome" else "編輯成員離開訊息"
            default = "歡迎 {user} 來到 {server}！" if key == "welcome_message" else "{user} 離開了，掰。"
            current = str(storage.get_setting(guild_id, key, default))
            modal = SettingsMessageModal(self.panel, key, title, current[:1500] or default)
            modal.panel_message = interaction.message
            await interaction.response.send_modal(modal)
            return
        await self.panel.refresh(interaction)


class SettingsFloodLimitSelect(discord.ui.Select):
    def __init__(self, panel):
        self.panel = panel
        current = int(storage.get_setting(panel.guild_id, "flood_limit", 0) or 0)
        values = [0, 3, 5, 8, 10, 15, 20, 30]
        if current > 0 and current not in values:
            values.append(current)
        options = [
            discord.SelectOption(
                label="關閉" if value == 0 else f"6 秒內 {value} 則",
                value=str(value),
                default=value == current,
            )
            for value in sorted(values)
        ]
        super().__init__(
            placeholder="調整短時間防刷門檻",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="server_settings:flood_limit",
            row=2,
        )

    async def callback(self, interaction):
        if not await self.panel.require_admin(interaction):
            return
        value = int(self.values[0])
        storage.set_setting(interaction.guild.id, "flood_limit", value)
        await self.panel.refresh(interaction)


class SettingsMentionLimitSelect(discord.ui.Select):
    def __init__(self, panel):
        self.panel = panel
        current = int(storage.get_setting(panel.guild_id, "mention_limit", 5) or 0)
        values = [0, 3, 5, 10, 15, 20, 30, 50]
        if 0 < current <= 50 and current not in values:
            values.append(current)
        options = [
            discord.SelectOption(
                label="關閉" if value == 0 else f"最多標記 {value} 個",
                value=str(value),
                description="關閉此上限" if value == 0 else f"每則訊息最多標記 {value} 個人／身分組",
                default=value == current,
            )
            for value in sorted(values)
        ]
        super().__init__(
            placeholder="調整 @ 標記上限（同步 Discord AutoMod）",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="server_settings:mention_limit",
            row=3,
        )

    async def callback(self, interaction):
        if not await self.panel.require_admin(interaction):
            return
        limit = int(self.values[0])
        moderation = self.panel.bot.get_cog("ModerationCog")
        if moderation is None:
            await interaction.response.send_message("防護模組目前未載入，沒有變更設定。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await moderation.sync_mention_automod(interaction.guild, limit)
        except discord.Forbidden:
            await interaction.followup.send("Discord 拒絕更新 AutoMod；標記上限未變更，請確認 Bot 的管理伺服器／AutoMod 權限。", ephemeral=True)
            return
        except discord.HTTPException:
            await interaction.followup.send("Discord AutoMod 更新失敗；標記上限未變更，請稍後重試。", ephemeral=True)
            return
        except Exception:
            await interaction.followup.send("這次無法同步 Discord AutoMod；標記上限未變更。", ephemeral=True)
            return
        storage.set_setting(interaction.guild.id, "mention_limit", limit)
        await interaction.followup.send(
            "已關閉 @ 標記上限。" if limit == 0 else f"已設定每則訊息最多標記 {limit} 個，並已同步 Discord AutoMod。",
            ephemeral=True,
        )
        view = SettingsPanelView.for_guild(self.panel.bot, interaction.guild, self.panel.page)
        try:
            await interaction.message.edit(embed=view.make_embed(interaction.guild), view=view)
        except (discord.HTTPException, AttributeError):
            pass


class SettingsChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, panel, key, placeholder, row):
        self.panel = panel
        self.key = key
        super().__init__(
            placeholder=placeholder,
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
            custom_id=f"server_settings:{key}",
            row=row,
        )

    async def callback(self, interaction):
        if not await self.panel.require_admin(interaction):
            return
        guild = interaction.guild
        selected = self.values[0] if self.values else None
        channel_id = getattr(selected, "id", None)
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel is None and channel_id:
            try:
                channel = await self.panel.bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                channel = None
        bot_member = guild.me or (
            guild.get_member(self.panel.bot.user.id)
            if self.panel.bot.user else None
        )
        if (
            not isinstance(channel, discord.TextChannel)
            or channel.guild.id != guild.id
            or bot_member is None
        ):
            await interaction.response.send_message(
                "無法確認這個文字頻道或 Bot 的伺服器身分，設定沒有變更。請重新開啟 /settings show 再選一次。",
                ephemeral=True,
            )
            return
        perms = channel.permissions_for(bot_member)
        if not perms.view_channel or not perms.send_messages:
            await interaction.response.send_message(
                "Bot 在這個頻道缺少查看頻道或傳送訊息權限，設定沒有變更。",
                ephemeral=True,
            )
            return
        storage.set_setting(guild.id, self.key, channel.id)
        await self.panel.refresh(interaction)


class SettingsRoleSelect(discord.ui.RoleSelect):
    def __init__(self, panel):
        self.panel = panel
        super().__init__(
            placeholder="選擇新人自動身分組",
            min_values=1,
            max_values=1,
            custom_id="server_settings:autorole_id",
            row=1,
        )

    async def callback(self, interaction):
        if not await self.panel.require_admin(interaction):
            return
        role = self.values[0]
        bot_member = interaction.guild.me
        if (
            not autorole_is_safe(role, bot_member)
        ):
            await interaction.response.send_message(
                "這個身分組不能安全自動發放，或 Bot 缺少管理身分組權限／身分組位階太低。設定沒有變更。",
                ephemeral=True,
            )
            return
        storage.set_setting(interaction.guild.id, "autorole_id", role.id)
        await self.panel.refresh(interaction)


class SettingsMessageModal(discord.ui.Modal):
    def __init__(self, panel, key, title, current):
        super().__init__(title=title, timeout=300)
        self.panel = panel
        self.key = key
        self.message_input = discord.ui.TextInput(
            label="訊息內容（可用 {user} 與 {server}）",
            style=discord.TextStyle.paragraph,
            max_length=1500,
            required=True,
            default=current[:1500],
        )
        self.add_item(self.message_input)
        self.panel_message = None

    async def on_submit(self, interaction):
        if not self.panel.authorized(interaction):
            await interaction.response.send_message("只有具備管理伺服器或管理員權限的成員能操作設定面板。", ephemeral=True)
            return
        message = self.message_input.value.strip()
        if not message:
            await interaction.response.send_message("訊息不能是空白，設定沒有變更。", ephemeral=True)
            return
        storage.set_setting(interaction.guild.id, self.key, message[:1500])
        view = SettingsPanelView.for_guild(self.panel.bot, interaction.guild, self.panel.page)
        if self.panel_message:
            try:
                await self.panel_message.edit(embed=view.make_embed(interaction.guild), view=view)
            except discord.HTTPException:
                pass
        await interaction.response.send_message("訊息已儲存，之後的歡迎／離開通知會使用新內容。", ephemeral=True)



class SettingsCog(commands.Cog):
    settings = app_commands.Group(name=app_commands.locale_str("settings", zh_TW="伺服器設定"), description=app_commands.locale_str("管理 Bot 在這個伺服器的設定", zh_TW="管理 Bot 在這個伺服器的設定"))
    note = app_commands.Group(name=app_commands.locale_str("note", zh_TW="備忘錄"), description=app_commands.locale_str("自己的小筆記，Bot 幫你記著", zh_TW="自己的小筆記，Bot 幫你記著"))

    def __init__(self, bot):
        self.bot = bot

    def admin(self, interaction):
        return bool(
            interaction.guild
            and isinstance(interaction.user, discord.Member)
            and (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild)
        )

    @settings.command(name=app_commands.locale_str("show", zh_TW="查看設定"), description=app_commands.locale_str("開啟互動式伺服器設定中心", zh_TW="開啟互動式伺服器設定中心"))
    async def show(self, interaction):
        if not self.admin(interaction):
            await interaction.response.send_message("這個只有管理人員能操作。", ephemeral=True)
            return
        view = SettingsPanelView.for_guild(self.bot, interaction.guild)
        await interaction.response.send_message(
            embed=view.make_embed(interaction.guild),
            view=view,
            ephemeral=True,
        )

    @settings.command(name=app_commands.locale_str("autorole", zh_TW="自動身分組"), description=app_commands.locale_str("設定新人加入後自動拿到的身分組", zh_TW="設定新人加入後自動拿到的身分組"))
    @app_commands.describe(role="新人要拿的身分組", public=app_commands.locale_str("是否公開顯示設定結果", zh_TW="是否公開顯示設定結果"))
    @app_commands.rename(public=app_commands.locale_str("公開", zh_TW="公開"))
    async def autorole(self, interaction, role: discord.Role | None = None, public: bool = True):
        if not self.admin(interaction):
            await interaction.response.send_message("這個設定要管理伺服器權限。", ephemeral=True)
            return
        if role:
            bot_member = interaction.guild.me
            if not autorole_is_safe(role, bot_member):
                await interaction.response.send_message("這個身分組不能自動發放（可能有管理權限），或它比 Bot 高，或 Bot 缺少管理身分組權限。", ephemeral=True)
                return
        storage.set_setting(interaction.guild.id, "autorole_id", role.id if role else 0)
        await interaction.response.send_message(f"新人身分組已{'設成 ' + role.mention if role else '關閉'}。", ephemeral=not public)

    @settings.command(name=app_commands.locale_str("welcome", zh_TW="歡迎訊息"), description=app_commands.locale_str("設定新人加入時的歡迎訊息", zh_TW="設定新人加入時的歡迎訊息"))
    @app_commands.describe(channel="歡迎頻道", message="訊息，可用 {user} 和 {server}", public=app_commands.locale_str("是否公開顯示設定結果", zh_TW="是否公開顯示設定結果"))
    @app_commands.rename(public=app_commands.locale_str("公開", zh_TW="公開"))
    async def welcome(self, interaction, channel: discord.TextChannel | None = None, message: str = "歡迎 {user} 來到 {server}！", public: bool = True):
        if not self.admin(interaction):
            await interaction.response.send_message("這個設定要管理伺服器權限。", ephemeral=True)
            return
        if channel:
            perms = channel.permissions_for(interaction.guild.me)
            if not perms.view_channel or not perms.send_messages:
                await interaction.response.send_message("我不能在那個頻道發歡迎訊息，先檢查 Bot 的頻道權限。", ephemeral=True)
                return
        message = message.strip()
        if not message or len(message) > 1500:
            await interaction.response.send_message("歡迎訊息不能空白，最多 1500 字；設定沒有變更。", ephemeral=True)
            return
        storage.set_setting(interaction.guild.id, "welcome_channel_id", channel.id if channel else 0)
        storage.set_setting(interaction.guild.id, "welcome_message", message)
        await interaction.response.send_message(
            f"歡迎訊息{'開好了，會發到 ' + channel.mention if channel else '關掉了'}。",
            ephemeral=True,
        )

    @settings.command(name=app_commands.locale_str("goodbye", zh_TW="離開訊息"), description=app_commands.locale_str("設定成員離開時的通知", zh_TW="設定成員離開時的通知"))
    @app_commands.describe(channel="離開通知頻道", message="訊息，可用 {user} 和 {server}", public=app_commands.locale_str("是否公開顯示設定結果", zh_TW="是否公開顯示設定結果"))
    @app_commands.rename(public=app_commands.locale_str("公開", zh_TW="公開"))
    async def goodbye(self, interaction, channel: discord.TextChannel | None = None, message: str = "{user} 離開了，掰。", public: bool = True):
        if not self.admin(interaction):
            await interaction.response.send_message("這個設定要管理伺服器權限。", ephemeral=True)
            return
        if channel:
            perms = channel.permissions_for(interaction.guild.me)
            if not perms.view_channel or not perms.send_messages:
                await interaction.response.send_message("我不能在那個頻道發離開通知，先檢查 Bot 的頻道權限。", ephemeral=True)
                return
        message = message.strip()
        if not message or len(message) > 1500:
            await interaction.response.send_message("離開通知不能空白，最多 1500 字；設定沒有變更。", ephemeral=True)
            return
        storage.set_setting(interaction.guild.id, "goodbye_channel_id", channel.id if channel else 0)
        storage.set_setting(interaction.guild.id, "goodbye_message", message)
        await interaction.response.send_message(
            f"離開通知{'開好了，會發到 ' + channel.mention if channel else '關掉了'}。",
            ephemeral=True,
        )

    @note.command(name=app_commands.locale_str("add", zh_TW="新增"), description=app_commands.locale_str("存一筆只有自己看得到的筆記", zh_TW="存一筆只有自己看得到的筆記"))
    @app_commands.describe(title="筆記標題", content="筆記內容")
    async def note_add(self, interaction, title: str, content: str):
        if not interaction.guild:
            await interaction.response.send_message("這個要在伺服器裡用。", ephemeral=True)
            return
        if not title.strip() or not content.strip() or len(content) > 1800:
            await interaction.response.send_message("標題和內容不能空白，內容最多 1800 字。", ephemeral=True)
            return
        nid = storage.add_note(interaction.guild.id, interaction.user.id, title.strip()[:100], content.strip()[:1800])
        await interaction.response.send_message(f"記好了，筆記編號 {nid}。", ephemeral=True)

    @note.command(name=app_commands.locale_str("list", zh_TW="列表"), description=app_commands.locale_str("看看自己存過哪些筆記", zh_TW="看看自己存過哪些筆記"))
    async def note_list(self, interaction):
        if not interaction.guild:
            await interaction.response.send_message("這個要在伺服器裡用。", ephemeral=True)
            return
        rows = storage.list_notes(interaction.guild.id, interaction.user.id)
        if not rows:
            await interaction.response.send_message("你還沒有筆記。", ephemeral=True)
            return
        text = "\n".join(f"{r['id']}｜{r['title'][:80]}" for r in rows[:15])
        await interaction.response.send_message(text, ephemeral=True)

    @note.command(name=app_commands.locale_str("show", zh_TW="查看設定"), description=app_commands.locale_str("查看自己的指定筆記", zh_TW="查看自己的指定筆記"))
    @app_commands.describe(note_id="筆記編號")
    async def note_show(self, interaction, note_id: int):
        if not interaction.guild:
            await interaction.response.send_message("這個要在伺服器裡用。", ephemeral=True)
            return
        row = storage.get_note(interaction.guild.id, interaction.user.id, note_id)
        if not row:
            await interaction.response.send_message("找不到這篇筆記。", ephemeral=True)
            return
        await interaction.response.send_message(f"📝 **{row['title']}**\n{row['content'][:1800]}", ephemeral=True)

    @note.command(name=app_commands.locale_str("delete", zh_TW="刪除"), description=app_commands.locale_str("刪掉自己的指定筆記", zh_TW="刪掉自己的指定筆記"))
    @app_commands.describe(note_id="筆記編號")
    async def note_delete(self, interaction, note_id: int):
        if not interaction.guild:
            await interaction.response.send_message("這個要在伺服器裡用。", ephemeral=True)
            return
        ok = storage.delete_note(interaction.guild.id, interaction.user.id, note_id)
        await interaction.response.send_message("刪掉了。" if ok else "找不到這篇筆記。", ephemeral=True)


class MemberEventsCog(commands.Cog):
    MESSAGE_SNAPSHOT_RETENTION_DAYS = 30

    def __init__(self, bot):
        self.bot = bot

    @staticmethod
    def _attachment_data(attachments):
        result = []
        for attachment in attachments or []:
            if isinstance(attachment, dict):
                item = attachment
            else:
                item = {
                    "id": getattr(attachment, "id", None),
                    "filename": getattr(attachment, "filename", "attachment"),
                    "size": getattr(attachment, "size", 0),
                    "url": getattr(attachment, "url", ""),
                    "content_type": getattr(attachment, "content_type", None),
                }
            result.append({
                "id": item.get("id"),
                "filename": str(item.get("filename") or "attachment")[:255],
                "size": max(0, int(item.get("size") or 0)),
                "url": str(item.get("url") or item.get("proxy_url") or "")[:2000],
                "content_type": item.get("content_type"),
            })
        return result

    @staticmethod
    def _format_size(size):
        size = max(0, int(size or 0))
        if size >= 1024 ** 3:
            return f"{size / (1024 ** 3):.2f} GB"
        if size >= 1024 ** 2:
            return f"{size / (1024 ** 2):.2f} MB"
        if size >= 1024:
            return f"{size / 1024:.2f} KB"
        return f"{size} B"

    @staticmethod
    def _format_timestamp(timestamp):
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
        return f"{discord.utils.format_dt(dt, style='F')}（{discord.utils.format_dt(dt, style='R')}）"

    async def _send_delete_log(self, guild_id, payload, snapshot=None, cached_message=None):
        log_channel_id = storage.get_setting(guild_id, "message_log_channel_id")
        if not log_channel_id or int(log_channel_id) == payload.channel_id:
            return

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel = guild.get_channel(int(log_channel_id))
        if not isinstance(channel, discord.TextChannel):
            try:
                fetched = await self.bot.fetch_channel(int(log_channel_id))
                channel = fetched if isinstance(fetched, discord.TextChannel) and fetched.guild.id == guild_id else None
            except (discord.NotFound, discord.Forbidden, discord.HTTPException, TypeError, ValueError):
                channel = None
        if channel is None:
            return

        if snapshot is not None:
            author_id = int(snapshot["author_id"])
            author_name = snapshot["author_display_name"] or snapshot["author_name"]
            avatar_url = snapshot["author_avatar_url"]
            content = snapshot["content"] or ""
            attachments = self._attachment_data(json.loads(snapshot["attachments_json"] or "[]"))
            created_at = float(snapshot["created_at"])
        elif cached_message is not None:
            author_id = cached_message.author.id
            author_name = getattr(cached_message.author, "display_name", str(cached_message.author))
            avatar_url = str(cached_message.author.display_avatar.url)
            content = cached_message.content or ""
            attachments = self._attachment_data(cached_message.attachments)
            created_at = cached_message.created_at.timestamp()
        else:
            author_id = None
            author_name = "無法取得作者資料"
            avatar_url = None
            content = ""
            attachments = []
            created_at = None

        if self.bot.user and author_id == self.bot.user.id:
            return

        embed = discord.Embed(
            color=discord.Color.red(),
            description=None,
        )
        embed.title = "🗑️ 訊息已刪除"
        if avatar_url:
            embed.set_author(name=str(author_name)[:256], icon_url=avatar_url)
        else:
            embed.set_author(name=str(author_name)[:256])

        if content:
            embed.add_field(name="訊息內容", value=content[:1024], inline=False)
            if len(content) > 1024:
                embed.add_field(name="訊息內容（續）", value=content[1024:2048], inline=False)
        else:
            embed.add_field(name="訊息內容", value="（沒有文字內容）", inline=False)

        if attachments:
            attachment_lines = []
            for item in attachments[:10]:
                filename = discord.utils.escape_markdown(str(item.get("filename") or "附件"))[:180]
                size_text = self._format_size(item.get("size", 0))
                url = str(item.get("url") or "")
                link = f" [🔗 下載]({url})" if url.startswith(("https://", "http://")) else ""
                attachment_lines.append(f"📦 **{filename}**（{size_text}）{link}")
            if len(attachments) > 10:
                attachment_lines.append(f"…另有 {len(attachments) - 10} 個附件")
            embed.add_field(name="附件", value="\n".join(attachment_lines)[:1024], inline=False)
        else:
            embed.add_field(name="附件", value="無", inline=False)

        if created_at is not None:
            embed.add_field(name="傳送時間", value=self._format_timestamp(created_at), inline=False)
        else:
            embed.add_field(name="傳送時間", value="無法取得（訊息未曾被 Bot 保存）", inline=False)
        embed.add_field(name="刪除時間", value=self._format_timestamp(discord.utils.utcnow().timestamp()), inline=False)
        embed.set_footer(text=f"頻道 ID：{payload.channel_id} ・ 訊息 ID：{payload.message_id}")

        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"Message deletion log delivery failed in guild {guild_id}, channel {log_channel_id}: {exc!r}")

    @commands.Cog.listener()
    async def on_message(self, message):
        if not message.guild or message.author.bot:
            return
        log_channel_id = storage.get_setting(message.guild.id, "message_log_channel_id")
        if not log_channel_id or int(log_channel_id) == message.channel.id:
            return
        try:
            storage.save_message_snapshot(
                message_id=message.id,
                guild_id=message.guild.id,
                channel_id=message.channel.id,
                author_id=message.author.id,
                author_name=str(message.author),
                author_display_name=getattr(message.author, "display_name", str(message.author)),
                author_avatar_url=str(message.author.display_avatar.url),
                content=message.content or "",
                attachments=self._attachment_data(message.attachments),
                created_at=message.created_at.timestamp(),
            )
        except Exception as exc:
            print(f"Message snapshot save failed for {message.id}: {exc!r}")

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload):
        if payload.guild_id is None:
            return
        log_channel_id = storage.get_setting(payload.guild_id, "message_log_channel_id")
        if log_channel_id and int(log_channel_id) == payload.channel_id:
            return
        suppressed_ids = getattr(self.bot, "_relay_suppressed_delete_log_ids", None)
        if suppressed_ids is not None and payload.message_id in suppressed_ids:
            suppressed_ids.discard(payload.message_id)
            storage.delete_message_snapshot(payload.message_id)
            return

        cached_message = payload.cached_message
        if cached_message is not None and cached_message.author.bot:
            storage.delete_message_snapshot(payload.message_id)
            return
        snapshot = storage.get_message_snapshot(payload.message_id)
        try:
            await self._send_delete_log(payload.guild_id, payload, snapshot, cached_message)
        finally:
            storage.delete_message_snapshot(payload.message_id)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload):
        if payload.guild_id is None:
            return
        log_channel_id = storage.get_setting(payload.guild_id, "message_log_channel_id")
        if log_channel_id and int(log_channel_id) == payload.channel_id:
            return
        data = payload.data or {}
        if "content" not in data and "attachments" not in data:
            return

        cached_message = payload.cached_message
        if cached_message is not None and cached_message.author.bot:
            return

        snapshot = storage.get_message_snapshot(payload.message_id)
        old_content = snapshot["content"] if snapshot is not None else (
            cached_message.content if cached_message is not None else None
        )
        new_content = data.get("content", old_content or "") or ""
        attachment_data = self._attachment_data(data.get("attachments")) if "attachments" in data else None

        # Discord can emit repeated raw edit events for the same message, including
        # later attachment/metadata updates. Compare the actual attachment identity
        # as well as text before deciding whether this is a real new edit.
        old_attachments = (
            self._attachment_data(json.loads(snapshot["attachments_json"] or "[]"))
            if snapshot is not None else (
                self._attachment_data(cached_message.attachments)
                if cached_message is not None else []
            )
        )

        def attachment_signature(items):
            return sorted(
                (
                    str(item.get("id") or ""),
                    str(item.get("filename") or ""),
                    int(item.get("size") or 0),
                    str(item.get("content_type") or ""),
                )
                for item in (items or [])
            )

        attachments_unchanged = (
            attachment_data is None
            or attachment_signature(old_attachments) == attachment_signature(attachment_data)
        )
        if old_content is not None and old_content == new_content and attachments_unchanged:
            return

        if snapshot is not None:
            storage.update_message_snapshot_content(payload.message_id, new_content, attachment_data)
        elif cached_message is not None:
            storage.save_message_snapshot(
                message_id=cached_message.id,
                guild_id=cached_message.guild.id,
                channel_id=cached_message.channel.id,
                author_id=cached_message.author.id,
                author_name=str(cached_message.author),
                author_display_name=getattr(cached_message.author, "display_name", str(cached_message.author)),
                author_avatar_url=str(cached_message.author.display_avatar.url),
                content=new_content,
                attachments=attachment_data if attachment_data is not None else self._attachment_data(cached_message.attachments),
                created_at=cached_message.created_at.timestamp(),
            )
        author = (
            f"{getattr(cached_message.author, 'display_name', str(cached_message.author))}（ID：{cached_message.author.id}）"
            if cached_message is not None else
            (f"{snapshot['author_display_name']}（ID：{snapshot['author_id']}）" if snapshot is not None else "無法取得作者資料")
        )
        description = (
            f"修改前：\n{old_content or '（沒有文字內容）'}\n\n"
            f"修改後：\n{new_content or '（沒有文字內容）'}"
            if old_content is not None else
            f"修改後：\n{new_content or '（沒有文字內容）'}\n\n修改前：Bot 尚未保存原文。"
        )
        await self._send_message_log(
            payload.guild_id,
            title="✏️ 訊息已編輯",
            description=description,
            color=discord.Color.orange(),
            author=author,
            channel_id=payload.channel_id,
            message_id=payload.message_id,
        )

    async def _send_message_log(self, guild_id, *, title, description, color, author=None, channel_id=None, message_id=None):
        log_channel_id = storage.get_setting(guild_id, "message_log_channel_id")
        if not log_channel_id or (channel_id and int(log_channel_id) == int(channel_id)):
            return
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel = guild.get_channel(int(log_channel_id))
        if not isinstance(channel, discord.TextChannel):
            try:
                fetched = await self.bot.fetch_channel(int(log_channel_id))
                channel = fetched if isinstance(fetched, discord.TextChannel) and fetched.guild.id == guild_id else None
            except (discord.NotFound, discord.Forbidden, discord.HTTPException, TypeError, ValueError):
                channel = None
        if channel is None:
            return
        embed = discord.Embed(title=title, description=(description or "（沒有文字內容）")[:4000], color=color)
        if author:
            embed.set_author(name=str(author)[:256])
        if channel_id:
            embed.add_field(name="來源頻道", value=f"<#{channel_id}>", inline=True)
        if message_id:
            embed.add_field(name="訊息 ID", value=str(message_id), inline=True)
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"Message log delivery failed in guild {guild_id}, channel {log_channel_id}: {exc!r}")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if member.bot:
            return
        role_id = storage.get_setting(member.guild.id, "autorole_id", 0)
        if role_id:
            role = member.guild.get_role(int(role_id))
            bot_member = member.guild.me
            if autorole_is_safe(role, bot_member):
                try:
                    await member.add_roles(role, reason="自動新人身分組")
                except (discord.Forbidden, discord.HTTPException) as exc:
                    print(f"Autorole assignment failed in guild {member.guild.id}: {exc!r}")
            else:
                
                storage.set_setting(member.guild.id, "autorole_id", 0)
        channel_id = storage.get_setting(member.guild.id, "welcome_channel_id", 0)
        channel = member.guild.get_channel(int(channel_id)) if channel_id else None
        if channel:
            msg = storage.get_setting(member.guild.id, "welcome_message", "歡迎 {user} 來到 {server}！")
            try:
                await channel.send(
                    msg.replace("{user}", member.mention).replace("{server}", member.guild.name),
                    allowed_mentions=discord.AllowedMentions(
                        users=[member], roles=False, everyone=False, replied_user=False
                    ),
                )
            except Exception:
                pass

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        channel_id = storage.get_setting(member.guild.id, "goodbye_channel_id", 0)
        channel = member.guild.get_channel(int(channel_id)) if channel_id else None
        if channel:
            msg = storage.get_setting(member.guild.id, "goodbye_message", "{user} 離開了，掰。")
            try:
                await channel.send(
                    msg.replace("{user}", str(member)).replace("{server}", member.guild.name),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:
                pass


async def setup(bot):
    await bot.add_cog(SettingsCog(bot))
    await bot.add_cog(MemberEventsCog(bot))
