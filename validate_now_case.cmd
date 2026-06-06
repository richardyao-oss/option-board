@echo off
cd /d "%~dp0"
".venv-futu\Scripts\python.exe" ".\historical_case_validator.py" --underlying US.NOW --dates 2026-05-14 2026-05-15 2026-05-18 2026-05-19
