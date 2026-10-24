# 專案審查紀錄 — 第 2／10 次（2026-10-24）

Repository: `pixelproto-x/discord-bot`
Branch: `main`
Validated commit: `ae85ad6f4a80e2f79198f1b0dd04a900b6230273`

## 本次目標

參考成熟的 discord.py 模組化專案與官方應用程式指令規格，優先處理可能造成伺服器資料損失的操作，再擴大回歸測試覆蓋率。採取小幅修改並保留既有功能，不做無關重寫。

## 研究參考

- discord.py 官方 FAQ 與應用程式指令文件：確認指令同步、指令註冊與權限設計應依照官方行為。
  - https://discordpy.readthedocs.io/en/stable/ext/commands/extensions.html
  - https://discordpy.readthedocs.io/en/stable/interactions/api.html
- Metolix 的 discord.py starter：模組化 Cogs、結構化錯誤處理、啟動／關閉流程及 GitHub Actions CI。
  - https://github.com/Metolix/discord_py_starter
- Discord 官方指令權限說明：指令可透過伺服器整合設定限制角色、成員與頻道；程式本身仍需對高風險操作做明確授權。
  - https://support-apps.discord.com/hc/en-us/articles/26501869403159-Command-Permissions

## 本次實際修改

### 1. 對破壞性備份還原加上明確確認

檔案：`cogs/hacks.py`

原本 `/apply` 會先改伺服器設定，再嘗試刪除頻道與可管理身分組，接著重建備份內容。即使此指令已限制為指定 Bot 擁有者使用，誤觸仍可能造成伺服器內容損失。

修改後：
- 新增可測試的 `is_backup_apply_confirmed` 驗證函式。
- 新增可選參數 `confirm_text`；必須明確輸入 `RESTORE` 才會進入任何伺服器修改步驟。
- 未確認時，私密回覆會先顯示備份名稱、現有頻道數、約可管理的身分組數與操作風險，並明確表示未修改伺服器。
- 保留原本備份 Key 的選擇與還原流程。

### 2. 新增確認邏輯測試

檔案：`tests/test_backup_apply.py`

測試錯誤、空白、大小寫不同、額外文字與非字串輸入都會被拒絕；只接受明確的 `RESTORE` 確認文字（可有前後空白）。

### 3. 擴大說明目錄的 CI 驗證範圍

檔案：`.github/workflows/quality.yml`

「Verify help catalog against registered commands」先前只載入部分 Cog，現在擴大至 `bot.py` 實際使用的 19 個 Cog 模組，降低測試只驗證部分指令而漏掉註冊錯誤的機率。

## 最新提交與 CI

最新提交：`ae85ad6f4a80e2f79198f1b0dd04a900b6230273`

- Python bot checks：成功
- Python Bot Checks：成功
- AI Persona Tests：成功
- Secret scan：成功
- GitHub Pages 部署：成功

工作流程連結：
- https://github.com/pixelproto-x/discord-bot/actions/runs/40071192085
- https://github.com/pixelproto-x/discord-bot/actions/runs/40071192078

## 後續仍需處理

- 需要在真實 Discord 測試伺服器中驗證備份還原的完整流程與權限限制；CI 不會實際刪除或重建真實伺服器內容。
- 繼續逐一檢查 OAuth、控制台 API、WebSocket 麥克風票證、訊息刪除／傳送與各 Cog 的授權和輸入驗證。
- 審查開放中的功能 PR，確認它們是否與最新 main 相容；不會只因 PR 存在就直接合併。
- 兩個 Python CI workflow 各自有不同的檢查內容，目前先保留，避免合併時遺失獨有驗證；後續可再評估減少重複安裝成本。

## 驗證界線

本次確認的是 GitHub Actions 在指定提交上的結果，並非所有外部 API、Discord 權限組合、使用者端與 Windows 本機執行環境的完整人工驗收。不得把 CI 通過等同於整個 Bot 已無錯誤。
