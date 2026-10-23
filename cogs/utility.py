import ast
import asyncio
import io
import math
import random
import re
import secrets
import string
import urllib.parse
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import aiohttp
import discord
import qrcode
from discord import app_commands
from discord.ext import commands

import storage


def parse_duration(text: str) -> int | None:
    raw = text.strip().lower()
    m = re.fullmatch(r"(\d+)\s*(s|m|h|d|秒|分鐘|分|小時|時|天)", raw)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)
    mult = {"s":1, "秒":1, "m":60, "分鐘":60, "分":60, "h":3600, "小時":3600, "時":3600, "d":86400, "天":86400}[unit]
    seconds = n * mult
    return seconds if 1 <= seconds <= 31_536_000 else None


def safe_calculate(expression: str) -> int | float:
    tree = ast.parse(expression.replace('^', '**'), mode='eval')
    if sum(1 for _ in ast.walk(tree)) > 45:
        raise ValueError('算式太複雜')

    def bounded(value):
        if type(value) is int:
            if value.bit_length() > 512:
                raise ValueError('數值太大')
            return value
        if type(value) is float and math.isfinite(value) and abs(value) <= 1e100:
            return value
        raise ValueError('結果不是可用的有限數值')

    def visit(node, depth=0):
        if depth > 20:
            raise ValueError('算式太深')
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return bounded(node.value)
        if isinstance(node, ast.UnaryOp):
            value = visit(node.operand, depth + 1)
            if isinstance(node.op, ast.UAdd):
                return bounded(value)
            if isinstance(node.op, ast.USub):
                return bounded(-value)
            raise ValueError('不支援的運算')
        if isinstance(node, ast.BinOp):
            left = visit(node.left, depth + 1)
            right = visit(node.right, depth + 1)
            if isinstance(node.op, ast.Add):
                result = left + right
            elif isinstance(node.op, ast.Sub):
                result = left - right
            elif isinstance(node.op, ast.Mult):
                result = left * right
            elif isinstance(node.op, ast.Div):
                result = left / right
            elif isinstance(node.op, ast.FloorDiv):
                result = left // right
            elif isinstance(node.op, ast.Mod):
                result = left % right
            elif isinstance(node.op, ast.Pow):
                if abs(right) > 8 or abs(left) > 1e12:
                    raise ValueError('次方太大')
                result = left ** right
            else:
                raise ValueError('不支援的運算')
            return bounded(result)
        raise ValueError('只允許數字與一般四則運算')

    return visit(tree.body)

class UtilityCog(commands.Cog):
    remind = app_commands.Group(name=app_commands.locale_str("remind", zh_TW="提醒"), description=app_commands.locale_str("設定與管理提醒", zh_TW="設定、查看及取消提醒"))

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @remind.command(name=app_commands.locale_str("add", zh_TW="新增"), description=app_commands.locale_str("設定一個到時間會提醒你的提醒", zh_TW="新增提醒"))
    @app_commands.describe(after="例如 10m、2h、1d", message="要提醒你的內容")
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def remind_add(self, interaction: discord.Interaction, after: str, message: str, public: bool = True):
        if not interaction.guild:
            await interaction.response.send_message("這個要在伺服器裡用喔。", ephemeral=True)
            return
        seconds = parse_duration(after)
        if seconds is None:
            await interaction.response.send_message("時間格式不太對，像 10m、2h、1d 這樣就可以。", ephemeral=True)
            return
        if not 1 <= len(message) <= 500:
            await interaction.response.send_message("提醒內容太長或是空的。", ephemeral=True)
            return
        due = datetime.now(timezone.utc).timestamp() + seconds
        rid = storage.add_reminder(interaction.guild.id, interaction.user.id, interaction.channel.id, message, due)
        if rid is None:
            await interaction.response.send_message(
                f"你最多只能同時有 {storage.MAX_ACTIVE_REMINDERS_PER_USER} 個待處理提醒，先取消幾個再試。",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"好，幫你記著了。⏰ {after} 後提醒你「{message}」\n提醒編號：{rid}",
            ephemeral=True,
        )

    @remind.command(name=app_commands.locale_str("list", zh_TW="列表"), description=app_commands.locale_str("看看自己現在有哪些提醒", zh_TW="查看提醒"))
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def remind_list(self, interaction: discord.Interaction, public: bool = True):
        if not interaction.guild:
            await interaction.response.send_message("這個要在伺服器裡用喔。", ephemeral=True)
            return
        rows = storage.list_reminders(interaction.guild.id, interaction.user.id)
        if not rows:
            await interaction.response.send_message("你目前沒有待處理的提醒。", ephemeral=True)
            return
        lines = []
        now = datetime.now(timezone.utc).timestamp()
        for row in rows[:15]:
            left = max(1, int(row["due_at"] - now))
            eta = f"{left} 秒" if left < 60 else f"{left//60} 分" if left < 3600 else f"{left//3600} 小時"
            lines.append(f"{row['id']} · {eta}後 · {row['message']}")
        await interaction.response.send_message("你現在的提醒：\n" + "\n".join(lines), ephemeral=True)

    @remind.command(name=app_commands.locale_str("cancel", zh_TW="取消"), description=app_commands.locale_str("取消自己的一個提醒", zh_TW="取消提醒"))
    @app_commands.describe(reminder_id="提醒編號")
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def remind_cancel(self, interaction: discord.Interaction, reminder_id: int, public: bool = True):
        if not interaction.guild:
            await interaction.response.send_message("這個要在伺服器裡用喔。", ephemeral=True)
            return
        if storage.cancel_reminder(interaction.guild.id, interaction.user.id, reminder_id):
            await interaction.response.send_message("好，這個提醒幫你取消了。", ephemeral=True)
        else:
            await interaction.response.send_message("找不到這個提醒，可能已經到時間或被取消了。", ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("timer", zh_TW="計時器"), description=app_commands.locale_str("跑一個到時間會通知的倒數", zh_TW="執行計時器並在時間到時通知"))
    @app_commands.describe(duration="例如 30s、5m、1h")
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def timer(self, interaction: discord.Interaction, duration: str, public: bool = True):
        if not interaction.guild:
            await interaction.response.send_message("這個要在伺服器裡用喔。", ephemeral=True)
            return
        seconds = parse_duration(duration)
        if seconds is None:
            await interaction.response.send_message("時間格式用 30s、5m、1h 這種就行。", ephemeral=True)
            return
        rid = storage.add_reminder(
            interaction.guild.id,
            interaction.user.id,
            interaction.channel.id,
            "⏰ 時間到啦！",
            datetime.now(timezone.utc).timestamp() + seconds,
        )
        if rid is None:
            await interaction.response.send_message(
                f"你最多只能同時有 {storage.MAX_ACTIVE_REMINDERS_PER_USER} 個待處理提醒，先取消幾個再試。",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"開始啦，{duration} 後提醒你。⏳\n提醒編號：{rid}",
            ephemeral=True,
        )

    @app_commands.command(name=app_commands.locale_str("countdown", zh_TW="倒數"), description=app_commands.locale_str("建立一個短倒數", zh_TW="建立短倒數"))
    @app_commands.describe(seconds="倒數秒數，1 到 3600")
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def countdown(self, interaction: discord.Interaction, seconds: app_commands.Range[int, 1, 3600], public: bool = True):
        if not interaction.guild:
            await interaction.response.send_message("這個要在伺服器裡用喔。", ephemeral=True)
            return
        rid = storage.add_reminder(
            interaction.guild.id,
            interaction.user.id,
            interaction.channel.id,
            "⏰ 好了，時間到。",
            datetime.now(timezone.utc).timestamp() + seconds,
        )
        if rid is None:
            await interaction.response.send_message(
                f"你最多只能同時有 {storage.MAX_ACTIVE_REMINDERS_PER_USER} 個待處理提醒，先取消幾個再試。",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"倒數 {seconds} 秒，開始。\n提醒編號：{rid}",
            ephemeral=True,
        )

    @app_commands.command(name=app_commands.locale_str("calc", zh_TW="計算"), description=app_commands.locale_str("算數學，直接輸入一般算式", zh_TW="計算一般數學算式"))
    @app_commands.describe(expression="例如 123*45+6")
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def calc(self, interaction: discord.Interaction, expression: str, public: bool = True):
        if len(expression) > 80 or not re.fullmatch(r"[0-9+\-*/().% \^]+", expression):
            await interaction.response.send_message("這個算式我看不懂，先只支援一般數學運算。", ephemeral=True)
            return
        try:
            value = safe_calculate(expression)
            formatted = str(value) if type(value) is int else f"{value:g}"
            await interaction.response.send_message(f"{expression} = **{formatted}**", ephemeral=not public)
        except Exception:
            await interaction.response.send_message("這題算不出來或數值太大，檢查一下算式吧。", ephemeral=True)
    @app_commands.command(name=app_commands.locale_str("convert", zh_TW="單位換算"), description=app_commands.locale_str("常用單位換算", zh_TW="轉換常用單位"))
    @app_commands.describe(value="數值", from_unit="原單位", to_unit="目標單位")
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def convert(self, interaction: discord.Interaction, value: float, from_unit: str, to_unit: str, public: bool = True):
        if not math.isfinite(value):
            await interaction.response.send_message("請輸入有效的有限數值。", ephemeral=True)
            return
        units = {
            ("km","m"): 1000, ("m","km"): .001,
            ("m","cm"): 100, ("cm","m"): .01,
            ("kg","g"): 1000, ("g","kg"): .001,
            ("h","min"): 60, ("min","h"): 1/60,
            ("min","s"): 60, ("s","min"): 1/60,
            ("c","f"): lambda x: x * 9 / 5 + 32,
            ("f","c"): lambda x: (x - 32) * 5 / 9,
        }
        key = (from_unit.strip().lower(), to_unit.strip().lower())
        if key not in units:
            await interaction.response.send_message("目前支援 km/m、m/cm、kg/g、h/min/s、C/F 這些常用換算。", ephemeral=True)
            return
        op = units[key]
        result = op(value) if callable(op) else value * op
        await interaction.response.send_message(f"換算好了：**{value:g} {from_unit} = {result:g} {to_unit}**", ephemeral=not public)

    @app_commands.command(name=app_commands.locale_str("coin", zh_TW="丟硬幣"), description=app_commands.locale_str("丟硬幣看正面還是反面", zh_TW="隨機決定硬幣正反面"))
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def coin(self, interaction: discord.Interaction, public: bool = True):
        await interaction.response.send_message(random.choice(["🪙 正面！", "🪙 反面！"]))

    @app_commands.command(name=app_commands.locale_str("dice", zh_TW="擲骰子"), description=app_commands.locale_str("丟骰子，預設六面", zh_TW="擲指定面數的骰子"))
    @app_commands.describe(sides="骰子面數，2 到 100")
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def dice(self, interaction: discord.Interaction, sides: app_commands.Range[int, 2, 100] = 6, public: bool = True):
        await interaction.response.send_message(f"🎲 擲出了 **{random.randint(1, sides)}**（D{sides}）", ephemeral=not public)

    @app_commands.command(name=app_commands.locale_str("roll", zh_TW="擲骰"), description=app_commands.locale_str("自訂骰子，例如 2d20+3", zh_TW="擲自訂骰子"))
    @app_commands.describe(formula="例如 2d20+3")
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def roll(self, interaction: discord.Interaction, formula: str, public: bool = True):
        m = re.fullmatch(r"(\d+)d(\d+)([+-]\d+)?", formula.lower().strip())
        if not m or not (1 <= int(m.group(1)) <= 20) or not (2 <= int(m.group(2)) <= 1000):
            await interaction.response.send_message("格式像 2d20+3，骰子最多 20 顆。", ephemeral=True)
            return
        count, sides, extra = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
        values = [random.randint(1, sides) for _ in range(count)]
        await interaction.response.send_message(f"🎲 {formula} → {values}，總和 **{sum(values)+extra}**", ephemeral=not public)

    @app_commands.command(name=app_commands.locale_str("choose", zh_TW="幫我選"), description=app_commands.locale_str("從多個選項裡隨機挑一個", zh_TW="從多個選項隨機挑選"))
    @app_commands.describe(options="用逗號分隔，例如 A,B,C")
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def choose(self, interaction: discord.Interaction, options: str, public: bool = True):
        if len(options) > 1500:
            await interaction.response.send_message("選項內容太長，請控制在 1500 字內。", ephemeral=True)
            return
        items = [x.strip() for x in options.split(",") if x.strip()]
        if len(items) < 2:
            await interaction.response.send_message("至少丟兩個選項給我啦，例如 A,B,C。", ephemeral=True)
            return
        if any(len(item) > 1800 for item in items):
            await interaction.response.send_message("單一選項最多 1800 字。", ephemeral=True)
            return
        await interaction.response.send_message(f"我幫你選：**{random.choice(items)}** 😎", ephemeral=not public)
    @app_commands.command(name=app_commands.locale_str("password", zh_TW="產生密碼"), description=app_commands.locale_str("產生一組隨機密碼", zh_TW="產生隨機密碼"))
    @app_commands.describe(length="長度 8 到 64")
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def password(self, interaction: discord.Interaction, length: app_commands.Range[int, 8, 64] = 16, public: bool = True):
        chars = string.ascii_letters + string.digits + "!@#$%^&*_-"
        value = "".join(secrets.choice(chars) for _ in range(length))
        await interaction.response.send_message(
            f"這組給你：{value}\n記得不要拿去公開頻道。",
            ephemeral=True,
        )

    @app_commands.command(name=app_commands.locale_str("translate", zh_TW="翻譯"), description=app_commands.locale_str("把一句話翻成指定語言", zh_TW="將文字翻譯成指定語言"))
    @app_commands.describe(text="要翻的內容", target="例如 zh-TW、en、ja")
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def translate(self, interaction: discord.Interaction, text: str, target: str = "zh-TW", public: bool = True):
        await interaction.response.defer(ephemeral=not public)
        url = "https://translate.googleapis.com/translate_a/single?" + urllib.parse.urlencode({
            "client":"gtx", "sl":"auto", "tl":target, "dt":"t", "q":text[:1000]
        })
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=8) as resp:
                    data = await resp.json(content_type=None)
            translated = "".join(part[0] for part in data[0] if part and part[0])
            await interaction.followup.send(f"翻好了：\n> {translated}", ephemeral=not public)
        except Exception:
            await interaction.followup.send("這次翻譯服務沒回應，再試一次。", ephemeral=not public)

    @app_commands.command(name=app_commands.locale_str("weather", zh_TW="天氣"), description=app_commands.locale_str("查指定城市現在的天氣", zh_TW="查詢指定城市的天氣"))
    @app_commands.describe(city="例如 Taipei、Taichung、Changhua")
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def weather(self, interaction: discord.Interaction, city: str, public: bool = True):
        await interaction.response.defer()
        url = "https://wttr.in/" + urllib.parse.quote(city) + "?format=j1"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=8) as resp:
                    data = await resp.json(content_type=None)
            cur = data["current_condition"][0]
            temp = cur.get("temp_C","?")
            feels = cur.get("FeelsLikeC","?")
            humidity = cur.get("humidity","?")
            desc = cur.get("lang_zh", [{"value": cur["weatherDesc"][0]["value"]}])[0]["value"]
            await interaction.followup.send(f"🌤️ **{city}**\n{desc}，現在 {temp}°C，體感 {feels}°C，濕度 {humidity}%。", ephemeral=not public)
        except Exception:
            await interaction.followup.send("天氣服務現在沒回應，過一下再查。", ephemeral=not public)

    @app_commands.command(name=app_commands.locale_str("age", zh_TW="年齡計算"), description=app_commands.locale_str("用生日算你現在幾歲", zh_TW="依生日計算年齡"))
    @app_commands.describe(birthday="格式 YYYY-MM-DD")
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def age(self, interaction: discord.Interaction, birthday: str, public: bool = True):
        try:
            birth = datetime.strptime(birthday, "%Y-%m-%d").date()
            today = datetime.now(ZoneInfo("Asia/Taipei")).date()
            if birth > today:
                raise ValueError
            years = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
            await interaction.response.send_message(f"你現在是 **{years} 歲**。", ephemeral=not public)
        except Exception:
            await interaction.response.send_message("生日格式用 YYYY-MM-DD，例如 2010-08-20。", ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("days", zh_TW="日期差"), description=app_commands.locale_str("算兩個日期相差幾天", zh_TW="計算兩個日期相差的天數"))
    @app_commands.describe(start="YYYY-MM-DD", end="YYYY-MM-DD")
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def days(self, interaction: discord.Interaction, start: str, end: str, public: bool = True):
        try:
            a = datetime.strptime(start, "%Y-%m-%d").date()
            b = datetime.strptime(end, "%Y-%m-%d").date()
            await interaction.response.send_message(f"這兩天差 **{abs((b-a).days)} 天**。", ephemeral=not public)
        except Exception:
            await interaction.response.send_message("日期格式用 YYYY-MM-DD。", ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("qr", zh_TW="產生二維碼"), description=app_commands.locale_str("把文字或網址做成 QR Code", zh_TW="將文字或網址轉成 QR Code"))
    @app_commands.describe(text="要放進 QR Code 的文字或網址")
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def qr(self, interaction: discord.Interaction, text: str, public: bool = True):
        if len(text) > 1500:
            await interaction.response.send_message("內容太長了，先控制在 1500 字內。", ephemeral=True)
            return
        qr = qrcode.QRCode(box_size=8, border=2)
        qr.add_data(text)
        qr.make(fit=True)
        image = qr.make_image()
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        buf.seek(0)
        await interaction.response.send_message("好了，QR Code 在這。", file=discord.File(buf, "qrcode.png"))

    @app_commands.command(name=app_commands.locale_str("time", zh_TW="現在時間"), description=app_commands.locale_str("看看現在台灣時間", zh_TW="查看台灣目前時間"))
    @app_commands.describe(public="公開或僅自己可見；關閉則僅自己可見")
    async def time_cmd(self, interaction: discord.Interaction, public: bool = True):
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Asia/Taipei"))
        await interaction.response.send_message(f"現在是 **{now:%Y-%m-%d %H:%M:%S}**（台灣時間）。", ephemeral=not public)


async def setup(bot: commands.Bot):
    await bot.add_cog(UtilityCog(bot))
