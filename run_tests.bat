@echo off
cd /d "%~dp0"
set PYTHONPATH=%~dp0
call venv\Scripts\activate.bat
echo.
echo  Running ALL Acceptance Gate Tests (Sprint 1 + Sprint 2)...
echo.
python -m pytest sentinel\tests\ -v --tb=short
echo.
pause
