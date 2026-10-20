import asyncio
import random
import re
import time

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


class GameCog(commands.Cog):
    game = app_commands.Group(name="game", description="朋友群裡隨手玩的小遊戲")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.guess_sessions = {}
        self.higher_sessions = {}
        self.wordle_sessions = {}
        self.hangman_sessions = {}
        self.answer_sessions = set()

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

    @game.command(name="rps", description="和我猜拳，看看誰贏")
    @app_commands.describe(choice="剪刀、石頭或布")
    async def rps(self, interaction: discord.Interaction, choice: str):
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
        await interaction.response.send_message(f"你出 **{choice}**，我出 **{bot_choice}**。{result}")

    @game.command(name="guess", description="猜 1 到 100，我會告訴你太大還太小")
    @app_commands.describe(number="你猜的數字")
    async def guess(self, interaction: discord.Interaction, number: app_commands.Range[int, 1, 100]):
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
            await interaction.response.send_message(f"中了！就是 **{target}** 🎯")
        else:
            await interaction.response.send_message("太大了。" if number > target else "太小了。")

    @game.command(name="trivia", description="來一題小常識")
    async def trivia(self, interaction: discord.Interaction):
        key = self.answer_key(interaction)
        if key in self.answer_sessions:
            await interaction.response.send_message("你已經有一個文字答案遊戲在跑了，先玩完那個。", ephemeral=True)
            return
        self.answer_sessions.add(key)

        question, options, answer = random.choice(TRIVIA)
        labels = "　".join(f"{i+1}. {x}" for i, x in enumerate(options))
        await interaction.response.send_message(f"🧠 **{question}**\n{labels}\n\n直接回 1～4 就行。")

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
            await interaction.followup.send("答對了 👍" if won else f"答錯啦，答案是 **{options[answer]}**。")
        except asyncio.TimeoutError:
            await self.finish_game(interaction, False)
            await interaction.followup.send(f"時間到，答案是 **{options[answer]}**。")
        finally:
            self.answer_sessions.discard(key)

    @game.command(name="blackjack", description="抽兩張牌，比比看誰比較接近 21")
    async def blackjack(self, interaction: discord.Interaction):
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
        await interaction.response.send_message(f"🃏 你：{player} = **{p}**\n我：{bot_cards} = **{b}**\n{result}")

    @game.command(name="higherlower", description="猜下一個數字比現在高還是低")
    @app_commands.describe(choice="high 或 low")
    async def higherlower(self, interaction: discord.Interaction, choice: str):
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
        )

    @game.command(name="wordle", description="玩簡單版 Wordle")
    @app_commands.describe(guess="第一次不填就開一局，之後輸入 5 個英文字母")
    async def wordle(self, interaction: discord.Interaction, guess: str | None = None):
        key = self.session_key(interaction)
        state = self.wordle_sessions.get(key)
        now = time.monotonic()

        if isinstance(state, dict) and now - state["started"] > 1800:
            self.wordle_sessions.pop(key, None)
            state = None

        if not guess:
            word = random.choice(WORDLE_WORDS)
            self.wordle_sessions[key] = {"word": word, "tries": 0, "started": now}
            await interaction.response.send_message("🟩 開局了，猜一個 5 個字母的英文單字。用 /game wordle guess:xxxxx 繼續。")
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
            await interaction.response.send_message(f"{''.join(marks)}\n中了！答案是 **{word}**。")
        elif state["tries"] >= 6:
            self.wordle_sessions.pop(key, None)
            await self.finish_game(interaction, False)
            await interaction.response.send_message(f"{''.join(marks)}\n6 次用完，答案是 **{word}**。")
        else:
            await interaction.response.send_message(f"{''.join(marks)}\n還有 {6 - state['tries']} 次。")

    @game.command(name="hangman", description="玩簡單版猜單字")
    @app_commands.describe(letter="第一次不填就開一局，之後輸入一個字母")
    async def hangman(self, interaction: discord.Interaction, letter: str | None = None):
        key = self.session_key(interaction)
        state = self.hangman_sessions.get(key)
        now = time.monotonic()

        if isinstance(state, dict) and now - state["started"] > 1800:
            self.hangman_sessions.pop(key, None)
            state = None

        if not letter:
            word = random.choice(HANGMAN_WORDS)
            self.hangman_sessions[key] = {"word": word, "guessed": set(), "wrong": 0, "started": now}
            await interaction.response.send_message("🎯 開局了，之後一個字母一個字母猜。")
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
            await interaction.response.send_message(f"**{display}**\n猜到了！🎉")
        elif state["wrong"] >= 6:
            self.hangman_sessions.pop(key, None)
            await self.finish_game(interaction, False)
            await interaction.response.send_message(f"**{state['word']}**\n錯太多次了，這局沒了。")
        else:
            await interaction.response.send_message(f"**{display}**\n失誤：{state['wrong']}/6")

    @game.command(name="reaction", description="測你的反應速度")
    async def reaction(self, interaction: discord.Interaction):
        await interaction.response.send_message("準備……")
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

    async def run_answer_game(self, interaction, prompt, expected=None, timeout=30):
        key = self.answer_key(interaction)
        if key in self.answer_sessions:
            await interaction.response.send_message("你已經有一個文字答案遊戲在跑了，先玩完那個。", ephemeral=True)
            return None
        self.answer_sessions.add(key)
        try:
            await interaction.response.send_message(prompt)

            def check(m):
                return m.author.id == interaction.user.id and m.channel.id == interaction.channel.id

            try:
                msg = await self.bot.wait_for("message", timeout=timeout, check=check)
            except asyncio.TimeoutError:
                await self.finish_game(interaction, False)
                await interaction.followup.send("時間到，這局先算了。")
                return False

            won = expected(msg) if callable(expected) else msg.content.strip() == expected
            await self.finish_game(interaction, won)
            await interaction.followup.send("答對！🎉" if won else "答錯啦。")
            return won
        finally:
            self.answer_sessions.discard(key)

    @game.command(name="typing", description="測測看你的打字速度")
    async def typing(self, interaction: discord.Interaction):
        phrase = random.choice(["discord 朋友群", "今天晚上開黑", "這局不要雷我", "Bot 又有新功能了"])
        started = time.perf_counter()
        key = self.answer_key(interaction)
        if key in self.answer_sessions:
            await interaction.response.send_message("你已經有一個文字答案遊戲在跑了，先玩完那個。", ephemeral=True)
            return
        self.answer_sessions.add(key)
        try:
            await interaction.response.send_message(f"⌨️ 把下面這句原樣打回來：\n\n**{phrase}**")
            def check(m):
                return m.author.id == interaction.user.id and m.channel.id == interaction.channel.id
            try:
                msg = await self.bot.wait_for("message", timeout=30, check=check)
                elapsed = time.perf_counter() - started
                won = msg.content.strip() == phrase
                await self.finish_game(interaction, won)
                await interaction.followup.send(f"{'中了！' if won else '字打錯了。'} 用時 **{elapsed:.2f} 秒**。")
            except asyncio.TimeoutError:
                await self.finish_game(interaction, False)
                await interaction.followup.send("太久啦，這局先算了。")
        finally:
            self.answer_sessions.discard(key)

    @game.command(name="memory", description="短暫記住一串圖案")
    async def memory(self, interaction: discord.Interaction):
        key = self.answer_key(interaction)
        if key in self.answer_sessions:
            await interaction.response.send_message("你已經有一個文字答案遊戲在跑了，先玩完那個。", ephemeral=True)
            return
        self.answer_sessions.add(key)
        try:
            symbols = random.sample(["🍎", "🍋", "🍒", "⭐", "🎲", "🔥", "🎵", "🎮", "🐱", "🚀"], 6)
            answer = "".join(symbols)
            await interaction.response.send_message("記住這串：\n" + " ".join(symbols))
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
                await interaction.followup.send("全對！🧠" if won else f"差一點，答案是 {answer}")
            except asyncio.TimeoutError:
                await self.finish_game(interaction, False)
                await interaction.followup.send("時間到。")
        finally:
            self.answer_sessions.discard(key)

    @game.command(name="math", description="算一道隨機心算題")
    async def math_game(self, interaction: discord.Interaction):
        key = self.answer_key(interaction)
        if key in self.answer_sessions:
            await interaction.response.send_message("你已經有一個文字答案遊戲在跑了，先玩完那個。", ephemeral=True)
            return
        self.answer_sessions.add(key)
        try:
            a, b = random.randint(5, 40), random.randint(5, 40)
            op = random.choice(["+", "-", "*"])
            answer = a + b if op == "+" else a - b if op == "-" else a * b
            await interaction.response.send_message(f"🧮 快算：**{a} {op} {b} = ?**")
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
                await interaction.followup.send("答對！" if won else f"答錯，答案是 **{answer}**。")
            except asyncio.TimeoutError:
                await self.finish_game(interaction, False)
                await interaction.followup.send(f"時間到，答案是 **{answer}**。")
        finally:
            self.answer_sessions.discard(key)

    @game.command(name="8ball", description="問我一個問題，我隨便回你")
    async def eightball(self, interaction: discord.Interaction, question: str):
        answers = ["可以。", "大概可以吧。", "我覺得不行。", "等等再決定。", "看運氣。", "很有機會。", "不要問，做就對了。"]
        await interaction.response.send_message(f"🎱 {random.choice(answers)}")

    @game.command(name="truth", description="隨機抽一題真心話")
    async def truth(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"🗣️ 真心話：{random.choice(TRUTH)}")

    @game.command(name="dare", description="隨機抽一個大冒險")
    async def dare(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"😈 大冒險：{random.choice(DARE)}")

    @game.command(name="wouldyourather", description="二選一，看你選哪個")
    async def wouldyourather(self, interaction: discord.Interaction):
        a, b = random.choice(WOULD)
        await interaction.response.send_message(f"你選哪個？\n🅰️ {a}\n🅱️ {b}")

    @game.command(name="neverhaveiever", description="從沒做過這件事嗎")
    async def neverhaveiever(self, interaction: discord.Interaction):
        items = ["沒熬夜到天亮過", "沒上課偷玩手機過", "沒把訊息傳錯人過", "沒玩遊戲玩到忘記時間過"]
        await interaction.response.send_message(f"🙅 從來沒有… **{random.choice(items)}**")


async def setup(bot: commands.Bot):
    await bot.add_cog(GameCog(bot))
