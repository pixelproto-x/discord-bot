import unittest
from types import SimpleNamespace

from web_dashboard import _apply_dashboard_security_headers


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


if __name__ == "__main__":
    unittest.main()
