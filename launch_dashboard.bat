@echo off
REM ============================================================
REM Project Sentinel — Launch Dashboard
REM Double-click to open dashboard in your browser
REM ============================================================

cd /d "%~dp0"
set PYTHONPATH=%~dp0

echo.
echo  Starting Project Sentinel Dashboard...
echo  Browser will open at http://localhost:8501
echo.
echo  To stop: press Ctrl+C in this window
echo.

call venv\Scripts\activate.bat
streamlit run sentinel\ui\dashboard.py

pause
