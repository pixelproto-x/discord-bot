from __future__ import annotations

import json
import os
import sqlite3
import threading
import webbrowser
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
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


PAGE = r"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>經濟資料庫編輯器</title>
<style>
:root{color-scheme:dark;font-family:system-ui,"Microsoft JhengHei",sans-serif}
body{margin:0;background:#10131a;color:#e8edf5}main{max-width:1200px;margin:28px auto;padding:0 16px}
h1{margin-bottom:6px}.muted{color:#aab5c5;overflow-wrap:anywhere}
.notice{padding:12px 14px;background:#252d3a;border:1px solid #39465a;border-radius:10px;margin:16px 0}
table{width:100%;border-collapse:collapse;background:#171c25;border-radius:12px;overflow:hidden}
th,td{padding:10px;border-bottom:1px solid #303847;text-align:left;vertical-align:top}
th{background:#202735}input{box-sizing:border-box;width:100%;min-width:110px;padding:9px;border-radius:7px;border:1px solid #48556a;background:#0f141c;color:#fff}
input.time{min-width:220px}button{padding:9px 13px;border:0;border-radius:7px;background:#78a9ff;color:#101827;font-weight:700;cursor:pointer}
button:hover{filter:brightness(1.08)}.save{white-space:nowrap}.empty{padding:20px;background:#171c25;border-radius:10px}
@media(max-width:750px){table,tbody,tr,td{display:block}thead{display:none}tr{border-bottom:2px solid #394253;padding:8px}td{border:0;padding:6px}td:before{content:attr(data-label);display:block;color:#aab5c5;font-size:12px;margin-bottom:4px}}
</style></head><body><main>
<h1>經濟資料庫編輯器</h1><div class="muted">資料庫：<code>__DB_PATH__</code></div>
<div class="notice">只在這台電腦的 127.0.0.1 開放。玩家名稱會從 Discord 讀取，優先使用伺服器暱稱；Discord 暫時無法連線時會顯示「無法取得名稱」。建議修改資料前先關閉 Discord Bot。時間欄位可留空，格式為 UTC ISO。</div>
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
 ["伺服器 ID",...fields.map(f=>f[1]),"操作"].forEach(label=>{const th=document.createElement("th");th.textContent=label;headrow.appendChild(th)});
 thead.appendChild(headrow);table.appendChild(thead);const tbody=document.createElement("tbody");
 for(const user of data.users){
  const tr=document.createElement("tr"),form=document.createElement("form");
  const formId="economy-save-"+String(user.guild_id)+"-"+String(user.user_id);form.id=formId;
  const guildCell=document.createElement("td");guildCell.dataset.label="伺服器 ID";guildCell.textContent=String(user.guild_id??"0");tr.appendChild(guildCell);
  for(const [key,label] of fields){
   const td=document.createElement("td");td.dataset.label=label;const input=document.createElement("input");
   input.name=key;input.value=user[key]??"";input.readOnly=key==="display_name";input.setAttribute("aria-label",label);input.setAttribute("form",formId);
   if(key==="wallet"||key==="bank"){input.type="text";input.inputMode="text";input.placeholder="整數、負數或 inf（無限）"}
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
   }catch(err){document.getElementById("status").textContent="無法儲存："+err}
   finally{button.disabled=false;button.textContent="儲存"}
  });
  tr.appendChild(form);tbody.appendChild(tr);
 }
 table.appendChild(tbody);root.replaceChildren(table);
}
load().catch(err=>document.getElementById("content").textContent="讀取失敗："+err);
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


def _parse_balance(value):
    raw = str(value).strip().lower()
    if raw == "inf":
        return "inf"
    if not raw or not raw.isascii():
        return None
    digits = raw[1:] if raw.startswith("-") else raw
    if not digits.isdecimal() or not 1 <= len(digits) <= 2000:
        return None
    return str(int(raw))


def connect():
    if not DB_PATH.is_file():
        raise FileNotFoundError(f"找不到資料庫：{DB_PATH}")
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=10000")
    return con


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
        if path == "/api/health":
            self.send_json(200, {"ok": True})
            return
        if path == "/api/users":
            try:
                with connect() as con:
                    rows = con.execute(
                        "SELECT user_id,guild_id,wallet,bank,last_daily,last_work,last_fish,last_rob FROM users ORDER BY guild_id,user_id"
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
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 20000:
                self.send_json(400, {"error": "請求資料大小不正確。"})
                return
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            guild_id = int(data["guild_id"])
            user_id = int(data["user_id"])
            wallet, bank = _parse_balance(data["wallet"]), _parse_balance(data["bank"])
            if guild_id < 0 or user_id <= 0 or wallet is None or bank is None:
                self.send_json(400, {"error": "ID 或金額不合法；金額可為整數、負數或 inf（無限），最多 2000 位數。"})
                return
            times = {}
            for key in ("last_daily", "last_work", "last_fish", "last_rob"):
                value = str(data.get(key, "")).strip()
                if value:
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
                    if len(value) > 80:
                        raise ValueError("時間欄位太長。")
                times[key] = value or None
            with connect() as con:
                if not con.execute("SELECT user_id FROM users WHERE guild_id=? AND user_id=?", (guild_id, user_id)).fetchone():
                    self.send_json(404, {"error": "找不到這位玩家，請重新整理頁面。"})
                    return
                con.execute(
                    "UPDATE users SET wallet=?,bank=?,last_daily=?,last_work=?,last_fish=?,last_rob=? WHERE guild_id=? AND user_id=?",
                    (wallet, bank, times["last_daily"], times["last_work"], times["last_fish"], times["last_rob"], guild_id, user_id),
                )
                con.commit()
            self.send_json(200, {"ok": True})
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": f"資料格式不正確：{exc}"})
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})

    def log_message(self, fmt, *args):
        print("[economy-editor]", fmt % args)


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
    print("關閉此視窗即可停止編輯器。建議修改前先停止 Discord Bot。")
    print("正在開啟經濟編輯器頁面；若瀏覽器已開啟，請使用現有分頁。")
    try:
        webbrowser.open(url, new=0)
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
