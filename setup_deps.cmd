@echo off
setlocal
cd /d "%~dp0"
echo setup_deps.cmd is a legacy entry. It no longer installs into .python-packages.
echo Running setup_venv.cmd instead...
call "%~dp0setup_venv.cmd"
exit /b %errorlevel%
