# 部署注意事項

## 必填環境變數
- `DISCORD_TOKEN`：Discord Bot Token。
- `OWNER_ID`、`ALLOWED_ROLE_ID`：受限管理功能使用的管理識別。

目前程式尚未實作 `GUILD_IDS` 伺服器白名單；請勿設定或依賴這個變數來限制 Bot 可加入或運作的伺服器。Slash commands 會依 Bot 的一般全域同步流程註冊。

## 音樂
音樂功能除了 Python 套件外，還需要系統層級的 FFmpeg。
可以直接把 ffmpeg 放進系統 PATH，或設定 `FFMPEG_PATH` 指向 executable。

## 資料庫
Bot 的 SQLite 會固定放在專案目錄的 `data/bot.sqlite3`。
部署平台必須提供持久化磁碟，否則重新部署／重建環境時，XP、提醒、設定、投票、抽獎、身分組面板與筆記都可能遺失。

## AI
AI 指令使用 Google Gemini，需設定 `GEMINI_API_KEY`；模型可透過 `GEMINI_MODEL` 指定，預設值見 `.env.example`。
AI 指令有基本使用冷卻。回答較長時會分段顯示，超長內容也會附上文字檔。

## 網站設定
在 `site-config.js` 設定 Bot 邀請連結、支援連結及狀態 API 才會顯示／更新相應資訊。未設定的邀請與支援按鈕會隱藏；未設定狀態 API 時會明確顯示尚未設定，不會假裝已連線。

## 匿名投票
匿名投票不再直接把 Discord 使用者 ID 存進投票紀錄；會使用 HMAC 產生只供同一場投票辨識用的匿名鍵。
