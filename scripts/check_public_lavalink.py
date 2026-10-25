"""Live smoke test for configured public Lavalink nodes and community candidates.

The active nodes mirror cogs/music.py. Candidate nodes are diagnostic only and
must never make the configured production fallback check pass by themselves.
"""
import asyncio

import aiohttp


ACTIVE_NODES = (
    ("nazha-v4", "https://lavalink.nazha.online:443", "nazhafreelava"),
    ("millohost-v4", "https://lava-v4.millohost.my.id:443", "https://discord.gg/mjS5J2K3ep"),
    ("serenetia-v4", "https://lavalinkv4.serenetia.com:443", "https://seretia.link/discord"),
    ("triniumhost-v4-alt", "http://lavalink.triniumhost.indevs.in:2333", "kirito"),
)
CANDIDATE_NODES = (
    ("stackryze-v4", "http://188.245.207.225:2333", "youshallnotpass"),
    ("itzrandom-v4", "http://node.itzrandom.cloud:9000", "lavalink@itzrandomcloud"),
    ("horizxon-sg-v4", "https://v4.lavalink.rocks:443", "horizxon.tech"),
    ("catfein-id-v4", "http://lava.catfein.com:4000", "catfein"),
    ("catfein-de-v4", "https://lavalink.alfari.id:443", "catfein"),
    ("meww-v4", "http://n2.meww.me:2555", "meww.me"),
    ("rudracloud-v4", "http://lavalink.rudracloud.com:2333", "RudraCloud.com"),
    ("akshat-v4", "https://lava.akshat.tech:443", "admin"),
    ("paradisehost-v4", "http://node1.paradisehost.xyz:27185", "Lavalinkhoster123"),
    ("nyc-jxshua-v4", "http://nyc01.jxshua.dev:4000", "youshallnotpass"),
    ("koi-v4", "http://lavav4.prplmoe.me:1118", "prplmoe.me"),
    ("techbyte-v4", "http://lavahatry4.techbyte.host:3000", "NAIGLAVA-dash.techbyte.host"),
    ("triniumhost-v4", "http://lavalink.triniumhost.indevs.in:4333", "free"),
    ("triniumhost-v4-ssl", "https://lavalink-v4.triniumhost.indevs.in:443", "free"),
)
QUERIES = (
    ("ytsearch", "ytsearch:Daft Punk One More Time"),
    ("ytmsearch", "ytmsearch:Daft Punk One More Time"),
    ("scsearch", "scsearch:Daft Punk One More Time"),
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
    all_nodes = ACTIVE_NODES + CANDIDATE_NODES
    connector = aiohttp.TCPConnector(limit=64, limit_per_host=3)
    async with aiohttp.ClientSession(connector=connector) as session:
        checks = {
            source_name: await asyncio.gather(
                *(check_source(session, node, source_name, query) for node in all_nodes)
            )
            for source_name, query in QUERIES
        }
        websocket_checks = await asyncio.gather(
            *(check_websocket(session, node) for node in all_nodes)
        )

    active_count = len(ACTIVE_NODES)
    for source_name, results in checks.items():
        active_results = results[:active_count]
        candidate_results = results[active_count:]
        print(
            f"Lavalink {source_name}: active={sum(active_results)}/{active_count}; "
            f"candidates={sum(candidate_results)}/{len(candidate_results)} returned real search results."
        )
    active_websockets = websocket_checks[:active_count]
    candidate_websockets = websocket_checks[active_count:]
    print(
        f"Lavalink V4 WebSocket: active={sum(active_websockets)}/{active_count}; "
        f"candidates={sum(candidate_websockets)}/{len(candidate_websockets)} accepted the handshake."
    )

    failures = [
        source_name
        for source_name, results in checks.items()
        if not any(results[:active_count])
    ]
    if failures:
        print(
            "ERROR: configured active Lavalink nodes returned no results for: "
            + ", ".join(failures)
        )
        return 1
    if not any(active_websockets):
        print("ERROR: no configured active Lavalink node accepted a V4 WebSocket handshake.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
