import asyncio
import logging
import os
import random
import re
from collections import defaultdict
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import discord
import wavelink
from discord import app_commands
from discord.ext import commands


MAX_QUEUE_SIZE = 10000
MAX_QUERY_LENGTH = 200
def _configured_lavalink_nodes() -> tuple[dict[str, str], ...]:
    """Load configured nodes first, then the currently listed public fallbacks."""
    nodes: list[dict[str, str]] = []
    seen_uris: set[str] = set()

    for index in range(1, 4):
        uri = os.getenv(f"LAVALINK_{index}_URI", "").strip()
        password = os.getenv(f"LAVALINK_{index}_PASSWORD", "")
        if not uri and not password:
            continue
        if not uri or not password:
            print(f"[Music] LAVALINK_{index}_URI 和 LAVALINK_{index}_PASSWORD 必須一起設定。")
            continue
        identifier = os.getenv(f"LAVALINK_{index}_IDENTIFIER", f"lavalink-{index}").strip() or f"lavalink-{index}"
        nodes.append({"identifier": identifier, "uri": uri, "password": password})
        seen_uris.add(uri.rstrip("/").lower())

    # Public fallback nodes currently listed by TriniumHost. Public nodes can
    # change availability or credentials; configure your own nodes above for reliability.
    public_nodes = (
        ("triniumhost-v4-ssl", "https://lavalink-v4.triniumhost.indevs.in:443", "free"),
        ("triniumhost-v4", "http://lavalink.triniumhost.indevs.in:4333", "free"),
    )
    for identifier, uri, password in public_nodes:
        normalized_uri = uri.rstrip("/").lower()
        if normalized_uri in seen_uris:
            continue
        nodes.append({"identifier": identifier, "uri": uri, "password": password})
        seen_uris.add(normalized_uri)

    print(f"[Music] 已載入 {len(nodes)} 個 Lavalink 候選節點（含免費公開備援）。")
    return tuple(nodes)

LAVALINK_NODES = _configured_lavalink_nodes()
LAVALINK_INACTIVE_PLAYER_TIMEOUT = 300
# A dead public node must never hold up the rest of the bot's startup. This
# bounds the first websocket attempt; retries are disabled on every node below.
LAVALINK_CONNECT_TIMEOUT_SECONDS = 3
SUPPORTED_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com|music\.youtube\.com|youtu\.be|open\.spotify\.com)(?:/[^\s<>]*)?",
    re.IGNORECASE,
)


@dataclass
class MusicSession:
    player: wavelink.Player
    queue: list = field(default_factory=list)
    current: object | None = None
    volume: int = 80
    loop_mode: str = "off"
    skip_requested: bool = False
    text_channel_id: int | None = None


def normalize_query(query: str) -> tuple[str | None, str | None]:
    value = query.strip() if isinstance(query, str) else ""
    if not value:
        return None, "請輸入歌名、歌手或 YouTube／Spotify 網址。"
    if len(value) > MAX_QUERY_LENGTH:
        return None, f"搜尋內容最多 {MAX_QUERY_LENGTH} 個字。"
    if re.match(r"^https?://", value, re.IGNORECASE):
        try:
            parsed = urlsplit(value)
            host = (parsed.hostname or "").lower().rstrip(".")
            port = parsed.port
        except ValueError:
            return None, "這個網址格式不正確。"
        allowed = (
            host == "youtu.be"
            or host == "youtube.com"
            or host.endswith(".youtube.com")
            or host == "spotify.com"
            or host.endswith(".spotify.com")
        )
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not allowed
            or parsed.username is not None
            or parsed.password is not None
            or port not in (None, 80 if parsed.scheme.lower() == "http" else 443)
        ):
            return None, "目前只接受 YouTube 或 Spotify 網址，或一般歌曲關鍵字。"
    return value, None


def track_title(track) -> str:
    info = getattr(track, "title", None)
    return str(info or "未知歌曲")


def track_url(track) -> str:
    return str(getattr(track, "uri", None) or getattr(track, "url", None) or "")


def track_duration(track) -> str:
    length = getattr(track, "length", None)
    if not length:
        return "直播／未知長度"
    seconds = int(length) // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


class MusicSearchButton(discord.ui.Button):
    def __init__(self, cog, track, index: int, requester_id: int):
        self.cog = cog
        self.track = track
        self.requester_id = requester_id
        super().__init__(
            label=f"{index}. {track_title(track)}"[:80],
            style=discord.ButtonStyle.primary,
            row=(index - 1) // 5,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("這份選歌清單是別人叫出的，請自己搜尋一次。", ephemeral=True)
            return
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("請在伺服器裡使用。", ephemeral=True)
            return
        member = interaction.user
        if not member.voice or not member.voice.channel:
            await interaction.response.send_message("你先加入語音頻道，我才知道要在哪裡播。", ephemeral=True)
            return
        # Acknowledge the component interaction before any Lavalink/network work;
        # Discord expires unacknowledged interactions after a short deadline.
        try:
            await interaction.response.defer()
        except discord.NotFound:
            return
        try:
            count, started = await self.cog.enqueue_tracks(
                interaction.guild,
                member.voice.channel,
                interaction.channel_id,
                member.id,
                [self.track],
            )
            session = self.cog.sessions[interaction.guild.id]
            if started and session.current:
                text = f"🎵 開始播放：**{track_title(session.current)}**"
            else:
                text = f"已加入佇列：**{track_title(self.track)}**"
            for item in self.view.children:
                item.disabled = True
            await interaction.edit_original_response(content=text, view=self.view)
        except Exception as exc:
            print(f"[Music] 選歌播放失敗：{type(exc).__name__}: {exc}")
            try:
                await interaction.edit_original_response(
                    content="這首歌目前無法播放，可能是 YouTube 限制或 Lavalink 節點問題；請重新搜尋並選另一個版本。",
                    view=self.view,
                )
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass


class MusicSearchView(discord.ui.View):
    def __init__(self, cog, tracks: list, requester_id: int):
        super().__init__(timeout=60)
        self.tracks = tracks[:25]
        for index, track in enumerate(self.tracks, 1):
            self.add_item(MusicSearchButton(cog, track, index, requester_id))


class MusicQueueSelect(discord.ui.Select):
    def __init__(self, cog, guild_id: int, requester_id: int):
        self.cog = cog
        self.guild_id = guild_id
        self.requester_id = requester_id
        session = cog.sessions.get(guild_id)
        queue = list(session.queue) if session else []
        options = [
            discord.SelectOption(label=track_title(track)[:100], description=f"第 {index} 首 · {track_duration(track)}"[:100], value=str(index - 1))
            for index, track in enumerate(queue[:25], 1)
        ]
        super().__init__(placeholder="選擇要刪除的待播歌曲", min_values=1, max_values=1,
                         options=options or [discord.SelectOption(label="沒有待播歌曲", value="none")],
                         disabled=not options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("只有開啟控制台的人可以操作這份清單。", ephemeral=True)
            return
        session = self.cog.sessions.get(self.guild_id)
        if not session or self.values[0] == "none":
            await interaction.response.send_message("目前沒有可刪除的待播歌曲。", ephemeral=True)
            return
        index = int(self.values[0])
        if index >= len(session.queue):
            await interaction.response.send_message("清單已變動，請重新開啟控制台。", ephemeral=True)
            return
        removed = session.queue.pop(index)
        await interaction.response.edit_message(content=self.cog.render_queue(session),
                                                view=MusicQueueView(self.cog, self.guild_id, self.requester_id))
        await interaction.followup.send(f"已從待播清單移除：**{track_title(removed)}**", ephemeral=True)


class MusicQueueView(discord.ui.View):
    def __init__(self, cog, guild_id: int, requester_id: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild_id = guild_id
        self.requester_id = requester_id
        self.add_item(MusicQueueSelect(cog, guild_id, requester_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.requester_id:
            return True
        await interaction.response.send_message("只有開啟控制台的人可以操作這份清單。", ephemeral=True)
        return False

    async def refresh(self, interaction: discord.Interaction, notice: str | None = None):
        session = self.cog.sessions.get(self.guild_id)
        content = self.cog.render_queue(session)
        if notice:
            content = f"{notice}\\n\\n{content}"
        await interaction.response.edit_message(content=content[:1900],
                                                view=MusicQueueView(self.cog, self.guild_id, self.requester_id))

    @discord.ui.button(label="下一首", style=discord.ButtonStyle.primary, row=1)
    async def next_track(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.cog.sessions.get(self.guild_id)
        if not session or not session.current:
            await interaction.response.send_message("目前沒有正在播放的歌曲。", ephemeral=True)
            return
        session.skip_requested = True
        await session.player.stop()
        await self.refresh(interaction, "已切到下一首。")

    @discord.ui.button(label="暫停／繼續", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.cog.sessions.get(self.guild_id)
        if not session or not session.current:
            await interaction.response.send_message("目前沒有正在播放的歌曲。", ephemeral=True)
            return
        paused = bool(getattr(session.player, "paused", False))
        await session.player.pause(not paused)
        await self.refresh(interaction, "已繼續播放。" if paused else "已暫停。")

    @discord.ui.button(label="倒退 5 秒", style=discord.ButtonStyle.secondary, row=1)
    async def rewind_five(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.cog.sessions.get(self.guild_id)
        if not session or not session.current:
            await interaction.response.send_message("目前沒有正在播放的歌曲。", ephemeral=True)
            return
        position = int(getattr(session.player, "position", 0) or 0)
        try:
            await session.player.seek(max(0, position - 5000))
            await self.refresh(interaction, "已倒退 5 秒。")
        except Exception as exc:
            print(f"[Music] 倒退失敗：{type(exc).__name__}: {exc}")
            await interaction.response.send_message("這個來源不支援倒退，或播放器暫時無法定位。", ephemeral=True)

    @discord.ui.button(label="更新清單", style=discord.ButtonStyle.success, row=1)
    async def refresh_list(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.refresh(interaction)


class MusicCog(commands.Cog):
    music = app_commands.Group(
        name=app_commands.locale_str("music", zh_TW="音樂"),
        description=app_commands.locale_str("搜尋並播放音樂", zh_TW="搜尋並播放音樂"),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions: dict[int, MusicSession] = {}
        self.locks = defaultdict(asyncio.Lock)
        self.node_error: str | None = None

    async def _discard_failed_node(self, node: wavelink.Node) -> None:
        """Remove a failed node and close its client session without log noise."""
        try:
            await node.close(eject=True)
        except Exception:
            pass

        # Wavelink only schedules session cleanup after a successful
        # connection. A timed-out initial connection has no such task.
        session = getattr(node, "_session", None)
        if session is not None and not session.closed:
            try:
                await session.close()
            except Exception:
                pass

    async def _connect_node_once(self, node: wavelink.Node) -> bool:
        """Try one node once, with a short timeout and no propagated error."""
        try:
            await asyncio.wait_for(
                wavelink.Pool.connect(nodes=(node,), client=self.bot),
                timeout=LAVALINK_CONNECT_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            await self._discard_failed_node(node)
            raise
        except Exception:
            await self._discard_failed_node(node)
            return False

        # With retries=0, Wavelink can return after a refused connection while
        # leaving a disconnected node in Pool. Do not count it as usable.
        if node.status is wavelink.NodeStatus.DISCONNECTED:
            await self._discard_failed_node(node)
            return False
        return True

    async def setup_nodes(self):
        """建立音樂節點；失效公開節點只嘗試一次，避免重試刷屏。"""
        if wavelink.Pool.nodes:
            print("[Music] Lavalink 節點池已存在，略過重複初始化。")
            return

        if not LAVALINK_NODES:
            self.node_error = "尚未設定 Lavalink 節點；請在 .env 設定 LAVALINK_1_URI 與 LAVALINK_1_PASSWORD。"
            print(f"[Music] {self.node_error}")
            return

        # Wavelink 會把每個公開節點的連線失敗逐條寫入日誌。
        # 降低其 logger 等級，只保留本函式下方的一行摘要。
        logging.getLogger("wavelink").setLevel(logging.CRITICAL)

        nodes = [
            wavelink.Node(
                identifier=config["identifier"],
                uri=config["uri"],
                password=config["password"],
                inactive_player_timeout=LAVALINK_INACTIVE_PLAYER_TIMEOUT,
                retries=0,
            )
            for config in LAVALINK_NODES
        ]

        results = [False] * len(nodes)
        try:
            results = await asyncio.gather(*(self._connect_node_once(node) for node in nodes))
        except Exception:
            # 單一節點不可用時，不讓音樂服務影響 Discord Bot 啟動。
            pass

        connected = [node for node, succeeded in zip(nodes, results) if succeeded]
        configured_count = len(nodes)
        failed_count = max(0, configured_count - len(connected))
        if connected:
            self.node_error = None
            print(f"[Music] 音樂節點可用：{len(connected)}/{configured_count}；已略過 {failed_count} 個失敗節點。")
        else:
            self.node_error = "目前沒有可用的 Lavalink 節點。"
            print(f"[Music] 沒有可用音樂節點，已略過 {failed_count} 個失敗節點；機器人其他功能照常啟動。")

    async def cog_load(self):
        await self.setup_nodes()

    def get_session(self, guild_id: int) -> MusicSession | None:
        return self.sessions.get(guild_id)

    async def ensure_player(self, guild: discord.Guild, channel: discord.VoiceChannel | discord.StageChannel, text_channel_id: int) -> MusicSession:
        session = self.sessions.get(guild.id)
        voice_client = guild.voice_client
        if isinstance(voice_client, wavelink.Player):
            player = voice_client
            if player.channel != channel:
                if session and (session.current or session.queue):
                    raise RuntimeError("我正在其他語音頻道播放音樂，不能直接切換。")
                await player.move_to(channel)
        else:
            player = await channel.connect(cls=wavelink.Player, self_deaf=True)
        if session is None or session.player is not player:
            session = MusicSession(player=player)
            self.sessions[guild.id] = session
        session.text_channel_id = text_channel_id
        try:
            await player.set_volume(session.volume)
        except Exception:
            pass
        return session

    async def resolve_query(self, query: str) -> list:
        value, error = normalize_query(query)
        if error:
            raise ValueError(error)
        assert value is not None
        is_url = bool(re.match(r"^https?://", value, re.IGNORECASE))
        identifier = value if is_url else f"ytsearch:{value}"
        result = await wavelink.Playable.search(identifier)
        if not result:
            return []
        if hasattr(result, "tracks"):
            tracks = list(result.tracks)
        else:
            tracks = list(result)

        # 歌名搜尋回傳候選歌曲供使用者選擇；網址則保留單曲／播放清單行為。
        return tracks

    async def enqueue_tracks(
        self,
        guild: discord.Guild,
        voice_channel: discord.VoiceChannel | discord.StageChannel,
        text_channel_id: int,
        requester_id: int,
        tracks: list,
    ) -> tuple[int, bool]:
        if not tracks:
            return 0, False
        session = await self.ensure_player(guild, voice_channel, text_channel_id)
        available = max(0, MAX_QUEUE_SIZE - len(session.queue) - (1 if session.current else 0))
        selected = tracks[:available]
        if not selected:
            raise OverflowError(f"等待佇列最多 {MAX_QUEUE_SIZE} 首，請先播放或移除幾首。")
        for track in selected:
            try:
                track.extras = {"requester_id": str(requester_id)}
            except Exception:
                pass
            session.queue.append(track)
        started = False
        if session.current is None and not session.player.playing:
            await self.play_next(guild.id)
            started = True
        return len(selected), started

    async def play_next(self, guild_id: int):
        async with self.locks[guild_id]:
            session = self.sessions.get(guild_id)
            if not session or session.current is not None:
                return
            if not session.queue:
                return
            track = session.queue.pop(0)
            session.current = track
            session.skip_requested = False
            try:
                await session.player.play(track)
                print(f"[Music] Playing: {track_title(track)} (guild={guild_id})")
            except Exception as exc:
                print(f"[Music] 播放失敗：{type(exc).__name__}: {exc}")
                session.current = None
                asyncio.create_task(self.play_next(guild_id))

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload):
        player = getattr(payload, "player", None)
        if player is None or player.guild is None:
            return
        guild_id = player.guild.id
        async with self.locks[guild_id]:
            session = self.sessions.get(guild_id)
            if not session or session.player is not player:
                return
            ended_track = session.current or getattr(payload, "track", None)
            reason = str(getattr(payload, "reason", "")).upper()
            skipped = session.skip_requested
            session.skip_requested = False
            if ended_track and not skipped and reason.endswith("FINISHED"):
                if session.loop_mode == "one":
                    session.current = ended_track
                    try:
                        await player.play(ended_track)
                    except Exception as exc:
                        print(f"[Music] 單曲循環失敗：{exc!r}")
                        session.current = None
                    return
                if session.loop_mode == "queue":
                    session.queue.append(ended_track)
            session.current = None
            if session.queue:
                next_track = session.queue.pop(0)
                session.current = next_track
                try:
                    await player.play(next_track)
                    return
                except Exception as exc:
                    print(f"[Music] 下一首播放失敗：{exc!r}")
                    session.current = None
                    asyncio.create_task(self.play_next(guild_id))
                    return
            # 保持語音連線；不因播放佇列暫時為空而自動離開。

    async def stop_player(self, guild_id: int):
        async with self.locks[guild_id]:
            session = self.sessions.pop(guild_id, None)
            guild = self.bot.get_guild(guild_id)
            voice_client = guild.voice_client if guild else None
            if session:
                voice_client = session.player
                session.queue.clear()
                session.current = None
                session.skip_requested = True
            speech_cog = self.bot.get_cog("SpeechControlCog")
            if speech_cog:
                mic_source = getattr(speech_cog, "active_mics", {}).get(guild_id)
                if mic_source:
                    await speech_cog._finish_mic(guild_id, mic_source)
                if guild_id in getattr(speech_cog, "active_guilds", set()):
                    await speech_cog._cleanup(guild_id, getattr(speech_cog, "temp_files", {}).get(guild_id))
            if voice_client and getattr(voice_client, "connected", True):
                try:
                    await voice_client.disconnect(force=True)
                except Exception as exc:
                    print(f"[Music] 離開語音失敗：{type(exc).__name__}: {exc}")

    async def play_query_from_message(self, message: discord.Message, query: str):
        if not message.guild or not isinstance(message.author, discord.Member):
            return
        if not message.author.voice or not message.author.voice.channel:
            await message.reply("你先加入語音頻道，我才知道要在哪裡播。", mention_author=False)
            return
        try:
            tracks = await self.resolve_query(query)
            if not tracks:
                await message.reply("找不到這個歌曲或網址，可能是節點不支援這個來源。", mention_author=False)
                return
            count, started = await self.enqueue_tracks(
                message.guild, message.author.voice.channel, message.channel.id, message.author.id, tracks
            )
            is_playlist = "list=" in query.lower() or "playlist" in query.lower()
            if len(tracks) > count:
                note = f"佇列已滿，只加入前 {count} 首。"
            elif is_playlist:
                note = f"播放清單已依 YouTube 順序加入 {count} 首。"
            else:
                note = f"已加入 {count} 首。"
            if started:
                await message.channel.send(f"🎵 開始播放：**{track_title(self.sessions[message.guild.id].current)}**")
            else:
                await message.channel.send(note)
        except OverflowError as exc:
            await message.reply(str(exc), mention_author=False)
        except Exception as exc:
            print(f"[Music] 自動播放失敗：{type(exc).__name__}: {exc}")
            await message.reply("這個連結目前無法解析，或 Lavalink 節點暫時不可用。", mention_author=False)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild or not message.content:
            return
        match = SUPPORTED_URL_RE.search(message.content)
        if not match:
            return
        await self.play_query_from_message(message, match.group(0).rstrip(".,!?)]}，。！？"))

    @music.command(name=app_commands.locale_str("play", zh_TW="播放音樂"), description=app_commands.locale_str("搜尋歌曲或播放 YouTube／Spotify 網址", zh_TW="搜尋歌曲或播放 YouTube／Spotify 網址"))
    @app_commands.describe(query="歌名、歌手、YouTube 或 Spotify 網址", public=app_commands.locale_str("是否讓其他人看見這則回覆", zh_TW="是否公開顯示回覆"))
    @app_commands.rename(public=app_commands.locale_str("公開", zh_TW="公開"))
    async def play(self, interaction: discord.Interaction, query: str, public: bool = True):
        if not interaction.guild:
            await interaction.response.send_message("請在伺服器裡使用。", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member) or not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("你先加入語音頻道，我才知道要在哪裡播。", ephemeral=True)
            return
        query, error = normalize_query(query)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        await interaction.response.defer(thinking=True, ephemeral=not public)
        try:
            tracks = await self.resolve_query(query)
            if not tracks:
                await interaction.followup.send("找不到歌曲；如果是 Spotify，節點必須安裝支援 Spotify 的 LavaSrc 外掛。", ephemeral=not public)
                return
            is_url = bool(re.match(r"^https?://", query, re.IGNORECASE))
            is_playlist = is_url and ("list=" in query or "playlist" in query.lower())
            if not is_url:
                view = MusicSearchView(self, tracks, interaction.user.id)
                display_tracks = tracks[:25]
                names = "\n".join(
                    f"{index}. **{track_title(track)}**"
                    for index, track in enumerate(display_tracks, 1)
                )
                if len(tracks) > 25:
                    names += f"\n…共找到 {len(tracks)} 首；Discord 一則訊息最多提供 25 個直接選歌按鈕。"
                await interaction.followup.send(f"🎵 找到這些，點你要的：\n{names}"[:1900], view=view, ephemeral=not public)
                return
            selected_tracks = tracks if is_playlist else tracks[:1]
            count, started = await self.enqueue_tracks(
                interaction.guild, interaction.user.voice.channel, interaction.channel_id, interaction.user.id, selected_tracks
            )
            if len(selected_tracks) > count:
                suffix = f"（佇列上限已到，只加入前 {count} 首）"
            elif is_playlist:
                suffix = f"（播放清單共加入 {count} 首）"
            else:
                suffix = ""
            session = self.sessions[interaction.guild.id]
            if started and session.current:
                await interaction.followup.send(f"🎵 開始播放：**{track_title(session.current)}**{suffix}", ephemeral=not public)
            else:
                await interaction.followup.send(f"已加入佇列：**{track_title(selected_tracks[0])}**{suffix}", ephemeral=not public)
        except OverflowError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
        except Exception as exc:
            print(f"[Music] /music play 錯誤：{type(exc).__name__}: {exc}")
            await interaction.followup.send("解析失敗，請確認 Lavalink 節點在線；Spotify 需要節點支援 LavaSrc。", ephemeral=True)

    def render_queue(self, session: MusicSession | None) -> str:
        if not session or (not session.current and not session.queue):
            return "🎵 目前沒有播放中的歌曲，待播清單也是空的。"
        lines = ["🎛️ **音樂控制台**"]
        if session.current:
            state = "（已暫停）" if getattr(session.player, "paused", False) else "（播放中）"
            lines.append(f"\n🎶 **目前播放**\n{track_title(session.current)} · {track_duration(session.current)} {state}")
        else:
            lines.append("\n🎶 **目前播放**\n沒有歌曲正在播放")
        lines.append("\n📃 **待播清單**")
        if session.queue:
            lines.extend(f"{index}. {track_title(track)} · {track_duration(track)}" for index, track in enumerate(session.queue, 1))
        else:
            lines.append("（沒有待播歌曲）")
        if len(session.queue) > 25:
            lines.append(f"下拉選單最多可操作前 25 首；其餘 {len(session.queue) - 25} 首仍保留在清單。")
        return "\n".join(lines)[:1900]

    @music.command(name=app_commands.locale_str("queue", zh_TW="控制台"), description=app_commands.locale_str("查看並控制目前音樂與待播清單", zh_TW="查看並控制目前音樂與待播清單"))
    @app_commands.describe(public=app_commands.locale_str("是否讓其他人看見這則回覆", zh_TW="是否公開顯示回覆"))
    @app_commands.rename(public=app_commands.locale_str("公開", zh_TW="公開"))
    async def queue(self, interaction: discord.Interaction, public: bool = True):
        if not interaction.guild:
            await interaction.response.send_message("請在伺服器裡使用。", ephemeral=True)
            return
        author_id = int(interaction.user.id)
        session = self.sessions.get(interaction.guild.id)
        await interaction.response.send_message(
            self.render_queue(session),
            view=MusicQueueView(self, interaction.guild.id, author_id),
            ephemeral=not public,
        )

    @music.command(name=app_commands.locale_str("pause", zh_TW="暫停"), description=app_commands.locale_str("暫停目前歌曲", zh_TW="暫停目前歌曲"))
    @app_commands.describe(public=app_commands.locale_str("是否讓其他人看見這則回覆", zh_TW="是否公開顯示回覆"))
    @app_commands.rename(public=app_commands.locale_str("公開", zh_TW="公開"))
    async def pause(self, interaction: discord.Interaction, public: bool = True):
        session = self.sessions.get(interaction.guild.id) if interaction.guild else None
        if not session or not session.player.playing:
            await interaction.response.send_message("現在沒有正在播放的歌。", ephemeral=True)
            return
        await session.player.pause(True)
        await interaction.response.send_message("已暫停。", ephemeral=not public)

    @music.command(name=app_commands.locale_str("resume", zh_TW="繼續"), description=app_commands.locale_str("繼續播放歌曲", zh_TW="繼續播放歌曲"))
    @app_commands.describe(public=app_commands.locale_str("是否讓其他人看見這則回覆", zh_TW="是否公開顯示回覆"))
    @app_commands.rename(public=app_commands.locale_str("公開", zh_TW="公開"))
    async def resume(self, interaction: discord.Interaction, public: bool = True):
        session = self.sessions.get(interaction.guild.id) if interaction.guild else None
        if not session or not session.player.paused:
            await interaction.response.send_message("現在沒有暫停中的歌。", ephemeral=True)
            return
        await session.player.pause(False)
        await interaction.response.send_message("繼續播放。", ephemeral=not public)

    @music.command(name=app_commands.locale_str("skip", zh_TW="跳過"), description=app_commands.locale_str("跳到下一首", zh_TW="跳到下一首"))
    @app_commands.describe(public=app_commands.locale_str("是否讓其他人看見這則回覆", zh_TW="是否公開顯示回覆"))
    @app_commands.rename(public=app_commands.locale_str("公開", zh_TW="公開"))
    async def skip(self, interaction: discord.Interaction, public: bool = True):
        session = self.sessions.get(interaction.guild.id) if interaction.guild else None
        if not session or not session.current:
            await interaction.response.send_message("現在沒有歌曲可以跳過。", ephemeral=True)
            return
        session.skip_requested = True
        await session.player.stop()
        await interaction.response.send_message("好，跳到下一首。", ephemeral=not public)

    @music.command(name=app_commands.locale_str("volume", zh_TW="音量"), description=app_commands.locale_str("設定音量 0 到 100", zh_TW="設定音量 0 到 100"))
    @app_commands.describe(value="音量百分比")
    @app_commands.describe(public=app_commands.locale_str("是否讓其他人看見這則回覆", zh_TW="是否公開顯示回覆"))
    @app_commands.rename(public=app_commands.locale_str("公開", zh_TW="公開"))
    async def volume(self, interaction: discord.Interaction, value: app_commands.Range[int, 0, 100], public: bool = True):
        session = self.sessions.get(interaction.guild.id) if interaction.guild else None
        if not session:
            await interaction.response.send_message("目前沒有音樂工作階段。", ephemeral=True)
            return
        session.volume = value
        await session.player.set_volume(value)
        await interaction.response.send_message(f"音量已調整為 **{value}%**。", ephemeral=not public)

    @music.command(name=app_commands.locale_str("loop", zh_TW="循環"), description=app_commands.locale_str("設定關閉、單曲或佇列循環", zh_TW="設定關閉、單曲或佇列循環"))
    @app_commands.describe(mode="循環模式")
    @app_commands.choices(mode=[
        app_commands.Choice(name="關閉", value="off"),
        app_commands.Choice(name="單曲", value="one"),
        app_commands.Choice(name="佇列", value="queue"),
    ])
    @app_commands.describe(public=app_commands.locale_str("是否讓其他人看見這則回覆", zh_TW="是否公開顯示回覆"))
    @app_commands.rename(public=app_commands.locale_str("公開", zh_TW="公開"))
    async def loop(self, interaction: discord.Interaction, mode: app_commands.Choice[str], public: bool = True):
        session = self.sessions.get(interaction.guild.id) if interaction.guild else None
        if not session:
            await interaction.response.send_message("目前沒有音樂工作階段。", ephemeral=True)
            return
        session.loop_mode = mode.value
        labels = {"off": "關閉", "one": "單曲", "queue": "佇列"}
        await interaction.response.send_message(f"循環模式已設定為 **{labels[mode.value]}**。", ephemeral=not public)

    @music.command(name=app_commands.locale_str("shuffle", zh_TW="隨機播放"), description=app_commands.locale_str("打亂目前等待中的歌曲", zh_TW="打亂目前等待中的歌曲"))
    @app_commands.describe(public=app_commands.locale_str("是否讓其他人看見這則回覆", zh_TW="是否公開顯示回覆"))
    @app_commands.rename(public=app_commands.locale_str("公開", zh_TW="公開"))
    async def shuffle(self, interaction: discord.Interaction, public: bool = True):
        session = self.sessions.get(interaction.guild.id) if interaction.guild else None
        if not session or len(session.queue) < 2:
            await interaction.response.send_message("等待佇列至少要有兩首歌才能洗牌。", ephemeral=True)
            return
        random.shuffle(session.queue)
        await interaction.response.send_message(f"已打亂 {len(session.queue)} 首等待中的歌曲。", ephemeral=not public)

    @commands.command(name="shuffle")
    async def prefix_shuffle(self, ctx: commands.Context):
        if not ctx.guild:
            await ctx.reply("請在伺服器裡使用。", mention_author=False)
            return
        session = self.sessions.get(ctx.guild.id)
        if not session or len(session.queue) < 2:
            await ctx.reply("等待佇列至少要有兩首歌才能洗牌。", mention_author=False)
            return
        random.shuffle(session.queue)
        await ctx.reply(f"已打亂 {len(session.queue)} 首等待中的歌曲。", mention_author=False)

    @app_commands.command(name=app_commands.locale_str("join_voice", zh_TW="加入語音"), description=app_commands.locale_str("加入你目前所在的語音頻道", zh_TW="加入你目前所在的語音頻道"))
    @app_commands.describe(public=app_commands.locale_str("是否讓其他人看見這則回覆", zh_TW="是否公開顯示回覆"))
    @app_commands.rename(public=app_commands.locale_str("公開", zh_TW="公開"))
    async def join_voice(self, interaction: discord.Interaction, public: bool = True):
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not interaction.user.voice:
            await interaction.response.send_message("你先加入一個語音頻道。", ephemeral=True)
            return
        try:
            await self.ensure_player(interaction.guild, interaction.user.voice.channel, interaction.channel_id)
            await interaction.response.send_message(f"我加入了 {interaction.user.voice.channel.mention}。", ephemeral=not public)
        except Exception as exc:
            print(f"[Music] 加入語音失敗：{type(exc).__name__}: {exc}")
            await interaction.response.send_message("加入語音失敗，請確認節點和 Bot 的語音權限。", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(MusicCog(bot))
