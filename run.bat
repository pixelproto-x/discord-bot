@echo off
setlocal EnableExtensions

rem Always run from this script's directory, even when launched from a shortcut.
pushd "%~dp0" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Could not open the Discord Bot project directory.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Missing .venv\Scripts\python.exe. Create the project's virtual environment first.
    popd
    pause
    exit /b 1
)

if not exist "bot.py" (
    echo [ERROR] Missing bot.py in the project directory.
    popd
    pause
    exit /b 1
)

if /I "%~1"=="/dry-run" (
    echo [OK] Working directory: %CD%
    echo [OK] Python: .venv\Scripts\python.exe
    echo [OK] Entry point: bot.py
    popd
    exit /b 0
)

rem Pull the latest main branch before every startup. Fast-forward only so local
rem edits are never overwritten by an automatic merge.
where git >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Git was not found in PATH. Install Git or fix PATH; bot startup cancelled.
    popd
    pause
    exit /b 1
)

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo [ERROR] This folder is not a Git repository. Bot startup cancelled.
    popd
    pause
    exit /b 1
)

for /f "delims=" %%B in ('git branch --show-current') do set "BOT_BRANCH=%%B"
if /I not "%BOT_BRANCH%"=="main" (
    echo [ERROR] Current Git branch is "%BOT_BRANCH%", not main. Switch to main before startup.
    popd
    pause
    exit /b 1
)

echo [UPDATE] Checking GitHub main for updates...
git fetch origin main
if errorlevel 1 (
    echo [ERROR] Could not fetch GitHub updates. Bot startup cancelled to avoid running an outdated version.
    popd
    pause
    exit /b 1
)

git pull --ff-only origin main
if errorlevel 1 (
    echo [ERROR] Update could not be applied as a fast-forward. Local changes were preserved; resolve Git status before starting.
    popd
    pause
    exit /b 1
)

echo.
echo Starting Discord Bot from %CD%
echo The bot manages FreeMindAI, Ollama, and Economy Editor without duplicate CMD windows.
echo.
".venv\Scripts\python.exe" "bot.py"
set "BOT_EXIT_CODE=%ERRORLEVEL%"

if not "%BOT_EXIT_CODE%"=="0" (
    echo.
    echo [ERROR] Discord Bot stopped with exit code %BOT_EXIT_CODE%.
    pause
)

popd
endlocal & exit /b %BOT_EXIT_CODE%
