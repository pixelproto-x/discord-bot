import unittest

from process_safety import command_line_targets_script


class ProcessSafetyTests(unittest.TestCase):
    def test_matches_the_exact_project_script_path(self):
        target = r"C:\Users\tester\discord-bot\bot.py"
        self.assertTrue(
            command_line_targets_script(
                r'python "C:\Users\tester\discord-bot\bot.py"',
                target,
            )
        )

    def test_does_not_match_only_a_basename_or_another_project(self):
        target = r"C:\Users\tester\discord-bot\bot.py"
        for command_line in (
            "python bot.py",
            r"python C:\OtherProject\bot.py",
            r"python C:\Users\tester\discord-bot\bot.py.evil",
            None,
            "",
        ):
            with self.subTest(command_line=command_line):
                self.assertFalse(command_line_targets_script(command_line, target))


if __name__ == "__main__":
    unittest.main()
