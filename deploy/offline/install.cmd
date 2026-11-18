@echo off
setlocal
cd /d "%~dp0"
where powershell.exe >NUL 2>&1
if errorlevel 1 (
  echo ERROR: Windows PowerShell 5.1 or newer is required.
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
exit /b %ERRORLEVEL%
