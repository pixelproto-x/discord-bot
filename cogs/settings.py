import discord
from discord import app_commands
from discord.ext import commands

import storage
from cogs.social import is_safe_self_assignable_role


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

    @settings.command(name="show", description="查看這個伺服器現在的 Bot 設定")
    async def show(self, interaction):
        if not self.admin(interaction):
            await interaction.response.send_message("這個只有管理人員能看。", ephemeral=True)
            return
        g = interaction.guild.id
        mention_limit = storage.get_setting(g, "mention_limit", 5)
        flood_limit = storage.get_setting(g, "flood_limit", 0)
        invites = storage.get_setting(g, "block_invites", False)
        caps = storage.get_setting(g, "block_caps", False)
        log_channel = storage.get_setting(g, "log_channel_id")
        voice_notice = storage.get_setting(g, "voice_notice_channel_id")
        autorole = storage.get_setting(g, "autorole_id")
        welcome = storage.get_setting(g, "welcome_channel_id")
        goodbye = storage.get_setting(g, "goodbye_channel_id")
        text = (
            f"@ 上限：{mention_limit}\n"
            f"防刷：{'開' if flood_limit else '關'}"
            + (f"（6 秒 {flood_limit} 則）" if flood_limit else "") + "\n"
            f"邀請連結攔截：{'開' if invites else '關'}\n"
            f"大寫刷屏攔截：{'開' if caps else '關'}\n"
            f"管理紀錄：{f'<#{log_channel}>' if log_channel else '關'}\n"
            f"語音通知：{f'<#{voice_notice}>' if voice_notice else '關'}\n"
            f"新人身分組：{f'<@&{autorole}>' if autorole else '關'}\n"
            f"歡迎頻道：{f'<#{welcome}>' if welcome else '關'}\n"
            f"離開通知：{f'<#{goodbye}>' if goodbye else '關'}"
        )
        await interaction.response.send_message("⚙️ 目前設定：\n" + text, ephemeral=True)

    @settings.command(name="autorole", description="設定新人加入後自動拿到的身分組")
    @app_commands.describe(role="新人要拿的身分組")
    async def autorole(self, interaction, role: discord.Role | None = None):
        if not self.admin(interaction):
            await interaction.response.send_message("這個設定要管理伺服器權限。", ephemeral=True)
            return
        if role:
            bot_member = interaction.guild.me
            if not is_safe_self_assignable_role(role) or role >= bot_member.top_role or not bot_member.guild_permissions.manage_roles:
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
        storage.set_setting(interaction.guild.id, "welcome_channel_id", channel.id if channel else 0)
        storage.set_setting(interaction.guild.id, "welcome_message", message.strip()[:1500])
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
        storage.set_setting(interaction.guild.id, "goodbye_channel_id", channel.id if channel else 0)
        storage.set_setting(interaction.guild.id, "goodbye_message", message.strip()[:1500])
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
            if role and role < member.guild.me.top_role:
                try:
                    await member.add_roles(role, reason="自動新人身分組")
                except Exception:
                    pass
        channel_id = storage.get_setting(member.guild.id, "welcome_channel_id", 0)
        channel = member.guild.get_channel(int(channel_id)) if channel_id else None
        if channel:
            msg = storage.get_setting(member.guild.id, "welcome_message", "歡迎 {user} 來到 {server}！")
            try:
                await channel.send(msg.replace("{user}", member.mention).replace("{server}", member.guild.name))
            except Exception:
                pass

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        channel_id = storage.get_setting(member.guild.id, "goodbye_channel_id", 0)
        channel = member.guild.get_channel(int(channel_id)) if channel_id else None
        if channel:
            msg = storage.get_setting(member.guild.id, "goodbye_message", "{user} 離開了，掰。")
            try:
                await channel.send(msg.replace("{user}", str(member)).replace("{server}", member.guild.name))
            except Exception:
                pass


async def setup(bot):
    await bot.add_cog(SettingsCog(bot))
    await bot.add_cog(MemberEventsCog(bot))
