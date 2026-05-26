@echo off
setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
  echo Virtual environment not found. Run setup.bat first.
  exit /b 1
)

if not exist "frontend\node_modules" (
  echo Installing React dependencies...
  pushd frontend
  call npm install
  if errorlevel 1 exit /b 1
  popd
)

echo Building React dashboard...
pushd frontend
call npm run build
if errorlevel 1 exit /b 1
popd

echo Starting Sentinel React dashboard on http://127.0.0.1:8765
venv\Scripts\python.exe -m sentinel.ui.react_api
