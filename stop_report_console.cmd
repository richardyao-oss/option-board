@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "LOG=%~dp0reports\stop_report_console.log"
if not exist "reports" mkdir "reports" >nul 2>nul
echo Stop report console started at %DATE% %TIME% > "%LOG%"

for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8765 .*LISTENING"') do (
  echo Stopping PID %%P >> "%LOG%"
  taskkill /PID %%P /F /T >> "%LOG%" 2>&1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $c = New-Object Net.Sockets.TcpClient; $iar = $c.BeginConnect('127.0.0.1', 8765, $null, $null); if ($iar.AsyncWaitHandle.WaitOne(1000)) { $c.EndConnect($iar); $c.Close(); exit 1 } exit 0 } catch { exit 0 }" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo.
  echo The report console still appears to be listening on 8765.
  echo Log file:
  echo %LOG%
  type "%LOG%"
  pause
  exit /b 1
)

echo Report console stopped or was not running.
echo Log file: %LOG%
exit /b 0
