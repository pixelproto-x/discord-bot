# 專案全面盤點紀錄 — 2026-10-24

Repository: `pixelproto-x/discord-bot`
Branch: `main`
Audit baseline: `e96488489171da84f18af850528295ab0d3b324c`
Post-fix validation commit: `2e3f4ab062a3d5ae6fce394896d9ca08c7cdc057`

## 1. 專案範圍

本次已從 GitHub 取得完整遞迴檔案樹（API 回報 `truncated=false`），並檢查主要啟動流程、19 個 Cog 的載入清單、SQLite 連線管理、AI 人格與回覆正規化、控制台認證與主要 API、訊息歷史及備份流程、測試檔和 GitHub Actions。

### 主要元件

- Bot 啟動與指令同步：`bot.py`、`localization.py`
- 資料層：`storage.py`、SQLite WAL、提醒、設定、使用者資料與其他持久化表
- AI：`cogs/media.py`、`cogs/ai_persona.py`、`chat_context.py`
- Discord 功能模組：`cogs/utility.py`、`social.py`、`games.py`、`music.py`、`moderation.py`、`profile.py`、`voice.py`、`giveaway.py`、`settings.py`、`banter.py`、`speech_control.py`、`help.py`、`web_tools.py`、`hacks.py`、`history.py`、`browser_mic.py`、`marriage.py`、`economy.py`
- 控制台與網站：`web_dashboard.py`、`web/index.html`、`index.html`、`app.js`、`commands.js`、CSS、隱私權與服務條款頁面
- 測試：`tests/` 中的 AI、指令註冊、瀏覽器麥克風、遊戲、抽獎、訊息歷史、音樂、提醒、語音及安全性測試
- CI／安全／依賴更新：`.github/workflows/`、`.github/dependabot.yml`、`requirements.txt`

## 2. 本次已修復

### 控制台刪除訊息查找錯誤

原本 `web_dashboard.py` 的刪除 API 將已選頻道 ID 預先放進 `seen` 集合，接著迴圈遇到該頻道就直接略過。因此，當目標訊息恰好位於已選頻道時，程式可能不會查詢該頻道。

修正方式：
- 抽出 `_find_message_in_readable_channels`，先查已選頻道，再查其他可讀取訊息歷史的頻道。
- 新增 `tests/test_dashboard_delete.py`，驗證已選頻道確實會被查詢。
- 保留原本的頻道權限檢查與跨頻道搜尋行為。

相關提交：
- `988a925defa38cb921b207d865f2b727cc8e032c`
- `2e3f4ab062a3d5ae6fce394896d9ca08c7cdc057`

### 指令註冊回歸測試

- 擴大 `.github/workflows/quality.yml` 的 Cog 註冊 smoke test，涵蓋 `bot.py` 實際載入的 19 個模組。
- 擴大 `tests/test_cog_registration.py`，避免只測試部分 Cog。
- 提交：`c6c06c7047d5a21af32dd6386dae9073e6d5ec3b`、`e96488489171da84f18af850528295ab0d3b324c`

## 3. 驗證結果

在提交 `2e3f4ab062a3d5ae6fce394896d9ca08c7cdc057` 上，GitHub Actions 已回報下列檢查成功：

- Python bot checks（語法、靜態檢查與額外 smoke tests）
- Python Bot Checks（回歸測試與依賴相容性）
- AI Persona Tests
- Secret scan
- GitHub Pages 部署

這些結果證明該提交通過了目前 CI 設定中的檢查；不代表已完成真實 Discord 伺服器互動、所有外部 API 額度／金鑰情境、Windows 本機環境或每個功能的人工端到端驗收。

## 4. 已研究的實作參考

- discord.py 官方 Extensions 文件：以 async `setup` 載入模組，並使用可測試的 Cog／extension 結構。
- discord.py 官方 Application Commands 文件：指令需符合 Discord 命名規則，並在註冊後正確同步。
- 社群 discord.py starter template：使用 Cog 模組化、獨立測試與 GitHub Actions CI。

參考：
- https://discordpy.readthedocs.io/en/stable/ext/commands/extensions.html
- https://discordpy.readthedocs.io/en/stable/interactions/api.html
- https://github.com/Metolix/discord_py_starter

## 5. 尚待後續階段處理

- 開放中的功能 PR 仍需逐一比較最新 main、檢查差異與測試，不可直接假設可安全合併：
  - #15 完整頻道訊息備份匯出
  - #18 刪除訊息紀錄顯示頻道
  - #22 高科技控制台介面
- 目前有兩個 Python CI workflow（`python-check.yml` 與 `quality.yml`）執行部分重複檢查；應評估合併以減少重複安裝與 CI 時間，同時保留各自獨有的檢查。
- 逐一完成 19 個 Cog 的權限、輸入驗證、錯誤處理與資源清理審查。
- 進一步測試控制台 OAuth／控制 Key、WebSocket 麥克風票證、跨頻道操作及手機端行為。
- 檢查資料庫各功能的資料保留、備份還原、併發與錯誤復原。
- 完成真實 Discord 環境中的私密回覆公開按鈕、訊息紀錄、備份還原、AI 備援與語音功能驗收。

## 6. 安全界線與狀態

- 本次沒有修改或輸出任何真實 API 金鑰、Discord Bot Token 或其他秘密。
- Secret scan 通過只表示該次掃描未回報失敗，不等於能保證歷史提交或所有外部設定完全沒有秘密。
- 不會在沒有實際測試證據時宣稱所有功能已經正常。
