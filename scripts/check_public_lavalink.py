"""Live integration smoke tests for configured public Lavalink V4 nodes."""
import asyncio

import aiohttp


NODES = (
    ("nazha-v4", "https://lavalink.nazha.online:443", "nazhafreelava"),
    ("millohost-v4", "https://lava-v4.millohost.my.id:443", "https://discord.gg/mjS5J2K3ep"),
    ("serenetia-v4", "https://lavalinkv4.serenetia.com:443", "https://seretia.link/discord"),
    ("triniumhost-v4-alt", "http://lavalink.triniumhost.indevs.in:2333", "kirito"),
)
SEARCHES = (
    ("ytsearch", "ytsearch:Daft Punk One More Time"),
    ("ytmsearch", "ytmsearch:Daft Punk One More Time"),
    ("scsearch", "scsearch:Daft Punk One More Time"),
)
DIRECT_URL = "https://www.youtube.com/watch?v=FGBhQbmPwH8"
TIMEOUT = aiohttp.ClientTimeout(total=12, connect=5, sock_read=8)
USER_AGENT = "discord-bot-lavalink-smoke-test/1.1"


async def check_auth(session: aiohttp.ClientSession, node: tuple[str, str, str]) -> bool:
    name, base_url, password = node
    url = f"{base_url.rstrip('/')}/v4/info"
    try:
        async with session.get(
            url,
            headers={"Authorization": password, "User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        ) as response:
            if response.status != 200:
                print(f"FAIL {name} auth: valid password returned HTTP {response.status}")
                return False
            info = await response.json(content_type=None)
            version = info.get("version", {}).get("semver", "unknown")
            print(f"PASS {name} auth: accepted valid password; Lavalink version={version}")
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        print(f"FAIL {name} auth: {type(exc).__name__}: {str(exc)[:160]}")
        return False

    try:
        async with session.get(
            url,
            headers={"Authorization": f"{password}-invalid-smoke-test", "User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        ) as response:
            if response.status not in (401, 403):
                print(f"FAIL {name} auth: rejected password returned HTTP {response.status}, expected 401/403")
                return False
            print(f"PASS {name} auth: invalid password rejected with HTTP {response.status}")
            return True
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        print(f"FAIL {name} auth: invalid-password check {type(exc).__name__}: {str(exc)[:160]}")
        return False


async def check_load(
    session: aiohttp.ClientSession,
    node: tuple[str, str, str],
    label: str,
    identifier: str,
    expected: set[str],
) -> bool:
    name, base_url, password = node
    url = f"{base_url.rstrip('/')}/v4/loadtracks"
    try:
        async with session.get(
            url,
            params={"identifier": identifier},
            headers={"Authorization": password, "User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        ) as response:
            if response.status != 200:
                print(f"FAIL {name} {label}: HTTP {response.status}")
                return False
            payload = await response.json(content_type=None)
            load_type = str(payload.get("loadType", "")).casefold()
            data = payload.get("data")
            if load_type in expected and isinstance(data, list) and data:
                first = data[0] if data else {}
                title = str(first.get("info", {}).get("title", "unknown"))[:90] if isinstance(first, dict) else "unknown"
                print(f"PASS {name} {label}: loadType={load_type}; results={len(data)}; first={title!r}")
                return True
            if load_type in expected and isinstance(data, dict):
                title = str(data.get("info", {}).get("title", "unknown"))[:90]
                print(f"PASS {name} {label}: loadType={load_type}; track={title!r}")
                return True
            print(f"FAIL {name} {label}: loadType={load_type!r}; result_type={type(data).__name__}")
            return False
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        print(f"FAIL {name} {label}: {type(exc).__name__}: {str(exc)[:160]}")
        return False


async def check_websocket(session: aiohttp.ClientSession, node: tuple[str, str, str]) -> bool:
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
        print(f"PASS {name} websocket: Lavalink V4 handshake accepted")
        return True
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        print(f"FAIL {name} websocket: {type(exc).__name__}: {str(exc)[:160]}")
        return False
    finally:
        if websocket is not None and not websocket.closed:
            await websocket.close()


async def main() -> int:
    connector = aiohttp.TCPConnector(limit=32, limit_per_host=3)
    async with aiohttp.ClientSession(connector=connector) as session:
        auth = await asyncio.gather(*(check_auth(session, node) for node in NODES))
        searches = {
            label: await asyncio.gather(
                *(check_load(session, node, label, query, {"search"}) for node in NODES)
            )
            for label, query in SEARCHES
        }
        direct_url = await asyncio.gather(
            *(check_load(session, node, "youtube-url", DIRECT_URL, {"track", "playlist"}) for node in NODES)
        )
        websockets = await asyncio.gather(*(check_websocket(session, node) for node in NODES))

    print(f"Lavalink valid/invalid password checks: {sum(auth)}/{len(NODES)} nodes passed both checks.")
    for label, results in searches.items():
        print(f"Lavalink {label}: {sum(results)}/{len(NODES)} nodes returned real results.")
    print(f"Lavalink direct YouTube URL: {sum(direct_url)}/{len(NODES)} nodes resolved the URL.")
    print(f"Lavalink V4 WebSocket: {sum(websockets)}/{len(NODES)} nodes accepted the handshake.")

    required = {
        "password authentication": any(auth),
        "ytsearch": any(searches["ytsearch"]),
        "ytmsearch": any(searches["ytmsearch"]),
        "scsearch": any(searches["scsearch"]),
        "direct YouTube URL": any(direct_url),
        "WebSocket handshake": any(websockets),
    }
    failed = [name for name, passed in required.items() if not passed]
    if failed:
        print("ERROR: no active node passed required check(s): " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
