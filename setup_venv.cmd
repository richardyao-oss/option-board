@echo off
setlocal
cd /d "%~dp0"
set "APPDATA=%CD%\.futu-appdata"
set "appdata=%APPDATA%"
if not exist "%APPDATA%" mkdir "%APPDATA%" >nul 2>nul
set "BASE_PY=C:\Python314\python.exe"
if not exist "%BASE_PY%" set "BASE_PY=python"
if not exist ".venv-futu\Scripts\python.exe" (
  "%BASE_PY%" -m venv ".venv-futu"
  if errorlevel 1 exit /b 1
)
echo Installing dependencies into .venv-futu...
".venv-futu\Scripts\python.exe" -m pip install -i https://pypi.org/simple futu-api pandas
if errorlevel 1 (
  echo.
  echo Official PyPI failed. Trying Aliyun mirror...
  ".venv-futu\Scripts\python.exe" -m pip install -i https://mirrors.aliyun.com/pypi/simple/ futu-api pandas
)
if errorlevel 1 (
  echo.
  echo Venv dependency install failed.
  exit /b 1
)
".venv-futu\Scripts\python.exe" -c "from runtime_env import configure_runtime; configure_runtime(); import pandas, futu; from futu import OpenQuoteContext; print('venv deps ok')"
