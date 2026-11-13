import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import startup_services
from startup_services import find_freemind_ai_project, freemind_ai_launcher_running, launch_economy_editor, launch_freemind_ai, stop_stale_freemind_ai_launch_windows


def make_project(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "run.bat").write_text("@echo off\r\necho test\r\n", encoding="utf-8")
    # Match the actual FreeMindAI repository layout: main.py is at the project root.
    (root / "main.py").write_text("app = None\n", encoding="utf-8")
    return root.resolve()


class FreeMindAILaunchCommandTests(unittest.TestCase):
    def test_launch_starts_detached_batch_without_blocking(self):
        with tempfile.TemporaryDirectory(prefix="FreeMind AI (3) ") as temp:
            project = Path(temp)
            launcher = project / "run.bat"
            launcher.write_text("@echo off\r\necho test\r\n", encoding="utf-8")
            with patch("startup_services.os.name", "nt"), patch(
                "startup_services.subprocess.Popen"
            ) as popen:
                launch_freemind_ai(launcher)
            args, kwargs = popen.call_args
            self.assertEqual(args[0], ["cmd.exe", "/d", "/c", "call", str(launcher.resolve())])
            self.assertEqual(kwargs["cwd"], str(project.resolve()))
            self.assertIs(kwargs["stdin"], startup_services.subprocess.DEVNULL)
            self.assertIs(kwargs["stderr"], startup_services.subprocess.STDOUT)
            self.assertEqual(kwargs["creationflags"], getattr(startup_services.subprocess, "CREATE_NO_WINDOW", 0))
            self.assertTrue(kwargs["stdout"].name.endswith("freemind-ai-launch.log"))
            self.assertTrue(kwargs["close_fds"])

    def test_economy_editor_launch_is_hidden_and_logged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "economy_editor_names.py"
            script.write_text("print('started')\n", encoding="utf-8")
            with patch("startup_services.os.name", "nt"), patch(
                "startup_services.subprocess.Popen"
            ) as popen:
                launch_economy_editor(script, root)
            args, kwargs = popen.call_args
            self.assertEqual(args[0], [os.sys.executable, str(script.resolve())])
            self.assertEqual(kwargs["cwd"], str(root.resolve()))
            self.assertIs(kwargs["stdin"], startup_services.subprocess.DEVNULL)
            self.assertIs(kwargs["stderr"], startup_services.subprocess.STDOUT)
            self.assertEqual(kwargs["creationflags"], getattr(startup_services.subprocess, "CREATE_NO_WINDOW", 0))
            self.assertTrue(kwargs["stdout"].name.endswith("economy-editor.log"))
            self.assertTrue(kwargs["close_fds"])


    def test_process_snapshot_parses_windows_tab_separated_fields(self):
        output = "98765\t1\tcmd.exe\tC:/Users/Test/FreeMindAI-main (3)/run.bat\r\n"
        result = type("Result", (), {"stdout": output, "returncode": 0})()
        with patch("startup_services.os.name", "nt"), patch(
            "startup_services.subprocess.run", return_value=result
        ):
            self.assertEqual(
                startup_services._windows_process_snapshot(),
                [(98765, 1, "cmd.exe", "c:/users/test/freemindai-main (3)/run.bat")],
            )

    def test_detects_active_freemindai_batch_launch(self):
        process = (98765, 1, "cmd.exe", "C:/Users/Test/FreeMindAI-main (3)/FreeMindAI-main/run.bat")
        with patch("startup_services.os.name", "nt"), patch(
            "startup_services._windows_process_snapshot", return_value=[process]
        ):
            self.assertTrue(freemind_ai_launcher_running())

    def test_ignores_old_idle_cmd_k_window(self):
        process = (98765, 1, "cmd.exe", "cmd.exe /d /k call C:/Users/Test/FreeMindAI-main/run.bat")
        with patch("startup_services.os.name", "nt"), patch(
            "startup_services._windows_process_snapshot", return_value=[process]
        ):
            self.assertFalse(freemind_ai_launcher_running())

    def test_closes_only_orphaned_legacy_error_window(self):
        process = (98765, 1, "cmd.exe", "cmd.exe /d /k call C:/Users/Test/FreeMindAI-main/run.bat")
        result = type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        with patch("startup_services.os.name", "nt"), patch(
            "startup_services._windows_process_snapshot", return_value=[process]
        ), patch("startup_services.subprocess.run", return_value=result) as run:
            self.assertEqual(stop_stale_freemind_ai_launch_windows(), [98765])
            run.assert_called_once_with(
                ["taskkill.exe", "/PID", "98765", "/T", "/F"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=5, check=False,
            )

    def test_keeps_a_legacy_window_that_still_has_a_child_process(self):
        processes = [
            (98765, 1, "cmd.exe", "cmd.exe /d /k call C:/Users/Test/FreeMindAI-main/run.bat"),
            (98766, 98765, "powershell.exe", "powershell.exe -file C:/Users/Test/FreeMindAI-main/update.bat"),
        ]
        with patch("startup_services.os.name", "nt"), patch(
            "startup_services._windows_process_snapshot", return_value=processes
        ), patch("startup_services.subprocess.run") as run:
            self.assertEqual(stop_stale_freemind_ai_launch_windows(), [])
            run.assert_not_called()


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

    def test_accepts_actual_freemind_ai_root_main_py_layout(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            expected = make_project(home / "Downloads" / "FreeMindAI-main")
            with patch.dict(os.environ, {"FREEMIND_AI_PROJECT_DIR": str(expected)}):
                self.assertEqual(find_freemind_ai_project(home / "discord-bot", home_dir=home), expected)

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
