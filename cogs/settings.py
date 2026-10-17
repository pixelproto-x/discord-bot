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
        channel = self.values[0]
        bot_member = interaction.guild.me
        if not isinstance(channel, discord.TextChannel) or bot_member is None:
            await interaction.response.send_message("請選擇有效的文字頻道。", ephemeral=True)
            return
        perms = channel.permissions_for(bot_member)
        if not perms.view_channel or not perms.send_messages:
            await interaction.response.send_message(
                "Bot 在這個頻道缺少查看頻道或傳送訊息權限，設定沒有變更。",
                ephemeral=True,
            )
            return
        storage.set_setting(interaction.guild.id, self.key, channel.id)
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
    settings = app_commands.Group(name="settings", description="管理 Bot 在這個伺服器的設定")
    note = app_commands.Group(name="note", description="自己的小筆記，Bot 幫你記著")

    def __init__(self, bot):
        self.bot = bot

    def admin(self, interaction):
        return bool(
            interaction.guild
            and isinstance(interaction.user, discord.Member)
            and (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild)
        )

    @settings.command(name="show", description="開啟互動式伺服器設定中心")
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

    @settings.command(name="autorole", description="設定新人加入後自動拿到的身分組")
    @app_commands.describe(role="新人要拿的身分組")
    async def autorole(self, interaction, role: discord.Role | None = None):
        if not self.admin(interaction):
            await interaction.response.send_message("這個設定要管理伺服器權限。", ephemeral=True)
            return
        if role:
            bot_member = interaction.guild.me
            if not autorole_is_safe(role, bot_member):
                await interaction.response.send_message("這個身分組不能自動發放（可能有管理權限），或它比 Bot 高，或 Bot 缺少管理身分組權限。", ephemeral=True)
                return
        storage.set_setting(interaction.guild.id, "autorole_id", role.id if role else 0)
        await interaction.response.send_message(f"新人身分組已{'設成 ' + role.mention if role else '關閉'}。", ephemeral=True)

    @settings.command(name="welcome", description="設定新人加入時的歡迎訊息")
    @app_commands.describe(channel="歡迎頻道", message="訊息，可用 {user} 和 {server}")
    async def welcome(self, interaction, channel: discord.TextChannel | None = None, message: str = "歡迎 {user} 來到 {server}！"):
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

    @settings.command(name="goodbye", description="設定成員離開時的通知")
    @app_commands.describe(channel="離開通知頻道", message="訊息，可用 {user} 和 {server}")
    async def goodbye(self, interaction, channel: discord.TextChannel | None = None, message: str = "{user} 離開了，掰。"):
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

    @note.command(name="add", description="存一筆只有自己看得到的筆記")
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

    @note.command(name="list", description="看看自己存過哪些筆記")
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

    @note.command(name="show", description="查看自己的指定筆記")
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

    @note.command(name="delete", description="刪掉自己的指定筆記")
    @app_commands.describe(note_id="筆記編號")
    async def note_delete(self, interaction, note_id: int):
        if not interaction.guild:
            await interaction.response.send_message("這個要在伺服器裡用。", ephemeral=True)
            return
        ok = storage.delete_note(interaction.guild.id, interaction.user.id, note_id)
        await interaction.response.send_message("刪掉了。" if ok else "找不到這篇筆記。", ephemeral=True)


class MemberEventsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

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
                # A saved role may have become privileged or moved above the bot since setup.
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
