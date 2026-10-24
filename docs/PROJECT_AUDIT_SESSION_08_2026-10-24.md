# Project Audit — Session 8 (2026-10-24)

## Goal
Prevent untrusted output from forging extra log entries or consuming excessive space in a single log record.

## Research
- OWASP Logging Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html
- OWASP Input Validation Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html

## Changes
- Added `sanitize_log_text` to escape carriage returns, line feeds, and NUL characters before log output is persisted.
- Bound each sanitized log value to 4,000 characters, with an explicit truncation marker.
- Applied sanitization before the existing credential-redaction patterns in `redact_sensitive_text`.
- Added regression tests using real CR/LF payloads, oversized strings, and secret-containing multi-line text.
- Added the safe-logging regression suite to the Python quality workflow.

## Scope and limitations
This mitigates forged multi-line log entries and oversized individual values. It does not guarantee detection of every possible credential format and is not a substitute for structured logging or log access controls.

## References
- https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html
- https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html
