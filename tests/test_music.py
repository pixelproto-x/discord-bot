import asyncio
import io
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import AsyncMock, patch

import wavelink
from cogs import music
from cogs.music import (
    MAX_QUERY_LENGTH,
    normalize_query,
    track_duration,
    track_title,
    track_url,
)


class MusicInputValidationTests(unittest.TestCase):
    def test_plain_search_is_trimmed(self):
        self.assertEqual(normalize_query("  song title  "), ("song title", None))

    def test_youtube_and_spotify_urls_are_accepted(self):
        for url in (
            "https://www.youtube.com/watch?v=example",
            "https://youtu.be/example",
            "https://open.spotify.com/track/example",
        ):
            with self.subTest(url=url):
                self.assertEqual(normalize_query(url), (url, None))

    def test_empty_and_long_queries_are_rejected(self):
        self.assertIsNotNone(normalize_query("   ")[1])
        self.assertIsNotNone(normalize_query("x" * (MAX_QUERY_LENGTH + 1))[1])

    def test_unsafe_urls_are_rejected(self):
        for url in (
            "https://example.com/watch",
            "https://youtube.com:8443/watch",
            "https://youtube.com.evil.example/watch",
            "https://user:pass@youtube.com/watch",
        ):
            with self.subTest(url=url):
                self.assertIsNotNone(normalize_query(url)[1])


class MusicTrackDisplayTests(unittest.TestCase):
    def test_track_metadata_has_safe_fallbacks(self):
        class Track:
            title = "Test song"
            uri = "https://example.com/song"
            length = 125000

        track = Track()
        self.assertEqual(track_title(track), "Test song")
        self.assertEqual(track_url(track), "https://example.com/song")
        self.assertEqual(track_duration(track), "2:05")

    def test_missing_track_metadata_has_safe_fallbacks(self):
        track = object()
        self.assertEqual(track_title(track), "未知歌曲")
        self.assertEqual(track_url(track), "")
        self.assertEqual(track_duration(track), "直播／未知長度")


class MusicNodeStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_nodes_are_bounded_and_do_not_block_a_healthy_node(self):
        class FakeNode:
            def __init__(self, *, identifier, **kwargs):
                self.identifier = identifier
                self.status = wavelink.NodeStatus.CONNECTING
                self._session = None
                self.close = AsyncMock()

        created_nodes = []

        def make_node(**kwargs):
            node = FakeNode(**kwargs)
            created_nodes.append(node)
            return node

        async def connect(*, nodes, client):
            node = next(iter(nodes))
            if node.identifier == "offline":
                await asyncio.Future()

        cog = music.MusicCog(object())
        output = io.StringIO()
        started = time.monotonic()
        with (
            patch.object(music, "LAVALINK_NODES", (
                {"identifier": "offline", "uri": "http://offline.invalid:2333", "password": "x"},
                {"identifier": "healthy", "uri": "http://healthy.invalid:2333", "password": "x"},
            )),
            patch.object(music, "LAVALINK_CONNECT_TIMEOUT_SECONDS", 0.05),
            patch.object(music.wavelink, "Node", side_effect=make_node),
            patch.object(music.wavelink.Pool, "connect", side_effect=connect) as connect_mock,
            redirect_stdout(output),
        ):
            await cog.setup_nodes()

        self.assertLess(time.monotonic() - started, 0.2)
        self.assertEqual(connect_mock.await_count, 2)
        self.assertIsNone(cog.node_error)
        self.assertEqual(created_nodes[0].close.await_count, 1)
        self.assertEqual(created_nodes[1].close.await_count, 0)
        self.assertIn("1/2", output.getvalue())




class MusicLocalAudioHandoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_music_resumes_at_saved_position_and_keeps_queue(self):
        from types import SimpleNamespace

        song = SimpleNamespace(title="Current song")
        queued_song = SimpleNamespace(title="Queued song")
        music_channel = SimpleNamespace()
        local_channel = SimpleNamespace()

        class FakePlayer:
            def __init__(self, channel):
                self.channel = channel
                self.current = song
                self.position = 42000
                self.paused = False
                self.volume = 77
                self.connected = True
                self.set_volume = AsyncMock()
                self.play = AsyncMock()
                self.disconnect = AsyncMock()

        old_player = FakePlayer(music_channel)
        restored_player = FakePlayer(music_channel)
        guild = SimpleNamespace(id=123, voice_client=old_player)

        async def disconnect_old(**kwargs):
            guild.voice_client = None

        old_player.disconnect.side_effect = disconnect_old
        music_channel.connect = AsyncMock(return_value=restored_player)
        bot = SimpleNamespace(get_guild=lambda guild_id: guild)
        cog = music.MusicCog(bot)
        session = music.MusicSession(
            player=old_player,
            queue=[queued_song],
            current=song,
            volume=77,
            loop_mode="queue",
        )
        cog.sessions[guild.id] = session

        with patch.object(music.wavelink, "Player", FakePlayer):
            self.assertTrue(await cog.suspend_for_local_audio(guild, local_channel))
            self.assertTrue(session.handoff)
            self.assertIs(session.current, song)
            self.assertEqual(session.queue, [queued_song])
            old_player.disconnect.assert_awaited_once_with(force=True)

            self.assertTrue(await cog.resume_after_local_audio(guild.id))

        self.assertFalse(session.handoff)
        self.assertIs(session.player, restored_player)
        self.assertIs(session.current, song)
        self.assertEqual(session.queue, [queued_song])
        restored_player.set_volume.assert_awaited_once_with(77)
        restored_player.play.assert_awaited_once_with(song, start=42000, paused=False)
        music_channel.connect.assert_awaited_once_with(cls=type(restored_player), self_deaf=False)
        self.assertNotIn(guild.id, cog.local_audio_handoffs)

    async def test_new_music_connection_does_not_self_deafen(self):
        from types import SimpleNamespace

        channel = SimpleNamespace()

        class FakePlayer:
            def __init__(self):
                self.channel = channel
                self.set_volume = AsyncMock()

        player = FakePlayer()
        channel.connect = AsyncMock(return_value=player)
        guild = SimpleNamespace(id=321, voice_client=None)
        cog = music.MusicCog(SimpleNamespace())

        with patch.object(music.wavelink, "Player", FakePlayer):
            session = await cog.ensure_player(guild, channel, text_channel_id=654)

        channel.connect.assert_awaited_once_with(cls=FakePlayer, self_deaf=False)
        self.assertIs(session.player, player)
        self.assertEqual(session.text_channel_id, 654)


    async def test_track_end_during_handoff_does_not_consume_queue(self):
        from types import SimpleNamespace

        song = SimpleNamespace(title="Current song")
        queued_song = SimpleNamespace(title="Queued song")
        channel = SimpleNamespace()
        player = SimpleNamespace(guild=SimpleNamespace(id=456), play=AsyncMock())
        cog = music.MusicCog(SimpleNamespace(get_guild=lambda guild_id: None))
        session = music.MusicSession(player=player, queue=[queued_song], current=song, handoff=True)
        cog.sessions[456] = session

        payload = SimpleNamespace(player=player, track=song, reason="FINISHED")
        await cog.on_wavelink_track_end(payload)

        self.assertIs(session.current, song)
        self.assertEqual(session.queue, [queued_song])
        player.play.assert_not_awaited()



    async def test_stop_during_handoff_disconnects_native_voice_client(self):
        from types import SimpleNamespace

        song = SimpleNamespace(title="Current song")
        old_lavalink_player = SimpleNamespace(disconnect=AsyncMock())
        native_voice_client = SimpleNamespace(connected=True, disconnect=AsyncMock())
        guild = SimpleNamespace(id=789, voice_client=native_voice_client)
        bot = SimpleNamespace(
            get_guild=lambda guild_id: guild,
            get_cog=lambda name: None,
        )
        cog = music.MusicCog(bot)
        session = music.MusicSession(player=old_lavalink_player, current=song, handoff=True)
        cog.sessions[guild.id] = session
        cog.local_audio_handoffs[guild.id] = {"session": session, "track": song}

        await cog.stop_player(guild.id)

        native_voice_client.disconnect.assert_awaited_once_with(force=True)
        old_lavalink_player.disconnect.assert_not_awaited()
        self.assertNotIn(guild.id, cog.local_audio_handoffs)
        self.assertNotIn(guild.id, cog.sessions)


class MusicTrackFailureRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_youtube_load_failure_uses_soundcloud_fallback(self):
        from types import SimpleNamespace

        song = SimpleNamespace(title="Test Track", author="Test Artist", source="youtube",
                               uri="https://www.youtube.com/watch?v=example", extras={"requester_id": "7"})
        fallback = SimpleNamespace(title="Test Track", author="Test Artist", source="soundcloud",
                                   uri="https://soundcloud.com/example/test-track", extras={})
        queued = SimpleNamespace(title="Next Song", source="youtube")
        node = SimpleNamespace(identifier="healthy", status=wavelink.NodeStatus.CONNECTED)
        player = SimpleNamespace(guild=SimpleNamespace(id=9001), play=AsyncMock())
        cog = music.MusicCog(SimpleNamespace(get_channel=lambda _channel_id: None))
        session = music.MusicSession(player=player, queue=[queued], current=song)
        cog.sessions[9001] = session
        payload = SimpleNamespace(player=player, track=song, original=song, reason="LOAD_FAILED")

        with (
            patch.object(wavelink.Pool, "nodes", {"healthy": node}),
            patch.object(music.wavelink.Playable, "search", new=AsyncMock(return_value=[fallback])) as search,
        ):
            await cog.on_wavelink_track_end(payload)

        search.assert_awaited_once_with("scsearch:Test Track Test Artist", source=None, node=node)
        player.play.assert_awaited_once_with(fallback)
        self.assertIs(session.current, fallback)
        self.assertEqual(session.queue, [queued])
        self.assertEqual(fallback.extras["requester_id"], "7")
        self.assertEqual(fallback.extras["fallback_from"], "youtube")
    async def test_failed_youtube_song_is_skipped_when_no_soundcloud_match_exists(self):
        from types import SimpleNamespace

        song = SimpleNamespace(title="Unavailable Track", author="Test Artist", source="youtube",
                               uri="https://www.youtube.com/watch?v=unavailable", extras={})
        queued = SimpleNamespace(title="Next Song", source="youtube")
        node = SimpleNamespace(identifier="healthy", status=wavelink.NodeStatus.CONNECTED)
        player = SimpleNamespace(guild=SimpleNamespace(id=9002), play=AsyncMock())
        cog = music.MusicCog(SimpleNamespace(get_channel=lambda _channel_id: None))
        session = music.MusicSession(player=player, queue=[queued], current=song)
        cog.sessions[9002] = session
        payload = SimpleNamespace(player=player, track=song, original=song, reason="LOAD_FAILED")

        with (
            patch.object(wavelink.Pool, "nodes", {"healthy": node}),
            patch.object(music.wavelink.Playable, "search", new=AsyncMock(return_value=[])) as search,
        ):
            await cog.on_wavelink_track_end(payload)

        search.assert_awaited_once_with("scsearch:Unavailable Track Test Artist", source=None, node=node)
        player.play.assert_awaited_once_with(queued)
        self.assertIs(session.current, queued)
        self.assertEqual(session.queue, [])
    async def test_track_exception_event_triggers_soundcloud_fallback(self):
        from types import SimpleNamespace

        song = SimpleNamespace(title="Track Exception Song", author="Track Artist", source="youtube",
                               uri="https://www.youtube.com/watch?v=track-exception", extras={"requester_id": "41"})
        fallback = SimpleNamespace(title="Track Exception Song", author="Track Artist", source="soundcloud",
                                   uri="https://soundcloud.com/example/track-exception", extras={})
        queued = SimpleNamespace(title="Queue Song", source="youtube")
        node = SimpleNamespace(identifier="healthy", status=wavelink.NodeStatus.CONNECTED)
        player = SimpleNamespace(guild=SimpleNamespace(id=9004), play=AsyncMock())
        cog = music.MusicCog(SimpleNamespace(get_channel=lambda _channel_id: None))
        session = music.MusicSession(player=player, queue=[queued], current=song)
        cog.sessions[9004] = session
        payload = SimpleNamespace(player=player, track=song,
                                  exception=SimpleNamespace(message="No supported audio streams available"))

        with (
            patch.object(wavelink.Pool, "nodes", {"healthy": node}),
            patch.object(music.wavelink.Playable, "search", new=AsyncMock(return_value=[fallback])) as search,
        ):
            await cog.on_wavelink_track_exception(payload)
            await cog.on_wavelink_track_end(
                SimpleNamespace(player=player, track=song, original=song, reason="LOAD_FAILED")
            )

        search.assert_awaited_once_with("scsearch:Track Exception Song Track Artist", source=None, node=node)
        player.play.assert_awaited_once_with(fallback)
        self.assertIs(session.current, fallback)
        self.assertEqual(session.queue, [queued])
    async def test_track_exception_without_fallback_advances_queue(self):
        from types import SimpleNamespace

        song = SimpleNamespace(title="Unavailable Exception Song", author="Track Artist", source="youtube",
                               uri="https://www.youtube.com/watch?v=unavailable-exception", extras={})
        queued = SimpleNamespace(title="Next Queue Song", source="youtube")
        node = SimpleNamespace(identifier="healthy", status=wavelink.NodeStatus.CONNECTED)
        player = SimpleNamespace(guild=SimpleNamespace(id=9005), play=AsyncMock())
        cog = music.MusicCog(SimpleNamespace(get_channel=lambda _channel_id: None))
        session = music.MusicSession(player=player, queue=[queued], current=song)
        cog.sessions[9005] = session
        payload = SimpleNamespace(player=player, track=song,
                                  exception=SimpleNamespace(message="This video requires login"))

        with (
            patch.object(wavelink.Pool, "nodes", {"healthy": node}),
            patch.object(music.wavelink.Playable, "search", new=AsyncMock(return_value=[])) as search,
        ):
            await cog.on_wavelink_track_exception(payload)

        search.assert_awaited_once_with("scsearch:Unavailable Exception Song Track Artist", source=None, node=node)
        player.play.assert_awaited_once_with(queued)
        self.assertIs(session.current, queued)
        self.assertEqual(session.queue, [])
    async def test_non_youtube_failure_skips_to_queue_without_soundcloud_search(self):
        from types import SimpleNamespace

        song = SimpleNamespace(
            title="Broken SoundCloud Track",
            author="Test Artist",
            source="soundcloud",
            uri="https://soundcloud.com/example/broken",
            extras={},
        )
        queued = SimpleNamespace(title="Next Song", source="youtube")
        player = SimpleNamespace(guild=SimpleNamespace(id=9003), play=AsyncMock())
        cog = music.MusicCog(SimpleNamespace(get_channel=lambda _channel_id: None))
        session = music.MusicSession(player=player, queue=[queued], current=song)
        cog.sessions[9003] = session
        payload = SimpleNamespace(player=player, track=song, original=song, reason="LOAD_FAILED")

        with patch.object(
            music.wavelink.Playable,
            "search",
            new=AsyncMock(),
        ) as search:
            await cog.on_wavelink_track_end(payload)

        search.assert_not_awaited()
        player.play.assert_awaited_once_with(queued)
        self.assertIs(session.current, queued)
        self.assertEqual(session.queue, [])

class MusicSearchFallbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from types import SimpleNamespace

        self.nodes = {
            "limited": SimpleNamespace(identifier="limited", status=wavelink.NodeStatus.CONNECTED),
            "healthy": SimpleNamespace(identifier="healthy", status=wavelink.NodeStatus.CONNECTED),
        }
        self.cog = music.MusicCog(object())

    async def test_search_tries_another_connected_node_when_youtube_returns_empty(self):
        from types import SimpleNamespace

        track = SimpleNamespace(title="Found Track", source="youtube")
        calls = []

        async def search(identifier, *, source=None, node=None):
            calls.append((identifier, node.identifier, source))
            if identifier == "ytsearch:artist track" and node.identifier == "limited":
                return []
            if identifier == "ytsearch:artist track" and node.identifier == "healthy":
                await asyncio.sleep(0.01)
                return [track]
            return []

        with (
            patch.object(wavelink.Pool, "nodes", self.nodes),
            patch.object(music.wavelink.Playable, "search", new=AsyncMock(side_effect=search)),
        ):
            result = await self.cog.resolve_query("artist track")

        self.assertEqual(result, [track])
        self.assertIn(("ytsearch:artist track", "limited", None), calls)
        self.assertIn(("ytsearch:artist track", "healthy", None), calls)
        self.assertFalse(any(identifier.startswith("scsearch:") for identifier, _, _ in calls))

    async def test_soundcloud_is_searched_when_all_youtube_nodes_return_empty(self):
        from types import SimpleNamespace

        track = SimpleNamespace(title="SoundCloud Track", source="soundcloud")
        calls = []

        async def search(identifier, *, source=None, node=None):
            calls.append((identifier, node.identifier, source))
            if identifier.startswith("scsearch:") and node.identifier == "healthy":
                await asyncio.sleep(0.01)
                return [track]
            return []

        with (
            patch.object(wavelink.Pool, "nodes", self.nodes),
            patch.object(music.wavelink.Playable, "search", new=AsyncMock(side_effect=search)),
        ):
            result = await self.cog.resolve_query("artist track")

        self.assertEqual(result, [track])
        self.assertIn(("ytsearch:artist track", "limited", None), calls)
        self.assertIn(("ytsearch:artist track", "healthy", None), calls)
        self.assertIn(("scsearch:artist track", "healthy", None), calls)

    async def test_url_search_does_not_change_into_keyword_or_soundcloud_search(self):
        from types import SimpleNamespace

        url = "https://www.youtube.com/watch?v=example"
        track = SimpleNamespace(title="URL Track", source="youtube")
        calls = []

        async def search(identifier, *, source=None, node=None):
            calls.append((identifier, node.identifier, source))
            if identifier == url and node.identifier == "healthy":
                await asyncio.sleep(0.01)
                return [track]
            return []

        with (
            patch.object(wavelink.Pool, "nodes", self.nodes),
            patch.object(music.wavelink.Playable, "search", new=AsyncMock(side_effect=search)),
        ):
            result = await self.cog.resolve_query(url)

        self.assertEqual(result, [track])
        self.assertTrue(calls)
        self.assertTrue(all(identifier == url and source is None for identifier, _, source in calls))

    async def test_search_fails_clearly_when_no_lavalink_node_is_connected(self):
        with patch.object(wavelink.Pool, "nodes", {}):
            with self.assertRaisesRegex(RuntimeError, "沒有已連線的 Lavalink"):
                await self.cog.resolve_query("artist track")

    async def test_youtube_prefix_reaches_lavalink_without_wavelink_auto_prefix(self):
        from types import SimpleNamespace

        node = SimpleNamespace(identifier="only-node", status=wavelink.NodeStatus.CONNECTED)
        track = SimpleNamespace(title="Found Track", source="youtube")
        calls = []

        async def fetch_tracks(query, /, *, node=None):
            calls.append((query, node))
            return [track] if query == "ytsearch:artist track" else []

        with (
            patch.object(wavelink.Pool, "nodes", {"only-node": node}),
            patch.object(wavelink.Pool, "fetch_tracks", new=AsyncMock(side_effect=fetch_tracks)),
        ):
            result = await self.cog.resolve_query("artist track")

        self.assertEqual(result, [track])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], ("ytsearch:artist track", node))

    async def test_soundcloud_prefix_reaches_lavalink_without_wavelink_auto_prefix(self):
        from types import SimpleNamespace

        node = SimpleNamespace(identifier="only-node", status=wavelink.NodeStatus.CONNECTED)
        track = SimpleNamespace(title="SoundCloud Track", source="soundcloud")
        calls = []

        async def fetch_tracks(query, /, *, node=None):
            calls.append((query, node))
            if query == "scsearch:artist track":
                return [track]
            return []

        with (
            patch.object(wavelink.Pool, "nodes", {"only-node": node}),
            patch.object(wavelink.Pool, "fetch_tracks", new=AsyncMock(side_effect=fetch_tracks)),
        ):
            result = await self.cog.resolve_query("artist track")

        self.assertEqual(result, [track])
        self.assertEqual(
            [query for query, _ in calls],
            [
                "ytsearch:artist track",
                "ytmsearch:artist track",
                "scsearch:artist track",
            ],
        )
        self.assertTrue(all(selected_node is node for _, selected_node in calls))


    async def test_track_failure_soundcloud_fallback_reaches_lavalink_with_exact_prefix(self):
        from types import SimpleNamespace

        failed_track = SimpleNamespace(title="Artist Track", author="Band", source="youtube")
        fallback = SimpleNamespace(title="Artist Track", author="Band", source="soundcloud")
        node = SimpleNamespace(identifier="only-node", status=wavelink.NodeStatus.CONNECTED)
        self.cog = music.MusicCog(SimpleNamespace())
        with (
            patch.object(wavelink.Pool, "nodes", {"only-node": node}),
            patch.object(music.wavelink.Playable, "search", new=AsyncMock(return_value=[fallback])) as search,
        ):
            result = await self.cog._find_soundcloud_fallback(failed_track)

        self.assertIs(result, fallback)
        search.assert_awaited_once_with("scsearch:Artist Track Band", source=None, node=node)

    async def test_soundcloud_fallback_times_out_instead_of_blocking_track_recovery(self):
        from types import SimpleNamespace

        failed_track = SimpleNamespace(title="Slow Track", author="Band", source="youtube")
        node = SimpleNamespace(identifier="slow-node", status=wavelink.NodeStatus.CONNECTED)

        async def hanging_search(*args, **kwargs):
            await asyncio.sleep(10)
            return []

        cog = music.MusicCog(SimpleNamespace())
        output = io.StringIO()
        started = time.monotonic()
        with (
            patch.object(wavelink.Pool, "nodes", {"slow-node": node}),
            patch.object(music, "MUSIC_SEARCH_TIMEOUT_SECONDS", 0.02),
            patch.object(music.wavelink.Playable, "search", new=AsyncMock(side_effect=hanging_search)),
            redirect_stdout(output),
        ):
            result = await cog._find_soundcloud_fallback(failed_track)

        self.assertIsNone(result)
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertIn("TimeoutError", output.getvalue())


    async def test_stalled_searches_are_bounded_across_all_sources(self):
        async def hanging_search(*args, **kwargs):
            await asyncio.sleep(5)
            return []

        started = time.monotonic()
        with (
            patch.object(wavelink.Pool, "nodes", self.nodes),
            patch.object(music, "MUSIC_SEARCH_TIMEOUT_SECONDS", 0.02),
            patch.object(music.wavelink.Playable, "search", new=AsyncMock(side_effect=hanging_search)) as search,
            redirect_stdout(io.StringIO()),
        ):
            result = await self.cog.resolve_query("slow search")

        self.assertEqual(result, [])
        self.assertLess(time.monotonic() - started, 0.5)
        identifiers = [call.args[0] for call in search.await_args_list]
        self.assertTrue(any(item.startswith("ytsearch:") for item in identifiers))
        self.assertTrue(any(item.startswith("ytmsearch:") for item in identifiers))
        self.assertTrue(any(item.startswith("scsearch:") for item in identifiers))

    async def test_healthy_node_wins_without_waiting_for_a_stalled_node(self):
        from types import SimpleNamespace

        track = SimpleNamespace(title="Fast result", source="youtube")

        async def search(identifier, *, source=None, node=None):
            if node.identifier == "limited":
                await asyncio.sleep(5)
                return []
            return [track]

        with (
            patch.object(wavelink.Pool, "nodes", self.nodes),
            patch.object(music, "MUSIC_SEARCH_TIMEOUT_SECONDS", 0.02),
            patch.object(music.wavelink.Playable, "search", new=AsyncMock(side_effect=search)),
        ):
            started = time.monotonic()
            result = await self.cog.resolve_query("fast result")

        self.assertEqual(result, [track])
        self.assertLess(time.monotonic() - started, 0.5)

if __name__ == "__main__":
    unittest.main()
