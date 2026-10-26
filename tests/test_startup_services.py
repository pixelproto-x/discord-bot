import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from startup_services import find_freemind_ai_project, freemind_ai_launcher_running, launch_freemind_ai


def make_project(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "run.bat").write_text("@echo off\r\necho test\r\n", encoding="utf-8")
    app = root / "freemind"
    app.mkdir(exist_ok=True)
    (app / "main.py").write_text("app = None\n", encoding="utf-8")
    return root.resolve()


class FreeMindAILaunchCommandTests(unittest.TestCase):
    def test_launch_uses_windows_shell_execute_without_cmd_quoting(self):
        with tempfile.TemporaryDirectory(prefix="FreeMind AI (3) ") as temp:
            project = Path(temp)
            launcher = project / "run.bat"
            launcher.write_text("@echo off\r\necho test\r\n", encoding="utf-8")
            with patch("startup_services.os.name", "nt"), patch(
                "startup_services.os.startfile", create=True
            ) as startfile:
                launch_freemind_ai(launcher)
            startfile.assert_called_once_with(
                str(launcher.resolve()), "open", cwd=str(project.resolve())
            )

    def test_detects_active_freemindai_batch_launch(self):
        result = type("Result", (), {
            "stdout": "123\tcmd.exe\tC:/Users/Test/FreeMindAI-main (3)/FreeMindAI-main/run.bat",
            "returncode": 0,
        })()
        with patch("startup_services.os.name", "nt"), patch(
            "startup_services.subprocess.run", return_value=result
        ):
            self.assertTrue(freemind_ai_launcher_running())

    def test_ignores_old_idle_cmd_k_window(self):
        result = type("Result", (), {
            "stdout": "123\tcmd.exe\tcmd.exe /d /k call C:/Users/Test/FreeMindAI-main/run.bat",
            "returncode": 0,
        })()
        with patch("startup_services.os.name", "nt"), patch(
            "startup_services.subprocess.run", return_value=result
        ):
            self.assertFalse(freemind_ai_launcher_running())


class FindFreeMindAIProjectTests(unittest.TestCase):
    def test_explicit_project_path_takes_priority(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            expected = make_project(base / "configured-ai")
            other = make_project(base / "Downloads" / "FreeMindAI-main")
            with patch.dict(os.environ, {"FREEMIND_AI_PROJECT_DIR": str(expected)}):
                found = find_freemind_ai_project(base / "discord-bot", home_dir=base)
            self.assertEqual(found, expected)
            self.assertNotEqual(found, other)

    def test_discovers_nested_windows_zip_extraction_in_downloads(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            expected = make_project(
                home / "Downloads" / "FreeMindAI-main (3)" / "FreeMindAI-main"
            )
            with patch.dict(os.environ, {"FREEMIND_AI_PROJECT_DIR": ""}):
                found = find_freemind_ai_project(
                    home / "OneDrive" / "Desktop" / "discord-bot",
                    home_dir=home,
                )
            self.assertEqual(found, expected)

    def test_prefers_downloads_when_duplicate_desktop_checkout_exists(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            expected = make_project(home / "Downloads" / "FreeMindAI-main (3)" / "FreeMindAI-main")
            stale = make_project(home / "OneDrive" / "Desktop" / "FreeMindAI-main (3)" / "FreeMindAI-main")
            with patch.dict(os.environ, {"FREEMIND_AI_PROJECT_DIR": ""}):
                found = find_freemind_ai_project(
                    home / "OneDrive" / "Desktop" / "discord-bot",
                    home_dir=home,
                )
            self.assertEqual(found, expected)
            self.assertNotEqual(found, stale)

    def test_discovers_normal_sibling_project_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            expected = make_project(base / "FreeMindAI-main")
            with patch.dict(os.environ, {"FREEMIND_AI_PROJECT_DIR": ""}):
                found = find_freemind_ai_project(base / "discord-bot", home_dir=base)
            self.assertEqual(found, expected)

    def test_ignores_incomplete_folder_that_only_matches_name(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            incomplete = home / "Downloads" / "FreeMindAI-main (2)"
            incomplete.mkdir(parents=True)
            (incomplete / "run.bat").write_text("@echo off\r\n", encoding="utf-8")
            with patch.dict(os.environ, {"FREEMIND_AI_PROJECT_DIR": ""}):
                found = find_freemind_ai_project(home / "discord-bot", home_dir=home)
            self.assertIsNone(found)


if __name__ == "__main__":
    unittest.main()
