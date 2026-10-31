from __future__ import annotations

import ast
import json
import operator
import os
import sqlite3
import threading
import webbrowser
import subprocess
import time
import traceback
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "economy.db"
HOST, PORT = "127.0.0.1", 8766
MAX_BALANCE = 9_000_000_000_000_000
API = "https://discord.com/api/v10"
CACHE_TTL = 900
_name_cache: dict[tuple[int, int], tuple[float, str]] = {}
_cache_lock = threading.Lock()


def _discord_token() -> str:
    token = os.getenv("DISCORD_TOKEN", "").strip().strip('"').strip("'")
    if token:
        return token
    try:
        for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                if key.strip() == "DISCORD_TOKEN":
                    return value.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _discord_get(path: str, token: str) -> dict:
    request = urllib.request.Request(
        API + path,
        headers={
            "Authorization": "Bot " + token,
            "Accept": "application/json",
            "User-Agent": "DiscordBot (economy-editor, 1.0)",
        },
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Discord API 回傳的資料格式不正確")
    return data


def _fetch_name(user_id: int, guild_id: int, token: str) -> str:
    paths = []
    if guild_id > 0:
        paths.append(f"/guilds/{guild_id}/members/{user_id}")
    paths.append(f"/users/{user_id}")
    for path in paths:
        try:
            data = _discord_get(path, token)
            user = data.get("user") if isinstance(data.get("user"), dict) else data
            name = data.get("nick") or user.get("global_name") or user.get("username")
            if isinstance(name, str) and name.strip():
                return name.strip()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, ValueError, TypeError):
            continue
    return "無法取得名稱"


def _add_display_names(rows: list[dict]) -> list[dict]:
    if not rows:
        return rows
    token = _discord_token()
    if not token:
        return [
            {
                **row,
                "user_id": str(row["user_id"]),
                "guild_id": str(row.get("guild_id") or 0),
                "display_name": "未設定 Discord Token",
            }
            for row in rows
        ]

    now = time.monotonic()
    pending = []
    names = {}
    for row in rows:
        key = (int(row.get("guild_id") or 0), int(row["user_id"]))
        with _cache_lock:
            cached = _name_cache.get(key)
        if cached and now - cached[0] < CACHE_TTL:
            names[key] = cached[1]
        else:
            pending.append((key, key[0], key[1]))

    if pending:
        with ThreadPoolExecutor(max_workers=min(8, len(pending))) as pool:
            futures = {pool.submit(_fetch_name, uid, gid, token): key for key, gid, uid in pending}
            for future, key in futures.items():
                try:
                    name = future.result()
                except Exception:
                    name = "無法取得名稱"
                names[key] = name
                with _cache_lock:
                    _name_cache[key] = (time.monotonic(), name)

    return [
        {
            **row,
            # Discord snowflake IDs must be sent to JavaScript as strings to avoid
            # IEEE-754 Number precision loss for 17–20 digit identifiers.
            "user_id": str(row["user_id"]),
            "guild_id": str(row.get("guild_id") or 0),
            "display_name": names.get(
                (int(row.get("guild_id") or 0), int(row["user_id"])),
                "無法取得名稱",
            ),
        }
        for row in rows
    ]


EDITOR_TAB_MANAGER_SCRIPT = r"""
self.addEventListener("install", event => event.waitUntil(self.skipWaiting()));
self.addEventListener("activate", event => event.waitUntil(self.clients.claim()));
self.addEventListener("message", event => {
    if (!event.data || event.data.type !== "takeover-economy-editor-tabs") return;
    const sourceClientId = event.source && event.source.id;
    const expectedOrigin = new URL(self.registration.scope).origin;
    event.waitUntil((async () => {
        const clients = await self.clients.matchAll({type: "window", includeUncontrolled: true});
        await Promise.all(clients.map(async client => {
            if (client.id === sourceClientId) return;
            let target;
            try { target = new URL(client.url); } catch { return; }
            if (target.origin !== expectedOrigin || !["/", "/index.html"].includes(target.pathname)) return;
            try { await client.navigate(new URL("/__economy_editor_replaced", expectedOrigin).href); } catch {}
        }));
    })());
});
"""

EDITOR_RETIRED_PAGE = """<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>經濟編輯器已更新</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#10131a;color:#e8edf5;font:16px system-ui,"Microsoft JhengHei",sans-serif}main{max-width:520px;margin:24px;padding:28px;border:1px solid #39465a;border-radius:16px;background:#171c25}p{color:#aab5c5;line-height:1.7}button{padding:10px 16px;border:0;border-radius:8px;background:#78a9ff;color:#101827;font-weight:700;cursor:pointer}</style>
</head><body><main><h1>舊的經濟編輯器已停用</h1>
<p>新版經濟資料庫編輯器已經開啟。這個舊分頁已切換到停用頁面，可以直接關閉。</p>
<button type="button" onclick="window.close()">關閉此分頁</button></main></body></html>"""

PAGE = r"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>經濟資料庫編輯器</title>
<style>
:root{color-scheme:dark;font-family:system-ui,"Microsoft JhengHei",sans-serif}
body{margin:0;background:#10131a;color:#e8edf5}main{max-width:1200px;margin:28px auto;padding:0 16px}
h1{margin-bottom:6px}.muted{color:#aab5c5;overflow-wrap:anywhere}
.notice{padding:12px 14px;background:#252d3a;border:1px solid #39465a;border-radius:10px;margin:16px 0}
.table-wrap{width:100%;max-width:100%;overflow-x:auto;border-radius:12px;border:1px solid #303847;background:#171c25;overscroll-behavior-x:contain}
table{width:max-content;min-width:100%;border-collapse:separate;border-spacing:0;background:#171c25}
th,td{padding:10px;border-bottom:1px solid #303847;text-align:left;vertical-align:top;white-space:nowrap}
th{background:#202735;position:sticky;top:0;z-index:1}
td:first-child,td:nth-child(2){font-family:ui-monospace,monospace}
td:nth-child(2){min-width:165px}
input{box-sizing:border-box;width:180px;min-width:180px;padding:9px;border-radius:7px;border:1px solid #48556a;background:#0f141c;color:#fff}
input[name="display_name"]{width:180px;min-width:180px}
input.time{width:230px;min-width:230px}
button{padding:9px 13px;border:0;border-radius:7px;background:#78a9ff;color:#101827;font-weight:700;cursor:pointer}
button:hover{filter:brightness(1.08)}.save{white-space:nowrap}.empty{padding:20px;background:#171c25;border-radius:10px}
@media(max-width:750px){main{margin:16px auto;padding:0 10px}.table-wrap{max-width:calc(100vw - 20px)}th,td{padding:8px}}
</style></head><body><main>
<h1>經濟資料庫編輯器</h1><div class="muted">資料庫：<code>__DB_PATH__</code></div>
<div class="notice">帳戶以 Discord 使用者 ID 核對；伺服器識別資料只在系統內部使用，不顯示於此編輯器。只在這台電腦的 127.0.0.1 開放。玩家名稱優先使用伺服器暱稱；建議修改資料前先關閉 Discord Bot。</div>
<div id="status" role="status"></div><div id="content">正在載入資料…</div></main>
<script>
const fields=[
 ["display_name","玩家名稱"],["wallet","現金"],["bank","銀行存款"],
 ["last_daily","上次簽到時間（UTC）"],["last_work","上次打工時間（UTC）"],
 ["last_fish","上次釣魚時間（UTC）"],["last_rob","上次搶劫時間（UTC）"]
];
async function load(){
 const res=await fetch("/api/users",{cache:"no-store"}),data=await res.json(),root=document.getElementById("content");
 if(!res.ok){root.textContent=data.error||"讀取失敗";return}
 if(!data.users.length){root.innerHTML='<div class="empty">目前沒有玩家資料。先在 Discord 使用一次經濟指令，建立資料後再重新整理。</div>';return}
 const table=document.createElement("table"),thead=document.createElement("thead"),headrow=document.createElement("tr");
 ["Discord 使用者 ID",...fields.map(f=>f[1]),"操作"].forEach(label=>{const th=document.createElement("th");th.textContent=label;headrow.appendChild(th)});
 thead.appendChild(headrow);table.appendChild(thead);const tbody=document.createElement("tbody");
 for(const user of data.users){
  const tr=document.createElement("tr"),form=document.createElement("form");
  const formId="economy-save-"+String(user.guild_id)+"-"+String(user.user_id);form.id=formId;
  const userIdCell=document.createElement("td");userIdCell.dataset.label="Discord 使用者 ID";userIdCell.textContent=String(user.user_id??"");userIdCell.style.fontFamily="ui-monospace,monospace";userIdCell.style.whiteSpace="nowrap";tr.appendChild(userIdCell);
  for(const [key,label] of fields){
   const td=document.createElement("td");td.dataset.label=label;const input=document.createElement("input");
   input.name=key;input.value=user[key]??"";input.dataset.serverValue=input.value;input.readOnly=key==="display_name";input.setAttribute("aria-label",label);input.setAttribute("form",formId);
   if(key==="wallet"||key==="bank"){input.type="text";input.inputMode="text";input.placeholder="整數、算式或 inf／-inf"}
   else if(key.startsWith("last_")){input.className="time";input.placeholder="留空代表沒有紀錄"}
   td.appendChild(input);tr.appendChild(td);
  }
  const action=document.createElement("td");action.dataset.label="操作";const button=document.createElement("button");
  button.className="save";button.textContent="儲存";button.type="submit";form.appendChild(button);action.appendChild(form);tr.appendChild(action);
  form.addEventListener("submit",async e=>{
   e.preventDefault();button.disabled=true;button.textContent="儲存中…";const payload={guild_id:user.guild_id,user_id:user.user_id};
   for(const [key] of fields)if(key!=="display_name")payload[key]=form.elements[key].value;
   try{
    const response=await fetch("/api/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    const result=await response.json(),status=document.getElementById("status");
    status.textContent=response.ok?'已儲存「'+(user.display_name||"玩家")+'」的資料。':(result.error||"儲存失敗");
    status.style.color=response.ok?"#8fe0ae":"#ff9b9b";
    if(response.ok)await load();
   }catch(err){document.getElementById("status").textContent="無法儲存："+err}
   finally{button.disabled=false;button.textContent="儲存"}
  });
  tr.appendChild(form);tbody.appendChild(tr);
 }
 table.appendChild(tbody);const wrap=document.createElement("div");wrap.className="table-wrap";wrap.appendChild(table);root.replaceChildren(wrap);
}
load().catch(err=>document.getElementById("content").textContent="讀取失敗："+err);
// Keep only the newest Economy Editor tab, using the same handoff pattern as 一條魚 Control.
const EDITOR_TAB_KEY="yitiaoyu-economy-editor-active-tab";
const EDITOR_TAB_ID=(()=>{try{return String(Date.now())+"-"+Array.from(crypto.getRandomValues(new Uint32Array(4))).join("-")}catch{return String(Date.now())+"-"+String(Math.random())}})();
window.addEventListener("storage",event=>{
 if(event.key!==EDITOR_TAB_KEY||!event.newValue||event.newValue===EDITOR_TAB_ID)return;
 try{window.close()}catch{}
 setTimeout(()=>{try{if(!window.closed)window.location.replace("/__economy_editor_replaced")}catch{}},0);
});
function releaseEditorTab(){try{if(localStorage.getItem(EDITOR_TAB_KEY)===EDITOR_TAB_ID)localStorage.removeItem(EDITOR_TAB_KEY)}catch{}}
try{localStorage.setItem(EDITOR_TAB_KEY,EDITOR_TAB_ID)}catch{}
window.addEventListener("pagehide",releaseEditorTab,{once:true});
async function takeOverOlderEditorTabs(){
 if(!("serviceWorker" in navigator)||location.protocol==="file:")return;
 try{
  const registration=await navigator.serviceWorker.register("/editor-tab-manager.js",{scope:"/"});
  await navigator.serviceWorker.ready;
  const worker=registration.active||navigator.serviceWorker.controller;
  if(worker)worker.postMessage({type:"takeover-economy-editor-tabs"});
 }catch(e){}
}
void takeOverOlderEditorTabs();

// Live updates: refresh every 2.5 seconds, but never replace the table while
// the user is typing or has unsaved edits in any field.
let liveRefreshRunning=false;
setInterval(async()=>{
 if(liveRefreshRunning)return;
 const inputs=[...document.querySelectorAll("#content input")];
 if(inputs.some(input=>document.activeElement===input||input.value!==input.dataset.serverValue))return;
 liveRefreshRunning=true;
 try{await load()}catch(err){document.getElementById("status").textContent="即時更新暫時失敗："+err}
 finally{liveRefreshRunning=false}
},2500);
let serverWasOffline=false,healthCheckRunning=false;
setInterval(async()=>{
 if(healthCheckRunning)return;
 healthCheckRunning=true;
 try{
  const response=await fetch("/api/health",{cache:"no-store"});
  if(!response.ok)throw new Error("服務尚未就緒");
  if(serverWasOffline)location.reload();
 }catch{serverWasOffline=true}
 finally{healthCheckRunning=false}
},1500);
</script></body></html>"""


MAX_BALANCE_DIGITS = 2000
_BINARY_OPERATORS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
}
_UNARY_OPERATORS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _evaluate_balance_expression(source):
    """Evaluate a bounded integer-only arithmetic expression without eval()."""
    if len(source) > 2000:
        raise ValueError("算式太長。")
    try:
        tree = ast.parse(source, mode="eval")
    except (SyntaxError, ValueError):
        raise ValueError("算式格式不正確。") from None
    if sum(1 for _ in ast.walk(tree)) > 100:
        raise ValueError("算式太複雜。")

    def evaluate(node):
        if isinstance(node, ast.Expression):
            result = evaluate(node.body)
        elif isinstance(node, ast.Constant) and type(node.value) is int:
            result = node.value
        elif isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
            result = _UNARY_OPERATORS[type(node.op)](evaluate(node.operand))
        elif isinstance(node, ast.BinOp):
            left, right = evaluate(node.left), evaluate(node.right)
            if isinstance(node.op, ast.Pow):
                if right < 0 or right > MAX_BALANCE_DIGITS:
                    raise ValueError("次方範圍不合法。")
                if left not in (-1, 0, 1) and len(str(abs(left))) * right > MAX_BALANCE_DIGITS:
                    raise ValueError("計算結果超過 2000 位數。")
                result = pow(left, right)
            elif isinstance(node.op, ast.Div):
                if right == 0 or left % right:
                    raise ValueError("金額必須是整數，除法結果不能有小數。")
                result = left // right
            elif type(node.op) in _BINARY_OPERATORS:
                if isinstance(node.op, (ast.FloorDiv, ast.Mod)) and right == 0:
                    raise ValueError("不能除以 0。")
                result = _BINARY_OPERATORS[type(node.op)](left, right)
            else:
                raise ValueError("只支援 +、-、*、/、//、% 和 **。")
        else:
            raise ValueError("只支援整數與基本運算。")
        if len(str(abs(result))) > MAX_BALANCE_DIGITS:
            raise ValueError("計算結果超過 2000 位數。")
        return result

    return evaluate(tree)


def _parse_balance(value):
    raw = str(value).strip().lower()
    if raw in {"inf", "+inf"}:
        return "inf"
    if raw == "-inf":
        return "-inf"
    if not raw or not raw.isascii():
        return None
    try:
        digits = raw.lstrip("+-")
        if digits.isdecimal():
            if len(digits) > MAX_BALANCE_DIGITS:
                return None
            result = int(raw)
        else:
            result = _evaluate_balance_expression(raw)
        return str(result)
    except (ValueError, ZeroDivisionError, OverflowError):
        return None


def connect():
    if not DB_PATH.is_file():
        raise FileNotFoundError(f"找不到資料庫：{DB_PATH}")
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=10000")
    return con



def _save_user_balances(user_id: int, wallet: str, bank: str, times: dict[str, str | None], retries: int = 3) -> bool:
    """Save one global economy account, retrying transient SQLite writer locks."""
    for attempt in range(retries + 1):
        try:
            # BEGIN IMMEDIATE obtains the writer reservation before checking/updating
            # the row, preventing a read-then-write race with the running Discord bot.
            with closing(connect()) as con:
                con.execute("BEGIN IMMEDIATE")
                exists = con.execute(
                    "SELECT 1 FROM users WHERE guild_id=0 AND user_id=?", (user_id,)
                ).fetchone()
                if not exists:
                    con.rollback()
                    return False
                con.execute(
                    "UPDATE users SET wallet=?,bank=?,last_daily=?,last_work=?,last_fish=?,last_rob=? "
                    "WHERE guild_id=0 AND user_id=?",
                    (wallet, bank, times["last_daily"], times["last_work"],
                     times["last_fish"], times["last_rob"], user_id),
                )
                con.commit()
                return True
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if not any(marker in message for marker in ("database is locked", "database is busy", "locked")) or attempt >= retries:
                raise
            time.sleep(0.2 * (2 ** attempt))
    return False

def _is_same_origin_post(origin: str, host: str) -> bool:
    """Only allow browser writes initiated by this exact loopback editor origin."""
    expected_host = f"{HOST}:{PORT}"
    if not isinstance(origin, str) or not isinstance(host, str):
        return False
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    return bool(
        parsed.scheme.lower() == "http"
        and parsed.netloc.lower() == expected_host
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
        and host.strip().lower() == expected_host
    )


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, value):
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/":
            html = PAGE.replace("__DB_PATH__", str(DB_PATH)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(html)
            return
        if path == "/editor-tab-manager.js":
            data = EDITOR_TAB_MANAGER_SCRIPT.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/__economy_editor_replaced":
            data = EDITOR_RETIRED_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/health":
            self.send_json(200, {"ok": True})
            return
        if path == "/api/users":
            try:
                with connect() as con:
                    rows = con.execute(
                        "SELECT user_id,0 AS guild_id,wallet,bank,last_daily,last_work,last_fish,last_rob FROM users WHERE guild_id=0 ORDER BY user_id"
                    ).fetchall()
                self.send_json(200, {"users": _add_display_names([dict(row) for row in rows])})
            except Exception as exc:
                self.send_json(500, {"error": str(exc)})
            return
        self.send_error(404)

    def do_POST(self):
        path = urlsplit(self.path).path
        if path != "/api/save":
            self.send_error(404)
            return
        if not _is_same_origin_post(self.headers.get("Origin", ""), self.headers.get("Host", "")):
            self.send_json(403, {"error": "已拒絕非本機控制台來源的修改請求。"})
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self.send_json(415, {"error": "修改請求必須使用 application/json。"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 20000:
                self.send_json(400, {"error": "請求資料大小不正確。"})
                return
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            # All servers edit the same global account; never trust a client-supplied guild scope.
            guild_id = 0
            user_id = int(data["user_id"])
            wallet, bank = _parse_balance(data["wallet"]), _parse_balance(data["bank"])
            if guild_id < 0 or user_id <= 0 or wallet is None or bank is None:
                self.send_json(400, {"error": "ID 或金額不合法；可輸入正負整數、整數算式、inf 或 -inf，最多 2000 位數。"})
                return
            times = {}
            for key in ("last_daily", "last_work", "last_fish", "last_rob"):
                value = str(data.get(key, "")).strip()
                if value:
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
                    if len(value) > 80:
                        raise ValueError("時間欄位太長。")
                times[key] = value or None
            if not _save_user_balances(user_id, wallet, bank, times):
                self.send_json(404, {"error": "找不到這位玩家，請重新整理頁面。"})
                return
            self.send_json(200, {"ok": True})
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": f"資料格式不正確：{exc}"})
        except Exception as exc:
            print(f"[economy-editor] /api/save 儲存失敗：{type(exc).__name__}: {exc}")
            traceback.print_exc()
            self.send_json(500, {"error": "資料庫儲存失敗，詳細原因已記錄在編輯器終端機。"})

    def log_message(self, fmt, *args):
        print("[economy-editor]", fmt % args)


def _close_previous_editor_window() -> None:
    """Close the previous editor tab without touching unrelated browser tabs."""
    if os.name != "nt":
        return
    profile = str((ROOT / "data" / "economy_editor_browser").resolve()).replace("'", "''")
    script = "\n".join([
        "Add-Type -AssemblyName UIAutomationClient",
        "Add-Type -AssemblyName UIAutomationTypes",
        "$profile = '" + profile + "'",
        "$closed = $false",
        "$targets = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -and $_.CommandLine.IndexOf($profile, [StringComparison]::OrdinalIgnoreCase) -ge 0 })",
        "foreach ($target in $targets) { Stop-Process -Id $target.ProcessId -Force -ErrorAction SilentlyContinue }",
        "$root = [System.Windows.Automation.AutomationElement]::RootElement",
        "$windows = $root.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)",
        "foreach ($window in $windows) {",
        "  try {",
        "    $condition = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::TabItem)",
        "    $tabs = $window.FindAll([System.Windows.Automation.TreeScope]::Descendants, $condition)",
        "    foreach ($tab in $tabs) {",
        "      if ($tab.Current.Name -like '*經濟資料庫編輯器*') {",
        "        $closeCondition = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Button)",
        "        $buttons = $tab.FindAll([System.Windows.Automation.TreeScope]::Descendants, $closeCondition)",
        "        foreach ($button in $buttons) {",
        "          if ($button.Current.Name -match '^(Close tab|關閉分頁|關閉索引標籤)$') {",
        "            $button.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()",
        "            $closed = $true; break",
        "          }",
        "        }",
        "        if ($closed) { break }",
        "      }",
        "    }",
        "  } catch {}",
        "  if ($closed) { break }",
        "}",
        "if ($closed) { Write-Output 'EDITOR_TAB_CLOSED' } else { Write-Output 'EDITOR_TAB_NOT_FOUND' }"
    ])
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-STA", "-Command", script], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12, check=False)
        if result.returncode != 0 and result.stderr:
            print(f"[economy-editor] 舊分頁清理失敗：{result.stderr.strip()[:500]}")
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[economy-editor] 無法執行舊分頁清理：{type(exc).__name__}")

def main():
    try:
        with connect() as con:
            con.execute("SELECT user_id FROM users LIMIT 1").fetchone()
    except Exception as exc:
        print(exc)
        input("按 Enter 結束…")
        return
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}/"
    print(f"經濟資料庫編輯器已啟動：{url}")
    _close_previous_editor_window()
    print("編輯器會在預設瀏覽器開啟一般分頁；重啟時先關閉標題符合的舊編輯器分頁。")
    try:
        if not webbrowser.open(url, new=2):
            print(f"無法自動開啟瀏覽器，請手動開啟：{url}")
    except Exception as exc:
        print(f"無法自動開啟瀏覽器：{type(exc).__name__}；請手動開啟 {url}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("編輯器已停止。")


if __name__ == "__main__":
    main()
