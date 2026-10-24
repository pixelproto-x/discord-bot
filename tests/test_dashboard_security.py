import unittest
from types import SimpleNamespace

from web_dashboard import _apply_dashboard_security_headers, _is_allowed_websocket_origin, _is_same_origin


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
        self.assertNotIn("Strict-Transport-Security", response.headers)

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


if __name__ == "__main__":
    unittest.main()
