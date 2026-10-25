# Project Audit — Session 14 (2026-10-25)

## Focus
Live verification of public Lavalink search reliability, reduced music search latency, and regression coverage for stalled search endpoints.

## Live external test
Added `scripts/check_public_lavalink.py` and `.github/workflows/lavalink-live-check.yml`. The workflow makes real authenticated Lavalink REST requests and verifies that `ytsearch:Daft Punk One More Time` returns actual track results; this is not a mocked test.

- [Public Lavalink Live Smoke Test](https://github.com/pixelproto-x/discord-bot/actions/runs/40189262208): success.
- Five independently listed public V4 nodes returned 20 search results each: TriniumHost V4 (HTTP 4333), TriniumHost V4 alternate (HTTP 2333), Nazha V4, Millohost V4, and Serenetia V4.
- Earlier exploratory checks found the TriniumHost SSL endpoint timing out and Jirayu V4 returning HTTP 500. Those endpoints were excluded from the default fallback list rather than treated as healthy.
- Public nodes are community-operated and can change availability/credentials; this test verifies search only at the time of the run. It does not establish that voice playback will always work.

## Code changes
- Replaced two unresponsive/intermittent defaults with three independent endpoints that passed the live search request.
- Reduced the per-node music search timeout from 15 seconds to 12 seconds.
- Kept node searches concurrent, returning the first non-empty result and cancelling remaining in-flight searches.
- Added regression tests proving a stalled node does not block a healthy node, and that all stalled source searches are bounded across YouTube, YouTube Music, and SoundCloud.
- The broader changes in Sessions 11–13 remain in place: atomic backup persistence, serialized store/delete operations, preflight restore validation, locale restoration, background attachment archival with finite timeout, and safe process cleanup.

## Verification
- [Python Bot Checks](https://github.com/pixelproto-x/discord-bot/actions/runs/40189277545): success.
- [Python bot checks](https://github.com/pixelproto-x/discord-bot/actions/runs/40189277594): success; all 120 unit/regression tests passed, including the two new stalled-search tests; syntax/compile, imports, command registration, storage smoke test, dependency compatibility, help catalog, static undefined-name check, and security checks passed.
- [AI Persona Tests](https://github.com/pixelproto-x/discord-bot/actions/runs/40189277696): success.
- [Secret scan](https://github.com/pixelproto-x/discord-bot/actions/runs/40189277621): success.
- [GitHub Pages deployment](https://github.com/pixelproto-x/discord-bot/actions/runs/40189277601): success.

## Research references
- [Wavelink 3.5 API reference](https://wavelink.readthedocs.io/en/latest/wavelink.html): `Playable.search` supports an explicit node and source prefix.
- [Public Lavalink node directory](https://lavalink-list.darrennathanael.com/): source for discovering community-run endpoints; endpoint health is confirmed by the live smoke test, not inferred from directory listings.

## Limits
- The live test verifies real YouTube search results through Lavalink REST, not a Discord voice connection, actual audio output, or a full user-credentialed bot session.
- The backup file lock is process-local, not cross-process.
- CI cannot prove every Discord command or external API behaves correctly in every server configuration.
- No Render service was created or used.
