import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit
from aiohttp import ClientSession, web
import discord

# Discord IDs explicitly approved to use the remote dashboard.
DASHBOARD_WHITELIST_IDS = {
    int(value.strip())
    for value in os.getenv("DASHBOARD_WHITELIST_IDS", "1338493569781989407").split(",")
    if value.strip().isdecimal() and int(value.strip()) > 0
}
DASHBOARD_OWNER_ID = int(os.getenv("OWNER_ID", "1140900506198351924"))

class SlidingWindowRateLimiter:
    def __init__(self, *, limit, window_seconds, max_keys=4096):
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self.attempts = {}

    def is_limited(self, key):
        now = time.monotonic()
        recent = [stamp for stamp in self.attempts.get(key, []) if now - stamp < self.window_seconds]
        if recent:
            self.attempts[key] = recent
        else:
            self.attempts.pop(key, None)
        return len(recent) >= self.limit

    def record(self, key):
        now = time.monotonic()
        recent = [stamp for stamp in self.attempts.get(key, []) if now - stamp < self.window_seconds]
        recent.append(now)
        self.attempts[key] = recent
        if len(self.attempts) > self.max_keys:
            oldest = min(self.attempts, key=lambda item: self.attempts[item][-1])
            if oldest != key:
                self.attempts.pop(oldest, None)

    def reset(self, key):
        self.attempts.pop(key, None)

PAGE = r"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>一條魚 Control</title>
<style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#131b2e;--line:#26334e;--text:#edf2ff;--muted:#94a3bd;--accent:#8b9cff;--good:#45d6a1;--bad:#ff8290}
.voice-card{position:relative;overflow:hidden;background:radial-gradient(ellipse at 100% 0,rgba(139,156,255,.15),transparent 48%),rgba(19,27,46,.96)}.voice-card-head{display:flex;gap:13px;align-items:flex-start;margin-bottom:17px}.voice-card-head h2{margin:0 0 5px}.voice-icon{display:grid;place-items:center;flex:0 0 44px;height:44px;border-radius:14px;background:rgba(139,156,255,.13);border:1px solid rgba(139,156,255,.28);color:#b9c5ff;font-weight:800}.voice-desc{margin:0;color:var(--muted);font-size:13px;line-height:1.65}.voice-room-meta{margin-top:8px;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:rgba(12,20,38,.75);font-size:12px;color:var(--muted)}.voice-card textarea{min-height:128px}.voice-actions{display:flex;align-items:center;gap:12px;margin-top:12px;flex-wrap:wrap}.voice-actions button{width:auto;min-width:150px;background:linear-gradient(135deg,#a9b7ff,#788cff);color:#10172b;border-color:transparent}.voice-limit{font-size:11px;color:var(--muted)}
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
<div class="top"><div><h1>一條魚 <span style="color:var(--accent)">Control</span></h1><div class="sub">Discord Bot 網頁控制台</div></div><div class="toolbar"><div class="pill" id="connection">等待連線</div><button class="secondary hidden" id="logout" style="width:auto">登出</button></div></div>
<section class="card" id="authCard"><h2>控制面板存取</h2><p>任何人都可以開啟此頁面；只有輸入管理員提供的 Key，或使用已授權的 Discord 帳號登入，才能操作 Bot。</p><label for="token">管理員提供的控制 Key</label><input id="token" type="password" autocomplete="off" placeholder="輸入控制 Key"><button id="connect" style="margin-top:10px">解鎖控制面板</button><a href="/login" style="display:block;text-decoration:none;margin-top:10px"><button type="button" class="secondary">使用 Discord 登入</button></a><div class="note">請勿公開分享控制 Key 或 Discord Bot Token。</div><div class="message" id="authMsg"></div></section>
<div id="app" class="hidden">
<div class="grid" style="margin-top:16px">
<section class="card wide"><div class="toolbar" style="justify-content:space-between"><h2>Bot 狀態</h2><button class="secondary" id="refresh" style="width:auto">重新整理</button></div><div class="stats"><div class="stat"><b id="botName">—</b><span>登入帳號</span></div><div class="stat"><b id="guildCount">—</b><span>已加入伺服器</span></div><div class="stat"><b id="latency">—</b><span>Gateway 延遲</span></div></div><div class="message" id="statusMsg"></div></section>
<section class="card"><h2>選擇伺服器與頻道</h2><label for="guild">伺服器</label><select id="guild"></select><label for="channel">文字頻道</label><select id="channel"></select><button id="selectTarget" style="margin-top:12px">設為目前控制頻道</button><div class="note" id="selectedInfo">尚未選擇</div><div class="message" id="selectMsg"></div></section>
<section class="card"><h2>傳送文字頻道訊息</h2><label for="sendText">要讓 一條魚 發送的內容</label><textarea id="sendText" maxlength="2000" placeholder="輸入訊息內容（最多 2000 字）"></textarea><button id="sendBtn" style="margin-top:12px">傳送到選定文字頻道</button><div class="message" id="sendMsg"></div></section>
<section class="card voice-card"><div class="voice-card-head"><div class="voice-icon">◖))</div><div><h2>即時語音朗讀</h2><p class="voice-desc">選擇語音房間，輸入文字，一條魚 就會加入語音並唸出來。</p></div></div><label for="voiceChannel">目標語音房間</label><select id="voiceChannel"></select><div class="voice-room-meta" id="voiceRoomMeta">先選擇伺服器，再選擇要朗讀的語音房間。</div><label for="voiceText">朗讀內容</label><textarea id="voiceText" maxlength="500" placeholder="例如：大家好，我現在可以直接在語音裡說話了。"></textarea><div class="voice-actions"><span class="voice-limit">最多 500 字 · 繁體中文語音</span><button id="voiceSendBtn">▶ 開始朗讀</button></div><div class="note">如果 Bot 正在播放音樂或其他音訊，會先拒絕朗讀，避免打斷播放。</div><div class="message" id="voiceSendMsg"></div></section>
<section class="card voice-card"><div class="voice-card-head"><div class="voice-icon">🎙</div><div><h2>即時麥克風傳聲</h2><p class="voice-desc">使用手機或電腦的瀏覽器麥克風，把你的聲音即時送進 Discord 語音頻道。</p></div></div><label for="micChannel">目標語音房間</label><select id="micChannel"></select><div class="voice-room-meta">允許瀏覽器使用麥克風；公開網站必須使用 HTTPS。</div><div class="voice-actions"><button id="micStartBtn">🎙 開始傳聲</button><button id="micMuteBtn" class="secondary" disabled>🔇 開啟麥克風</button><button id="micStopBtn" class="secondary" disabled>■ 停止傳聲</button></div><div class="note">開始後會持續傳送麥克風聲音，按停止即可結束。Bot 正在播放音樂時不會中斷音樂。</div><div class="message" id="micMsg"></div></section>
<section class="card wide"><h2>引用指定訊息回覆</h2><div class="row"><div><label for="messageId">Discord 訊息 ID</label><input id="messageId" inputmode="numeric" placeholder="右鍵訊息 → 複製訊息 ID"></div><div><label for="replyText">一條魚 的回覆內容</label><textarea id="replyText" maxlength="2000" placeholder="輸入要回覆的內容"></textarea></div></div><button id="replyBtn" style="margin-top:12px">引用該訊息並回覆</button><div class="note">必須先選擇該訊息所在的伺服器與頻道；一條魚 需要能查看頻道及訊息歷史。</div><div class="message" id="replyMsg"></div></section>
<section class="card"><h2>刪除指定訊息</h2><label for="deleteMessageId">Discord 訊息 ID</label><input id="deleteMessageId" inputmode="numeric" placeholder="右鍵目標訊息 → 複製訊息 ID"><button id="deleteBtn" style="margin-top:12px">刪除指定訊息</button><div class="note">刪除目前選定頻道中的訊息；Bot 必須有管理訊息權限。刪除後無法復原。</div><div class="message" id="deleteMsg"></div></section>
<section class="card"><h2>私下模仿整個伺服器的群聊語氣</h2><label for="mimicPrompt">要讓 AI 模仿著表達的內容</label><textarea id="mimicPrompt" maxlength="2000" placeholder="輸入一句話或要改寫的內容"></textarea><button id="mimicBtn" style="margin-top:12px">模仿訊息（結果只有你看得到）</button><label for="mimicResult">模仿結果</label><textarea id="mimicResult" readonly placeholder="結果會顯示在這裡"></textarea><div class="message" id="mimicMsg"></div></section>
</div></div>
</main>
<script>
let token="",selectedGuild="",selectedChannel="";
const GUILD_PREF_KEY="discord-dashboard-last-guild";
const $=id=>document.getElementById(id);
function message(id,text,error=false){$(id).textContent=text;$(id).classList.toggle("error",error)}
function setLockedControls(locked){document.querySelectorAll("#app button,#app input,#app select,#app textarea").forEach(el=>{el.disabled=locked})}
async function api(path,body){
 const options={headers:{"X-Control-Token":token}};
 if(body!==undefined){options.method="POST";options.headers["Content-Type"]="application/json";options.body=JSON.stringify(body)}
 const response=await fetch(path,options);let data={};try{data=await response.json()}catch{}
 if(!response.ok)throw new Error(data.error||("HTTP "+response.status));return data
}
async function loadStatus(){
 const data=await api("/api/status");$("botName").textContent=data.user||"未連線";$("guildCount").textContent=data.guild_count;$("latency").textContent=data.latency_ms+" ms";
 const savedGuild=localStorage.getItem(GUILD_PREF_KEY)||"";
 selectedGuild=String(savedGuild||data.selected_guild_id||selectedGuild||"");selectedChannel=String(data.selected_channel_id||selectedChannel||"");
 $("selectedInfo").textContent=data.selected_guild_name?(data.selected_guild_name+" / #"+data.selected_channel_name):"尚未選擇控制頻道";
}
async function loadGuilds(){
 const data=await api("/api/guilds");const select=$("guild");select.replaceChildren();
 for(const guild of data.guilds){const o=document.createElement("option");o.value=guild.id;o.textContent=guild.name;select.append(o)}
 const preferred=selectedGuild||localStorage.getItem(GUILD_PREF_KEY)||"";
 if(preferred&&[...select.options].some(o=>o.value===preferred))select.value=preferred;
 if(select.value){selectedGuild=select.value;localStorage.setItem(GUILD_PREF_KEY,selectedGuild);await loadChannels()}
}
async function loadChannels(){
 selectedGuild=$("guild").value;const data=await api("/api/channels?guild_id="+encodeURIComponent(selectedGuild));const select=$("channel");select.replaceChildren();
 for(const channel of data.channels){const o=document.createElement("option");o.value=channel.id;o.textContent="#"+channel.name;select.append(o)}
 if(selectedChannel&&[...select.options].some(o=>o.value===selectedChannel))select.value=selectedChannel;
 if(select.value)selectedChannel=select.value;
 await loadVoiceChannels();
}
async function loadVoiceChannels(){
 if(!$("guild").value)return;
 const data=await api("/api/voice-channels?guild_id="+encodeURIComponent($("guild").value));
 for(const id of ["voiceChannel","micChannel"]){
  const select=$(id);select.replaceChildren();const groups=new Map();
  for(const channel of data.channels){const category=channel.category||"未分類";if(!groups.has(category)){const group=document.createElement("optgroup");group.label="▰ "+category;groups.set(category,group);select.append(group)}const o=document.createElement("option");o.value=channel.id;o.textContent="🔊 "+channel.name;groups.get(category).append(o)}
  if(!select.options.length){const o=document.createElement("option");o.value="";o.textContent="沒有可用語音房間";select.append(o);select.disabled=true}else{select.disabled=false}
 }
 if($("voiceChannel").options.length)$("voiceRoomMeta").textContent="已載入 "+$("voiceChannel").options.length+" 個可用語音房間；選好房間後輸入文字即可朗讀。";
}
async function connect(){
 token=$("token").value.trim();if(!token){message("authMsg","請先輸入本機控制 Token。",true);return}
 try{await loadStatus();await loadGuilds();setLockedControls(false);$("authCard").classList.add("hidden");$("app").classList.remove("hidden");$("logout").classList.remove("hidden");$("connection").textContent="已解鎖";message("statusMsg","控制台已連接。")}
 catch(e){token="";message("authMsg",e.message,true)}
}
function showLocked(){token="";$("authCard").classList.remove("hidden");$("app").classList.add("hidden");setLockedControls(true);$("logout").classList.add("hidden");$("connection").textContent="已登出";$("token").value="";}
async function logout(){try{await fetch("/logout",{credentials:"same-origin"})}catch{}showLocked();message("authMsg","已登出控制面板。")}
$("connect").onclick=connect;$("logout").onclick=logout;window.addEventListener("load",async()=>{try{await loadStatus();await loadGuilds();$("authCard").classList.add("hidden");$("app").classList.remove("hidden");$("logout").classList.remove("hidden");$("connection").textContent="Discord 已驗證登入";message("statusMsg","已使用白名單 Discord 帳號登入。")}catch(e){$("app").classList.remove("hidden");setLockedControls(true);/* public read-only view; key required for API actions */}});$("refresh").onclick=async()=>{try{await loadStatus();await loadGuilds();message("statusMsg","狀態已更新。")}catch(e){message("statusMsg",e.message,true)}};
$("guild").onchange=async()=>{selectedChannel="";selectedGuild=$("guild").value;if(selectedGuild)localStorage.setItem(GUILD_PREF_KEY,selectedGuild);try{await loadChannels()}catch(e){message("selectMsg",e.message,true)}};
$("channel").onchange=()=>{selectedChannel=$("channel").value};
$("selectTarget").onclick=async()=>{try{const d=await api("/api/select",{guild_id:$("guild").value,channel_id:$("channel").value});selectedGuild=d.guild_id;selectedChannel=d.channel_id;await loadStatus();message("selectMsg","已設定控制頻道。")}catch(e){message("selectMsg",e.message,true)}};
$("sendBtn").onclick=async()=>{try{const d=await api("/api/send",{channel_id:$("channel").value,content:$("sendText").value});message("sendMsg",d.message);$("sendText").value=""}catch(e){message("sendMsg",e.message,true)}};
$("replyBtn").onclick=async()=>{try{const d=await api("/api/reply",{channel_id:$("channel").value,message_id:$("messageId").value.trim(),content:$("replyText").value});message("replyMsg",d.message);$("replyText").value=""}catch(e){message("replyMsg",e.message,true)}};
$("deleteBtn").onclick=async()=>{if(!confirm("確定刪除目前選定頻道中的這則訊息？此操作無法復原。"))return;try{const d=await api("/api/delete",{channel_id:$("channel").value,message_id:$("deleteMessageId").value.trim()});message("deleteMsg",d.message);$("deleteMessageId").value=""}catch(e){message("deleteMsg",e.message,true)}};
$("mimicBtn").onclick=async()=>{const prompt=$("mimicPrompt").value.trim();if(!prompt){message("mimicMsg","請先輸入要模仿著表達的內容。",true);return}const btn=$("mimicBtn");btn.disabled=true;message("mimicMsg","正在讀取群聊樣本並產生結果…");try{const d=await api("/api/mimic",{channel_id:$("channel").value,prompt});$("mimicResult").value=d.answer;message("mimicMsg","完成。結果只有目前控制台使用者看得到。")}catch(e){message("mimicMsg",e.message,true)}finally{btn.disabled=false}};
$("voiceChannel").onchange=()=>{const o=$("voiceChannel").selectedOptions[0];$("voiceRoomMeta").textContent=o&&o.value?"目標房間："+o.textContent+" · 按下朗讀後 Bot 會自動加入或切換到此房間。":"目前伺服器沒有可用語音房間。"}; $("voiceSendBtn").onclick=async()=>{const content=$("voiceText").value.trim();if(!content){message("voiceSendMsg","先輸入要唸的文字。",true);return}const btn=$("voiceSendBtn");btn.disabled=true;btn.textContent="正在產生語音…";try{const d=await api("/api/send-voice",{channel_id:$("voiceChannel").value,content});message("voiceSendMsg",d.message);$("voiceText").value=""}catch(e){message("voiceSendMsg",e.message,true)}finally{btn.disabled=false;btn.textContent="▶ 開始朗讀"}};

let micStream=null,micContext=null,micSocket=null,micProcessor=null,micSourceNode=null,micGain=null,micBuffer=new Float32Array(0),micActive=false,micMuted=true;
function closeBrowserMic(){
 micActive=false;if(micProcessor)micProcessor.onaudioprocess=null;
 try{if(micSocket&&micSocket.readyState===WebSocket.OPEN)micSocket.close()}catch(e){}
 try{if(micStream)micStream.getTracks().forEach(t=>t.stop())}catch(e){}
 try{if(micContext)micContext.close()}catch(e){}
 micStream=null;micContext=null;micSocket=null;micProcessor=null;micSourceNode=null;micGain=null;micBuffer=new Float32Array(0);micMuted=true;
 $("micStartBtn").disabled=false;$("micMuteBtn").disabled=true;$("micMuteBtn").textContent="🔇 開啟麥克風";$("micStopBtn").disabled=true;
}
$("micStartBtn").onclick=async()=>{
 if(micActive)return;
 const channelId=$("micChannel").value;if(!channelId){message("micMsg","先選擇語音房間。",true);return}
 const btn=$("micStartBtn");btn.disabled=true;message("micMsg","正在連接 Discord 語音並要求麥克風權限…");
 try{
  if(!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia)throw new Error("此頁面無法使用麥克風；請使用 HTTPS 網址或 localhost。");
  const ticketData=await api("/api/mic/start",{channel_id:channelId});
  const wsProto=location.protocol==="https:"?"wss:":"ws:";
  micSocket=new WebSocket(wsProto+"//"+location.host+"/api/mic/stream?ticket="+encodeURIComponent(ticketData.ticket));
  micSocket.binaryType="arraybuffer";
  await new Promise((resolve,reject)=>{micSocket.onopen=resolve;micSocket.onerror=()=>reject(new Error("麥克風串流連線失敗。"))});
  micStream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
  micContext=new (window.AudioContext||window.webkitAudioContext)({sampleRate:48000});await micContext.resume();
  micSourceNode=micContext.createMediaStreamSource(micStream);micProcessor=micContext.createScriptProcessor(4096,1,1);
  micGain=micContext.createGain();micGain.gain.value=0;
  micProcessor.onaudioprocess=event=>{
   if(!micActive||!micSocket||micSocket.readyState!==WebSocket.OPEN)return;
   const input=event.inputBuffer.getChannelData(0),ratio=micContext.sampleRate/48000;
   const outLen=Math.floor(input.length/ratio),resampled=new Float32Array(outLen);
   for(let i=0;i<outLen;i++){const pos=i*ratio,lo=Math.floor(pos),hi=Math.min(lo+1,input.length-1),frac=pos-lo;resampled[i]=input[lo]*(1-frac)+input[hi]*frac}
   const combined=new Float32Array(micBuffer.length+resampled.length);combined.set(micBuffer);combined.set(resampled,micBuffer.length);
   let offset=0;
   while(combined.length-offset>=960&&micSocket.readyState===WebSocket.OPEN){
    const pcm=new Int16Array(960);if(micMuted){pcm.fill(0)}else{for(let i=0;i<960;i++){const v=Math.max(-1,Math.min(1,combined[offset+i]));pcm[i]=v<0?v*32768:v*32767}}
    micSocket.send(pcm.buffer);offset+=960;
   }
   micBuffer=combined.slice(offset);
  };
  micSourceNode.connect(micProcessor);micProcessor.connect(micGain);micGain.connect(micContext.destination);
  micActive=true;micMuted=false;micStream.getAudioTracks().forEach(track=>track.enabled=true);btn.disabled=true;$("micMuteBtn").disabled=false;$("micMuteBtn").textContent="🔇 關閉麥克風";$("micStopBtn").disabled=false;message("micMsg","麥克風已開啟，正在即時傳送聲音到 Discord。");
 }catch(e){closeBrowserMic();try{await api("/api/mic/stop",{channel_id:channelId})}catch(err){}message("micMsg",e.message||"啟動麥克風失敗。",true)}
};
$("micMuteBtn").onclick=()=>{if(!micActive||!micStream)return;micMuted=!micMuted;micStream.getAudioTracks().forEach(track=>track.enabled=!micMuted);$("micMuteBtn").textContent=micMuted?"🎙 開啟麥克風":"🔇 關閉麥克風";message("micMsg",micMuted?"麥克風已關閉，傳聲連線仍保持。":"麥克風已開啟，正在即時傳送聲音到 Discord。")};
$("micStopBtn").onclick=async()=>{
 const channelId=$("micChannel").value;closeBrowserMic();try{const d=await api("/api/mic/stop",{channel_id:channelId});message("micMsg",d.message||"已停止麥克風傳聲。")}catch(e){message("micMsg",e.message,true)}
};
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

async def _find_message_in_readable_channels(bot, selected_channel, message_id):
    """Find a message, checking the selected channel before searching other channels."""
    candidates = [selected_channel]
    seen = set()
    for guild in bot.guilds:
        candidates.extend(guild.text_channels)
        candidates.extend(guild.threads)

    for candidate in candidates:
        if candidate.id in seen:
            continue
        seen.add(candidate.id)
        guild = getattr(candidate, "guild", None)
        me = getattr(guild, "me", None) if guild else None
        perms = candidate.permissions_for(me) if me else None
        if not perms or not perms.view_channel or not perms.read_message_history:
            continue
        try:
            return candidate, await candidate.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden):
            continue
        except discord.HTTPException:
            continue
    return None, None

def _session_secret():
    return os.getenv("DASHBOARD_SESSION_SECRET", "")

def _sign_session(user_id):
    secret = _session_secret().encode()
    payload = base64.urlsafe_b64encode(json.dumps({"uid": int(user_id), "exp": int(time.time()) + 30 * 24 * 3600}, separators=(",", ":")).encode()).decode().rstrip("=")
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

def _is_same_origin(origin, expected_origin):
    """Return True only when an Origin header exactly matches the dashboard origin."""
    if not isinstance(origin, str) or not origin or not isinstance(expected_origin, str) or not expected_origin:
        return False
    try:
        supplied = urlsplit(origin)
        expected = urlsplit(expected_origin)
        if supplied.scheme not in ("http", "https") or not supplied.netloc:
            return False
        if supplied.username is not None or supplied.password is not None:
            return False
        if supplied.path or supplied.query or supplied.fragment:
            return False
        return (
            supplied.scheme.lower() == expected.scheme.lower()
            and supplied.netloc.lower() == expected.netloc.lower()
            and expected.scheme.lower() in ("http", "https")
            and bool(expected.netloc)
        )
    except ValueError:
        return False


def _is_allowed_websocket_origin(origin, expected_origin):
    """Require browser WebSocket handshakes to originate from the dashboard itself."""
    return _is_same_origin(origin, expected_origin)


def _dashboard_content_security_policy():
    """Allow only the dashboard's exact inline script and deny plugin/base injection."""
    scripts = re.findall(r"<script>(.*?)</script>", PAGE, flags=re.DOTALL)
    script_hashes = [
        base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode("ascii")
        for script in scripts
    ]
    script_source = " ".join("'sha256-" + value + "'" for value in script_hashes)
    return (
        "default-src 'self'; "
        "script-src " + (script_source or "'none'") + "; "
        "style-src 'self' 'unsafe-inline'; "
        "connect-src 'self' ws: wss:; "
        "img-src 'self' data: blob:; "
        "media-src 'self' blob:; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'; "
        "form-action 'self' https://discord.com"
    )


def _apply_dashboard_security_headers(response, *, public_https=False):
    """Apply browser security headers without changing dashboard functionality."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(self), geolocation=()"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = _dashboard_content_security_policy()
    if public_https:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"


async def start_dashboard(bot):
    if getattr(bot, "_dashboard_runner", None):
        return
    token = secrets.token_urlsafe(24)
    public_url = os.getenv("DASHBOARD_PUBLIC_URL", "").rstrip("/")
    oauth_client_id = os.getenv("DISCORD_OAUTH_CLIENT_ID", "")
    oauth_client_secret = os.getenv("DISCORD_OAUTH_CLIENT_SECRET", "")
    session_secret = _session_secret()
    # OAuth can run on HTTPS for a deployed dashboard, or on loopback HTTP for a local-only dashboard.
    local_public_url = public_url.startswith(("http://127.0.0.1:", "http://localhost:"))
    valid_oauth_url = public_url.startswith("https://") or local_public_url
    oauth_enabled = bool(valid_oauth_url and oauth_client_id and oauth_client_secret and len(session_secret) >= 32)
    parsed_public_url = urlsplit(public_url) if public_url else None
    configured_origin = (
        f"{parsed_public_url.scheme}://{parsed_public_url.netloc}"
        if parsed_public_url and parsed_public_url.scheme and parsed_public_url.netloc
        else ""
    )
    auth_rate_limiter = SlidingWindowRateLimiter(limit=10, window_seconds=60)
    app = web.Application(client_max_size=1024 * 32)

    async def add_security_headers(request, response):
        _apply_dashboard_security_headers(
            response,
            public_https=public_url.startswith("https://"),
        )

    app.on_response_prepare.append(add_security_headers)

    @web.middleware
    async def auth_middleware(request, handler):
        # The WebSocket uses a short-lived, single-use ticket issued by the authenticated POST endpoint.
        if request.path == "/api/mic/stream":
            return await handler(request)
        if request.path in ("/", "/login", "/oauth/callback", "/logout") and request.method == "GET":
            return await handler(request)
        session_uid = _verify_session(request.cookies.get("dashboard_session", ""))
        if session_uid is not None:
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                expected_origin = configured_origin or f"{request.scheme}://{request.host}"
                if not _is_same_origin(request.headers.get("Origin", ""), expected_origin):
                    return _json_error("跨來源請求已拒絕，請重新載入控制台後再試。", 403)
            request["dashboard_user_id"] = session_uid
            return await handler(request)
        supplied = request.headers.get("X-Control-Token", "")
        if supplied:
            # Behind a reverse proxy, only trust forwarded client addresses when
            # the operator explicitly confirms that the proxy overwrites the header.
            client_key = request.remote or "unknown"
            if os.getenv("DASHBOARD_TRUST_PROXY", "").strip().lower() in ("1", "true", "yes"):
                forwarded = request.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
                if forwarded:
                    client_key = forwarded
            if auth_rate_limiter.is_limited(client_key):
                return _json_error("控制 Key 嘗試次數過多，請稍後再試。", 429)
        if hmac.compare_digest(supplied, token):
            if supplied:
                auth_rate_limiter.reset(client_key)
            request["dashboard_user_id"] = DASHBOARD_OWNER_ID
            return await handler(request)
        if supplied:
            auth_rate_limiter.record(client_key)
        return _json_error("請先使用已加入白名單的 Discord 帳號登入。", 401)

    app.middlewares.append(auth_middleware)

    async def index(request):
        response = web.Response(text=PAGE, content_type="text/html", charset="utf-8")
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        return response

    async def favicon(request):
        return web.Response(status=204)

    async def login(request):
        if not oauth_enabled:
            return web.Response(text="Discord OAuth 尚未設定。請設定 DASHBOARD_PUBLIC_URL、DISCORD_OAUTH_CLIENT_ID、DISCORD_OAUTH_CLIENT_SECRET 與至少 32 字元的 DASHBOARD_SESSION_SECRET。", status=503, content_type="text/plain", charset="utf-8")
        state = secrets.token_urlsafe(24)
        redirect_uri = public_url + "/oauth/callback"
        params = {"client_id": oauth_client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": "identify", "state": state}
        response = web.HTTPFound("https://discord.com/oauth2/authorize?" + urlencode(params))
        response.set_cookie("dashboard_oauth_state", state, max_age=600, httponly=True, secure=not local_public_url, samesite="Lax", path="/")
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
        response.set_cookie("dashboard_session", _sign_session(user_id), max_age=30 * 24 * 3600, httponly=True, secure=not local_public_url, samesite="Lax", path="/")
        response.del_cookie("dashboard_oauth_state", path="/")
        raise response

    async def logout(request):
        response = web.HTTPFound("/")
        response.del_cookie("dashboard_session", path="/")
        raise response

    async def status(request):
        selected_guild_id = getattr(bot, "_dashboard_guild_id", None)
        selected_channel_id = getattr(bot, "_dashboard_channel_id", None)
        guild = bot.get_guild(selected_guild_id) if selected_guild_id else None
        channel = bot.get_channel(selected_channel_id) if selected_channel_id else None
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

    async def voice_channels(request):
        try:
            guild_id = int(request.query.get("guild_id", ""))
        except ValueError:
            return _json_error("請先選擇伺服器。")
        guild = bot.get_guild(guild_id)
        if not guild:
            return _json_error("找不到伺服器，或 Bot 尚未加入。", 404)
        me = guild.me
        items = []
        for channel in guild.voice_channels:
            perms = channel.permissions_for(me) if me else None
            if perms and perms.view_channel and perms.connect and perms.speak:
                items.append({
                    "id": str(channel.id),
                    "name": channel.name,
                    "category": channel.category.name if channel.category else "未分類",
                })
        return web.json_response({"channels": sorted(items, key=lambda item: (item["category"].casefold(), item["name"].casefold()))})

    async def send_voice_message(request):
        try:
            data = await request.json()
            channel_id = int(data.get("channel_id", ""))
            content = str(data.get("content", "")).strip()
        except (ValueError, TypeError):
            return _json_error("語音房間或文字格式錯誤。")
        channel = bot.get_channel(channel_id)
        if not isinstance(channel, discord.VoiceChannel):
            return _json_error("找不到指定的語音房間。", 404)
        if not content or len(content) > 500:
            return _json_error("朗讀內容不能空白，且最多 500 字。")
        me = channel.guild.me
        perms = channel.permissions_for(me) if me else None
        if not perms or not perms.view_channel or not perms.connect or not perms.speak:
            return _json_error("Bot 在這個語音房間需要「查看頻道」、「連線」和「說話」權限。", 403)
        speech = bot.get_cog("SpeechControlCog")
        if not speech:
            return _json_error("語音朗讀模組尚未載入。", 503)
        ok, result = await speech.speak_to_channel(channel, content)
        if not ok:
            return _json_error(result, 409)
        return web.json_response({"message": result})

    async def start_browser_mic(request):
        try:
            data = await request.json()
            channel_id = int(data.get("channel_id", ""))
        except (ValueError, TypeError):
            return _json_error("請先選擇語音房間。")
        channel = bot.get_channel(channel_id)
        if not isinstance(channel, discord.VoiceChannel):
            return _json_error("找不到指定的語音房間。", 404)
        speech = bot.get_cog("BrowserMicCog")
        if not speech:
            return _json_error("瀏覽器麥克風模組尚未載入，請重新啟動 Bot。", 503)
        ok, message_text, ticket = await speech.start_stream(channel)
        if not ok:
            return _json_error(message_text, 409)
        return web.json_response({"message": message_text, "ticket": ticket})

    async def stop_browser_mic(request):
        try:
            data = await request.json()
            channel_id = int(data.get("channel_id", ""))
        except (ValueError, TypeError):
            return _json_error("語音房間 ID 格式錯誤。")
        channel = bot.get_channel(channel_id)
        if not isinstance(channel, discord.VoiceChannel):
            return _json_error("找不到指定的語音房間。", 404)
        speech = bot.get_cog("BrowserMicCog")
        if not speech:
            return _json_error("瀏覽器麥克風模組尚未載入。", 503)
        stopped = await speech.stop_stream(channel.guild.id)
        return web.json_response({"message": "已停止麥克風傳聲。" if stopped else "目前沒有啟動中的瀏覽器麥克風。"})

    async def browser_mic_stream(request):
        expected_origin = configured_origin or f"{request.scheme}://{request.host}"
        if not _is_allowed_websocket_origin(request.headers.get("Origin", ""), expected_origin):
            return _json_error("麥克風串流拒絕非控制台來源的連線。", 403)
        ticket = request.query.get("ticket", "")
        speech = bot.get_cog("BrowserMicCog")
        guild_id = speech.consume_ticket(ticket) if speech and ticket else None
        if guild_id is None:
            return _json_error("麥克風串流票證無效或已過期，請重新按開始。", 401)
        ws = web.WebSocketResponse(max_msg_size=4096, heartbeat=20)
        await ws.prepare(request)
        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.BINARY:
                    if len(msg.data) != 1920:
                        await ws.close(code=1003, message=b"expected 20ms mono PCM16 frame")
                        break
                    if not speech.feed_frame(guild_id, msg.data):
                        await ws.close(code=1011, message=b"audio source is no longer active")
                        break
                elif msg.type in (web.WSMsgType.CLOSE, web.WSMsgType.CLOSED, web.WSMsgType.ERROR):
                    break
        finally:
            await speech.stop_stream(guild_id)
        return ws

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
        bot._dashboard_guild_id = guild.id
        bot._dashboard_channel_id = channel.id
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
        except discord.NotFound:
            # A message ID is globally unique, but the dashboard's selected
            # channel may not be the channel where the user copied the message.
            # Search readable text channels in the selected guild as a fallback.
            target = None
            # Message IDs are globally unique, but Discord requires a channel
            # ID to fetch a message. Search channels visible to this bot, not
            # only the currently selected channel/server.
            seen_channel_ids = {channel.id}
            candidates = []
            for guild in bot.guilds:
                candidates.extend(guild.text_channels)
                candidates.extend(guild.threads)
            for candidate in candidates:
                if candidate.id in seen_channel_ids:
                    continue
                seen_channel_ids.add(candidate.id)
                guild = candidate.guild
                me = guild.me
                perms = candidate.permissions_for(me) if me else None
                if not perms or not perms.view_channel or not perms.read_message_history:
                    continue
                try:
                    target = await candidate.fetch_message(message_id)
                    channel = candidate
                    break
                except (discord.NotFound, discord.Forbidden):
                    continue
                except discord.HTTPException:
                    continue
            if target is None:
                return _json_error(
                    "找不到該訊息：請確認訊息 ID 正確、訊息仍存在，而且 Bot 有權限查看該訊息所在頻道及讀取訊息歷史。請在訊息所在伺服器中確認 Bot 已加入。",
                    404,
                )
        except discord.Forbidden:
            return _json_error("Bot 沒有查看訊息歷史或傳送回覆的權限。", 403)
        except discord.HTTPException:
            return _json_error("Discord 讀取訊息失敗，請稍後再試。", 502)
        try:
            await target.reply(content, mention_author=False, allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:
            return _json_error("Bot 找到了訊息，但沒有在該頻道傳送回覆的權限。", 403)
        except discord.HTTPException:
            return _json_error("Discord 引用回覆失敗，請稍後再試。", 502)
        return web.json_response({"message": "已在 #" + channel.name + " 引用 " + str(target.author) + " 的訊息並回覆。"})

    app.router.add_get("/", index)
    app.router.add_get("/favicon.ico", favicon)
    app.router.add_get("/login", login)
    app.router.add_get("/oauth/callback", oauth_callback)
    app.router.add_get("/logout", logout)
    app.router.add_get("/api/status", status)
    app.router.add_get("/api/guilds", guilds)
    app.router.add_get("/api/channels", channels)
    app.router.add_get("/api/voice-channels", voice_channels)
    app.router.add_post("/api/send-voice", send_voice_message)
    app.router.add_post("/api/mic/start", start_browser_mic)
    app.router.add_post("/api/mic/stop", stop_browser_mic)
    app.router.add_get("/api/mic/stream", browser_mic_stream)
    app.router.add_post("/api/select", select_target)
    app.router.add_post("/api/send", send_message)
    async def delete_message(request):
        try:
            data = await request.json()
            channel = _get_channel(bot, data.get("channel_id"))
            message_id = int(data.get("message_id", ""))
        except (ValueError, TypeError):
            return _json_error("訊息 ID 或請求格式錯誤。")
        if message_id <= 0:
            return _json_error("請輸入有效的 Discord 訊息 ID。")
        if not channel:
            return _json_error("找不到可用的文字頻道。", 404)

        channel, target = await _find_message_in_readable_channels(
            bot, channel, message_id
        )
        if target is None:
            return _json_error(
                "找不到這則訊息。請確認訊息 ID 正確、訊息仍存在，而且 Bot 有權查看該頻道及讀取訊息歷史。",
                404,
            )

        me = channel.guild.me
        perms = channel.permissions_for(me) if me else None
        is_own_message = bool(bot.user and target.author.id == bot.user.id)
        if not perms or not perms.view_channel or not perms.read_message_history:
            return _json_error("Bot 缺少查看訊息或讀取歷史的權限。", 403)
        if not is_own_message and not perms.manage_messages:
            return _json_error("刪除其他成員的訊息需要 Bot 的「管理訊息」權限。", 403)

        try:
            await target.delete()
        except discord.Forbidden:
            return _json_error("Discord 拒絕刪除。請確認 Bot 的頻道權限。", 403)
        except discord.HTTPException:
            return _json_error("Discord 刪除訊息失敗，請稍後再試。", 502)
        return web.json_response({"message": "已刪除 #" + channel.name + " 中的指定訊息。"})

    async def mimic_message(request):
        try:
            data = await request.json()
            channel = _get_channel(bot, data.get("channel_id"))
            prompt = str(data.get("prompt", "")).strip()
        except (ValueError, TypeError):
            return _json_error("請求格式錯誤。")
        if not channel:
            return _json_error("找不到可用的文字頻道。", 404)
        if not prompt or len(prompt) > 2000:
            return _json_error("模仿內容不能空白，且最多 2000 字。")
        cog = bot.get_cog("SearchCog")
        if not cog:
            return _json_error("AI 模組尚未載入；請重新啟動 Bot。", 503)
        if not (getattr(cog, "freemind_base_url", "") or getattr(cog, "gemini_client", None) or getattr(cog, "groq_client", None)):
            return _json_error("沒有可用的 AI 服務。請檢查 FreeMind AI、Gemini 或 Groq 設定。", 503)
        try:
            from chat_context import collect_server_chat_context
            user_id = int(request["dashboard_user_id"])
            requester = channel.guild.get_member(user_id)
            if requester is None:
                return _json_error(
                    "目前登入的 Discord 帳號不在所選伺服器中，無法依照該帳號的頻道可見權限讀取聊天樣本。",
                    403,
                )
            context = await collect_server_chat_context(channel, bot, requester=requester)
            prompt_parts = [
                "【任務】根據伺服器聊天樣本，模仿整個伺服器所有成員共同呈現的聊天語氣、用詞、節奏、常見梗與互動風格，改寫或表達使用者指定的內容。",
                "不要只模仿單一成員，不要逐字複製任何人的完整訊息，也不要假裝自己就是某一位特定成員。只輸出結果，不要分析。",
            ]
            if context:
                prompt_parts.append("【伺服器各頻道近期聊天樣本】\n" + context)
            else:
                prompt_parts.append("目前沒有足夠的聊天樣本；使用自然的繁體中文 Discord 群聊語氣，不要捏造群內梗。")
            prompt_parts.append("【要模仿著表達的內容】\n" + prompt)
            answer = await cog._generate_gemini_chat("\n\n".join(prompt_parts))
            if not answer:
                return _json_error("AI 沒有產生結果。請檢查 Bot 終端機中的 AI 錯誤，或確認 API 額度與模型設定。", 502)
            return web.json_response({"answer": answer[:1900]})
        except Exception as exc:
            print(f"[Dashboard mimic] {type(exc).__name__}: {exc}")
            return _json_error("模仿失敗，請查看 Bot 終端機的 [Dashboard mimic] 錯誤紀錄。", 502)

    app.router.add_post("/api/reply", reply_message)
    app.router.add_post("/api/delete", delete_message)
    app.router.add_post("/api/mimic", mimic_message)

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    configured_port = int(os.getenv("PORT", "8765"))
    bind_host = "0.0.0.0" if public_url and not local_public_url else "127.0.0.1"
    site = None
    port = configured_port

    # A previous Bot process may still own 8765 and serve an older dashboard
    # without newer routes such as /api/reply. In local mode, move to the next
    # available loopback port instead of silently leaving the old panel active.
    candidate_ports = [configured_port] if public_url else range(configured_port, configured_port + 11)
    for port in candidate_ports:
        try:
            site = web.TCPSite(runner, bind_host, port)
            await site.start()
            break
        except OSError as exc:
            if public_url or port == configured_port + 10:
                await runner.cleanup()
                print(f"[Dashboard] Could not bind {bind_host}:{port}; web panel was not started: {exc}")
                return
            print(f"[Dashboard] Port {port} is already in use; trying {port + 1}.")
            site = None

    if site is None:
        await runner.cleanup()
        print("[Dashboard] No available local dashboard port found.")
        return

    bot._dashboard_runner = runner
    bot._dashboard_token = token
    print(f"\n[Dashboard] 一條魚 control panel: http://127.0.0.1:{port}")
    if not public_url and port != configured_port:
        print(f"[Dashboard] IMPORTANT: another process is using port {configured_port}; use this new URL, not the old browser tab.")
    if public_url:
        print(f"[Dashboard] Public URL configured: {public_url} (Discord OAuth {'enabled' if oauth_enabled else 'NOT configured'})")
    print("[Dashboard] Local control token (never share): " + token)
