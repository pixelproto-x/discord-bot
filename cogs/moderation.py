import asyncio
import re
import time
from collections import deque
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

import storage

INVITE_RE = re.compile(r"(?:https?://)?(?:www\.)?(?:discord\.gg/|discord(?:app)?\.com/invite/)[A-Za-z0-9-]+", re.I)


class ModerationCog(commands.Cog):
    security = app_commands.Group(name="security", name_localizations={"zh-TW": "安全管理"}, description="防刷、防大量標記與連結濫用", description_localizations={"zh-TW": "防刷、防大量標記與連結濫用"})
    modlog = app_commands.Group(name="modlog", name_localizations={"zh-TW": "管理紀錄"}, description="設定管理紀錄要發去哪裡", description_localizations={"zh-TW": "設定管理紀錄要發去哪裡"})

    def __init__(self, bot):
        self.bot = bot
        self.message_times: dict[tuple[int, int], deque[float]] = {}
        self._last_message_times_prune = 0.0

    def _prune_message_times(self, now: float | None = None, *, force: bool = False):
        """Expire inactive anti-spam buckets so departed/idle users do not accumulate forever."""
        now = time.monotonic() if now is None else now
        if not force and now - self._last_message_times_prune < 60:
            return
        self._last_message_times_prune = now
        for key, timestamps in list(self.message_times.items()):
            while timestamps and now - timestamps[0] > 6:
                timestamps.popleft()
            if not timestamps:
                self.message_times.pop(key, None)


    @staticmethod
    def count_message_mentions(user_ids, role_ids, mentions_everyone: bool = False) -> int:
        """Count user/role mentions plus Discord's @everyone/@here broadcast mention."""
        return len(set(user_ids) | set(role_ids)) + int(bool(mentions_everyone))

    def can_manage(self, interaction: discord.Interaction) -> bool:
        return bool(
            interaction.guild and isinstance(interaction.user, discord.Member)
            and (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild)
        )

    def can_mod(self, interaction: discord.Interaction) -> bool:
        return bool(
            interaction.guild and isinstance(interaction.user, discord.Member)
            and (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.moderate_members)
        )

    def can_kick(self, interaction: discord.Interaction) -> bool:
        return bool(interaction.guild and isinstance(interaction.user, discord.Member)
                    and (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.kick_members))

    def can_ban(self, interaction: discord.Interaction) -> bool:
        return bool(interaction.guild and isinstance(interaction.user, discord.Member)
                    and (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.ban_members))

    def can_target(self, interaction: discord.Interaction, member: discord.Member) -> bool:
        guild = interaction.guild
        actor = interaction.user if isinstance(interaction.user, discord.Member) else None
        bot_member = guild.me if guild else None
        if not guild or not actor or not bot_member:
            return False
        if member.id in {guild.owner_id, bot_member.id}:
            return False
        if actor.id != guild.owner_id and member.top_role >= actor.top_role:
            return False
        return member.top_role < bot_member.top_role

    async def log_event(self, guild: discord.Guild, title: str, description: str):
        channel_id = storage.get_setting(guild.id, "log_channel_id")
        if not channel_id:
            return
        channel = guild.get_channel(int(channel_id))
        if not channel:
            return
        try:
            embed = discord.Embed(title=title, description=description[:4000], color=0xED4245, timestamp=discord.utils.utcnow())
            await channel.send(embed=embed)
        except Exception:
            pass

    async def punish_message(self, message: discord.Message, reason: str):
        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            return False
        await self.log_event(
            message.guild,
            "🛑 我攔了一則訊息",
            f"**使用者**：{message.author.mention}\n**頻道**：{message.channel.mention}\n**原因**：{reason}",
        )
        return True

    async def sync_mention_automod(self, guild: discord.Guild, limit: int):
        rules = await guild.fetch_automod_rules()
        name = "朋友群 Bot｜@ 防刷"
        current = next((rule for rule in rules if rule.name == name), None)

        if limit <= 0:
            if current and current.enabled:
                await current.edit(enabled=False, reason="停用 Mention 防刷")
            return

        trigger = discord.AutoModTrigger(mention_limit=limit)
        actions = [discord.AutoModRuleAction()]
        if current:
            await current.edit(
                trigger=trigger,
                actions=actions,
                enabled=True,
                reason=f"設定 Mention 上限 {limit}",
            )
            return

        await guild.create_automod_rule(
            name=name,
            event_type=discord.AutoModRuleEventType.message_send,
            trigger=trigger,
            actions=actions,
            enabled=True,
            reason=f"設定 Mention 上限 {limit}",
        )

    @commands.Cog.listener()
    async def on_automod_action(self, execution: discord.AutoModAction):
        try:
            guild = self.bot.get_guild(execution.guild_id)
            if not guild:
                return
            rule = await execution.fetch_rule()
            if rule.name != "朋友群 Bot｜@ 防刷":
                return
            guild = self.bot.get_guild(execution.guild_id)
            if not guild:
                return
            member = guild.get_member(execution.user_id)
            channel = guild.get_channel(execution.channel_id)
            await self.log_event(
                guild,
                "🚨 @ 防刷攔截",
                f"**使用者**：{member.mention if member else execution.user_id}\n"
                f"**頻道**：{channel.mention if channel else execution.channel_id}\n"
                f"**原因**：超過 Mention 上限",
            )
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        if not isinstance(message.author, discord.Member):
            return
        self._prune_message_times()
        if (
            message.author.guild_permissions.administrator
            or message.author.guild_permissions.manage_messages
            or message.author.guild_permissions.manage_guild
        ):
            return

        limit = int(storage.get_setting(message.guild.id, "mention_limit", 5))
        mentioned_users = set(message.raw_mentions)
        mentioned_roles = set(getattr(message, "raw_role_mentions", []))
        mention_count = self.count_message_mentions(
            mentioned_users,
            mentioned_roles,
            getattr(message, "mention_everyone", False),
        )
        if limit > 0 and mention_count > limit:
            await self.punish_message(message, f"一次標記了 {mention_count} 個人／身分組，超過上限 {limit}")
            return

        if bool(storage.get_setting(message.guild.id, "block_invites", False)) and INVITE_RE.search(message.content):
            if await self.punish_message(message, "邀請連結防護"):
                return

        if bool(storage.get_setting(message.guild.id, "block_caps", False)):
            letters = [c for c in message.content if c.isalpha()]
            if len(letters) >= 20 and sum(c.isupper() for c in letters) / len(letters) >= 0.85:
                if await self.punish_message(message, "大量大寫字母"):
                    return

        flood_limit = int(storage.get_setting(message.guild.id, "flood_limit", 0))
        if flood_limit > 0:
            now = time.monotonic()
            key = (message.guild.id, message.author.id)
            q = self.message_times.setdefault(key, deque())
            q.append(now)
            while q and now - q[0] > 6:
                q.popleft()
            if len(q) >= flood_limit:
                await self.punish_message(message, f"短時間連續發送 {len(q)} 則")
                q.clear()

    @security.command(name="mention-limit", description="設定一則訊息最多能標記幾個人")
    @app_commands.describe(limit="例如 5；0 可關閉，最多 50")
    async def mention_limit(self, interaction, limit: app_commands.Range[int, 0, 50]):
        if not self.can_manage(interaction):
            await interaction.response.send_message("這個設定要管理權限。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            await self.sync_mention_automod(interaction.guild, limit)
            storage.set_setting(interaction.guild.id, "mention_limit", limit)
            await interaction.followup.send(
                f"好了，現在一則訊息最多 @ {limit} 個。超過會在送出前直接被 Discord 擋掉。"
                if limit else "@ 防刷關掉了。",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "Discord AutoMod 沒有成功更新；原本的後備防護設定仍保留。若你剛才要關閉，請確認權限後重試。",
                ephemeral=True,
            )
        except discord.HTTPException:
            await interaction.followup.send(
                "Discord AutoMod 這次沒設定成功；原本的後備防護設定仍保留，請稍後重試。",
                ephemeral=True,
            )
        except Exception:
            await interaction.followup.send(
                "這次安全設定沒成功，原本的後備防護設定先保留。",
                ephemeral=True,
            )

    @security.command(name="antispam", description="開關短時間訊息刷屏防護")
    @app_commands.describe(enabled="是否開啟", limit="幾則訊息算刷屏")
    async def antispam(self, interaction, enabled: bool, limit: app_commands.Range[int, 3, 30] = 8):
        if not self.can_manage(interaction):
            await interaction.response.send_message("這個設定要管理權限。", ephemeral=True)
            return
        storage.set_setting(interaction.guild.id, "flood_limit", limit if enabled else 0)
        await interaction.response.send_message(
            f"防刷已{'開啟' if enabled else '關閉'}。{('6 秒內 ' + str(limit) + ' 則會開始攔。') if enabled else ''}",
            ephemeral=True,
        )

    @security.command(name="invites", name_localizations={"zh-TW": "邀請連結管理"}, description_localizations={"zh-TW": "開關 Discord 邀請連結防護"}, description="開關 Discord 邀請連結防護")
    async def invites(self, interaction, enabled: bool):
        if not self.can_manage(interaction):
            await interaction.response.send_message("這個設定要管理權限。", ephemeral=True)
            return
        storage.set_setting(interaction.guild.id, "block_invites", enabled)
        await interaction.response.send_message(f"邀請連結防護已{'開啟' if enabled else '關閉'}。", ephemeral=True)

    @security.command(name="caps", name_localizations={"zh-TW": "大寫字限制"}, description_localizations={"zh-TW": "開關大量大寫字母防護"}, description="開關大量大寫字母防護")
    async def caps(self, interaction, enabled: bool):
        if not self.can_manage(interaction):
            await interaction.response.send_message("這個設定要管理權限。", ephemeral=True)
            return
        storage.set_setting(interaction.guild.id, "block_caps", enabled)
        await interaction.response.send_message(f"大寫刷屏防護已{'開啟' if enabled else '關閉'}。", ephemeral=True)

    @modlog.command(name="set", description="把管理紀錄送到指定頻道")
    @app_commands.describe(channel="紀錄頻道")
    async def modlog_set(self, interaction, channel: discord.TextChannel):
        if not self.can_manage(interaction):
            await interaction.response.send_message("這個設定要管理權限。", ephemeral=True)
            return
        storage.set_setting(interaction.guild.id, "log_channel_id", channel.id)
        await interaction.response.send_message(f"好，以後管理紀錄會丟到 {channel.mention}。", ephemeral=True)

    @modlog.command(name="disable", name_localizations={"zh-TW": "停用"}, description_localizations={"zh-TW": "關閉管理紀錄"}, description="關閉管理紀錄")
    async def modlog_disable(self, interaction):
        if not self.can_manage(interaction):
            await interaction.response.send_message("這個設定要管理權限。", ephemeral=True)
            return
        storage.set_setting(interaction.guild.id, "log_channel_id", None)
        await interaction.response.send_message("管理紀錄關掉了。", ephemeral=True)

    @app_commands.command(name="clear", description="清掉目前頻道最近的一批訊息")
    @app_commands.describe(amount="1 到 100")
    async def clear(self, interaction, amount: app_commands.Range[int, 1, 100]):
        if not isinstance(interaction.channel, discord.TextChannel) or not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("你需要管理訊息權限。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await interaction.channel.purge(limit=amount)
            await interaction.followup.send(f"清掉 {len(deleted)} 則。", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("Discord 不讓我刪，檢查 Bot 的管理訊息權限。", ephemeral=True)

    @app_commands.command(name="slowmode", description="設定目前頻道慢速模式")
    @app_commands.describe(seconds="0 代表關閉，最多 21600 秒")
    async def slowmode(self, interaction, seconds: app_commands.Range[int, 0, 21600]):
        if not isinstance(interaction.channel, discord.TextChannel) or not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message("你需要管理頻道權限。", ephemeral=True)
            return
        try:
            await interaction.channel.edit(slowmode_delay=seconds, reason=f"設定慢速模式 by {interaction.user}")
            await interaction.response.send_message(f"慢速模式設成 {seconds} 秒。" if seconds else "慢速模式關掉了。")
        except discord.Forbidden:
            await interaction.response.send_message("我沒權限改這個頻道。", ephemeral=True)

    @app_commands.command(name="lock", name_localizations={"zh-TW": "鎖定頻道"}, description_localizations={"zh-TW": "暫時鎖住目前頻道，不讓一般成員發言"}, description="暫時鎖住目前頻道，不讓一般成員發言")
    async def lock(self, interaction):
        if not isinstance(interaction.channel, discord.TextChannel) or not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message("你需要管理頻道權限。", ephemeral=True)
            return
        guild = interaction.guild
        channel = interaction.channel
        key = f"lock_original_send_messages:{channel.id}"
        saved = storage.get_setting(guild.id, key, "__missing__")
        overwrite = channel.overwrites_for(guild.default_role)
        if saved == "__missing__":
            original = overwrite.send_messages
            storage.set_setting(guild.id, key, "__unset__" if original is None else original)
        overwrite.send_messages = False
        try:
            await channel.set_permissions(guild.default_role, overwrite=overwrite, reason=f"鎖頻道 by {interaction.user}")
            await interaction.response.send_message("先鎖起來了。🔒")
        except discord.Forbidden:
            await interaction.response.send_message("我沒辦法改這個頻道權限。", ephemeral=True)
        except discord.HTTPException:
            await interaction.response.send_message("Discord 這次沒有成功鎖頻道，請稍後重試。", ephemeral=True)

    @app_commands.command(name="unlock", name_localizations={"zh-TW": "解除鎖定"}, description_localizations={"zh-TW": "解除目前頻道的鎖定"}, description="解除目前頻道的鎖定")
    async def unlock(self, interaction):
        if not isinstance(interaction.channel, discord.TextChannel) or not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message("你需要管理頻道權限。", ephemeral=True)
            return
        guild = interaction.guild
        channel = interaction.channel
        key = f"lock_original_send_messages:{channel.id}"
        saved = storage.get_setting(guild.id, key, "__missing__")
        overwrite = channel.overwrites_for(guild.default_role)
        if saved == "__unset__":
            overwrite.send_messages = None
        elif isinstance(saved, bool):
            overwrite.send_messages = saved
        else:
            
            overwrite.send_messages = None
        try:
            await channel.set_permissions(guild.default_role, overwrite=overwrite, reason=f"解鎖頻道 by {interaction.user}")
            storage.set_setting(guild.id, key, "__missing__")
            await interaction.response.send_message("好了，解鎖。🔓")
        except discord.Forbidden:
            await interaction.response.send_message("我沒辦法改這個頻道權限。", ephemeral=True)
        except discord.HTTPException:
            await interaction.response.send_message("Discord 這次沒有成功解鎖頻道，請稍後重試。", ephemeral=True)
    @app_commands.command(name="warn", description="給成員一個警告並留下紀錄")
    @app_commands.describe(member="要警告的人", reason="原因")
    async def warn(self, interaction, member: discord.Member, reason: str):
        if not self.can_mod(interaction):
            await interaction.response.send_message("你需要管理成員權限。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        reason = reason[:1000]
        wid = storage.add_warning(interaction.guild.id, member.id, interaction.user.id, reason)
        await self.log_event(interaction.guild, "⚠️ 成員警告", f"{member.mention} 被 {interaction.user.mention} 警告。\n原因：{reason}")
        await interaction.followup.send(f"記下來了。{member.mention} 這次是警告，編號 {wid}。", ephemeral=True)

    @app_commands.command(name="warnings", description="查看某位成員的警告紀錄")
    @app_commands.describe(member="要查看的人")
    async def warnings(self, interaction, member: discord.Member):
        if not self.can_mod(interaction):
            await interaction.response.send_message("你需要管理成員權限。", ephemeral=True)
            return
        rows = storage.get_warnings(interaction.guild.id, member.id)
        if not rows:
            await interaction.response.send_message(f"{member.display_name} 目前沒有警告紀錄。", ephemeral=True)
            return
        lines = [f"{i+1}. {row['reason'][:160]}" for i, row in enumerate(rows[:10])]
        await interaction.response.send_message(f"{member.mention} 的警告：\n" + "\n".join(lines), ephemeral=True)

    @app_commands.command(name="timeout", description="暫時讓成員不能聊天")
    @app_commands.describe(member="要處理的人", minutes="分鐘（最多 28 天）", reason="原因")
    async def timeout(self, interaction, member: discord.Member, minutes: app_commands.Range[int, 1, 40320], reason: str = "沒有寫原因"):
        guild = interaction.guild
        actor = interaction.user if isinstance(interaction.user, discord.Member) else None
        bot_member = guild.me if guild else None
        if not guild or not actor or not bot_member:
            await interaction.response.send_message("這個指令只能在伺服器內使用。", ephemeral=True)
            return
        if not (actor.guild_permissions.administrator or actor.guild_permissions.moderate_members):
            await interaction.response.send_message("你需要「調節成員」或管理員權限才能使用禁言。", ephemeral=True)
            return
        if not (bot_member.guild_permissions.moderate_members or bot_member.guild_permissions.administrator):
            await interaction.response.send_message("Bot 缺少「調節成員」權限，無法執行禁言。", ephemeral=True)
            return
        # 防止透過 Bot 越權：伺服器擁有者，或最高身分組高於 Bot 的管理者才能使用。
        if actor.id != guild.owner_id and actor.top_role <= bot_member.top_role:
            await interaction.response.send_message(
                "你的最高身分組必須高於 Bot 的最高身分組，才能透過 Bot 使用禁言。",
                ephemeral=True,
            )
            return
        if member.id in {guild.owner_id, bot_member.id, actor.id}:
            await interaction.response.send_message("不能禁言伺服器擁有者、Bot 自己或你自己。", ephemeral=True)
            return
        if actor.id != guild.owner_id and member.top_role >= actor.top_role:
            await interaction.response.send_message("不能禁言身分組與你相同或比你高的成員。", ephemeral=True)
            return
        if member.top_role >= bot_member.top_role:
            await interaction.response.send_message("不能禁言身分組與 Bot 相同或比 Bot 高的成員。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        reason = reason[:1000]
        try:
            await member.timeout(discord.utils.utcnow() + timedelta(minutes=minutes), reason=reason)
            await self.log_event(guild, "⏱️ 成員禁言", f"{member.mention} 被 {actor.mention} 禁言 {minutes} 分鐘。\\n原因：{reason}")
            await interaction.followup.send(f"已將 {member.mention} 禁言 {minutes} 分鐘。", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("Discord 拒絕執行禁言，請確認 Bot 的權限與目標身分組階級。", ephemeral=True)
        except discord.HTTPException:
            await interaction.followup.send("Discord 暫時無法執行禁言，請稍後再試。", ephemeral=True)

    @app_commands.command(name="kick", name_localizations={"zh-TW": "踢出成員"}, description_localizations={"zh-TW": "把成員踢出伺服器"}, description="把成員踢出伺服器")
    async def kick(self, interaction, member: discord.Member, reason: str = "沒有寫原因"):
        if not self.can_kick(interaction):
            await interaction.response.send_message("你需要踢出成員權限。", ephemeral=True)
            return
        if not self.can_target(interaction, member):
            await interaction.response.send_message("這個成員我不能踢，通常是身分組階級太高或對方是伺服器擁有者。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        reason = reason[:1000]
        try:
            await member.kick(reason=reason)
            await self.log_event(interaction.guild, "👢 成員被踢出", f"{member} 被 {interaction.user.mention} 踢出。\n原因：{reason}")
            await interaction.followup.send(f"處理好了，{member.display_name} 已離開伺服器。", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("我沒有權限踢這個人。", ephemeral=True)

    @app_commands.command(name="ban", name_localizations={"zh-TW": "封鎖成員"}, description_localizations={"zh-TW": "把成員封鎖並踢出伺服器"}, description="把成員封鎖並踢出伺服器")
    async def ban(self, interaction, member: discord.Member, reason: str = "沒有寫原因"):
        if not self.can_ban(interaction):
            await interaction.response.send_message("你需要封鎖成員權限。", ephemeral=True)
            return
        if not self.can_target(interaction, member):
            await interaction.response.send_message("這個成員我不能封鎖，通常是身分組階級太高或對方是伺服器擁有者。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        reason = reason[:1000]
        try:
            await member.ban(reason=reason)
            await self.log_event(interaction.guild, "🔨 成員被封鎖", f"{member} 被 {interaction.user.mention} 封鎖。\n原因：{reason}")
            await interaction.followup.send(f"好了，{member.display_name} 已被封鎖。", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("我沒有權限封鎖這個人。", ephemeral=True)

    @app_commands.command(name="unban", description="用使用者 ID 解除封鎖")
    @app_commands.describe(user_id="Discord 使用者 ID")
    async def unban(self, interaction, user_id: str):
        if not self.can_ban(interaction):
            await interaction.response.send_message("你需要封鎖成員權限。", ephemeral=True)
            return
        normalized_id = user_id.strip()
        if not re.fullmatch(r"\d{15,25}", normalized_id):
            await interaction.response.send_message("請輸入有效的 Discord 使用者 ID（15～25 位數字）。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            user = await self.bot.fetch_user(int(normalized_id))
            await interaction.guild.unban(user, reason=f"解除封鎖 by {interaction.user}")
            await self.log_event(
                interaction.guild,
                "🔓 成員解除封鎖",
                f"**使用者**：{user.mention}（{user.id}）\\n**操作者**：{interaction.user.mention}",
            )
            await interaction.followup.send(f"好了，{user} 解鎖。", ephemeral=True)
        except (ValueError, discord.NotFound):
            await interaction.followup.send("這個 ID 找不到，或他本來就沒被封。", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("我沒有解除封鎖的權限。", ephemeral=True)



async def setup(bot):
    await bot.add_cog(ModerationCog(bot))
