import re
import time
import json
import random

import discord
from discord import app_commands, ui
from discord.ext import commands

import storage


ROLE_ID_RE = re.compile(r"(?:<@&)?(\d{15,25})>?")

SENSITIVE_SELF_ROLE_PERMISSIONS = (
    "administrator",
    "manage_guild",
    "manage_roles",
    "manage_channels",
    "manage_messages",
    "moderate_members",
    "kick_members",
    "ban_members",
    "manage_webhooks",
    "manage_expressions",
    "manage_emojis_and_stickers",
    "manage_events",
    "manage_threads",
    "manage_nicknames",
    "view_audit_log",
    "mention_everyone",
    "mute_members",
    "deafen_members",
    "move_members",
    "administrator",
)


def is_safe_self_assignable_role(role: discord.Role) -> bool:
    if role.is_default():
        return False
    permissions = role.permissions
    return not any(getattr(permissions, name, False) for name in SENSITIVE_SELF_ROLE_PERMISSIONS)

def parse_role_ids(text: str) -> list[int]:
    ids = [int(x) for x in ROLE_ID_RE.findall(text)]
    return list(dict.fromkeys(ids))


def casual_actor(user: discord.abc.User) -> str:
    return getattr(user, "display_name", None) or getattr(user, "name", "你")


class RolePanelView(ui.View):
    def __init__(self, panel_row, guild=None):
        super().__init__(timeout=None)
        self.panel_id = int(panel_row["id"])
        self.mode = panel_row["mode"]
        self.role_ids = [int(x) for x in json.loads(panel_row["role_ids"])][:25]

        if not guild:
            return

        roles = [guild.get_role(role_id) for role_id in self.role_ids]
        roles = [role for role in roles if role and not role.is_default()]

        if self.mode == "select":
            options = [
                discord.SelectOption(
                    label=role.name[:100],
                    value=str(role.id),
                    description="有就移除，沒有就拿到",
                )
                for role in roles
            ]
            if options:
                select = ui.StringSelect(
                    placeholder="選你想要的身分組（可多選）",
                    min_values=1,
                    max_values=min(25, len(options)),
                    options=options,
                    custom_id=f"rolepanel:{self.panel_id}",
                )

                async def select_callback(interaction: discord.Interaction):
                    await interaction.response.defer(ephemeral=True)
                    member = interaction.guild.get_member(interaction.user.id)
                    bot_member = interaction.guild.me
                    if not member or not bot_member:
                        await interaction.followup.send("我找不到你的成員資料。", ephemeral=True)
                        return

                    changes = []
                    for raw_id in select.values:
                        target = interaction.guild.get_role(int(raw_id))
                        if not target or not is_safe_self_assignable_role(target):
                            changes.append("這個身分組有管理權限，不能自助領取")
                            continue
                        if target >= bot_member.top_role:
                            changes.append(f"{target.name} 我碰不到")
                            continue
                        try:
                            if target in member.roles:
                                await member.remove_roles(target, reason="自助身分組面板")
                                changes.append(f"拿掉 {target.mention}")
                            else:
                                await member.add_roles(target, reason="自助身分組面板")
                                changes.append(f"拿到 {target.mention}")
                        except discord.Forbidden:
                            changes.append(f"{target.name} 改不了")

                    await interaction.followup.send(
                        "、".join(changes) + "。" if changes else "這次沒有可變更的身分組。",
                        ephemeral=True,
                    )

                select.callback = select_callback
                self.add_item(select)
        else:
            for index, role in enumerate(roles):
                button = ui.Button(
                    label=role.name[:80],
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"rolebutton:{self.panel_id}:{role.id}",
                    row=index // 5,
                )

                async def button_callback(interaction: discord.Interaction, role_id=role.id):
                    await interaction.response.defer(ephemeral=True)
                    member = interaction.guild.get_member(interaction.user.id)
                    target = interaction.guild.get_role(role_id)
                    bot_member = interaction.guild.me
                    if not member or not target or not bot_member:
                        await interaction.followup.send("這個身分組找不到了。", ephemeral=True)
                        return
                    if not is_safe_self_assignable_role(target) or target >= bot_member.top_role:
                        await interaction.followup.send("這個身分組我碰不到，請把 Bot 的最高身分組往上移。", ephemeral=True)
                        return
                    try:
                        if target in member.roles:
                            await member.remove_roles(target, reason="自助身分組面板")
                            await interaction.followup.send(f"好，{target.mention} 幫你拿掉了。", ephemeral=True)
                        else:
                            await member.add_roles(target, reason="自助身分組面板")
                            await interaction.followup.send(f"好了，{target.mention} 給你。", ephemeral=True)
                    except discord.Forbidden:
                        await interaction.followup.send("Discord 不讓我改這個身分組，檢查一下 Bot 身分組位置。", ephemeral=True)

                button.callback = button_callback
                self.add_item(button)

    @classmethod
    def for_guild(cls, panel_row, guild):
        return cls(panel_row, guild)


class AutoReplyCog(commands.Cog):
    autoreply = app_commands.Group(name="autoreply", description="自己設定一句話，Bot 回什麼都由你決定")

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def admin(self, interaction: discord.Interaction) -> bool:
        return bool(interaction.guild and isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.manage_guild)

    @autoreply.command(name="add", description="新增一組觸發文字與回覆")
    @app_commands.describe(trigger="你要講的話", response="Bot 要回的話", exact="是否要完全一樣才觸發")
    async def add(self, interaction: discord.Interaction, trigger: str, response: str, exact: bool = False):
        if not self.admin(interaction):
            await interaction.response.send_message("這個設定只有有管理伺服器權限的人可以改。", ephemeral=True)
            return
        if not trigger.strip() or len(trigger) > 100 or len(response) > 1500:
            await interaction.response.send_message("觸發文字最多 100 字，回覆最多 1500 字。", ephemeral=True)
            return
        rid = storage.add_autoreply(interaction.guild.id, trigger.strip(), response.strip(), "exact" if exact else "contains")
        await interaction.response.send_message(f"好，設好了。有人講「{trigger.strip()}」時，我就回你設定的那句。編號：{rid}", ephemeral=True)

    @autoreply.command(name="remove", description="刪掉一組自動回覆")
    @app_commands.describe(rule_id="自動回覆編號")
    async def remove(self, interaction: discord.Interaction, rule_id: int):
        if not self.admin(interaction):
            await interaction.response.send_message("這個設定只有管理人員可以改。", ephemeral=True)
            return
        ok = storage.delete_autoreply(interaction.guild.id, rule_id)
        await interaction.response.send_message("刪掉了。" if ok else "找不到這個編號。", ephemeral=True)

    @autoreply.command(name="list", description="看看目前設定了哪些自動回覆")
    async def list_rules(self, interaction: discord.Interaction):
        if not self.admin(interaction):
            await interaction.response.send_message("這個設定只有管理人員可以看。", ephemeral=True)
            return
        rows = storage.list_autoreplies(interaction.guild.id)
        if not rows:
            await interaction.response.send_message("目前還沒設定任何自動回覆。", ephemeral=True)
            return
        lines = []
        for r in rows[:25]:
            channel = f"<#{r['channel_id']}>" if r["channel_id"] else "所有頻道"
            state = "開著" if r["enabled"] else "關掉"
            lines.append(f"{r['id']}｜{r['trigger']} → {r['response'][:60]}｜{channel}｜{state}")
        await interaction.response.send_message("\n".join(lines[:12]), ephemeral=True)

    @autoreply.command(name="edit", description="修改既有自動回覆")
    @app_commands.describe(rule_id="編號", trigger="新的觸發文字", response="新的回覆", exact="新的匹配方式")
    async def edit(self, interaction: discord.Interaction, rule_id: int, trigger: str | None = None, response: str | None = None, exact: bool | None = None):
        if not self.admin(interaction):
            await interaction.response.send_message("這個設定只有管理人員可以改。", ephemeral=True)
            return
        if trigger is not None and (not trigger.strip() or len(trigger) > 100):
            await interaction.response.send_message("觸發文字最多 100 字，而且不能空白。", ephemeral=True)
            return
        if response is not None and len(response) > 1500:
            await interaction.response.send_message("回覆最多 1500 字。", ephemeral=True)
            return
        if trigger is not None:
            trigger = trigger.strip()
        if response is not None:
            response = response.strip()
        mode = "exact" if exact else "contains" if exact is not None else None
        ok = storage.update_autoreply(interaction.guild.id, rule_id, trigger, response, mode)
        await interaction.response.send_message("改好了。" if ok else "找不到這個編號。", ephemeral=True)


    @autoreply.command(name="on", description="重新開啟一組已經關掉的自動回覆")
    @app_commands.describe(rule_id="自動回覆編號")
    async def enable(self, interaction: discord.Interaction, rule_id: int):
        if not self.admin(interaction):
            await interaction.response.send_message("這個設定只有管理人員可以改。", ephemeral=True)
            return
        ok = storage.update_autoreply(interaction.guild.id, rule_id, enabled=1)
        await interaction.response.send_message("開回來了。" if ok else "找不到這個編號。", ephemeral=True)

    @autoreply.command(name="off", description="暫時關掉一組自動回覆，不用刪除")
    @app_commands.describe(rule_id="自動回覆編號")
    async def disable(self, interaction: discord.Interaction, rule_id: int):
        if not self.admin(interaction):
            await interaction.response.send_message("這個設定只有管理人員可以改。", ephemeral=True)
            return
        ok = storage.update_autoreply(interaction.guild.id, rule_id, enabled=0)
        await interaction.response.send_message("先關掉了。" if ok else "找不到這個編號。", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot or not message.content.strip():
            return
        rules = storage.find_autoreplies(message.guild.id, message.content, message.channel.id)
        if not rules:
            return
        # 一則訊息最多觸發一組，避免洗屏。
        response = rules[0]["response"]
        response = (
            response
            .replace("{user}", getattr(message.author, "display_name", message.author.name))
            .replace("{server}", message.guild.name)
            .replace("{channel}", getattr(message.channel, "name", "這裡"))
        )
        try:
            await message.channel.send(
                response[:2000],
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            pass


class RolePanelCog(commands.Cog):
    rolepanel = app_commands.Group(name="rolepanel", description="建立按鈕或下拉選單讓大家自己領身分組")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.views_registered = False

    @commands.Cog.listener()
    async def on_ready(self):
        if self.views_registered:
            return
        for row in storage.get_role_panels():
            guild = self.bot.get_guild(row["guild_id"])
            if not guild or not row["message_id"]:
                continue
            try:
                self.bot.add_view(
                    RolePanelView.for_guild(row, guild),
                    message_id=row["message_id"],
                )
            except Exception as exc:
                print(f"Failed to restore role panel {row['id']}: {exc!r}")
        self.views_registered = True

    def admin(self, interaction: discord.Interaction) -> bool:
        return bool(interaction.guild and isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.manage_roles)

    @rolepanel.command(name="create", description="建立一個身分組領取面板")
    @app_commands.describe(title="面板標題", description="面板說明", mode="按鈕或下拉選單", roles="身分組 ID 或 @身分組，用逗號分隔")
    @app_commands.choices(mode=[
        app_commands.Choice(name="按鈕", value="buttons"),
        app_commands.Choice(name="下拉選單（可多選）", value="select"),
    ])
    async def create(self, interaction: discord.Interaction, title: str, description: str, mode: app_commands.Choice[str], roles: str):
        if not self.admin(interaction):
            await interaction.response.send_message("要管理身分組的人才能建立這個。", ephemeral=True)
            return
        if not interaction.guild.me.guild_permissions.manage_roles:
            await interaction.response.send_message("我自己沒有管理身分組權限。", ephemeral=True)
            return
        role_ids = parse_role_ids(roles)
        if not role_ids or len(role_ids) > 25:
            await interaction.response.send_message("請丟 1～25 個身分組 ID 或 @身分組給我。", ephemeral=True)
            return
        real_roles = [interaction.guild.get_role(rid) for rid in role_ids]
        real_roles = [r for r in real_roles if r]
        if not real_roles:
            await interaction.response.send_message("這些身分組我找不到。", ephemeral=True)
            return
        bot_member = interaction.guild.me
        if any(not is_safe_self_assignable_role(role) for role in real_roles):
            await interaction.response.send_message("自助身分組不能包含 @everyone 或具有管理、封禁、改頻道等高權限的身分組。", ephemeral=True)
            return
        if any(r >= bot_member.top_role for r in real_roles):
            await interaction.response.send_message("有身分組比我的最高身分組還高，先把 Bot 身分組往上移。", ephemeral=True)
            return

        if not title.strip() or len(title) > 256 or not description.strip() or len(description) > 4000:
            await interaction.response.send_message("標題不能空白（最多 256 字），說明最多 4000 字。", ephemeral=True)
            return
        panel_id = storage.save_role_panel(
            interaction.guild.id, interaction.channel.id, 0, title.strip(), description.strip()[:4000],
            mode.value, [r.id for r in real_roles]
        )
        row = next(r for r in storage.get_role_panels(interaction.guild.id) if r["id"] == panel_id)
        view = RolePanelView.for_guild(row, interaction.guild)
        embed = discord.Embed(title=title[:256], description=description[:4000], color=0x5865F2)
        if mode.value == "buttons":
            embed.add_field(name="怎麼用", value="點一下拿到，再點一次就會拿掉。", inline=False)
        else:
            embed.add_field(name="怎麼用", value="可以一次選好幾個，送出後就會套用；再選一次也能取消。", inline=False)
        try:
            await interaction.response.defer()
            msg = await interaction.followup.send(embed=embed, view=view, wait=True)
            with storage.connect() as con:
                con.execute("UPDATE role_panels SET message_id=? WHERE id=?", (msg.id, panel_id))
            await interaction.followup.send(f"面板好了，編號是 {panel_id}。重開 Bot 也會保留。", ephemeral=True)
        except Exception:
            storage.delete_role_panel(interaction.guild.id, panel_id)
            raise

    @rolepanel.command(name="list", description="看看這個伺服器有哪些身分組面板")
    async def list_panels(self, interaction: discord.Interaction):
        if not self.admin(interaction):
            await interaction.response.send_message("只有管理人員可以看這個。", ephemeral=True)
            return
        rows = storage.get_role_panels(interaction.guild.id)
        if not rows:
            await interaction.response.send_message("目前沒有身分組面板。", ephemeral=True)
            return
        await interaction.response.send_message("\n".join(f"{r['id']}｜{r['title'][:60]}｜{r['mode']}｜<#{r['channel_id']}>" for r in rows[:15]), ephemeral=True)

    @rolepanel.command(name="delete", description="刪掉一個身分組面板紀錄")
    @app_commands.describe(panel_id="面板編號")
    async def delete(self, interaction: discord.Interaction, panel_id: int):
        if not self.admin(interaction):
            await interaction.response.send_message("只有管理人員可以做這個。", ephemeral=True)
            return
        row = next((r for r in storage.get_role_panels(interaction.guild.id) if r["id"] == panel_id), None)
        if not row:
            await interaction.response.send_message("找不到這個面板。", ephemeral=True)
            return
        ch = interaction.guild.get_channel(row["channel_id"])
        if ch:
            try:
                msg = await ch.fetch_message(row["message_id"])
                await msg.delete()
            except Exception:
                pass
        storage.delete_role_panel(interaction.guild.id, panel_id)
        await interaction.response.send_message("面板刪掉了。", ephemeral=True)


class PollView(ui.View):
    def __init__(self, poll_id: int, options: list[str], multiple: bool):
        super().__init__(timeout=None)
        self.poll_id = poll_id
        self.multiple = multiple
        select = ui.StringSelect(
            placeholder="選你的答案" if not multiple else "可以選一個以上",
            min_values=1,
            max_values=len(options) if multiple else 1,
            options=[discord.SelectOption(label=o[:100], value=str(i)) for i, o in enumerate(options[:10])],
            custom_id=f"poll:{poll_id}",
        )
        async def callback(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            row = storage.get_poll(self.poll_id)
            if not row or row["closed"]:
                await interaction.followup.send("這個投票已經關了。", ephemeral=True)
                return
            if row["ends_at"] and row["ends_at"] <= time.time():
                storage.close_poll(self.poll_id)
                await interaction.followup.send("時間到了，這票已經結束。", ephemeral=True)
                return
            indexes = [int(v) for v in select.values]
            storage.replace_poll_votes(self.poll_id, interaction.user.id, indexes)
            counts = storage.get_poll_counts(self.poll_id)
            embed = PollCog.build_embed(row, counts)
            try:
                await interaction.message.edit(embed=embed, view=self)
            except Exception:
                pass
            await interaction.followup.send("收到，你的票記好了。", ephemeral=True)
        select.callback = callback
        self.add_item(select)


class PollCog(commands.Cog):
    poll = app_commands.Group(name="poll", description="建立好玩的群組投票")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.views_registered = False

    @commands.Cog.listener()
    async def on_ready(self):
        if self.views_registered:
            return
        for row in storage.list_active_polls():
            guild = self.bot.get_guild(row["guild_id"])
            if not guild or not row["message_id"]:
                continue
            try:
                options = json.loads(row["options"])
                self.bot.add_view(
                    PollView(int(row["id"]), options, bool(row["multiple"])),
                    message_id=row["message_id"],
                )
            except Exception as exc:
                print(f"Failed to restore poll {row['id']}: {exc!r}")
        self.views_registered = True

    @staticmethod
    def build_embed(row, counts):
        options = json.loads(row["options"])
        total = sum(counts.values())
        lines = []
        for i, option in enumerate(options):
            n = counts.get(i, 0)
            pct = (n / total * 100) if total else 0
            lines.append(f"**{i+1}. {option}** — {n} 票（{pct:.0f}%）")
        embed = discord.Embed(title="🗳️ " + row["question"], description="\n".join(lines), color=0x5865F2)
        embed.set_footer(text=(f"共 {total} 份選擇" if row["multiple"] else f"共 {total} 票"))
        return embed

    @poll.command(name="create", description="建立一個下拉選單投票")
    @app_commands.describe(question="投票問題", options="選項用逗號分隔，2～10 個", multiple="可以選多個嗎", anonymous="是否不顯示投票者", minutes="幾分鐘後結束，0 代表不設時間")
    async def create(self, interaction: discord.Interaction, question: str, options: str, multiple: bool = False, anonymous: bool = False, minutes: app_commands.Range[int,0,10080] = 0):
        if not interaction.guild:
            await interaction.response.send_message("這個要在伺服器裡用喔。", ephemeral=True)
            return
        if not question.strip() or len(question) > 250:
            await interaction.response.send_message("投票問題不能空白，最多 250 字。", ephemeral=True)
            return
        items = [x.strip() for x in options.split(",") if x.strip()]
        if not (2 <= len(items) <= 10) or any(len(x) > 100 for x in items):
            await interaction.response.send_message("選項請放 2～10 個，中間用逗號隔開。", ephemeral=True)
            return
        import time
        ends_at = time.time() + minutes * 60 if minutes else None
        poll_id = storage.save_poll(interaction.guild.id, interaction.channel.id, 0, question.strip(), items, anonymous, multiple, ends_at)
        row = storage.get_poll(poll_id)
        view = PollView(poll_id, items, multiple)
        embed = PollCog.build_embed(row, {})
        embed.set_footer(text=("可複選" if multiple else "單選") + (f" · <t:{int(ends_at)}:R>" if ends_at else " · 不限時間"))
        try:
            await interaction.response.defer()
            msg = await interaction.followup.send(embed=embed, view=view, wait=True)
            with storage.connect() as con:
                con.execute("UPDATE polls SET message_id=? WHERE id=?", (msg.id, poll_id))
        except Exception:
            storage.delete_poll(poll_id)
            raise
        await interaction.followup.send(f"投票開好了，編號 {poll_id}。", ephemeral=True)

    @poll.command(name="end", description="提前結束一個投票")
    @app_commands.describe(poll_id="投票編號")
    async def end(self, interaction: discord.Interaction, poll_id: int):
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("這個只有管理人員可以結束。", ephemeral=True)
            return
        row = storage.get_poll(poll_id)
        if not row or row["guild_id"] != interaction.guild.id:
            await interaction.response.send_message("找不到這個投票。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        storage.close_poll(poll_id)
        counts = storage.get_poll_counts(poll_id)
        ch = interaction.guild.get_channel(row["channel_id"])
        if ch:
            try:
                msg = await ch.fetch_message(row["message_id"])
                await msg.edit(embed=PollCog.build_embed(row, counts), view=None)
            except Exception:
                pass
        await interaction.followup.send("投票關掉了。", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoReplyCog(bot))
    await bot.add_cog(RolePanelCog(bot))
    await bot.add_cog(PollCog(bot))
