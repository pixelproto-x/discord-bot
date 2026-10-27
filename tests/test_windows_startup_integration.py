import os
import tempfile
import time
import unittest
from pathlib import Path

from startup_services import launch_freemind_ai


@unittest.skipUnless(os.name == "nt", "requires Windows ShellExecute and CMD")
class WindowsFreeMindAILaunchIntegrationTests(unittest.TestCase):
    def test_batch_launch_succeeds_from_path_with_spaces_and_parentheses(self):
        with tempfile.TemporaryDirectory(prefix="FreeMindAI-main (3) ") as temp:
            project = Path(temp)
            marker = project / "launch-ok.txt"
            launcher = project / "run.bat"
            launcher.write_text(
                '@echo off\r\necho launch-ok>"%~dp0launch-ok.txt"\r\nexit\r\n',
                encoding="utf-8",
            )

            launch_freemind_ai(launcher)

            deadline = time.monotonic() + 12
            while time.monotonic() < deadline and not marker.is_file():
                time.sleep(0.1)

            self.assertTrue(marker.is_file(), "Windows did not execute the batch file.")
            self.assertEqual(marker.read_text(encoding="utf-8").strip(), "launch-ok")


if __name__ == "__main__":
    unittest.main()
