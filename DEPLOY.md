# 部署注意事項

## 必填環境變數
- `DISCORD_TOKEN`：Discord Bot Token。
- `OWNER_ID`、`ALLOWED_ROLE_ID`：受限管理功能使用的管理識別。

### 可選：伺服器白名單
設定 `GUILD_IDS` 可限制 Bot 的 Slash commands 只註冊在指定伺服器，並在執行時拒絕其他伺服器的指令。格式為逗號分隔的伺服器 ID，例如 `GUILD_IDS=123456789012345678,234567890123456789`。留空代表維持全域指令、不限制伺服器。

啟用白名單後，啟動時會清除 Bot 已註冊的全域 Slash commands，並將指令同步至白名單中的伺服器。Discord 端的舊指令顯示可能需要一點時間才更新。確認 ID 正確後再部署；若設定了不存在或 Bot 無法存取的伺服器 ID，同步可能失敗。

## Discord Developer Portal 權限
在 Discord Developer Portal 的 Bot 設定中，請依功能啟用需要的 Privileged Gateway Intents：
- **Server Members Intent**：新人自動身分組、歡迎訊息、離開通知。
- **Message Content Intent**：自動回覆、防刷與依訊息內容判斷的功能。
- Bot 還必須在伺服器具有相應權限，例如讀取／傳送訊息、管理身分組、管理訊息；身分組面板與新人身分組要求 Bot 的最高身分組高於目標身分組。

程式碼開啟了相關 intents，但仍必須在 Developer Portal 同時開啟對應項目；否則相關事件或訊息功能可能不會正常運作。
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
