@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  py -3 importer.py menu
) else (
  python importer.py menu
)
pause
