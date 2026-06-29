@echo off
cd /d "%~dp0"
call "%~dp0git_sync_update.cmd" preopen
exit /b %ERRORLEVEL%
