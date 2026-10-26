from __future__ import annotations

import random
import sqlite3
from contextlib import closing

import discord
from discord import app_commands
from discord.ext import commands

from cogs.economy import (
    DB_PATH,
    MAX_BALANCE,
    EconomyStore,
    add_balance,
    format_coins,
    is_infinite,
    subtract_balance,
)
from cogs.public_response import PublicizeView

MAX_BET = 100000


class MiniGameStore:
    def __init__(self):
        self.economy = EconomyStore()

    def play(self, guild_id: int, user_id: int, bet: int, payout: int, game: str, detail: str):
        if type(bet) is not int or not 1 <= bet <= MAX_BET:
            return False, f"下注金額必須介於 1 至 {MAX_BET:,} 金幣。"
        with closing(self.economy.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            self.economy.ensure(con, guild_id, user_id)
            row = con.execute("SELECT wallet FROM users WHERE user_id=?", (user_id,)).fetchone()
            wallet = row["wallet"]
            if not is_infinite(wallet) and int(wallet) < bet:
                con.rollback()
                return False, f"現金不足，下注需要 {bet:,}，目前有 {format_coins(wallet)}。"
            if not is_infinite(wallet) and int(wallet) - bet + payout > MAX_BALANCE:
                con.rollback()
                return False, "結算後現金會超出系統上限，本局取消。"
            updated = "inf" if is_infinite(wallet) else str(int(wallet) - bet + payout)
            con.execute("UPDATE users SET wallet=? WHERE user_id=?", (updated, user_id))
            con.commit()
        net = payout - bet
        result = "贏得" if net > 0 else "退回" if net == 0 else "損失"
        return True, (
            f"{detail}\n"
            f"下注：{bet:,} 金幣\n"
            f"本局{result}：{abs(net):,} 金幣\n"
            f"實際返還：{payout:,} 金幣\n"
            f"目前現金：{format_coins(updated)} 金幣"
        )


class EconomyMiniGames(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = MiniGameStore()

    async def _guild(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id is not None:
            return True
        await interaction.response.send_message("小遊戲只能在伺服器裡使用。", ephemeral=True)
        return False

    async def _reply(self, interaction: discord.Interaction, content: str, public: bool):
        if public:
            await interaction.response.send_message(content, ephemeral=False, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.response.send_message(
                content,
                ephemeral=True,
                view=PublicizeView(interaction.user.id, content=content),
                allowed_mentions=discord.AllowedMentions.none(),
            )

    @app_commands.command(name=app_commands.locale_str("guess-number", zh_TW="猜數字"), description="猜中 1 至 5 的數字可獲得 4 倍返還")
    @app_commands.choices(guess=[
        app_commands.Choice(name="1", value=1),
        app_commands.Choice(name="2", value=2),
        app_commands.Choice(name="3", value=3),
        app_commands.Choice(name="4", value=4),
        app_commands.Choice(name="5", value=5),
    ])
    @app_commands.rename(guess=app_commands.locale_str("guess", zh_TW="猜的數字"), bet=app_commands.locale_str("bet", zh_TW="下注金額"), public=app_commands.locale_str("public", zh_TW="公開"))
    @app_commands.describe(bet="下注 1 至 100,000 金幣，猜中返還 4 倍下注", public="是否將遊戲結果公開發到頻道")
    async def guess_number(self, interaction: discord.Interaction, guess: app_commands.Choice[int], bet: app_commands.Range[int, 1, MAX_BET], public: bool = False):
        if not await self._guild(interaction):
            return
        answer = random.randint(1, 5)
        payout = bet * 4 if guess.value == answer else 0
        detail = f"猜數字結果：你猜 {guess.value}，答案是 {answer}。"
        _, message = self.store.play(interaction.guild_id, interaction.user.id, bet, payout, "guess", detail)
        await self._reply(interaction, message, public)

    @app_commands.command(name=app_commands.locale_str("rock-paper-scissors", zh_TW="猜拳"), description="與機器人猜拳，贏得 2 倍返還，平手退回下注")
    @app_commands.choices(choice=[
        app_commands.Choice(name="石頭", value="rock"),
        app_commands.Choice(name="剪刀", value="scissors"),
        app_commands.Choice(name="布", value="paper"),
    ])
    @app_commands.rename(choice=app_commands.locale_str("choice", zh_TW="出拳"), bet=app_commands.locale_str("bet", zh_TW="下注金額"), public=app_commands.locale_str("public", zh_TW="公開"))
    @app_commands.describe(bet="下注 1 至 100,000 金幣", public="是否將遊戲結果公開發到頻道")
    async def rock_paper_scissors(self, interaction: discord.Interaction, choice: app_commands.Choice[str], bet: app_commands.Range[int, 1, MAX_BET], public: bool = False):
        if not await self._guild(interaction):
            return
        bot_choice = random.choice(("rock", "scissors", "paper"))
        labels = {"rock": "石頭", "scissors": "剪刀", "paper": "布"}
        if choice.value == bot_choice:
            payout, result = bet, "平手，退回下注。"
        elif (choice.value, bot_choice) in {("rock", "scissors"), ("scissors", "paper"), ("paper", "rock")}:
            payout, result = bet * 2, "你贏了！"
        else:
            payout, result = 0, "你輸了。"
        detail = f"猜拳結果：你出{labels[choice.value]}，機器人出{labels[bot_choice]}。{result}"
        _, message = self.store.play(interaction.guild_id, interaction.user.id, bet, payout, "rps", detail)
        await self._reply(interaction, message, public)

    @app_commands.command(name=app_commands.locale_str("scratch-card", zh_TW="刮刮樂"), description="購買一張虛擬刮刮樂，隨機獲得不同返還")
    @app_commands.rename(bet=app_commands.locale_str("bet", zh_TW="票券價格"), public=app_commands.locale_str("public", zh_TW="公開"))
    @app_commands.describe(bet="每張 1 至 100,000 金幣", public="是否將遊戲結果公開發到頻道")
    async def scratch_card(self, interaction: discord.Interaction, bet: app_commands.Range[int, 1, MAX_BET], public: bool = False):
        if not await self._guild(interaction):
            return
        multiplier, title = random.choices(
            [(0, "銘謝惠顧"), (1, "回本獎"), (2, "小獎"), (5, "大獎")],
            weights=[45, 30, 20, 5],
            k=1,
        )[0]
        payout = bet * multiplier
        detail = f"刮刮樂結果：{title}，返還倍率 ×{multiplier}。"
        _, message = self.store.play(interaction.guild_id, interaction.user.id, bet, payout, "scratch", detail)
        await self._reply(interaction, message, public)


async def setup(bot: commands.Bot):
    await bot.add_cog(EconomyMiniGames(bot))
