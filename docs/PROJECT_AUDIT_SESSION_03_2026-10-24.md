# 專案審查紀錄 — 第 3／10 次（2026-10-24）

Repository: `pixelproto-x/discord-bot`
Branch: `main`

## 本次目標

依照 OWASP 對 REST API 與 Session 管理的建議，檢查網頁控制台的 HTTP 回應安全設定；採取小幅、可測試的強化，不改動既有 Discord OAuth、控制 Key、訊息、語音朗讀或麥克風串流流程。

## 研究參考

- OWASP REST Security Cheat Sheet：建議限制管理介面暴露、避免快取敏感回應、設定安全回應標頭，並避免向客戶端洩露不必要資訊。
  - https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html
- OWASP Session Management Cheat Sheet：強調瀏覽器 Session 與敏感資料需要適當保護。
  - https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html

## 本次實際修改

### 1. 控制台所有 HTTP 回應新增安全標頭

檔案：`web_dashboard.py`

透過 aiohttp 的 `on_response_prepare` 統一套用標頭，讓一般頁面、API 回應及重新導向都能套用，不需要逐個路由重複設定：

- `X-Content-Type-Options: nosniff`：避免瀏覽器自行猜測回應內容類型。
- `X-Frame-Options: DENY`：禁止其他頁面以 iframe 嵌入控制台，降低點擊劫持風險。
- `Referrer-Policy: no-referrer`：不把來源網址轉送到其他網站。
- `Permissions-Policy`：限制相機與定位功能；保留控制台既有麥克風需求。
- `Cache-Control: no-store`：避免瀏覽器或中介快取控制台/API 回應。
- 僅當設定的公開網址使用 HTTPS 時，才新增 `Strict-Transport-Security: max-age=31536000`；本機 HTTP 開發不會被強制升級。

此修改不會自行替服務啟用 HTTPS；公開控制台仍需正確設定 HTTPS 網址及反向代理。

### 2. 新增自動化測試

檔案：`tests/test_dashboard_security.py`

驗證一般回應的安全標頭、麥克風權限政策、禁止快取，以及 HSTS 僅在公開 HTTPS 模式啟用。

## 驗證界線

GitHub Actions 結果以本報告提交後的最新 `main` 提交為準。這些單元測試驗證標頭設定，不代表已完成公開部署環境的滲透測試或真實瀏覽器端到端驗收。
