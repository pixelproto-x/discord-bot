import asyncio
import random
import re
import time
from datetime import timedelta

import discord
from discord import app_commands, ui
from discord.ext import commands

import storage


RPS = {"剪刀": "布", "石頭": "剪刀", "布": "石頭"}
TRIVIA = [
    ("Discord 是用什麼顏色的品牌主色之一？", ["藍紫", "紅色", "綠色", "橘色"], 0),
    ("地球上最大的海洋是？", ["大西洋", "印度洋", "太平洋", "北冰洋"], 2),
    ("1 小時有幾分鐘？", ["30", "45", "60", "90"], 2),
    ("Minecraft 的苦力怕最常見的顏色是？", ["藍色", "綠色", "白色", "紫色"], 1),
]
TRUTH = ["你最近最常玩的遊戲是什麼？", "群組裡你最常跟誰一起玩？", "你做過最蠢但最好笑的事是什麼？", "最近有沒有一件小事讓你很開心？"]
DARE = ["用一句超中二的話講話 30 秒。", "把你的 Discord 狀態改成好笑的句子 10 分鐘。", "下一句話只能用 emoji 表達。", "在語音裡用很認真的語氣講一句很荒謬的話。"]
WOULD = [
    ("每天只能玩一款遊戲", "每天只能看一部影片"),
    ("永遠不能吃炸雞", "永遠不能喝珍珠奶茶"),
    ("手機沒電一天", "網路斷一天"),
]
WORDLE_WORDS = ["apple", "grape", "table", "chair", "house", "world", "plant", "music", "brain", "smile"]
HANGMAN_WORDS = ["discord", "minecraft", "roblox", "python", "youtube", "gaming", "friend"]



def eligible_roulette_members(participant_ids, guild, bot_member):
    """Return opted-in members that the bot is allowed to timeout."""
    eligible = []
    for member_id in participant_ids:
        member = guild.get_member(member_id)
        if member is None or member.bot:
            continue
        if member.id in {guild.owner_id, bot_member.id}:
            continue
        if member.top_role >= bot_member.top_role:
            continue
        eligible.append(member)
    return eligible


class RouletteView(ui.View):
    def __init__(self, cog, guild, host_id, session_key):
        super().__init__(timeout=90)
        self.cog = cog
        self.guild = guild
        self.host_id = host_id
        self.session_key = session_key
        self.participant_ids = {host_id}
        self.message = None

    def status_text(self):
        mentions = [f"<@{member_id}>" for member_id in sorted(self.participant_ids)]
        joined = "、".join(mentions) if mentions else "目前沒有人"
        return (
            "🎲 **俄羅斯輪盤｜隨機抽選**\n"
            "按「加入遊戲」代表自願參加；主持人按「開始抽選」後，"
            "系統會從參加者中隨機抽出一位，並由 Bot Timeout 60 秒。\n\n"
            f"**目前參加者（{len(self.participant_ids)}）**：{joined}\n"
            "至少需要 2 位參加者才能抽選。"
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id != self.guild.id:
            await interaction.response.send_message("這場遊戲不在這個伺服器。", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        self.cog.roulette_sessions.pop(self.session_key, None)
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(content="⌛ 這場俄羅斯輪盤已逾時結束。", view=self)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

    @ui.button(label="加入遊戲", style=discord.ButtonStyle.primary)
    async def join_button(self, interaction: discord.Interaction, button: ui.Button):
        member = self.guild.get_member(interaction.user.id)
        bot_member = self.guild.me
        if member is None or bot_member is None:
            await interaction.response.send_message("找不到伺服器成員資料，請稍後再試。", ephemeral=True)
            return
        if member.bot or member.id in {self.guild.owner_id, bot_member.id}:
            await interaction.response.send_message("這個帳號不能參加抽選。", ephemeral=True)
            return
        if member.top_role >= bot_member.top_role:
            await interaction.response.send_message("Bot 無法對這個身分組階級的成員執行 Timeout，因此不能加入。", ephemeral=True)
            return
        if member.id in self.participant_ids:
            await interaction.response.send_message("你已經加入這場遊戲了。", ephemeral=True)
            return
        self.participant_ids.add(member.id)
        button.label = f"加入遊戲（{len(self.participant_ids)}）"
        await interaction.response.edit_message(content=self.status_text(), view=self)

    @ui.button(label="開始抽選", style=discord.ButtonStyle.danger)
    async def draw_button(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.host_id:
            await interaction.response.send_message("只有開啟這場遊戲的人可以開始抽選。", ephemeral=True)
            return
        bot_member = self.guild.me
        if bot_member is None or not bot_member.guild_permissions.moderate_members:
            await interaction.response.send_message("Bot 缺少「調節成員」權限，無法執行 60 秒 Timeout。", ephemeral=True)
            return
        participants = eligible_roulette_members(self.participant_ids, self.guild, bot_member)
        if len(participants) < 2:
            await interaction.response.send_message("至少需要 2 位仍在伺服器、且可被 Bot Timeout 的參加者。", ephemeral=True)
            return

        selected = random.choice(participants)
        for item in self.children:
            item.disabled = True
        await interaction.response.defer()
        self.stop()
        self.cog.roulette_sessions.pop(self.session_key, None)
        try:
            await selected.timeout(
                discord.utils.utcnow() + timedelta(minutes=1),
                reason="俄羅斯輪盤小遊戲隨機抽選（參加者自願加入）",
            )
        except discord.Forbidden:
            result = f"抽中了 {selected.mention}，但 Discord 拒絕執行 Timeout；請檢查 Bot 權限與身分組階級。"
        except discord.HTTPException:
            result = f"抽中了 {selected.mention}，但 Discord 暫時無法執行 Timeout，請稍後再試。"
        else:
            result = f"🎯 抽選結果：{selected.mention}\n已依照遊戲規則 Timeout **1 分鐘**。"
        try:
            await interaction.message.edit(content=result, view=None)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    @ui.button(label="取消遊戲", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.host_id:
            await interaction.response.send_message("只有開啟這場遊戲的人可以取消。", ephemeral=True)
            return
        self.cog.roulette_sessions.pop(self.session_key, None)
        self.stop()
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="已取消這場俄羅斯輪盤。", view=None)


class GameCog(commands.Cog):
    game = app_commands.Group(name=app_commands.locale_str("game", zh_TW="遊戲"), description=app_commands.locale_str("朋友群裡隨手玩的小遊戲", zh_TW="朋友群裡隨手玩的小遊戲"))

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.guess_sessions = {}
        self.higher_sessions = {}
        self.wordle_sessions = {}
        self.hangman_sessions = {}
        self.answer_sessions = set()
        self.roulette_sessions = {}

    def allowed(self, interaction: discord.Interaction) -> bool:
        return bool(interaction.guild)

    def _prune_expired_sessions(self, now: float | None = None):
        now = time.monotonic() if now is None else now
        for name, ttl in (
            ("guess_sessions", 600),
            ("wordle_sessions", 1800),
            ("hangman_sessions", 1800),
        ):
            sessions = getattr(self, name, None)
            if not isinstance(sessions, dict):
                continue
            for key, state in list(sessions.items()):
                started = state.get("started") if isinstance(state, dict) else None
                if not isinstance(started, (int, float)) or now - started > ttl:
                    sessions.pop(key, None)

        higher_sessions = getattr(self, "higher_sessions", None)
        if isinstance(higher_sessions, dict):
            for key, state in list(higher_sessions.items()):
                if not isinstance(state, dict) or now > state.get("expires", 0):
                    higher_sessions.pop(key, None)

    def session_key(self, interaction: discord.Interaction):
        self._prune_expired_sessions()
        return (interaction.guild.id if interaction.guild else 0, interaction.user.id)

    def answer_key(self, interaction: discord.Interaction):
        self._prune_expired_sessions()
        return (
            interaction.guild.id if interaction.guild else 0,
            interaction.channel.id if interaction.channel else 0,
            interaction.user.id,
        )

    async def finish_game(self, interaction, won: bool):
        if interaction.guild:
            storage.record_game(interaction.guild.id, interaction.user.id, won=won)


    @game.command(name=app_commands.locale_str("roulette", zh_TW="俄羅斯輪盤"), description=app_commands.locale_str("參加隨機抽選，被抽中的玩家會 Timeout 1 分鐘", zh_TW="參加隨機抽選，被抽中的玩家會 Timeout 1 分鐘"))
    async def roulette(self, interaction: discord.Interaction):
        guild = interaction.guild
        bot_member = guild.me if guild else None
        if guild is None or bot_member is None:
            await interaction.response.send_message("這個遊戲只能在伺服器內使用。", ephemeral=True)
            return
        if not bot_member.guild_permissions.moderate_members:
            await interaction.response.send_message("Bot 缺少「調節成員」權限，無法執行 60 秒 Timeout。", ephemeral=True)
            return
        host = guild.get_member(interaction.user.id)
        if host is None or host.bot or host.id == guild.owner_id or host.top_role >= bot_member.top_role:
            await interaction.response.send_message("你目前不符合參加條件，Bot 必須能對你執行 Timeout。", ephemeral=True)
            return
        session_key = (guild.id, interaction.channel_id)
        if session_key in self.roulette_sessions:
            await interaction.response.send_message("這個頻道已經有一場俄羅斯輪盤正在進行。", ephemeral=True)
            return

        view = RouletteView(self, guild, host.id, session_key)
        self.roulette_sessions[session_key] = view
        await interaction.response.send_message(view.status_text(), view=view)
        view.message = await interaction.original_response()

    @game.command(name="rps", description="和我猜拳，看看誰贏")
    @app_commands.describe(choice="剪刀、石頭或布")
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def rps(self, interaction: discord.Interaction, choice: str, public: bool = True):
        choice = choice.strip()
        if choice not in RPS:
            await interaction.response.send_message("打 剪刀、石頭 或 布 就好啦。", ephemeral=True)
            return
        bot_choice = random.choice(list(RPS))
        if choice == bot_choice:
            result = "平手。"
            won = False
        elif RPS[choice] == bot_choice:
            result = "你贏了！"
            won = True
        else:
            result = "我贏了 😎"
            won = False
        await self.finish_game(interaction, won)
        await interaction.response.send_message(f"你出 **{choice}**，我出 **{bot_choice}**。{result}", ephemeral=not public)

    @game.command(name="guess", description="猜 1 到 100，我會告訴你太大還太小")
    @app_commands.describe(number="你猜的數字")
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def guess(self, interaction: discord.Interaction, number: app_commands.Range[int, 1, 100], public: bool = True):
        key = self.session_key(interaction)
        state = self.guess_sessions.get(key)
        now = time.monotonic()

        if isinstance(state, dict) and now - state["started"] > 600:
            self.guess_sessions.pop(key, None)
            state = None

        if state is None:
            state = {"target": random.randint(1, 100), "started": now}
            self.guess_sessions[key] = state

        target = state["target"]
        if number == target:
            self.guess_sessions.pop(key, None)
            await self.finish_game(interaction, True)
            await interaction.response.send_message(f"中了！就是 **{target}** 🎯", ephemeral=not public)
        else:
            await interaction.response.send_message("太大了。" if number > target else "太小了。", ephemeral=not public)

    @game.command(name=app_commands.locale_str("trivia", zh_TW="知識問答"), description=app_commands.locale_str("來一題小常識", zh_TW="來一題小常識"))
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def trivia(self, interaction: discord.Interaction, public: bool = True):
        key = self.answer_key(interaction)
        if key in self.answer_sessions:
            await interaction.response.send_message("你已經有一個文字答案遊戲在跑了，先玩完那個。", ephemeral=True)
            return
        self.answer_sessions.add(key)

        question, options, answer = random.choice(TRIVIA)
        labels = "　".join(f"{i+1}. {x}" for i, x in enumerate(options))
        await interaction.response.send_message(f"🧠 **{question}**\n{labels}\n\n直接回 1～4 就行。", ephemeral=not public)

        def check(m):
            return (
                m.author.id == interaction.user.id
                and m.channel.id == interaction.channel.id
                and m.content.strip() in {"1", "2", "3", "4"}
            )

        try:
            msg = await self.bot.wait_for("message", timeout=20, check=check)
            won = int(msg.content.strip()) - 1 == answer
            await self.finish_game(interaction, won)
            await interaction.followup.send("答對了 👍" if won else f"答錯啦，答案是 **{options[answer]}**。", ephemeral=not public)
        except asyncio.TimeoutError:
            await self.finish_game(interaction, False)
            await interaction.followup.send(f"時間到，答案是 **{options[answer]}**。", ephemeral=not public)
        finally:
            self.answer_sessions.discard(key)

    @game.command(name=app_commands.locale_str("blackjack", zh_TW="二十一點"), description=app_commands.locale_str("抽兩張牌，比比看誰比較接近 21", zh_TW="抽兩張牌，比比看誰比較接近 21"))
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def blackjack(self, interaction: discord.Interaction, public: bool = True):
        deck = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10] * 4
        player = random.sample(deck, 2)
        bot_cards = random.sample(deck, 2)
        p, b = sum(player), sum(bot_cards)
        if p > 21:
            won = False
        elif b > 21 or p > b:
            won = True
        else:
            won = False
        await self.finish_game(interaction, won)
        if p > 21:
            result = "你爆掉，我贏了。"
        elif p == b:
            result = "平手。"
        elif won:
            result = "你贏了！"
        else:
            result = "我贏了。"
        await interaction.response.send_message(f"🃏 你：{player} = **{p}**\n我：{bot_cards} = **{b}**\n{result}", ephemeral=not public)

    @game.command(name="higherlower", description="猜下一個數字比現在高還是低")
    @app_commands.describe(choice="high 或 low")
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def higherlower(self, interaction: discord.Interaction, choice: str, public: bool = True):
        key = self.session_key(interaction)
        choice = choice.lower().strip()
        if choice not in {"high", "low", "高", "低"}:
            await interaction.response.send_message("輸入 high 或 low。", ephemeral=True)
            return

        state = self.higher_sessions.get(key)
        now = time.monotonic()
        if not isinstance(state, dict) or now > state.get("expires", 0):
            self.higher_sessions.pop(key, None)
            current = random.randint(1, 99)
        else:
            current = state["value"]

        next_value = random.randint(1, 100)
        chose_high = choice in {"high", "高"}
        if next_value == current:
            result = "平手。"
            won = False
        else:
            actual_high = next_value > current
            won = actual_high == chose_high
            result = "猜中！" if won else "猜錯啦。"

        self.higher_sessions[key] = {"value": next_value, "expires": time.monotonic() + 1800}
        await self.finish_game(interaction, won)
        await interaction.response.send_message(
            f"現在是 **{current}**，下一張 **{next_value}**。你猜{'高' if chose_high else '低'}，{result}"
        , ephemeral=not public)

    @game.command(name="wordle", description="玩簡單版 Wordle")
    @app_commands.describe(guess="第一次不填就開一局，之後輸入 5 個英文字母")
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def wordle(self, interaction: discord.Interaction, guess: str | None = None, public: bool = True):
        key = self.session_key(interaction)
        state = self.wordle_sessions.get(key)
        now = time.monotonic()

        if isinstance(state, dict) and now - state["started"] > 1800:
            self.wordle_sessions.pop(key, None)
            state = None

        if not guess:
            word = random.choice(WORDLE_WORDS)
            self.wordle_sessions[key] = {"word": word, "tries": 0, "started": now}
            await interaction.response.send_message("🟩 開局了，猜一個 5 個字母的英文單字。用 /game wordle guess:xxxxx 繼續。", ephemeral=not public)
            return
        if not state:
            await interaction.response.send_message("先用 /game wordle 開一局。", ephemeral=True)
            return

        guess = guess.lower().strip()
        if not re.fullmatch(r"[a-z]{5}", guess):
            await interaction.response.send_message("要 5 個英文字母。", ephemeral=True)
            return

        word = state["word"]
        state["tries"] += 1
        marks = ["⬜"] * 5
        remaining = {}
        for c in word:
            remaining[c] = remaining.get(c, 0) + 1

        for i, c in enumerate(guess):
            if c == word[i]:
                marks[i] = "🟩"
                remaining[c] -= 1

        for i, c in enumerate(guess):
            if marks[i] == "🟩":
                continue
            if remaining.get(c, 0) > 0:
                marks[i] = "🟨"
                remaining[c] -= 1

        if guess == word:
            self.wordle_sessions.pop(key, None)
            await self.finish_game(interaction, True)
            await interaction.response.send_message(f"{''.join(marks)}\n中了！答案是 **{word}**。", ephemeral=not public)
        elif state["tries"] >= 6:
            self.wordle_sessions.pop(key, None)
            await self.finish_game(interaction, False)
            await interaction.response.send_message(f"{''.join(marks)}\n6 次用完，答案是 **{word}**。", ephemeral=not public)
        else:
            await interaction.response.send_message(f"{''.join(marks)}\n還有 {6 - state['tries']} 次。", ephemeral=not public)

    @game.command(name="hangman", description="玩簡單版猜單字")
    @app_commands.describe(letter="第一次不填就開一局，之後輸入一個字母")
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def hangman(self, interaction: discord.Interaction, letter: str | None = None, public: bool = True):
        key = self.session_key(interaction)
        state = self.hangman_sessions.get(key)
        now = time.monotonic()

        if isinstance(state, dict) and now - state["started"] > 1800:
            self.hangman_sessions.pop(key, None)
            state = None

        if not letter:
            word = random.choice(HANGMAN_WORDS)
            self.hangman_sessions[key] = {"word": word, "guessed": set(), "wrong": 0, "started": now}
            await interaction.response.send_message("🎯 開局了，之後一個字母一個字母猜。", ephemeral=not public)
            return
        if not state:
            await interaction.response.send_message("先用 /game hangman 開一局。", ephemeral=True)
            return

        letter = letter.lower().strip()
        if not re.fullmatch(r"[a-z]", letter):
            await interaction.response.send_message("一次猜一個英文字母。", ephemeral=True)
            return
        if letter in state["guessed"]:
            await interaction.response.send_message("這個字母你猜過了。", ephemeral=True)
            return

        state["guessed"].add(letter)
        if letter not in state["word"]:
            state["wrong"] += 1

        display = " ".join(c if c in state["guessed"] else "_" for c in state["word"])
        if "_" not in display:
            self.hangman_sessions.pop(key, None)
            await self.finish_game(interaction, True)
            await interaction.response.send_message(f"**{display}**\n猜到了！🎉", ephemeral=not public)
        elif state["wrong"] >= 6:
            self.hangman_sessions.pop(key, None)
            await self.finish_game(interaction, False)
            await interaction.response.send_message(f"**{state['word']}**\n錯太多次了，這局沒了。", ephemeral=not public)
        else:
            await interaction.response.send_message(f"**{display}**\n失誤：{state['wrong']}/6", ephemeral=not public)

    @game.command(name=app_commands.locale_str("reaction", zh_TW="反應力"), description=app_commands.locale_str("測你的反應速度", zh_TW="測你的反應速度"))
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def reaction(self, interaction: discord.Interaction, public: bool = True):
        await interaction.response.send_message("準備……", ephemeral=not public)
        await asyncio.sleep(random.uniform(1.5, 4))
        button = ui.Button(label="點我！", style=discord.ButtonStyle.success)
        view = ui.View(timeout=10)
        started = time.perf_counter()
        clicked = False

        async def callback(i: discord.Interaction):
            nonlocal clicked
            if i.user.id != interaction.user.id:
                await i.response.send_message("不是你啦 😂", ephemeral=True)
                return
            elapsed = (time.perf_counter() - started) * 1000
            clicked = True
            view.stop()
            await i.response.edit_message(content=f"⚡ 反應時間：**{elapsed:.0f} ms**", view=None)
            await self.finish_game(i, elapsed < 700)

        button.callback = callback
        view.add_item(button)
        await interaction.edit_original_response(content="🔥 現在！", view=view)
        await view.wait()
        if not clicked:
            await interaction.edit_original_response(content="太慢啦。", view=None)
            await self.finish_game(interaction, False)

    async def run_answer_game(self, interaction, prompt, expected=None, timeout=30, public=True):
        key = self.answer_key(interaction)
        if key in self.answer_sessions:
            await interaction.response.send_message("你已經有一個文字答案遊戲在跑了，先玩完那個。", ephemeral=True)
            return None
        self.answer_sessions.add(key)
        try:
            await interaction.response.send_message(prompt, ephemeral=not public)

            def check(m):
                return m.author.id == interaction.user.id and m.channel.id == interaction.channel.id

            try:
                msg = await self.bot.wait_for("message", timeout=timeout, check=check)
            except asyncio.TimeoutError:
                await self.finish_game(interaction, False)
                await interaction.followup.send("時間到，這局先算了。", ephemeral=not public)
                return False

            won = expected(msg) if callable(expected) else msg.content.strip() == expected
            await self.finish_game(interaction, won)
            await interaction.followup.send("答對！🎉" if won else "答錯啦。", ephemeral=not public)
            return won
        finally:
            self.answer_sessions.discard(key)

    @game.command(name=app_commands.locale_str("typing", zh_TW="打字挑戰"), description=app_commands.locale_str("測測看你的打字速度", zh_TW="測測看你的打字速度"))
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def typing(self, interaction: discord.Interaction, public: bool = True):
        phrase = random.choice(["discord 朋友群", "今天晚上開黑", "這局不要雷我", "Bot 又有新功能了"])
        started = time.perf_counter()
        key = self.answer_key(interaction)
        if key in self.answer_sessions:
            await interaction.response.send_message("你已經有一個文字答案遊戲在跑了，先玩完那個。", ephemeral=True)
            return
        self.answer_sessions.add(key)
        try:
            await interaction.response.send_message(f"⌨️ 把下面這句原樣打回來：\n\n**{phrase}**", ephemeral=not public)
            def check(m):
                return m.author.id == interaction.user.id and m.channel.id == interaction.channel.id
            try:
                msg = await self.bot.wait_for("message", timeout=30, check=check)
                elapsed = time.perf_counter() - started
                won = msg.content.strip() == phrase
                await self.finish_game(interaction, won)
                await interaction.followup.send(f"{'中了！' if won else '字打錯了。'} 用時 **{elapsed:.2f} 秒**。", ephemeral=not public)
            except asyncio.TimeoutError:
                await self.finish_game(interaction, False)
                await interaction.followup.send("太久啦，這局先算了。", ephemeral=not public)
        finally:
            self.answer_sessions.discard(key)

    @game.command(name=app_commands.locale_str("memory", zh_TW="記憶力"), description=app_commands.locale_str("短暫記住一串圖案", zh_TW="短暫記住一串圖案"))
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def memory(self, interaction: discord.Interaction, public: bool = True):
        key = self.answer_key(interaction)
        if key in self.answer_sessions:
            await interaction.response.send_message("你已經有一個文字答案遊戲在跑了，先玩完那個。", ephemeral=True)
            return
        self.answer_sessions.add(key)
        try:
            symbols = random.sample(["🍎", "🍋", "🍒", "⭐", "🎲", "🔥", "🎵", "🎮", "🐱", "🚀"], 6)
            answer = "".join(symbols)
            await interaction.response.send_message("記住這串：\n" + " ".join(symbols), ephemeral=not public)
            await asyncio.sleep(3)
            try:
                await interaction.edit_original_response(content="現在只剩下：⬜ ⬜ ⬜ ⬜ ⬜ ⬜\n把剛才順序打回來。")
            except Exception:
                pass
            def check(m):
                return m.author.id == interaction.user.id and m.channel.id == interaction.channel.id
            try:
                msg = await self.bot.wait_for("message", timeout=15, check=check)
                won = msg.content.replace(" ", "") == answer
                await self.finish_game(interaction, won)
                await interaction.followup.send("全對！🧠" if won else f"差一點，答案是 {answer}", ephemeral=not public)
            except asyncio.TimeoutError:
                await self.finish_game(interaction, False)
                await interaction.followup.send("時間到。", ephemeral=not public)
        finally:
            self.answer_sessions.discard(key)

    @game.command(name=app_commands.locale_str("math", zh_TW="數學挑戰"), description=app_commands.locale_str("算一道隨機心算題", zh_TW="算一道隨機心算題"))
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def math_game(self, interaction: discord.Interaction, public: bool = True):
        key = self.answer_key(interaction)
        if key in self.answer_sessions:
            await interaction.response.send_message("你已經有一個文字答案遊戲在跑了，先玩完那個。", ephemeral=True)
            return
        self.answer_sessions.add(key)
        try:
            a, b = random.randint(5, 40), random.randint(5, 40)
            op = random.choice(["+", "-", "*"])
            answer = a + b if op == "+" else a - b if op == "-" else a * b
            await interaction.response.send_message(f"🧮 快算：**{a} {op} {b} = ?**", ephemeral=not public)
            def check(m):
                return (
                    m.author.id == interaction.user.id
                    and m.channel.id == interaction.channel.id
                    and m.content.strip().lstrip("-").isdigit()
                )
            try:
                msg = await self.bot.wait_for("message", timeout=15, check=check)
                won = int(msg.content.strip()) == answer
                await self.finish_game(interaction, won)
                await interaction.followup.send("答對！" if won else f"答錯，答案是 **{answer}**。", ephemeral=not public)
            except asyncio.TimeoutError:
                await self.finish_game(interaction, False)
                await interaction.followup.send(f"時間到，答案是 **{answer}**。", ephemeral=not public)
        finally:
            self.answer_sessions.discard(key)

    @game.command(name=app_commands.locale_str("8ball", zh_TW="水晶球"), description=app_commands.locale_str("問我一個問題，我隨便回你", zh_TW="問我一個問題，我隨便回你"))
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def eightball(self, interaction: discord.Interaction, question: str, public: bool = True):
        answers = ["可以。", "大概可以吧。", "我覺得不行。", "等等再決定。", "看運氣。", "很有機會。", "不要問，做就對了。"]
        await interaction.response.send_message(f"🎱 {random.choice(answers)}", ephemeral=not public)

    @game.command(name=app_commands.locale_str("truth", zh_TW="真心話"), description=app_commands.locale_str("隨機抽一題真心話", zh_TW="隨機抽一題真心話"))
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def truth(self, interaction: discord.Interaction, public: bool = True):
        await interaction.response.send_message(f"🗣️ 真心話：{random.choice(TRUTH)}", ephemeral=not public)

    @game.command(name=app_commands.locale_str("dare", zh_TW="大冒險"), description=app_commands.locale_str("隨機抽一個大冒險", zh_TW="隨機抽一個大冒險"))
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def dare(self, interaction: discord.Interaction, public: bool = True):
        await interaction.response.send_message(f"😈 大冒險：{random.choice(DARE)}", ephemeral=not public)

    @game.command(name=app_commands.locale_str("wouldyourather", zh_TW="二選一"), description=app_commands.locale_str("二選一，看你選哪個", zh_TW="二選一，看你選哪個"))
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def wouldyourather(self, interaction: discord.Interaction, public: bool = True):
        a, b = random.choice(WOULD)
        await interaction.response.send_message(f"你選哪個？\n🅰️ {a}\n🅱️ {b}", ephemeral=not public)

    @game.command(name=app_commands.locale_str("neverhaveiever", zh_TW="我從來沒有"), description=app_commands.locale_str("從沒做過這件事嗎", zh_TW="從沒做過這件事嗎"))
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def neverhaveiever(self, interaction: discord.Interaction, public: bool = True):
        items = ["沒熬夜到天亮過", "沒上課偷玩手機過", "沒把訊息傳錯人過", "沒玩遊戲玩到忘記時間過"]
        await interaction.response.send_message(f"🙅 從來沒有… **{random.choice(items)}**", ephemeral=not public)


async def setup(bot: commands.Bot):
    await bot.add_cog(GameCog(bot))
