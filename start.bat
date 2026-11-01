@echo off
setlocal
cd /d "%~dp0"
title Cuelog

rem ---- find Python 3.10+ -------------------------------------------------------
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo Python 3.10 or newer is required. Install it from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during setup.
  pause
  exit /b 1
)

rem ---- create the private environment on first run ---------------------------
if not exist ".venv\Scripts\pythonw.exe" (
  echo First start: setting up Cuelog. This takes a minute...
  %PY% -m venv .venv || goto :fail
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail
)

rem ---- install missing packages after updates ----------------------------------
".venv\Scripts\python.exe" -c "import PySide6, webview" 2>nul || ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail

start "" ".venv\Scripts\pythonw.exe" -m cuelog %*
exit /b 0

:fail
echo.
echo Setup failed. Check your internet connection and try again.
pause
exit /b 1
