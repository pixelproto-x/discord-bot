import base64
import hashlib
import re
import unittest
from types import SimpleNamespace

from unittest.mock import patch

from web_dashboard import (
    SlidingWindowRateLimiter,
    _apply_dashboard_security_headers,
    _dashboard_content_security_policy,
    _is_allowed_websocket_origin,
    _is_same_origin,
)


class DashboardSecurityHeaderTests(unittest.TestCase):
    def test_default_headers_protect_responses_without_disabling_local_http(self):
        response = SimpleNamespace(headers={})

        _apply_dashboard_security_headers(response)

        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(
            response.headers["Permissions-Policy"],
            "camera=(), microphone=(self), geolocation=()",
        )
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("Content-Security-Policy", response.headers)
        self.assertIn("object-src 'none'", response.headers["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        self.assertNotIn("Strict-Transport-Security", response.headers)

    def test_csp_hash_allows_only_the_exact_dashboard_inline_script(self):
        scripts = re.findall(r"<script>(.*?)</script>", __import__("web_dashboard").PAGE, flags=re.DOTALL)
        self.assertEqual(len(scripts), 1)
        digest = base64.b64encode(hashlib.sha256(scripts[0].encode("utf-8")).digest()).decode("ascii")
        policy = _dashboard_content_security_policy()
        self.assertIn("'sha256-" + digest + "'", policy)
        script_directive = next(part for part in policy.split(";") if part.strip().startswith("script-src"))
        self.assertNotIn("'unsafe-inline'", script_directive)
        self.assertIn("base-uri 'none'", policy)

    def test_hsts_is_only_enabled_for_public_https(self):
        response = SimpleNamespace(headers={})

        _apply_dashboard_security_headers(response, public_https=True)

        self.assertEqual(
            response.headers["Strict-Transport-Security"],
            "max-age=31536000",
        )


class DashboardWebSocketOriginTests(unittest.TestCase):
    def test_accepts_dashboard_origin(self):
        self.assertTrue(_is_allowed_websocket_origin("https://control.example.com", "https://control.example.com"))

    def test_rejects_missing_null_and_foreign_origins(self):
        for origin in ("", None, "null", "https://other.example.com", "http://control.example.com"):
            with self.subTest(origin=origin):
                self.assertFalse(_is_allowed_websocket_origin(origin, "https://control.example.com"))


class DashboardOriginValidationTests(unittest.TestCase):
    def test_accepts_exact_origin(self):
        self.assertTrue(_is_same_origin("https://control.example.com", "https://control.example.com"))

    def test_origin_host_comparison_is_case_insensitive(self):
        self.assertTrue(_is_same_origin("HTTPS://CONTROL.EXAMPLE.COM", "https://control.example.com"))

    def test_rejects_missing_or_malformed_origin(self):
        for origin in ("", None, "null", "not a URL", "https://control.example.com/path"):
            with self.subTest(origin=origin):
                self.assertFalse(_is_same_origin(origin, "https://control.example.com"))

    def test_rejects_cross_origin_and_scheme_or_port_mismatch(self):
        for origin in (
            "https://evil.example.com",
            "http://control.example.com",
            "https://control.example.com:8443",
        ):
            with self.subTest(origin=origin):
                self.assertFalse(_is_same_origin(origin, "https://control.example.com"))

    def test_rejects_credentials_embedded_in_origin(self):
        self.assertFalse(
            _is_same_origin(
                "https://user@control.example.com",
                "https://control.example.com",
            )
        )


class DashboardRateLimitTests(unittest.TestCase):
    def test_limits_after_ten_failed_attempts_and_expires_old_attempts(self):
        limiter = SlidingWindowRateLimiter(limit=3, window_seconds=60)
        with patch("web_dashboard.time.monotonic", return_value=100):
            limiter.record("client")
            limiter.record("client")
            self.assertFalse(limiter.is_limited("client"))
            limiter.record("client")
            self.assertTrue(limiter.is_limited("client"))
        with patch("web_dashboard.time.monotonic", return_value=161):
            self.assertFalse(limiter.is_limited("client"))

    def test_reset_clears_attempts(self):
        limiter = SlidingWindowRateLimiter(limit=2, window_seconds=60)
        with patch("web_dashboard.time.monotonic", return_value=100):
            limiter.record("client")
            limiter.record("client")
            self.assertTrue(limiter.is_limited("client"))
            limiter.reset("client")
            self.assertFalse(limiter.is_limited("client"))

    def test_keys_are_isolated_and_memory_is_bounded(self):
        limiter = SlidingWindowRateLimiter(limit=1, window_seconds=60, max_keys=2)
        with patch("web_dashboard.time.monotonic", side_effect=[1, 2, 3, 3, 3]):
            limiter.record("first")
            limiter.record("second")
            limiter.record("third")
            self.assertLessEqual(len(limiter.attempts), 2)
            self.assertFalse(limiter.is_limited("first"))


if __name__ == "__main__":
    unittest.main()
