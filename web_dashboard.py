import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import urlencode
from aiohttp import ClientSession, web
import discord

# Discord IDs explicitly approved to use the remote dashboard.
DASHBOARD_WHITELIST_IDS = {
    int(value.strip())
    for value in os.getenv("DASHBOARD_WHITELIST_IDS", "1338493569781989407").split(",")
    if value.strip().isdecimal() and int(value.strip()) > 0
}
DASHBOARD_OWNER_ID = int(os.getenv("OWNER_ID", "1140900506198351924"))

PAGE = r"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LogRelay Control</title>
<style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#131b2e;--line:#26334e;--text:#edf2ff;--muted:#94a3bd;--accent:#8b9cff;--good:#45d6a1;--bad:#ff8290}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at top,#172343,#0b1020 58%);color:var(--text);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1050px;margin:auto;padding:28px 18px 60px}.top{display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:22px}
h1{font-size:28px;margin:0;letter-spacing:.3px}.sub{color:var(--muted);margin-top:4px}.pill{border:1px solid var(--line);border-radius:999px;padding:7px 12px;color:var(--good);font-size:13px}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.card{background:rgba(19,27,46,.94);border:1px solid var(--line);border-radius:18px;padding:19px;box-shadow:0 14px 35px #0002}.wide{grid-column:1/-1}h2{font-size:17px;margin:0 0 14px}
label{display:block;color:var(--muted);font-size:13px;margin:10px 0 6px}input,select,textarea,button{font:inherit;border-radius:10px;border:1px solid var(--line);background:#0c1426;color:var(--text);padding:11px 12px;width:100%}textarea{min-height:100px;resize:vertical}
button{cursor:pointer;background:#26365e;border-color:#3b4f82;font-weight:650;transition:.15s}button:hover{filter:brightness(1.18)}button.secondary{background:#172239}button:disabled{opacity:.5;cursor:wait}
.row{display:grid;grid-template-columns:1fr 1fr;gap:10px}.stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.stat{background:#0c1426;border:1px solid var(--line);border-radius:12px;padding:13px}.stat b{display:block;font-size:20px}.stat span{color:var(--muted);font-size:12px}
.note{color:var(--muted);font-size:12px;margin-top:10px}.message{min-height:22px;color:var(--good);margin-top:10px;white-space:pre-wrap}.error{color:var(--bad)}.hidden{display:none}.toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.toolbar button{width:auto}
@media(max-width:680px){.grid{grid-template-columns:1fr}.wide{grid-column:auto}.row{grid-template-columns:1fr}.stats{grid-template-columns:1fr 1fr}main{padding:18px 12px 40px}}
</style>
</head>
<body><main>
<div class="top"><div><h1>LogRelay <span style="color:var(--accent)">Control</span></h1><div class="sub">本機網頁控制台 · 連接目前執行中的 Discord Bot</div></div><div class="pill" id="connection">等待連線</div></div>
<section class="card" id="authCard"><h2>登入控制台</h2><p>使用 Discord 帳號登入。只有服主與白名單中的 Discord 使用者可以操作。</p><a href="/login" style="display:block;text-decoration:none"><button type="button">使用 Discord 登入</button></a><details style="margin-top:14px"><summary>本機管理員連線</summary><label for="token">終端機顯示的本機控制 Token</label><input id="token" type="password" autocomplete="off" placeholder="只限本機管理員"><button id="connect" style="margin-top:10px">本機連線</button></details><div class="note">不要分享本機控制 Token 或 Discord Bot Token。</div><div class="message" id="authMsg"></div></section>
<div id="app" class="hidden">
<div class="grid" style="margin-top:16px">
<section class="card wide"><div class="toolbar" style="justify-content:space-between"><h2>Bot 狀態</h2><button class="secondary" id="refresh" style="width:auto">重新整理</button></div><div class="stats"><div class="stat"><b id="botName">—</b><span>登入帳號</span></div><div class="stat"><b id="guildCount">—</b><span>已加入伺服器</span></div><div class="stat"><b id="latency">—</b><span>Gateway 延遲</span></div></div><div class="message" id="statusMsg"></div></section>
<section class="card"><h2>選擇伺服器與頻道</h2><label for="guild">伺服器</label><select id="guild"></select><label for="channel">文字頻道</label><select id="channel"></select><button id="selectTarget" style="margin-top:12px">設為目前控制頻道</button><div class="note" id="selectedInfo">尚未選擇</div><div class="message" id="selectMsg"></div></section>
<section class="card"><h2>傳送訊息</h2><label for="sendText">要讓 LogRelay 發送的內容</label><textarea id="sendText" maxlength="2000" placeholder="輸入訊息內容（最多 2000 字）"></textarea><button id="sendBtn" style="margin-top:12px">傳送到選定頻道</button><div class="message" id="sendMsg"></div></section>
<section class="card wide"><h2>引用指定訊息回覆</h2><div class="row"><div><label for="messageId">Discord 訊息 ID</label><input id="messageId" inputmode="numeric" placeholder="右鍵訊息 → 複製訊息 ID"></div><div><label for="replyText">LogRelay 的回覆內容</label><textarea id="replyText" maxlength="2000" placeholder="輸入要回覆的內容"></textarea></div></div><button id="replyBtn" style="margin-top:12px">引用該訊息並回覆</button><div class="note">必須先選擇該訊息所在的伺服器與頻道；LogRelay 需要能查看頻道及訊息歷史。</div><div class="message" id="replyMsg"></div></section>
</div></div>
</main>
<script>
let token="",selectedGuild="",selectedChannel="";
const $=id=>document.getElementById(id);
function message(id,text,error=false){$(id).textContent=text;$(id).classList.toggle("error",error)}
async function api(path,body){
 const options={headers:{"X-Control-Token":token}};
 if(body!==undefined){options.method="POST";options.headers["Content-Type"]="application/json";options.body=JSON.stringify(body)}
 const response=await fetch(path,options);let data={};try{data=await response.json()}catch{}
 if(!response.ok)throw new Error(data.error||("HTTP "+response.status));return data
}
async function loadStatus(){
 const data=await api("/api/status");$("botName").textContent=data.user||"未連線";$("guildCount").textContent=data.guild_count;$("latency").textContent=data.latency_ms+" ms";
 selectedGuild=String(data.selected_guild_id||selectedGuild||"");selectedChannel=String(data.selected_channel_id||selectedChannel||"");
 $("selectedInfo").textContent=data.selected_guild_name?(data.selected_guild_name+" / #"+data.selected_channel_name):"尚未選擇控制頻道";
}
async function loadGuilds(){
 const data=await api("/api/guilds");const select=$("guild");select.replaceChildren();
 for(const guild of data.guilds){const o=document.createElement("option");o.value=guild.id;o.textContent=guild.name;select.append(o)}
 if(selectedGuild&&[...select.options].some(o=>o.value===selectedGuild))select.value=selectedGuild;
 if(select.value){selectedGuild=select.value;await loadChannels()}
}
async function loadChannels(){
 selectedGuild=$("guild").value;const data=await api("/api/channels?guild_id="+encodeURIComponent(selectedGuild));const select=$("channel");select.replaceChildren();
 for(const channel of data.channels){const o=document.createElement("option");o.value=channel.id;o.textContent="#"+channel.name;select.append(o)}
 if(selectedChannel&&[...select.options].some(o=>o.value===selectedChannel))select.value=selectedChannel;
 if(select.value)selectedChannel=select.value;
}
async function connect(){
 token=$("token").value.trim();if(!token){message("authMsg","請先輸入本機控制 Token。",true);return}
 try{await loadStatus();await loadGuilds();$("authCard").classList.add("hidden");$("app").classList.remove("hidden");$("connection").textContent="已連線";message("statusMsg","控制台已連接。")}
 catch(e){token="";message("authMsg",e.message,true)}
}
$("connect").onclick=connect;window.addEventListener("load",async()=>{try{await loadStatus();await loadGuilds();$("authCard").classList.add("hidden");$("app").classList.remove("hidden");$("connection").textContent="Discord 已驗證登入";message("statusMsg","已使用白名單 Discord 帳號登入。")}catch(e){/* not logged in yet; keep login card visible */}});$("refresh").onclick=async()=>{try{await loadStatus();await loadGuilds();message("statusMsg","狀態已更新。")}catch(e){message("statusMsg",e.message,true)}};
$("guild").onchange=async()=>{selectedChannel="";try{await loadChannels()}catch(e){message("selectMsg",e.message,true)}};
$("channel").onchange=()=>{selectedChannel=$("channel").value};
$("selectTarget").onclick=async()=>{try{const d=await api("/api/select",{guild_id:$("guild").value,channel_id:$("channel").value});selectedGuild=d.guild_id;selectedChannel=d.channel_id;await loadStatus();message("selectMsg","已設定控制頻道。")}catch(e){message("selectMsg",e.message,true)}};
$("sendBtn").onclick=async()=>{try{const d=await api("/api/send",{channel_id:$("channel").value,content:$("sendText").value});message("sendMsg",d.message);$("sendText").value=""}catch(e){message("sendMsg",e.message,true)}};
$("replyBtn").onclick=async()=>{try{const d=await api("/api/reply",{channel_id:$("channel").value,message_id:$("messageId").value.trim(),content:$("replyText").value});message("replyMsg",d.message);$("replyText").value=""}catch(e){message("replyMsg",e.message,true)}};
</script></body></html>"""

def _json_error(message, status=400):
    return web.json_response({"error": message}, status=status)

def _get_channel(bot, channel_id):
    try:
        channel_id = int(channel_id)
    except (TypeError, ValueError):
        return None
    channel = bot.get_channel(channel_id)
    if isinstance(channel, (discord.TextChannel, discord.Thread)):
        return channel
    return None

def _session_secret():
    return os.getenv("DASHBOARD_SESSION_SECRET", "")

def _sign_session(user_id):
    secret = _session_secret().encode()
    payload = base64.urlsafe_b64encode(json.dumps({"uid": int(user_id), "exp": int(time.time()) + 12 * 3600}, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
    return payload + "." + signature

def _verify_session(value):
    secret = _session_secret().encode()
    if not secret or not value or "." not in value:
        return None
    payload, signature = value.rsplit(".", 1)
    expected = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        data = json.loads(decoded)
        uid = int(data["uid"])
        if int(data["exp"]) < int(time.time()):
            return None
        if uid != DASHBOARD_OWNER_ID and uid not in DASHBOARD_WHITELIST_IDS:
            return None
        return uid
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None

async def start_dashboard(bot):
    if getattr(bot, "_dashboard_runner", None):
        return
    token = secrets.token_urlsafe(24)
    public_url = os.getenv("DASHBOARD_PUBLIC_URL", "").rstrip("/")
    oauth_client_id = os.getenv("DISCORD_OAUTH_CLIENT_ID", "")
    oauth_client_secret = os.getenv("DISCORD_OAUTH_CLIENT_SECRET", "")
    session_secret = _session_secret()
    oauth_enabled = bool(public_url.startswith("https://") and oauth_client_id and oauth_client_secret and len(session_secret) >= 32)
    app = web.Application(client_max_size=1024 * 32)

    @web.middleware
    async def auth_middleware(request, handler):
        if request.path in ("/", "/login", "/oauth/callback", "/logout") and request.method == "GET":
            return await handler(request)
        session_uid = _verify_session(request.cookies.get("dashboard_session", ""))
        if session_uid is not None:
            request["dashboard_user_id"] = session_uid
            return await handler(request)
        supplied = request.headers.get("X-Control-Token", "")
        peer = request.remote or ""
        local_peer = peer in ("127.0.0.1", "::1", "localhost")
        if local_peer and hmac.compare_digest(supplied, token):
            request["dashboard_user_id"] = DASHBOARD_OWNER_ID
            return await handler(request)
        return _json_error("請先使用已加入白名單的 Discord 帳號登入。", 401)

    app.middlewares.append(auth_middleware)

    async def index(request):
        return web.Response(text=PAGE, content_type="text/html", charset="utf-8")

    async def login(request):
        if not oauth_enabled:
            return web.Response(text="Discord OAuth 尚未設定。請設定 DASHBOARD_PUBLIC_URL、DISCORD_OAUTH_CLIENT_ID、DISCORD_OAUTH_CLIENT_SECRET 與至少 32 字元的 DASHBOARD_SESSION_SECRET。", status=503, content_type="text/plain", charset="utf-8")
        state = secrets.token_urlsafe(24)
        redirect_uri = public_url + "/oauth/callback"
        params = {"client_id": oauth_client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": "identify", "state": state, "prompt": "consent"}
        response = web.HTTPFound("https://discord.com/oauth2/authorize?" + urlencode(params))
        response.set_cookie("dashboard_oauth_state", state, max_age=600, httponly=True, secure=True, samesite="Lax", path="/")
        raise response

    async def oauth_callback(request):
        if not oauth_enabled:
            return web.Response(text="Discord OAuth 尚未設定。", status=503, content_type="text/plain", charset="utf-8")
        state = request.query.get("state", "")
        expected_state = request.cookies.get("dashboard_oauth_state", "")
        code = request.query.get("code", "")
        if not state or not expected_state or not hmac.compare_digest(state, expected_state) or not code:
            return web.Response(text="登入驗證失敗，請返回控制台重試。", status=400, content_type="text/plain", charset="utf-8")
        redirect_uri = public_url + "/oauth/callback"
        try:
            async with ClientSession() as session:
                async with session.post("https://discord.com/api/oauth2/token", data={"client_id": oauth_client_id, "client_secret": oauth_client_secret, "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri}, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=15) as response:
                    if response.status != 200:
                        return web.Response(text="Discord OAuth 換取 Token 失敗。", status=502, content_type="text/plain", charset="utf-8")
                    oauth_data = await response.json()
                async with session.get("https://discord.com/api/users/@me", headers={"Authorization": "Bearer " + oauth_data.get("access_token", "")}, timeout=15) as response:
                    if response.status != 200:
                        return web.Response(text="無法讀取 Discord 使用者身分。", status=502, content_type="text/plain", charset="utf-8")
                    user = await response.json()
        except Exception:
            return web.Response(text="Discord 登入服務暫時無法使用，請稍後再試。", status=502, content_type="text/plain", charset="utf-8")
        try:
            user_id = int(user.get("id", "0"))
        except (ValueError, TypeError):
            user_id = 0
        if user_id != DASHBOARD_OWNER_ID and user_id not in DASHBOARD_WHITELIST_IDS:
            response = web.Response(text="你的 Discord 帳號不在控制面板白名單中。", status=403, content_type="text/plain", charset="utf-8")
            response.del_cookie("dashboard_oauth_state", path="/")
            return response
        response = web.HTTPFound("/")
        response.set_cookie("dashboard_session", _sign_session(user_id), max_age=12 * 3600, httponly=True, secure=True, samesite="Lax", path="/")
        response.del_cookie("dashboard_oauth_state", path="/")
        raise response

    async def logout(request):
        response = web.HTTPFound("/")
        response.del_cookie("dashboard_session", path="/")
        raise response

    async def status(request):
        guild = bot.get_guild(bot._terminal_guild_id) if bot._terminal_guild_id else None
        channel = bot.get_channel(bot._terminal_channel_id) if bot._terminal_channel_id else None
        return web.json_response({
            "ready": bot.is_ready(),
            "user": str(bot.user) if bot.user else None,
            "guild_count": len(bot.guilds),
            "latency_ms": round(bot.latency * 1000) if bot.is_ready() else 0,
            "selected_guild_id": guild.id if guild else None,
            "selected_guild_name": guild.name if guild else None,
            "selected_channel_id": channel.id if channel else None,
            "selected_channel_name": channel.name if channel else None,
        })

    async def guilds(request):
        return web.json_response({"guilds": [
            {"id": str(g.id), "name": g.name}
            for g in sorted(bot.guilds, key=lambda x: x.name.casefold())
        ]})

    async def channels(request):
        try:
            guild_id = int(request.query.get("guild_id", ""))
        except ValueError:
            return _json_error("請先選擇伺服器。")
        guild = bot.get_guild(guild_id)
        if not guild:
            return _json_error("找不到伺服器，或 Bot 尚未加入。", 404)
        items = []
        for ch in guild.text_channels:
            me = guild.me
            perms = ch.permissions_for(me) if me else None
            if perms and perms.view_channel and perms.send_messages:
                items.append({"id": str(ch.id), "name": ch.name})
        for ch in guild.threads:
            me = guild.me
            perms = ch.permissions_for(me) if me else None
            if perms and perms.view_channel and perms.send_messages:
                items.append({"id": str(ch.id), "name": ch.name})
        return web.json_response({"channels": sorted(items, key=lambda x: x["name"].casefold())})

    async def select_target(request):
        try:
            data = await request.json()
            guild_id = int(data.get("guild_id", ""))
            channel_id = int(data.get("channel_id", ""))
        except (ValueError, TypeError):
            return _json_error("伺服器或頻道 ID 格式錯誤。")
        guild = bot.get_guild(guild_id)
        channel = _get_channel(bot, channel_id)
        if not guild or not channel or channel.guild.id != guild.id:
            return _json_error("頻道不屬於所選伺服器。")
        me = guild.me
        perms = channel.permissions_for(me) if me else None
        if not perms or not perms.view_channel or not perms.send_messages:
            return _json_error("Bot 沒有查看或傳送訊息的權限。", 403)
        bot._terminal_guild_id = guild.id
        bot._terminal_channel_id = channel.id
        return web.json_response({"guild_id": str(guild.id), "channel_id": str(channel.id)})

    async def send_message(request):
        try:
            data = await request.json()
            channel = _get_channel(bot, data.get("channel_id"))
            content = str(data.get("content", "")).strip()
        except (ValueError, TypeError):
            return _json_error("請求格式錯誤。")
        if not channel:
            return _json_error("找不到可用的文字頻道。", 404)
        if not content or len(content) > 2000:
            return _json_error("訊息不能空白，且最多 2000 字。")
        try:
            await channel.send(content, allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:
            return _json_error("Bot 沒有在這個頻道發送訊息的權限。", 403)
        except discord.HTTPException:
            return _json_error("Discord 傳送失敗，請稍後再試。", 502)
        return web.json_response({"message": "已傳送訊息到 #" + channel.name + "。"})

    async def reply_message(request):
        try:
            data = await request.json()
            channel = _get_channel(bot, data.get("channel_id"))
            message_id = int(data.get("message_id", ""))
            content = str(data.get("content", "")).strip()
        except (ValueError, TypeError):
            return _json_error("訊息 ID 或請求格式錯誤。")
        if not channel:
            return _json_error("找不到可用的文字頻道。", 404)
        if not content or len(content) > 2000:
            return _json_error("回覆不能空白，且最多 2000 字。")
        try:
            target = await channel.fetch_message(message_id)
            await target.reply(content, mention_author=False, allowed_mentions=discord.AllowedMentions.none())
        except discord.NotFound:
            return _json_error("找不到該訊息，請確認訊息 ID 和頻道。", 404)
        except discord.Forbidden:
            return _json_error("Bot 沒有查看訊息歷史或傳送回覆的權限。", 403)
        except discord.HTTPException:
            return _json_error("Discord 引用回覆失敗，請稍後再試。", 502)
        return web.json_response({"message": "已引用 " + str(target.author) + " 的訊息並回覆。"})

    app.router.add_get("/", index)
    app.router.add_get("/login", login)
    app.router.add_get("/oauth/callback", oauth_callback)
    app.router.add_get("/logout", logout)
    app.router.add_get("/api/status", status)
    app.router.add_get("/api/guilds", guilds)
    app.router.add_get("/api/channels", channels)
    app.router.add_post("/api/select", select_target)
    app.router.add_post("/api/send", send_message)
    app.router.add_post("/api/reply", reply_message)

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    port = int(os.getenv("PORT", "8765"))
    bind_host = "0.0.0.0" if public_url else "127.0.0.1"
    try:
        site = web.TCPSite(runner, bind_host, port)
        await site.start()
    except OSError:
        await runner.cleanup()
        print(f"[Dashboard] Could not bind {bind_host}:{port}; web panel was not started.")
        return
    bot._dashboard_runner = runner
    bot._dashboard_token = token
    print(f"\n[Dashboard] LogRelay control panel: http://127.0.0.1:{port}")
    if public_url:
        print(f"[Dashboard] Public URL configured: {public_url} (Discord OAuth {'enabled' if oauth_enabled else 'NOT configured'})")
    print("[Dashboard] Local control token (never share): " + token)
