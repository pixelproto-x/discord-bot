# 專案審查紀錄 — 第 4／10 次（2026-10-24）

Repository: `pixelproto-x/discord-bot`
Branch: `main`

## 本次目標

參考 OWASP CSRF 防護建議與 Discord OAuth 文件，強化網頁控制台使用已登入 Cookie 執行變更操作時的跨來源請求防護，並以單元測試覆蓋來源驗證。

## 研究參考

- OWASP CSRF Prevention Cheat Sheet：對以 Cookie 驗證的狀態變更請求採用來源驗證與縱深防禦。
  - https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html
- OWASP Session Management Cheat Sheet：Cookie 的 SameSite 屬性有幫助，但不應將其視為所有 CSRF 情境的唯一防線。
  - https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html
- Discord OAuth2 文件：OAuth 登入流程必須驗證 state，以降低登入 CSRF 風險。
  - https://discord.com/developers/docs/topics/oauth2

## 本次實際修改

### 1. Cookie 登入的變更請求增加 Origin 驗證

檔案：`web_dashboard.py`

- 新增 `_is_same_origin`，要求 Origin 的 scheme 與 host/port 都必須符合控制台來源。
- 已登入 Cookie 的非安全 HTTP 方法（例如 POST）在執行路由前，必須通過來源驗證；缺少 Origin、來源不匹配或格式異常都會拒絕。
- 若有設定公開網址，優先使用設定網址的來源；本機模式則依請求來源建立預期值。
- 保留既有控制 Key 驗證流程，沒有改動 Discord OAuth 的 state 檢查或麥克風短效票證流程。

### 2. 新增來源驗證測試

檔案：`tests/test_dashboard_security.py`

涵蓋完全相同的來源、大小寫差異、缺少或格式錯誤的 Origin、跨來源、scheme/port 不匹配，以及含使用者資訊的異常來源。

## 驗證界線

CI 可驗證來源比對邏輯與既有測試是否通過；它不等於公開部署環境的完整滲透測試。若公開網址經反向代理轉送，`DASHBOARD_PUBLIC_URL` 必須設定為使用者實際開啟的正式 HTTPS 網址。
