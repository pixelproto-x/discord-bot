from __future__ import annotations

import os
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

    search_roots = (
        bot_project_dir.expanduser().parent,
        home / "Downloads",
        home / "Desktop",
        home / "OneDrive" / "Desktop",
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
