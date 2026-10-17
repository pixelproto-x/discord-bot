import random
import time
import json

import discord
from discord import app_commands, ui
from discord.ext import commands

import storage


class GiveawayView(ui.View):
    def __init__(self, giveaway_id: int):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id
        button = ui.Button(label="🎉 參加抽獎", style=discord.ButtonStyle.success, custom_id=f"giveaway:{giveaway_id}")
        async def callback(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            row = storage.get_giveaway(self.giveaway_id)
            if not row or row["ended"] or row["ends_at"] <= time.time():
                await interaction.followup.send("這個抽獎已經結束了。", ephemeral=True)
                return
            storage.enter_giveaway(self.giveaway_id, interaction.user.id)
            entries = storage.get_giveaway_entries(self.giveaway_id)
            try:
                await interaction.message.edit(content=GiveawayCog.text(row, len(entries)))
            except Exception:
                pass
            await interaction.followup.send("你有進抽獎名單了，祝你好運。🍀", ephemeral=True)
        button.callback = callback
        self.add_item(button)


class GiveawayCog(commands.Cog):
    giveaway = app_commands.Group(name="giveaway", description="辦個簡單又好玩的抽獎")

    def __init__(self, bot):
        self.bot = bot
        self.views_registered = False
        self.finish_locks = {}

    @commands.Cog.listener()
    async def on_ready(self):
        if self.views_registered:
            return
        for row in storage.list_active_giveaways():
            guild = self.bot.get_guild(row["guild_id"])
            if not guild or not row["message_id"]:
                continue
            try:
                self.bot.add_view(
                    GiveawayView(int(row["id"])),
                    message_id=row["message_id"],
                )
            except Exception as exc:
                print(f"Failed to restore giveaway {row['id']}: {exc!r}")
        self.views_registered = True


    @staticmethod
    def text(row, entry_count: int):
        left = max(0, int(row["ends_at"] - time.time()))
        return (
            f"🎁 **抽獎：{row['prize']}**\n"
            f"得獎人數：**{row['winners']}**\n"
            f"目前參加：**{entry_count} 人**\n"
            f"剩下：<t:{int(row['ends_at'])}:R>\n\n"
            f"點下面按鈕參加，最後我會直接抽。"
        )

    @giveaway.command(name="create", description="建立一個限時抽獎")
    @app_commands.describe(prize="獎品", duration="例如 10m、1h、1d", winners="得獎人數")
    async def create(self, interaction, prize: str, duration: str, winners: app_commands.Range[int,1,20] = 1):
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("這個只有管理人員可以開。", ephemeral=True)
            return
        from cogs.utility import parse_duration
        seconds = parse_duration(duration)
        if seconds is None:
            await interaction.response.send_message("時間格式用 10m、1h、1d。", ephemeral=True)
            return
        ends_at = time.time() + seconds
        if not prize.strip() or len(prize) > 200:
            await interaction.response.send_message("獎品不能空白，最多 200 字。", ephemeral=True)
            return
        gid = storage.save_giveaway(interaction.guild.id, interaction.channel.id, 0, prize.strip(), winners, ends_at)
        try:
            await interaction.response.defer()
            msg = await interaction.followup.send(
                GiveawayCog.text(storage.get_giveaway(gid), 0),
                view=GiveawayView(gid),
                wait=True,
            )
            with storage.connect() as con:
                con.execute("UPDATE giveaways SET message_id=? WHERE id=?", (msg.id, gid))
        except Exception:
            storage.delete_giveaway(gid)
            raise
        await interaction.followup.send(f"抽獎開了，編號 {gid}。", ephemeral=True)

    @giveaway.command(name="reroll", description="重新抽一位新的得獎者")
    @app_commands.describe(giveaway_id="抽獎編號")
    async def reroll(self, interaction, giveaway_id: int):
        if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("這個只有管理人員可以用。", ephemeral=True)
            return
        row = storage.get_giveaway(giveaway_id)
        if not row or row["guild_id"] != interaction.guild.id:
            await interaction.response.send_message("找不到這個抽獎。", ephemeral=True)
            return
        if not row["ended"]:
            await interaction.response.send_message("抽獎還沒結束，先讓它結束再重抽。", ephemeral=True)
            return
        entries = storage.get_giveaway_entries(giveaway_id)
        if not entries:
            await interaction.response.send_message("沒有人參加，沒有得獎者可以抽。", ephemeral=True)
            return
        previous = storage.get_giveaway_previous_winners(giveaway_id)
        candidates = [uid for uid in entries if uid not in previous]
        if not candidates:
            await interaction.response.send_message("所有參加者都已經抽過了，沒有新的得獎者。", ephemeral=True)
            return
        winners = random.sample(candidates, min(row["winners"], len(candidates)))
        storage.record_giveaway_winners(giveaway_id, winners)
        await interaction.response.send_message("🎉 重新抽到：" + " ".join(f"<@{uid}>" for uid in winners))

    @giveaway.command(name="end", description="提前結束抽獎並抽出得獎者")
    @app_commands.describe(giveaway_id="抽獎編號")
    async def end(self, interaction, giveaway_id: int):
        if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("這個只有管理人員可以用。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        finished = await self.finish(giveaway_id, interaction.guild, manual=True)
        await interaction.followup.send(
            "抽獎已結束並完成抽選。" if finished
            else "找不到這個抽獎、它已經結束，或它不屬於這個伺服器。",
            ephemeral=True,
        )

    async def finish(self, giveaway_id, guild, manual=False):
        lock = self.finish_locks.setdefault(int(giveaway_id), __import__("asyncio").Lock())
        async with lock:
            row = storage.get_giveaway(giveaway_id)
            if not row or row["ended"] or row["guild_id"] != guild.id:
                return False

            entries = storage.get_giveaway_entries(giveaway_id)
            winners = random.sample(entries, min(row["winners"], len(entries))) if entries else []

            # Persist the result and close entries before awaiting Discord API calls.
            # Late button clicks then cannot join after the winner list is selected.
            storage.record_giveaway_winners(giveaway_id, winners)
            storage.end_giveaway(giveaway_id)

            ch = guild.get_channel(row["channel_id"])
            if ch and row["message_id"]:
                ending = (
                    f"🎁 **抽獎：{row['prize']}**\n"
                    f"得獎人數：**{row['winners']}**\n"
                    f"目前參加：**{len(entries)} 人**\n"
                    f"剩下：<t:{int(row['ends_at'])}:R>\n\n"
                    f"🏁 **抽獎結束**\n"
                    + (f"🎉 得獎：{' '.join(f'<@{uid}>' for uid in winners)}" if winners else "沒有人參加。")
                )
                try:
                    msg = await ch.fetch_message(row["message_id"])
                    await msg.edit(content=ending, view=None)
                except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                    pass
                if winners:
                    try:
                        await ch.send(f"🎉 恭喜 {' '.join(f'<@{uid}>' for uid in winners)}！抽到的是 **{row['prize']}**。")
                    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                        pass
            return True




async def setup(bot):
    await bot.add_cog(GiveawayCog(bot))
