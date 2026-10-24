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
