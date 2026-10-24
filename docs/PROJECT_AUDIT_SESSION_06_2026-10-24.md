# 專案審查紀錄 — 第 6／10 次（2026-10-24）

Repository: `pixelproto-x/discord-bot`
Branch: `main`

## 本次目標

參考 OWASP Logging Cheat Sheet 與 Python logging 官方文件，降低 Bot 發生例外或印出診斷資訊時，將常見 API 金鑰、存取憑證或 Discord Webhook URL 寫入本機日誌的風險。

## 研究參考

- OWASP Logging Cheat Sheet：日誌不應直接保存存取權杖、密碼、加密金鑰等機密；也建議集中處理日誌資料並防範日誌注入。
  - https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html
- Python Logging HOWTO：Python logging 提供多種 handler；RotatingFileHandler 可限制日誌檔案成長並保留有限份輪替檔。
  - https://docs.python.org/3/howto/logging.html
- Python Logging Cookbook：說明多個 handler 與日誌輪替的常見做法。
  - https://docs.python.org/3/howto/logging-cookbook.html

## 本次實際修改

### 1. 新增集中式日誌機密遮罩

檔案：`safe_logging.py`

- 新增 `redact_sensitive_text`，遮罩常見的環境變數式憑證、Authorization/Bearer 值、Discord Webhook URL，以及常見供應商金鑰格式。
- 保留一般診斷訊息，方便查看錯誤原因。
- 這是依常見格式進行的風險降低措施，不能保證識別所有自訂或未知格式的秘密。

### 2. 接入 Bot 的檔案日誌

檔案：`bot.py`

- 在寫入 `logs/bot.log` 前，先對每一筆完整日誌內容執行遮罩。
- 保留既有檔案輪替設定與終端輸出行為，不改動 Discord 指令、資料庫或控制台功能。

### 3. 新增測試

檔案：`tests/test_safe_logging.py`

- 測試環境變數憑證、Authorization/Bearer、Discord Webhook、常見 API 金鑰格式，以及一般診斷訊息不應被誤遮罩。

## 驗證界線

自動化測試能驗證已列出的憑證格式；它不等於所有第三方錯誤訊息都不可能含有機密。仍應避免把金鑰貼入公開頻道、Issue 或日誌，若曾曝光則應到相應服務撤銷並重新產生。
