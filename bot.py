import asyncio
import os
import traceback
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

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
    help as help_cog,
)


OWNER_ID = int(os.getenv("OWNER_ID", "1140900506198351924"))
ALLOWED_ROLE_ID = int(os.getenv("ALLOWED_ROLE_ID", "1509577038443319416"))
MAX_COUNT = 1_000_000_000_000
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
            help_cog,
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

        for attempt in range(3):
            try:
                if ALLOWED_GUILD_IDS:
                    # Copy local commands to each allowlisted guild before clearing the
                    # global command set, so commands are not accidentally discarded.
                    guilds = [discord.Object(id=guild_id) for guild_id in sorted(ALLOWED_GUILD_IDS)]
                    for guild in guilds:
                        self.tree.copy_global_to(guild=guild)
                    self.tree.clear_commands(guild=None)
                    await self.tree.sync()
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
        print(f"Logged in as {self.user} ({self.user.id})")
        print(f"Connected guilds: {len(self.guilds)}")
        if not reminder_worker.is_running():
            reminder_worker.start()


    async def close(self):
        reminder_worker.cancel()
        for job_key in list(active_spam):
            active_spam[job_key]["running"] = False
        await super().close()


bot = Bot()


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


@bot.tree.command(name="spam", description="在多個頻道快速發送同一句訊息")
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

    active_spam[job_key] = {"running": False, "task": None}
    view = ChannelSelectView()
    await interaction.response.send_message("要發去哪幾個頻道？選完我再開始。", view=view, ephemeral=True)
    await view.wait()

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

    active_spam[job_key] = {"running": True, "task": None}
    embed = discord.Embed(
        title="好，開始了",
        description=f"次數：{count}\n頻道：{', '.join(c.mention for c in valid_channels)}",
        color=0x57F287,
    )
    if skipped:
        embed.add_field(name="我跳過了", value=", ".join(skipped), inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)

    task = asyncio.create_task(
        run_spam(job_key, interaction.channel, valid_channels, content, count)
    )
    active_spam[job_key]["task"] = task


@bot.tree.command(name="stopspam", description="停止自己或有權限時停止別人的發送工作")
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
        active_spam[target_key]["running"] = False
        task = active_spam[target_key].get("task")
        if task and not task.done():
            task.cancel()
        await interaction.response.send_message(f"好，{target.mention} 的工作停掉了。", ephemeral=True)
    else:
        await interaction.response.send_message("他現在沒有正在跑的發送工作。", ephemeral=True)


@bot.tree.command(name="history", description="搜尋並清掉歷史訊息")
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
        for row in storage.due_reminders():
            channel = bot.get_channel(row["channel_id"])
            if not channel:
                continue
            try:
                await channel.send(f"<@{row['user_id']}> ⏰ 你之前叫我提醒你的：{row['message']}")
                storage.complete_reminder(row["id"])
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                continue

        now = __import__("time").time()
        for row in storage.list_active_polls():
            if row["ends_at"] and row["ends_at"] <= now:
                storage.close_poll(row["id"])
                channel = bot.get_channel(row["channel_id"])
                if channel and row["message_id"]:
                    try:
                        msg = await channel.fetch_message(row["message_id"])
                        counts = storage.get_poll_counts(row["id"])
                        embed = social.PollCog.build_embed(row, counts)
                        embed.set_footer(text="投票結束")
                        await msg.edit(embed=embed, view=None)
                    except Exception:
                        pass

        for row in storage.list_active_giveaways():
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
