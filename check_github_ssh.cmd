@echo off
setlocal
cd /d "%~dp0"

set "LOG=reports\github_ssh_check.log"
if not exist "reports" mkdir "reports"
echo GitHub SSH check started at %DATE% %TIME% > "%LOG%"

echo.
echo This checks whether this Windows user can connect to GitHub by SSH.
echo.
echo If you see:
echo   Are you sure you want to continue connecting
echo type:
echo   yes
echo then press Enter.
echo.
echo After this succeeds, run:
echo   publish_initial_git_sync.cmd
echo.

ssh -T git@github.com

echo.
echo Rechecking and writing log...
ssh -T git@github.com > "%LOG%" 2>&1
set "SSH_EXIT=%ERRORLEVEL%"

echo.
type "%LOG%"

findstr /C:"successfully authenticated" "%LOG%" >nul
if "%ERRORLEVEL%"=="0" goto ok

findstr /C:"does not provide shell access" "%LOG%" >nul
if "%ERRORLEVEL%"=="0" goto ok

echo.
echo GitHub SSH check failed with exit code %SSH_EXIT%.
echo Log file:
echo %CD%\%LOG%
echo.
pause
exit /b %SSH_EXIT%

:ok
echo.
echo GitHub SSH trust/auth is ready.
echo Now run publish_initial_git_sync.cmd.
echo GitHub SSH trust/auth is ready. >> "%LOG%"
echo.
pause
exit /b 0
