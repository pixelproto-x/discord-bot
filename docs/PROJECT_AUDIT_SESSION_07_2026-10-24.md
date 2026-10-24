# Project Audit — Session 7 (2026-10-24)

## Goal
Reduce repeated guessing of the dashboard control key without changing the existing control-key or Discord OAuth login flows.

## Research
- OWASP Authentication Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html
- OWASP Bot Management and Anti-Automation Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Bot_Management_and_Anti-Automation_Cheat_Sheet.html

## Changes
- Added an in-memory sliding-window limiter for failed non-empty dashboard control-key attempts: 10 attempts per client address in 60 seconds.
- Returns HTTP 429 after the limit is reached; successful control-key authentication clears that client's failure history.
- Keeps the limiter's key count bounded to avoid unbounded in-memory growth.
- Uses the direct peer address by default. If and only if the operator has a trusted reverse proxy that overwrites `X-Forwarded-For`, `DASHBOARD_TRUST_PROXY=1` may be set to use the first forwarded address. Do not enable this setting unless the proxy is trusted.
- Added unit tests for sliding-window expiry, reset behavior, independent clients, and bounded key storage.
- Added the dashboard security regression tests to the Python quality workflow.

## Scope and limitations
This is process-local throttling, so counters reset when the Bot restarts and are not shared across multiple workers. It is a defensive layer, not a replacement for a strong control key, HTTPS, OAuth allowlisting, or trusted proxy configuration. CI results are tracked by the GitHub Actions runs for the final commit.

## References
- https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html
- https://cheatsheetseries.owasp.org/cheatsheets/Bot_Management_and_Anti-Automation_Cheat_Sheet.html
