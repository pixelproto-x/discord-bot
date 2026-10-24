# 專案審查紀錄 — 第 5／10 次（2026-10-24）

Repository: `pixelproto-x/discord-bot`
Branch: `main`

## 本次目標

參考 OWASP WebSocket 安全指引，補上瀏覽器麥克風 WebSocket 握手的來源驗證，避免控制台的即時音訊端點接受任意網站來源。

## 研究參考

- OWASP WebSocket Security Cheat Sheet：建議每次 WebSocket 握手都驗證 Origin，使用明確允許的來源，並避免任意來源連線。
  - https://cheatsheetseries.owasp.org/cheatsheets/WebSocket_Security_Cheat_Sheet.html
- OWASP Web Security Testing Guide：指出伺服器應驗證 WebSocket 初始握手的 Origin，以降低跨站 WebSocket 劫持風險。
  - https://owasp.org/www-project-web-security-testing-guide/

## 本次實際修改

### 1. 麥克風 WebSocket 握手加入來源限制

檔案：`web_dashboard.py`

- 新增 `_is_allowed_websocket_origin`，沿用嚴格的同源驗證規則。
- `/api/mic/stream` 在讀取及消耗短效票證之前，先確認 `Origin` 必須符合已設定的控制台來源；本機模式則使用目前請求來源。
- 缺少 Origin、`null`、格式異常或其他網站來源會收到 HTTP 403，票證不會因此被消耗。
- 原有短效、一次性麥克風票證仍保留；沒有改動音訊資料格式或語音播放流程。

### 2. 新增自動化測試

檔案：`tests/test_dashboard_security.py`

新增測試確認控制台自身來源可通過，缺少來源、`null`、其他網站或協定不一致的來源會被拒絕。

## 驗證界線

GitHub Actions 的測試結果可確認來源驗證單元測試與專案 CI 狀態，但不代表已對公開部署執行完整滲透測試。公開網址仍應使用 HTTPS，並確保 `DASHBOARD_PUBLIC_URL` 設為使用者實際開啟的控制台網址。
