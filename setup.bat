@echo off
cd /d "%~dp0"
set PYTHONPATH=%~dp0

echo.
echo  ================================================
echo   Project Sentinel v5.0 — Sprint 2 Setup
echo  ================================================
echo.
echo  Running from: %~dp0
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python not found.
    echo  Install Python 3.11+ from https://python.org
    pause
    exit /b 1
)

echo  [1/6] Python found:
python --version
echo.

if not exist "venv" (
    echo  [2/6] Creating virtual environment...
    python -m venv venv
    echo  Done.
) else (
    echo  [2/6] Virtual environment already exists.
)
echo.

echo  [3/6] Activating virtual environment...
call venv\Scripts\activate.bat
echo  Done.
echo.

echo  [4/6] Upgrading pip...
python -m pip install --upgrade pip --quiet
echo  Done.
echo.

echo  [5/6] Installing dependencies (3-5 minutes)...
pip install -r requirements.txt --quiet
echo  Done.
echo.

echo  [6/6] Running all acceptance gate tests...
echo.
python -m pytest sentinel\tests\ -v --tb=short

echo.
echo  ================================================
echo   Setup complete!
echo   Next: double-click  launch_dashboard.bat
echo  ================================================
echo.
pause
