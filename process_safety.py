"""Safe matching helpers for deciding whether a process belongs to this project."""
import os
import re


def command_line_targets_script(command_line: str | None, script_path: str) -> bool:
    """Match a full script path as a command-line token, not just a common basename."""
    if not isinstance(command_line, str) or not command_line.strip() or not script_path:
        return False
    target = os.path.normpath(script_path).replace("/", "\\")
    command = command_line.replace("/", "\\")
    pattern = r'(?i)(?<![\w.-])"?' + re.escape(target) + r'"?(?![\w.-])'
    return re.search(pattern, command) is not None
