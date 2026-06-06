@echo off
setlocal
cd /d "%~dp0"
set "REMOTE=git@github.com:richardyao-oss/option-board.git"

git -c safe.directory="%CD%" rev-parse --is-inside-work-tree >nul 2>nul
if errorlevel 1 (
  git init
  if errorlevel 1 exit /b %errorlevel%
)

git -c safe.directory="%CD%" branch -M main
git -c safe.directory="%CD%" remote get-url origin >nul 2>nul
if errorlevel 1 (
  git -c safe.directory="%CD%" remote add origin "%REMOTE%"
) else (
  git -c safe.directory="%CD%" remote set-url origin "%REMOTE%"
)

echo Git sync remote is configured:
git -c safe.directory="%CD%" remote -v
exit /b %errorlevel%
