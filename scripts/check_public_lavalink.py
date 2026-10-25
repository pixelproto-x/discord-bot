"""Live smoke test for the public Lavalink V4 fallback nodes.

This intentionally checks the same ytsearch REST path used by Wavelink without
requiring Discord credentials or connecting a bot to a voice channel.
"""
import asyncio
import os
from urllib.parse import quote

import aiohttp


NODES = (
    ("triniumhost-v4-ssl", "https://lavalink-v4.triniumhost.indevs.in:443", "free"),
    ("triniumhost-v4", "http://lavalink.triniumhost.indevs.in:4333", "free"),
    ("triniumhost-v4-alt", "http://lavalink.triniumhost.indevs.in:2333", "kirito"),
)
QUERY = "ytsearch:Daft Punk One More Time"
TIMEOUT = aiohttp.ClientTimeout(total=12, connect=5, sock_read=8)


async def check_node(session: aiohttp.ClientSession, node: tuple[str, str, str]) -> bool:
    name, base_url, password = node
    url = f"{base_url.rstrip('/')}/v4/loadtracks"
    try:
        async with session.get(
            url,
            params={"identifier": QUERY},
            headers={"Authorization": password, "User-Agent": "discord-bot-lavalink-smoke-test/1.0"},
            timeout=TIMEOUT,
        ) as response:
            if response.status != 200:
                print(f"FAIL {name}: HTTP {response.status}")
                return False
            payload = await response.json(content_type=None)
            load_type = payload.get("loadType")
            tracks = payload.get("data")
            if load_type == "search" and isinstance(tracks, list) and tracks:
                title = str(tracks[0].get("info", {}).get("title", "unknown"))[:100]
                print(f"PASS {name}: ytsearch returned {len(tracks)} tracks; first title={title!r}")
                return True
            print(f"FAIL {name}: loadType={load_type!r}, result_count={len(tracks) if isinstance(tracks, list) else 0}")
            return False
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        print(f"FAIL {name}: {type(exc).__name__}: {str(exc)[:160]}")
        return False


async def main() -> int:
    connector = aiohttp.TCPConnector(limit_per_host=2)
    async with aiohttp.ClientSession(connector=connector) as session:
        results = await asyncio.gather(*(check_node(session, node) for node in NODES))
    healthy = sum(results)
    print(f"Live Lavalink ytsearch smoke test: {healthy}/{len(NODES)} nodes returned real search results.")
    if healthy == 0:
        print("ERROR: no configured public Lavalink V4 node returned a YouTube search result.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
