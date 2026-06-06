@echo off
setlocal
cd /d "%~dp0"

set "LOG=reports\publish_initial_git_sync.log"
if not exist "reports" mkdir "reports"
echo Initial Git publish started at %DATE% %TIME% > "%LOG%"

call "%~dp0setup_git_sync.cmd" >> "%LOG%" 2>&1
if errorlevel 1 goto fail

for /f "delims=" %%A in ('git -c safe.directory^="%CD%" config --get user.name 2^>nul') do set "GIT_USER_NAME=%%A"
if not defined GIT_USER_NAME (
  git -c safe.directory="%CD%" config user.name "Richard Yao" >> "%LOG%" 2>&1
  if errorlevel 1 goto fail
)

for /f "delims=" %%A in ('git -c safe.directory^="%CD%" config --get user.email 2^>nul') do set "GIT_USER_EMAIL=%%A"
if not defined GIT_USER_EMAIL (
  git -c safe.directory="%CD%" config user.email "richardyao-oss@users.noreply.github.com" >> "%LOG%" 2>&1
  if errorlevel 1 goto fail
)

git -c safe.directory="%CD%" add . >> "%LOG%" 2>&1
if errorlevel 1 goto fail

git -c safe.directory="%CD%" diff --cached --quiet >> "%LOG%" 2>&1
if "%ERRORLEVEL%"=="0" (
  echo Nothing to commit. >> "%LOG%"
) else (
  git -c safe.directory="%CD%" commit -m "chore: initialize option board git sync" >> "%LOG%" 2>&1
  if errorlevel 1 goto fail
)

git -c safe.directory="%CD%" push -u origin main >> "%LOG%" 2>&1
if errorlevel 1 goto fail

echo.
echo Initial Git publish completed.
echo Initial Git publish completed. >> "%LOG%"
pause
exit /b 0

:fail
set "EXITCODE=%ERRORLEVEL%"
echo.
echo Initial Git publish failed with exit code %EXITCODE%.
echo Log file:
echo %CD%\%LOG%
echo.
type "%LOG%"
echo.
pause
exit /b %EXITCODE%
