import discord
from discord import app_commands
from discord.ext import commands

CATEGORIES = [
    ("🛠️ 日常工具", "提醒、計時、計算、日期、隨機與 QR Code 等平常會用到的小工具。", [
        ("/remind add", "設定一個到時間會叫你的提醒。"),
        ("/remind list", "查看自己還沒完成的提醒。"),
        ("/remind cancel", "取消指定提醒。"),
        ("/timer", "跑一個倒數，時間到會通知你。"),
        ("/countdown", "快速開始一個短倒數。"),
        ("/calc", "直接輸入算式幫你算。"),
        ("/convert", "做常見的單位換算。"),
        ("/coin", "丟硬幣決定正面或反面。"),
        ("/dice", "丟指定面數的骰子。"),
        ("/roll", "玩像 2d20+3 的自訂骰子。"),
        ("/choose", "從你給的選項裡隨機挑一個。"),
        ("/password", "產生一組隨機密碼。"),
        ("/translate", "把文字翻成指定語言。"),
        ("/weather", "查指定城市天氣。"),
        ("/age", "用生日算年齡。"),
        ("/days", "算兩個日期相差幾天。"),
        ("/qr", "把文字或網址做成 QR Code。"),
        ("/time", "看現在台灣時間。"),
    ]),
    ("💬 自動回覆", "你自己決定觸發句子和 Bot 回什麼。", [
        ("/autoreply add", "打開表單填觸發文字和回覆內容，不用擠在指令欄裡。"),
        ("/autoreply edit", "修改現有自動回覆。"),
        ("/autoreply remove", "刪掉指定自動回覆。"),
        ("/autoreply list", "查看目前的自動回覆。"),
        ("/autoreply on", "重新開啟指定回覆。"),
        ("/autoreply off", "暫時關閉指定回覆。"),
    ]),
    ("🎭 身分組", "讓大家自己點按鈕或用下拉選單拿身分組。", [
        ("/rolepanel create", "建立按鈕或下拉多選的身分組面板。"),
        ("/rolepanel list", "查看已建立的面板。"),
        ("/rolepanel delete", "刪掉指定面板。"),
    ]),
    ("🗳️ 投票", "不用手動數票，直接在面板上選。", [
        ("/poll create", "建立單選或多選投票，可設定時間。"),
        ("/poll end", "提早結束一個投票。"),
    ]),
    ("🕹️ 小遊戲", "閒著沒事就可以直接在 Discord 裡玩。", [
        ("/game rps", "和 Bot 猜拳。"),
        ("/game guess", "猜 1～100 的數字。"),
        ("/game trivia", "來一題小常識。"),
        ("/game blackjack", "玩簡化版 21 點。"),
        ("/game higherlower", "猜下一張牌高還是低。"),
        ("/game wordle", "玩 5 字母猜字。"),
        ("/game hangman", "猜字母過關。"),
        ("/game reaction", "測反應速度。"),
        ("/game typing", "看看打字速度。"),
        ("/game memory", "玩記憶小遊戲。"),
        ("/game math", "來一道即時計算題。"),
        ("/game 8ball", "問問題看我怎麼回。"),
        ("/game truth", "抽 Truth 題目。"),
        ("/game dare", "抽 Dare 挑戰。"),
        ("/game wouldyourather", "玩二選一。"),
        ("/game neverhaveiever", "玩 Never Have I Ever。"),
    ]),
    ("🏆 排行榜／XP", "聊天、語音和遊戲留下紀錄，再慢慢升級。", [
        ("/leaderboard", "查看 XP、聊天、語音和遊戲排行。"),
        ("/profile", "查看自己的等級與統計。"),
        ("/achievements", "查看成就進度。"),
    ]),
    ("🎁 抽獎", "開一個限時抽獎，到時間自動抽人。", [
        ("/giveaway create", "建立抽獎活動。"),
        ("/giveaway end", "提前結束並抽人。"),
        ("/giveaway reroll", "重新抽得獎者。"),
    ]),
    ("🎵 音樂", "使用 Lavalink 播放音樂；可搜尋歌曲、選歌、控制播放和管理待播清單。", [
        ("/join_voice", "讓 Bot 加入你目前所在的語音頻道。"),
        ("/music play", "輸入歌名搜尋，或貼上 YouTube／Spotify 網址播放。"),
        ("/music queue", "查看目前播放與待播清單，並開啟控制台。"),
        ("/music pause", "暫停目前歌曲。"),
        ("/music resume", "繼續播放。"),
        ("/music skip", "跳到下一首。"),
        ("/music volume", "調整音量。"),
        ("/music shuffle", "打亂等待中的歌曲。"),
        ("/music loop", "設定關閉、單曲或佇列循環。"),
    ]),
    ("🖼️ 圖片／資訊", "頭像、Banner、伺服器資訊和圖片處理。", [
        ("/avatar", "放大查看頭像。"),
        ("/banner", "查看 Banner。"),
        ("/userinfo", "查看成員基本資料。"),
        ("/serverinfo", "查看伺服器基本資料。"),
        ("/membercount", "查看伺服器成員數。"),
        ("/image resize", "縮放圖片。"),
        ("/image color", "抓圖片平均顏色。"),
        ("/image ocr", "從圖片抓文字。"),
    ]),
    ("🔎 搜尋", "直接在 Discord 查常用網站。", [
        ("/search youtube", "搜尋 YouTube。"),
        ("/search wikipedia", "搜尋 Wikipedia。"),
        ("/search github", "搜尋 GitHub 公開專案。"),
    ]),
    ("🤖 AI", "文字 AI 優先連接本機 FreeMind AI；圖片理解及本機服務不可用時仍可使用既有 API 備援。", [
        ("/ai ask", "問問題或請我幫你想東西。"),
        ("/ai chat", "和 Bot 聊天、吐槽與接梗。"),
        ("/ai translate", "翻譯並順一下語氣。"),
        ("/ai summarize", "把長文字整理成重點。"),
    ]),
    ("🧪 AI 連線測試", "檢查 Bot 連接 AI 服務是否正常；一般聊天不用使用這些測試指令。", [
        ("/ai freemind-test", "測試 FreeMind AI 連線。"),
        ("/ai groq-test", "測試 Groq API 與模型。"),
        ("/ai test", "測試 Gemini API 與模型。"),
        ("/ai persona-reset", "重置 AI 人設違規處分；僅供有權限的人使用。"),
    ]),
    ("🔊 語音", "管理臨時語音和語音時間統計。", [
        ("/voice temp", "開一個臨時語音頻道。"),
        ("/voice notice", "設定語音進出通知。"),
        ("/voice noticeoff", "關閉語音進出通知。"),
        ("/voice stats", "查看自己待過語音多久。"),
    ]),
    ("🛡️ 管理", "清理訊息、處理成員和頻道管理。", [
        ("/clear", "清掉目前頻道最近的訊息。"),
        ("/slowmode", "設定頻道慢速模式。"),
        ("/lock", "暫時鎖住頻道。"),
        ("/unlock", "解除頻道鎖定。"),
        ("/warn", "留下成員警告紀錄。"),
        ("/warnings", "查看警告紀錄。"),
        ("/timeout", "暫時限制成員聊天。"),
        ("/kick", "踢出成員。"),
        ("/ban", "封鎖成員。"),
        ("/unban", "解除封鎖。"),
        ("/channel_backup", "把指定文字頻道的歷史訊息匯出成 ZIP，包含 HTML 與 JSON。"),
    ]),
    ("🚨 防濫用", "特別處理大量 @、刷屏、邀請連結和全大寫。", [
        ("/security mention-limit", "設定每則訊息最多 @ 幾個人；可以設成 5。"),
        ("/security antispam", "抓短時間連續刷訊息。"),
        ("/security invites", "擋 Discord 邀請連結。"),
        ("/security caps", "擋大量全大寫刷屏。"),
    ]),
    ("⚙️ 紀錄／設定", "把 Bot 的伺服器設定集中管理。", [
        ("/modlog set", "指定管理 Log 頻道。"),
        ("/modlog disable", "關閉管理 Log。"),
        ("/settings show", "查看目前設定。"),
        ("/settings autorole", "設定新人自動身分組。"),
        ("/settings welcome", "設定新人歡迎訊息。"),
        ("/settings goodbye", "設定離開通知。"),
        ("/note add", "存一筆自己的小筆記。"),
        ("/note list", "列出自己的筆記。"),
        ("/note show", "查看指定筆記。"),
        ("/note delete", "刪掉指定筆記。"),
    ]),
]


def help_embed(index=None):
    if index is None:
        embed = discord.Embed(
            title="🤖 一條魚",
            description="這顆就是放朋友群裡一起玩的。下面每一類都有實際在幹嘛的說明。",
            color=discord.Color.blurple(),
        )
        for name, description, commands_ in CATEGORIES:
            embed.add_field(
                name=name,
                value=description + f"\n共 {len(commands_)} 個功能。",
                inline=False,
            )
        return embed

    name, description, commands_ = CATEGORIES[index]
    embed = discord.Embed(title=name, description=description, color=discord.Color.blurple())
    for command, desc in commands_:
        embed.add_field(name=command, value=desc, inline=False)
    embed.set_footer(text="想看別類，直接用上面的選單切換。")
    return embed


class HelpView(discord.ui.View):
    def __init__(self, owner_id):
        super().__init__(timeout=180)
        self.owner_id = owner_id
        select = discord.ui.StringSelect(
            placeholder="選一類看詳細功能",
            options=[
                discord.SelectOption(label=name[2:], emoji=name[0], value=str(i))
                for i, (name, _, _) in enumerate(CATEGORIES)
            ],
        )
        async def callback(interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("這個選單是別人的啦 😂", ephemeral=True)
                return
            await interaction.response.edit_message(
                embed=help_embed(int(select.values[0])),
                view=self,
            )
        select.callback = callback
        self.add_item(select)


class HelpCog(commands.Cog):
    @app_commands.command(name="help", description="查看一條魚的功能和每個功能在做什麼")
    @app_commands.describe(public="公開或僅自己可見")
    @app_commands.rename(public="公開")
    async def help_command(self, interaction, public: bool = True):
        await interaction.response.send_message(
            embed=help_embed(),
            view=HelpView(interaction.user.id),
            ephemeral=not public,
        )


async def setup(bot):
    await bot.add_cog(HelpCog(bot))
