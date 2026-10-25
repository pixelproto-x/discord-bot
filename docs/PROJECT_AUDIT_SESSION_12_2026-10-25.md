# Project Audit — Session 12 (2026-10-25)

## Scope
Continued the repository-wide review on `main`, focusing on concurrency in backup persistence, destructive restore preflight validation, guild locale restoration, and the reported Lavalink search timeout behavior.

## Changes made
- Serialized backup deletion through the same process-local lock as backup creation. Previously, `delete-backup-key` performed an unlocked read-modify-write, so a concurrent save could be silently lost even though the file replacement itself was atomic.
- Added a concurrent store/delete regression test that verifies unrelated backups survive simultaneous operations and that the last-backup key remains valid.
- Bounded SoundCloud recovery searches using the same per-node timeout and connected-node failover used by ordinary music search. Previously, track-failure recovery called `Playable.search` without a timeout and could hold the guild's music lock while a node stalled.
- Added a timeout regression test that simulates a Lavalink search hanging and verifies recovery returns within the configured deadline.
- Added backup restore preflight validation for role/channel entries, numeric fields, overwrites, forum tags, emoji/sticker collections, and guild settings before any destructive server mutation starts.
- Fixed `preferred_locale` restoration. The backup stores locale codes such as `zh-TW`, but the old restore path attempted `int("zh-TW")`, silently skipping this setting. It now converts the stored locale code to `discord.Locale`.
- Added regression tests for malformed restore payloads and locale conversion.

## Verification
- [Python bot checks](https://github.com/pixelproto-x/discord-bot/actions/runs/40186599262): success. Full regression suite passed (116 tests); dashboard security tests, safe logging tests, static undefined-name check, module import, Cog command registration, storage smoke test, feature constructors, help catalog, and calculator/role security checks also passed.
- [Python Bot Checks](https://github.com/pixelproto-x/discord-bot/actions/runs/40186599279): success, including compileall, complete unittest discovery, and dependency compatibility.
- [AI Persona Tests](https://github.com/pixelproto-x/discord-bot/actions/runs/40186599278): success.
- [Secret scan](https://github.com/pixelproto-x/discord-bot/actions/runs/40186599269): success.
- [GitHub Pages deployment](https://github.com/pixelproto-x/discord-bot/actions/runs/40186599270): success.

## Research references
- [discord.py API reference](https://discordpy.readthedocs.io/en/latest/api.html): `Guild.preferred_locale` is a `Locale` enum.
- [Python threading locks](https://docs.python.org/3/library/threading.html): serialize in-process read-modify-write operations.
- [Python temporary files](https://docs.python.org/3/library/tempfile.html): unique temporary files for atomic replacement workflows.

## Limits still in force
- The backup lock protects operations inside one Python process, not multiple independent bot processes sharing one JSON file.
- The GitHub CI tests use mocks for Discord/Lavalink interactions. They verify timeout and failure behavior deterministically, but do not prove public Lavalink endpoints are currently healthy or that live Discord API operations succeed.
- The broader audit is still in progress; do not interpret this session's passing tests as proof every command and external integration has been end-to-end tested.
- No Render service was created or used.
