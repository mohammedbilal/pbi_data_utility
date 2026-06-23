@echo off
title PBI Test Utility - First Time Setup
echo =============================================
echo  PBI Test Utility - First Time Setup
echo =============================================
echo.

:: ── Backend ─────────────────────────────────────
echo [1/4] Creating Python virtual environment...
cd /d "%~dp0backend"
if not exist ".venv" (
    python -m venv .venv
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment.
        echo Make sure Python 3.10+ is installed and on your PATH.
        pause & exit /b 1
    )
) else (
    echo       Already exists, skipping.
)

echo [2/4] Installing Python dependencies...
call .venv\Scripts\activate.bat
pip install -r requirements.txt -q
if errorlevel 1 (
    echo ERROR: pip install failed.
    pause & exit /b 1
)

:: ── Frontend ─────────────────────────────────────
echo [3/4] Installing Node packages...
cd /d "%~dp0frontend"
if not exist "node_modules" (
    where npm >nul 2>&1
    if errorlevel 1 (
        echo ERROR: npm not found.
        echo Please install Node.js LTS from https://nodejs.org and re-run Setup.bat.
        pause & exit /b 1
    )
    npm install
    if errorlevel 1 (
        echo ERROR: npm install failed.
        pause & exit /b 1
    )
) else (
    echo       Already exists, skipping.
)

echo [4/4] Setup complete!
echo.
echo =============================================
echo  You can now launch the app by double-clicking
echo  Launch.vbs
echo =============================================
echo.
pause
