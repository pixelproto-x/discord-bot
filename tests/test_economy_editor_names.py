import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import economy_editor_names as editor


class DiscordDisplayNameTests(unittest.TestCase):
    def test_prefers_server_nickname(self):
        with patch.object(
            editor, "_discord_get",
            return_value={"nick": "伺服器暱稱", "user": {"global_name": "全域名稱", "username": "username"}},
        ):
            self.assertEqual(editor._fetch_name(123, 456, "token"), "伺服器暱稱")

    def test_falls_back_to_global_name_when_member_lookup_fails(self):
        def fake_get(path, token):
            if path.startswith("/guilds/"):
                raise editor.urllib.error.HTTPError(path, 404, "Not Found", None, None)
            return {"id": "123", "global_name": "全域名稱", "username": "username"}

        with patch.object(editor, "_discord_get", side_effect=fake_get):
            self.assertEqual(editor._fetch_name(123, 456, "token"), "全域名稱")

    def test_falls_back_to_username_when_global_name_missing(self):
        with patch.object(editor, "_discord_get", return_value={"id": "123", "username": "username"}):
            self.assertEqual(editor._fetch_name(123, 0, "token"), "username")

    def test_editor_displays_name_and_keeps_id_only_as_internal_save_key(self):
        self.assertIn('["display_name","玩家名稱"]', editor.PAGE)
        self.assertNotIn('["user_id","Discord 使用者 ID"]', editor.PAGE)
        self.assertIn("const payload={guild_id:user.guild_id,user_id:user.user_id}", editor.PAGE)
        self.assertIn('const formId="economy-save-"+String(user.guild_id)+"-"+String(user.user_id)', editor.PAGE)

    def test_editor_automatically_refreshes_without_overwriting_unsaved_edits(self):
        self.assertIn("},2500);", editor.PAGE)
        self.assertIn("input.dataset.serverValue=input.value", editor.PAGE)
        self.assertIn("input.value!==input.dataset.serverValue", editor.PAGE)
        self.assertIn("if(response.ok)await load();", editor.PAGE)

    def test_display_name_response_keeps_guild_id_as_exact_string(self):
        with patch.object(editor, "_discord_token", return_value=""):
            row = editor._add_display_names([{"user_id": 123, "guild_id": 987654321012345678}])[0]
        self.assertEqual(row["guild_id"], "987654321012345678")

    def test_missing_token_returns_explicit_name_fallback_without_network(self):
        with patch.object(editor, "_discord_token", return_value=""):
            self.assertEqual(
                editor._add_display_names([{"user_id": 123, "guild_id": 456}])[0]["display_name"],
                "未設定 Discord Token",
            )



class EconomyEditorRequestSecurityTests(unittest.TestCase):
    def test_accepts_only_exact_loopback_editor_origin(self):
        self.assertTrue(editor._is_same_origin_post(
            "http://127.0.0.1:8766", "127.0.0.1:8766"
        ))

    def test_rejects_cross_origin_and_missing_origin_requests(self):
        for origin in (
            "https://attacker.example",
            "http://localhost:8766",
            "null",
            "",
        ):
            with self.subTest(origin=origin):
                self.assertFalse(editor._is_same_origin_post(origin, "127.0.0.1:8766"))

    def test_rejects_host_mismatch_and_origin_paths(self):
        self.assertFalse(editor._is_same_origin_post(
            "http://127.0.0.1:8766", "attacker.example"
        ))
        self.assertFalse(editor._is_same_origin_post(
            "http://127.0.0.1:8766/evil", "127.0.0.1:8766"
        ))

if __name__ == "__main__":
    unittest.main()
