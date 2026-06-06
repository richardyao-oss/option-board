@echo off
setlocal
cd /d "%~dp0"

set "LOG=reports\setup_github_ssh_key.log"
if not exist "reports" mkdir "reports"
echo GitHub SSH key setup started at %DATE% %TIME% > "%LOG%"

echo.
echo This will prepare an SSH key for GitHub under your Windows user.
echo It will not overwrite an existing default SSH key.
echo.

set "SSH_DIR=%USERPROFILE%\.ssh"
set "KEY=%SSH_DIR%\id_ed25519"
set "PUB=%KEY%.pub"

if not exist "%SSH_DIR%" (
  mkdir "%SSH_DIR%" >> "%LOG%" 2>&1
  if errorlevel 1 goto fail
)

if not exist "%PUB%" (
  if exist "%KEY%" (
    echo Existing private key found, regenerating public key only. >> "%LOG%"
    ssh-keygen -y -f "%KEY%" > "%PUB%" 2>> "%LOG%"
    if errorlevel 1 goto fail
  ) else (
    echo Creating new SSH key: %KEY% >> "%LOG%"
    ssh-keygen -t ed25519 -C "richardyao-oss option-board" -f "%KEY%" -N "" >> "%LOG%" 2>&1
    if errorlevel 1 goto fail
  )
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; $pub=$env:USERPROFILE + '\.ssh\id_ed25519.pub'; $publicKey=Get-Content -LiteralPath $pub -Raw; $publicKey | Set-Clipboard; Write-Host ''; Write-Host 'Public key copied to clipboard:'; Write-Host ''; Write-Host $publicKey; Write-Host ''; Write-Host 'Next step: add this key in GitHub -> Settings -> SSH and GPG keys -> New SSH key.'; Write-Host 'After adding it, run check_github_ssh.cmd again.'" ^
  >> "%LOG%" 2>&1

if errorlevel 1 goto fail

echo.
type "%LOG%"
echo.
echo SSH public key is copied to your clipboard.
echo Add it to GitHub, then run check_github_ssh.cmd again.
echo.
pause
exit /b 0

:fail
set "EXITCODE=%ERRORLEVEL%"
echo.
echo GitHub SSH key setup failed with exit code %EXITCODE%.
echo Log file:
echo %CD%\%LOG%
echo.
type "%LOG%"
echo.
pause
exit /b %EXITCODE%
