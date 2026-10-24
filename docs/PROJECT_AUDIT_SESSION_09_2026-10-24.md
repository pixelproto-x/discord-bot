# Project Audit — Session 9 (2026-10-24)

## Goal
Add a Content Security Policy (CSP) to the web dashboard that restricts JavaScript execution without breaking the existing single-page dashboard.

## Research
- OWASP Content Security Policy Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Content_Security_Policy_Cheat_Sheet.html
- OWASP Session Management Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html

## Changes
- Added a CSP to dashboard responses through the existing security-header hook.
- Computes a SHA-256 source for each inline script in the fixed dashboard page, allowing the exact script content rather than enabling `script-src 'unsafe-inline'`.
- Denies plugin/object content and base-URL injection, blocks framing through CSP, restricts form submissions to the dashboard and Discord OAuth, and declares the resource/connect sources needed by the current page.
- Added regression tests verifying the CSP header, critical directives, exact inline-script hash, and that the script policy does not allow `unsafe-inline`.
- Reused the existing dashboard security test step in `.github/workflows/quality.yml`.

## Scope and limitations
This is defense in depth against some script-injection and content-loading paths; it does not replace safe output handling, authorization checks, dependency maintenance, or HTTPS. The current dashboard uses inline style attributes, so `style-src 'unsafe-inline'` remains enabled for compatibility. The WebSocket source list allows `ws:` and `wss:` because the dashboard streams microphone audio over WebSocket; deploy behind HTTPS for public use.

## References
- https://cheatsheetseries.owasp.org/cheatsheets/Content_Security_Policy_Cheat_Sheet.html
- https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html
