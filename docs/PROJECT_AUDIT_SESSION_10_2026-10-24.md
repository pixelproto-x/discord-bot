# Project Audit — Session 10 (2026-10-24)

## Goal
Make dashboard API request parsing fail safely when clients send malformed JSON, an unsupported body content type, or valid JSON with the wrong top-level shape.

## Research
- OWASP REST Security Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html
- OWASP Input Validation Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html
- aiohttp web request reference: https://docs.aiohttp.org/en/stable/web_reference.html

## Changes
- Added a shared JSON-object parser for dashboard API request bodies.
- Converts JSON parsing/content-type failures to a consistent validation error handled by the endpoints, instead of allowing malformed requests to escape as unhandled server errors.
- Rejects valid JSON arrays, strings, numbers, booleans, and null; API handlers now receive only JSON objects.
- Updated all eight dashboard JSON body handlers to use the shared parser.
- Added regression tests for a valid object, parser/content-type failure, and non-object JSON values.

## Scope and limitations
The dashboard retains its existing 32 KiB request-body limit. This change standardizes parsing and top-level shape validation; it does not replace endpoint-specific field validation, authorization checks, or rate limiting. Request-size errors continue to be handled by aiohttp's request-size enforcement.

## References
- https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html
- https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html
- https://docs.aiohttp.org/en/stable/web_reference.html
