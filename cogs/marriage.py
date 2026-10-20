import time

import discord
from discord import app_commands
from discord.ext import commands

import storage


def init_marriage_tables():
    with storage.connect() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS game_marriages (
            guild_id INTEGER NOT NULL,
            user1_id INTEGER NOT NULL,
            user2_id INTEGER NOT NULL,
            married_at REAL NOT NULL,
            PRIMARY KEY (guild_id, user1_id, user2_id)
        );
        CREATE TABLE IF NOT EXISTS game_marriage_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            proposer_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            created_at REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
        );
        CREATE INDEX IF NOT EXISTS idx_game_marriage_proposals_pending
        ON game_marriage_proposals(guild_id, proposer_id, target_id, status);
        """)


def get_marriage(guild_id: int, user_id: int):
    with storage.connect() as con:
        return con.execute(
            """SELECT * FROM game_marriages
               WHERE guild_id=? AND (user1_id=? OR user2_id=?)
               LIMIT 1""",
            (guild_id, user_id, user_id),
        ).fetchone()


class ProposalView(discord.ui.View):
    def __init__(self, proposal_id: int, proposer_id: int, target_id: int):
        super().__init__(timeout=86400)
        self.proposal_id = proposal_id
        self.proposer_id = proposer_id
        self.target_id = target_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.target_id:
            await interaction.response.send_message(
                "這個求婚是給被求婚的那位，讓他本人選就好啦。", ephemeral=True
            )
            return False
        return True

    async def resolve_proposal(self, interaction: discord.Interaction, accept: bool):
        if not interaction.guild_id:
            await interaction.response.send_message("這個選項只能在原本的伺服器使用。", ephemeral=True)
            return

        with storage.connect() as con:
            proposal = con.execute(
                "SELECT * FROM game_marriage_proposals WHERE id=? AND guild_id=?",
                (self.proposal_id, interaction.guild_id),
            ).fetchone()
            if not proposal or proposal["status"] != "pending":
                await interaction.response.send_message("這份求婚已經處理過了。", ephemeral=True)
                return

            if accept:
                con.execute("BEGIN IMMEDIATE")
                proposer_marriage = con.execute(
                    "SELECT 1 FROM game_marriages WHERE guild_id=? AND (user1_id=? OR user2_id=?)",
                    (interaction.guild_id, self.proposer_id, self.proposer_id),
                ).fetchone()
                target_marriage = con.execute(
                    "SELECT 1 FROM game_marriages WHERE guild_id=? AND (user1_id=? OR user2_id=?)",
                    (interaction.guild_id, self.target_id, self.target_id),
                ).fetchone()
                if proposer_marriage or target_marriage:
                    con.execute(
                        "UPDATE game_marriage_proposals SET status='cancelled' WHERE id=?",
                        (self.proposal_id,),
                    )
                    await interaction.response.send_message(
                        "這段期間有人已經有配偶了，這份求婚不能再成立。", ephemeral=True
                    )
                    return
                con.execute(
                    "INSERT INTO game_marriages (guild_id,user1_id,user2_id,married_at) VALUES (?,?,?,?)",
                    (interaction.guild_id, min(self.proposer_id, self.target_id),
                     max(self.proposer_id, self.target_id), time.time()),
                )
                con.execute(
                    "UPDATE game_marriage_proposals SET status='accepted' WHERE id=?",
                    (self.proposal_id,),
                )
            else:
                con.execute(
                    "UPDATE game_marriage_proposals SET status='declined' WHERE id=?",
                    (self.proposal_id,),
                )

        self.disable_all_items()
        proposer = interaction.guild.get_member(self.proposer_id)
        proposer_name = proposer.mention if proposer else f"<@{self.proposer_id}>"
        if accept:
            embed = discord.Embed(
                title="💍 求婚成功！",
                description=f"{interaction.user.mention} 接受了 {proposer_name} 的求婚！\n\n從今天開始，成為伺服器裡的遊戲夫妻啦 💖",
                color=0xFF78B9,
            )
        else:
            embed = discord.Embed(
                title="💌 求婚被拒絕了",
                description=f"{interaction.user.mention} 婉拒了 {proposer_name} 的求婚。\n沒關係，勇氣值得一個拍拍。",
                color=0x7F8C8D,
            )
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    @discord.ui.button(label="同意 💖", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.resolve_proposal(interaction, True)

    @discord.ui.button(label="拒絕 💔", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.resolve_proposal(interaction, False)


class MarriageCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_marriage_tables()

    @app_commands.command(name="propose", name_localizations={"zh-TW": "求婚"}, description_localizations={"zh-TW": "向伺服器裡的成員求婚，對方可以選同意或拒絕"}, description="向伺服器裡的成員求婚，對方可以選同意或拒絕")
    @app_commands.describe(member="你要求婚的成員", message="你想對他說的話")
    async def propose(self, interaction: discord.Interaction, member: discord.Member, message: str):
        if not interaction.guild:
            await interaction.response.send_message("這個指令只能在伺服器裡使用。", ephemeral=True)
            return
        if member.id == interaction.user.id:
            await interaction.response.send_message("不能向自己求婚啦 😂", ephemeral=True)
            return
        if member.bot:
            await interaction.response.send_message("機器人不能接受這種求婚啦。", ephemeral=True)
            return
        if get_marriage(interaction.guild.id, interaction.user.id):
            await interaction.response.send_message("你已經有遊戲配偶了，要先用 /離婚 才能再求婚。", ephemeral=True)
            return
        if get_marriage(interaction.guild.id, member.id):
            await interaction.response.send_message("對方已經有遊戲配偶了。", ephemeral=True)
            return
        with storage.connect() as con:
            pending = con.execute(
                """SELECT 1 FROM game_marriage_proposals
                   WHERE guild_id=? AND proposer_id=? AND target_id=? AND status='pending'""",
                (interaction.guild.id, interaction.user.id, member.id),
            ).fetchone()
            if pending:
                await interaction.response.send_message("你已經向這位成員求婚過了，等對方選擇吧。", ephemeral=True)
                return
            proposal_id = con.execute(
                """INSERT INTO game_marriage_proposals
                   (guild_id,proposer_id,target_id,channel_id,created_at,status)
                   VALUES (?,?,?,?,?,'pending')""",
                (interaction.guild.id, interaction.user.id, member.id, interaction.channel_id, time.time()),
            ).lastrowid

        embed = discord.Embed(
            title="💌 有人向你求婚了！",
            description=(
                f"**{interaction.user.display_name}** 正式向 **{member.display_name}** 求婚！\n\n"
                f"> {message.strip() or '我想和你一起創造更多回憶。'}\n\n"
                "請由被求婚的本人選擇是否同意。這只是伺服器裡的娛樂功能 💗"
            ),
            color=0xFF78B9,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_author(name=f"給 {member.display_name} 的求婚", icon_url=member.display_avatar.url)
        embed.set_footer(text="求婚有效時間：24 小時")
        view = ProposalView(int(proposal_id), interaction.user.id, member.id)
        await interaction.response.send_message(
            content=member.mention,
            embed=embed,
            view=view,
            allowed_mentions=discord.AllowedMentions(users=[member]),
        )

    @app_commands.command(name="marriage", name_localizations={"zh-TW": "婚姻狀態"}, description_localizations={"zh-TW": "查看你或其他成員的遊戲婚姻狀態"}, description="查看你或其他成員的遊戲婚姻狀態")
    @app_commands.describe(member="想查看的成員，不填就是自己")
    async def marriage_status(self, interaction: discord.Interaction, member: discord.Member | None = None):
        if not interaction.guild:
            await interaction.response.send_message("這個指令只能在伺服器裡使用。", ephemeral=True)
            return
        subject = member or interaction.user
        row = get_marriage(interaction.guild.id, subject.id)
        if not row:
            await interaction.response.send_message(f"{subject.mention} 目前單身中 💌")
            return
        spouse_id = row["user2_id"] if row["user1_id"] == subject.id else row["user1_id"]
        spouse = interaction.guild.get_member(spouse_id)
        married_at = f"<t:{int(row['married_at'])}:D>"
        embed = discord.Embed(
            title="💍 遊戲婚姻狀態",
            description=f"{subject.mention} 的遊戲配偶是 {spouse.mention if spouse else f'<@{spouse_id}>'}\n結婚紀念日：{married_at}",
            color=0xFF78B9,
        )
        embed.set_thumbnail(url=subject.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="divorce", name_localizations={"zh-TW": "離婚"}, description_localizations={"zh-TW": "結束目前的遊戲婚姻關係"}, description="結束目前的遊戲婚姻關係")
    async def divorce(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("這個指令只能在伺服器裡使用。", ephemeral=True)
            return
        row = get_marriage(interaction.guild.id, interaction.user.id)
        if not row:
            await interaction.response.send_message("你目前沒有遊戲配偶。", ephemeral=True)
            return
        with storage.connect() as con:
            con.execute(
                "DELETE FROM game_marriages WHERE guild_id=? AND user1_id=? AND user2_id=?",
                (interaction.guild.id, row["user1_id"], row["user2_id"]),
            )
        spouse_id = row["user2_id"] if row["user1_id"] == interaction.user.id else row["user1_id"]
        await interaction.response.send_message(
            f"💔 {interaction.user.mention} 和 <@{spouse_id}> 已結束遊戲婚姻關係。只是遊戲設定，不影響現實。"
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(MarriageCog(bot))
