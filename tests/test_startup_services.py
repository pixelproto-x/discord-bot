import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from startup_services import find_freemind_ai_project, freemind_ai_launch_command


def make_project(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "run.bat").write_text("@echo off\necho test\n", encoding="utf-8")
    app = root / "freemind"
    app.mkdir(exist_ok=True)
    (app / "main.py").write_text("app = None\n", encoding="utf-8")
    return root.resolve()


class FreeMindAILaunchCommandTests(unittest.TestCase):
    def test_launch_command_keeps_spaced_path_as_a_separate_argument(self):
        project = Path(r"C:\Users\Test User\OneDrive\Desktop\FreeMindAI-main (3)\FreeMindAI-main")
        command = freemind_ai_launch_command(project)
        self.assertEqual(command[:4], ["cmd.exe", "/d", "/k", "call"])
        self.assertEqual(command[4], str(project / "run.bat"))
        self.assertNotIn('"', command[4])


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
            (incomplete / "run.bat").write_text("@echo off\n", encoding="utf-8")
            with patch.dict(os.environ, {"FREEMIND_AI_PROJECT_DIR": ""}):
                found = find_freemind_ai_project(home / "discord-bot", home_dir=home)
            self.assertIsNone(found)


if __name__ == "__main__":
    unittest.main()
