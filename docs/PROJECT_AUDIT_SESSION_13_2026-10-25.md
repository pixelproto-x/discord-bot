# Project Audit — Session 13 (2026-10-25)

## Scope
Continue the main-branch audit with emphasis on responsive message-history handling and avoiding collateral process termination during local startup.

## Changes made
- Message-history snapshots are still persisted before network I/O, but attachment archival now runs as a tracked background task. Deletion events no longer wait for a potentially slow GoFile download/upload when the original snapshot was missed; the stored attachment metadata is updated after archival finishes.
- Added a finite 120-second total timeout (15-second connect, 20-second socket-read) for each attachment download/upload session instead of allowing the operation to have no total deadline.
- Added regression coverage for attachment archival completing after deletion, cached/raw delete deduplication, and the no-snapshot fallback.
- Hardened local process cleanup. Dashboard startup now checks the process command line for the exact project `bot.py` path before terminating a process that owns the configured port. Economy-editor cleanup now targets only a process command line containing this project's exact `economy_editor.py` path; it no longer kills any arbitrary process merely because it listens on port 8766.
- Added unit tests for exact script-path matching and rejection of basename-only, different-project, and path-prefix matches.

## Combined fixes from this audit pass
- Serialized backup-store and backup-delete read/modify/write transactions with the same in-process lock; atomic file replacement uses unique temporary files.
- Added concurrent backup store/delete regression tests.
- Bounded SoundCloud fallback searches across connected Lavalink nodes using the same timeout/failover logic as normal music searches.
- Added a timeout test for a stalled SoundCloud search.
- Added preflight validation for nested backup restore data before destructive server edits/deletions begin.
- Fixed restoring `preferred_locale` values such as `zh-TW` as a `discord.Locale` instead of incorrectly trying to parse them as integers.

## Verification on commit `840a2f825648ded14fa822dac2013cdba06ec365`
- [Python bot checks](https://github.com/pixelproto-x/discord-bot/actions/runs/40186817087): success; complete unittest discovery passed with 118 tests, plus compileall and dependency compatibility.
- [Python Bot Checks](https://github.com/pixelproto-x/discord-bot/actions/runs/40186817025): success; dashboard security, safe logging, complete regression suite, undefined-name scan, bot import, Cog command registration, storage smoke test, Discord feature constructors, help catalog, and calculator/role-security checks passed.
- [AI Persona Tests](https://github.com/pixelproto-x/discord-bot/actions/runs/40186817052): success.
- [Secret scan](https://github.com/pixelproto-x/discord-bot/actions/runs/40186817020): success.
- [GitHub Pages deployment](https://github.com/pixelproto-x/discord-bot/actions/runs/40186817021): success.

## Research references
- [discord.py API reference](https://discordpy.readthedocs.io/en/latest/api.html): `Guild.preferred_locale` is a `Locale` enum.
- [TriniumHost live Lavalink status](https://lavalink-status.triniumhost.com/): current public listing shows the V4 and V4 SSL endpoints online; a public status page does not guarantee a specific song search will succeed.
- [Python threading locks](https://docs.python.org/3/library/threading.html).
- [Python temporary files](https://docs.python.org/3/library/tempfile.html).

## Remaining limits
- Tests use mocks for Discord/Lavalink calls. They verify failure, timeout, persistence, registration, and security behavior, but cannot prove a real Discord server interaction or actual public-node song playback without running the user's bot with their credentials.
- The JSON backup lock is process-local, not a cross-process file lock.
- This is a substantial audit pass, not a guarantee that every external service or every command has been exercised end-to-end.
- No Render service was created or used.
