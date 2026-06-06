@echo off
setlocal
cd /d "%~dp0"
set "TARGET=%CD%\start_report_console_background.cmd"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SHORTCUT=%STARTUP%\Option Report Console.lnk"
if not exist "%STARTUP%" mkdir "%STARTUP%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws=New-Object -ComObject WScript.Shell; $s=$ws.CreateShortcut($env:SHORTCUT); $s.TargetPath=$env:TARGET; $s.WorkingDirectory=(Split-Path $env:TARGET); $s.WindowStyle=7; $s.Description='Start the local option report console'; $s.Save()"
if errorlevel 1 (
  echo Failed to create startup shortcut.
  exit /b 1
)
echo Startup shortcut created:
echo %SHORTCUT%
