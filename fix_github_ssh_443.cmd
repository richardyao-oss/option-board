@echo off
setlocal
cd /d "%~dp0"

set "LOG=reports\fix_github_ssh_443.log"
if not exist "reports" mkdir "reports"
echo GitHub SSH 443 fix started at %DATE% %TIME% > "%LOG%"

echo.
echo This configures GitHub SSH to use port 443 instead of port 22.
echo It is useful when a network blocks or closes SSH port 22.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop';" ^
  "$sshDir=Join-Path $env:USERPROFILE '.ssh';" ^
  "if (!(Test-Path -LiteralPath $sshDir)) { New-Item -ItemType Directory -Path $sshDir | Out-Null };" ^
  "$config=Join-Path $sshDir 'config';" ^
  "$block=@('Host github.com','  HostName ssh.github.com','  Port 443','  User git') -join [Environment]::NewLine;" ^
  "if (Test-Path -LiteralPath $config) {" ^
  "  $text=Get-Content -LiteralPath $config -Raw;" ^
  "  if ($text -match '(?ms)^Host\s+github\.com\b.*?(?=^Host\s+|\z)') {" ^
  "    $text=[regex]::Replace($text,'(?ms)^Host\s+github\.com\b.*?(?=^Host\s+|\z)',$block + [Environment]::NewLine);" ^
  "  } else {" ^
  "    $text=$text.TrimEnd() + [Environment]::NewLine + [Environment]::NewLine + $block + [Environment]::NewLine;" ^
  "  }" ^
  "} else {" ^
  "  $text=$block + [Environment]::NewLine;" ^
  "}" ^
  "Set-Content -LiteralPath $config -Value $text -Encoding ascii;" ^
  "Write-Host 'Wrote SSH config:' $config;" ^
  "Write-Host '';" ^
  "Get-Content -LiteralPath $config;" ^
  >> "%LOG%" 2>&1
if errorlevel 1 goto fail

echo.
echo Testing GitHub SSH over port 443...
ssh -T git@github.com >> "%LOG%" 2>&1
set "SSH_EXIT=%ERRORLEVEL%"

type "%LOG%"

findstr /C:"successfully authenticated" "%LOG%" >nul
if "%ERRORLEVEL%"=="0" goto ok

findstr /C:"does not provide shell access" "%LOG%" >nul
if "%ERRORLEVEL%"=="0" goto ok

echo.
echo GitHub SSH 443 test failed with exit code %SSH_EXIT%.
echo Log file:
echo %CD%\%LOG%
echo.
pause
exit /b %SSH_EXIT%

:ok
echo.
echo GitHub SSH over port 443 is ready.
echo Now run: git push
echo.
pause
exit /b 0

:fail
set "EXITCODE=%ERRORLEVEL%"
echo.
echo GitHub SSH 443 fix failed with exit code %EXITCODE%.
echo Log file:
echo %CD%\%LOG%
echo.
type "%LOG%"
echo.
pause
exit /b %EXITCODE%
