from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _valid_freemind_ai_project(path: Path) -> Path | None:
    """Return a FreeMindAI project root only when its launcher and app entry point exist."""
    try:
        candidate = path.expanduser()
        if candidate.is_file() and candidate.name.casefold() == "run.bat":
            candidate = candidate.parent
        candidate = candidate.resolve()
        if (
            candidate.is_dir()
            and (candidate / "run.bat").is_file()
            and (candidate / "freemind" / "main.py").is_file()
        ):
            return candidate
    except (OSError, RuntimeError):
        pass
    return None


def launch_freemind_ai(run_bat: Path) -> None:
    """Open the Windows batch file through ShellExecute, avoiding nested CMD quoting."""
    if os.name != "nt":
        raise OSError("FreeMindAI batch launch is only supported on Windows.")

    launcher = run_bat.expanduser().resolve()
    if not launcher.is_file() or launcher.suffix.casefold() != ".bat":
        raise FileNotFoundError(f"FreeMindAI launcher not found: {launcher}")

    # os.startfile delegates path parsing to Windows ShellExecute. This safely
    # handles spaces and parentheses without assembling a cmd.exe command line.
    os.startfile(str(launcher), "open", cwd=str(launcher.parent))


def freemind_ai_launcher_running() -> bool:
    """Detect a FreeMindAI batch launcher already starting, across duplicate checkouts."""
    if os.name != "nt":
        return False

    query = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -in @('cmd.exe','python.exe','pythonw.exe') -and $_.CommandLine } | "
        "ForEach-Object { Write-Output ($_.ProcessId.ToString() + [char]9 + $_.Name + [char]9 + $_.CommandLine) }"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", query],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False

    for line in result.stdout.splitlines():
        fields = line.split("\t", 2)
        if len(fields) != 3 or not fields[0].strip().isdigit():
            continue
        process_name = fields[1].strip().casefold()
        command_line = fields[2].casefold()
        if "freemindai" not in command_line:
            continue
        if process_name == "cmd.exe":
            # Ignore an old, idle /k window left behind by the broken launcher.
            if "/k" in command_line:
                continue
            if "run.bat" in command_line or "update.bat" in command_line:
                return True
        elif "main.py" in command_line:
            return True

    return False


def find_freemind_ai_project(
    bot_project_dir: Path,
    *,
    home_dir: Path | None = None,
) -> Path | None:
    """Find a local FreeMindAI checkout without assuming it is beside the Discord bot.

    An explicit FREEMIND_AI_PROJECT_DIR wins. Otherwise search likely project folders
    in the bot's parent, Downloads, Desktop, OneDrive/Desktop, and Documents. The
    bounded one-level scan also handles Windows ZIP extractions such as
    'FreeMindAI-main (3)\\FreeMindAI-main'.
    """
    home = (home_dir or Path.home()).expanduser()
    candidates: list[Path] = []

    configured = os.path.expandvars(os.getenv("FREEMIND_AI_PROJECT_DIR", "").strip().strip('"').strip("'"))
    if configured:
        candidates.append(Path(configured).expanduser())

    # The previous update log showed the current ZIP checkout under Downloads,
    # while an older duplicate remained on OneDrive/Desktop. Search Downloads
    # first so an unconfigured installation doesn't silently launch the stale copy.
    search_roots = (
        home / "Downloads",
        home / "OneDrive" / "Desktop",
        home / "Desktop",
        bot_project_dir.expanduser().parent,
        home / "Documents",
    )

    for root in search_roots:
        candidates.extend((root / "FreeMindAI-main", root / "FreeMindAI"))
        try:
            if not root.is_dir():
                continue
            for entry in root.iterdir():
                if not entry.is_dir() or not entry.name.casefold().startswith("freemindai"):
                    continue
                candidates.extend((
                    entry,
                    entry / "FreeMindAI-main",
                    entry / "FreeMindAI",
                ))
        except OSError:
            continue

    seen: set[str] = set()
    for candidate in candidates:
        try:
            key = os.path.normcase(str(candidate.expanduser().resolve(strict=False)))
        except (OSError, RuntimeError):
            key = os.path.normcase(str(candidate))
        if key in seen:
            continue
        seen.add(key)
        found = _valid_freemind_ai_project(candidate)
        if found is not None:
            return found
    return None
