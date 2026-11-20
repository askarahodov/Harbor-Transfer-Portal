@echo off
setlocal
cd /d "%~dp0"
where powershell.exe >NUL 2>&1
if errorlevel 1 (
  echo ERROR: Windows PowerShell 5.1 or newer is required. 1>&2
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows-lifecycle.ps1" -Action Stop %*
exit /b %ERRORLEVEL%
