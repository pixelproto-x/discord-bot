"""Live smoke test for the public Lavalink V4 fallback nodes.

This intentionally checks the same ytsearch REST path used by Wavelink without
requiring Discord credentials or connecting a bot to a voice channel.
"""
import asyncio
import os
from urllib.parse import quote

import aiohttp


NODES = (
    ("triniumhost-v4", "http://lavalink.triniumhost.indevs.in:4333", "free"),
    ("triniumhost-v4-ssl", "https://lavalink-v4.triniumhost.indevs.in:443", "free"),
    ("triniumhost-v4-alt", "http://lavalink.triniumhost.indevs.in:2333", "kirito"),
    ("nazha-v4", "https://lavalink.nazha.online:443", "nazhafreelava"),
    ("millohost-v4", "https://lava-v4.millohost.my.id:443", "https://discord.gg/mjS5J2K3ep"),
    ("serenetia-v4", "https://lavalinkv4.serenetia.com:443", "https://seretia.link/discord"),
)
QUERIES = (
    ("ytsearch", "ytsearch:Daft Punk One More Time", True),
    ("ytmsearch", "ytmsearch:Daft Punk One More Time", False),
    ("scsearch", "scsearch:Daft Punk One More Time", False),
)
TIMEOUT = aiohttp.ClientTimeout(total=12, connect=5, sock_read=8)


async def check_source(
    session: aiohttp.ClientSession,
    node: tuple[str, str, str],
    source_name: str,
    query: str,
) -> bool:
    name, base_url, password = node
    url = f"{base_url.rstrip('/')}/v4/loadtracks"
    try:
        async with session.get(
            url,
            params={"identifier": query},
            headers={"Authorization": password, "User-Agent": "discord-bot-lavalink-smoke-test/1.0"},
            timeout=TIMEOUT,
        ) as response:
            if response.status != 200:
                print(f"FAIL {name} {source_name}: HTTP {response.status}")
                return False
            payload = await response.json(content_type=None)
            load_type = payload.get("loadType")
            tracks = payload.get("data")
            if load_type == "search" and isinstance(tracks, list) and tracks:
                title = str(tracks[0].get("info", {}).get("title", "unknown"))[:100]
                print(f"PASS {name} {source_name}: {len(tracks)} tracks; first title={title!r}")
                return True
            print(f"FAIL {name} {source_name}: loadType={load_type!r}, result_count={len(tracks) if isinstance(tracks, list) else 0}")
            return False
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        print(f"FAIL {name} {source_name}: {type(exc).__name__}: {str(exc)[:160]}")
        return False


async def check_websocket(session: aiohttp.ClientSession, node: tuple[str, str, str]) -> bool:
    """Verify the actual Lavalink V4 WebSocket handshake, not just REST search."""
    name, base_url, password = node
    websocket_url = base_url.rstrip("/").replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    websocket_url += "/v4/websocket"
    websocket = None
    try:
        websocket = await asyncio.wait_for(
            session.ws_connect(
                websocket_url,
                headers={
                    "Authorization": password,
                    "User-Id": "123456789012345678",
                    "Client-Name": "Wavelink/3.5.0",
                },
                heartbeat=15,
            ),
            timeout=8,
        )
        if websocket.closed:
            print(f"FAIL {name} websocket: connection closed during handshake")
            return False
        print(f"PASS {name} websocket: Lavalink V4 WebSocket handshake accepted")
        return True
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        print(f"FAIL {name} websocket: {type(exc).__name__}: {str(exc)[:160]}")
        return False
    finally:
        if websocket is not None and not websocket.closed:
            await websocket.close()


async def main() -> int:
    connector = aiohttp.TCPConnector(limit_per_host=3)
    async with aiohttp.ClientSession(connector=connector) as session:
        checks = {
            source_name: await asyncio.gather(
                *(check_source(session, node, source_name, query) for node in NODES)
            )
            for source_name, query, _required in QUERIES
        }
        websocket_checks = await asyncio.gather(
            *(check_websocket(session, node) for node in NODES)
        )
    for source_name, results in checks.items():
        print(f"Live Lavalink {source_name}: {sum(results)}/{len(NODES)} nodes returned real search results.")
    print(f"Live Lavalink WebSocket: {sum(websocket_checks)}/{len(NODES)} nodes accepted the V4 handshake.")
    required_sources = {name for name, _query, required in QUERIES if required}
    missing = sorted(name for name in required_sources if not any(checks[name]))
    if missing:
        print(f"ERROR: no configured public Lavalink node returned results for required source(s): {', '.join(missing)}")
        return 1
    if not any(websocket_checks):
        print("ERROR: no configured public Lavalink node accepted a V4 WebSocket handshake.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
