import unittest
from unittest.mock import AsyncMock

from cogs.browser_mic import BrowserMicAudioSource, BrowserMicCog
from web_dashboard import _serve_browser_mic_websocket


class BrowserMicWebSocketCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_websocket_upgrade_still_stops_microphone_stream(self):
        class FailedUpgradeSocket:
            async def prepare(self, request):
                raise RuntimeError("simulated websocket upgrade failure")

        speech = type("Speech", (), {})()
        speech.stop_stream = AsyncMock(return_value=True)

        with self.assertRaisesRegex(RuntimeError, "simulated websocket upgrade failure"):
            await _serve_browser_mic_websocket(
                object(),
                speech,
                123,
                FailedUpgradeSocket(),
            )

        speech.stop_stream.assert_awaited_once_with(123)


class BrowserMicAudioSourceTests(unittest.TestCase):
    def test_mono_frame_is_duplicated_to_stereo(self):
        source = BrowserMicAudioSource()
        try:
            mono = b"".join((sample).to_bytes(2, "little", signed=True) for sample in (1, -2, 300))
            mono = mono + bytes(BrowserMicAudioSource.MONO_BYTES_PER_FRAME - len(mono))
            source.push(mono)
            stereo = source.read()
            self.assertEqual(len(stereo), BrowserMicAudioSource.STEREO_BYTES_PER_FRAME)
            self.assertEqual(stereo[:12], b"".join(
                (sample).to_bytes(2, "little", signed=True) * 2
                for sample in (1, -2, 300)
            ))
        finally:
            source.cleanup()

    def test_invalid_frame_is_ignored_and_read_returns_silence(self):
        source = BrowserMicAudioSource()
        try:
            source.push(b"too short")
            self.assertEqual(source.read(), BrowserMicAudioSource.SILENCE)
        finally:
            source.cleanup()

    def test_ticket_is_single_use_and_expires(self):
        cog = BrowserMicCog(bot=None)
        ticket = cog.issue_ticket(123)
        self.assertEqual(cog.consume_ticket(ticket), 123)
        self.assertIsNone(cog.consume_ticket(ticket))
        self.assertIsNone(cog.consume_ticket("not-a-ticket"))


if __name__ == "__main__":
    unittest.main()
