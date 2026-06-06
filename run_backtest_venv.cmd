@echo off
cd /d "%~dp0"
".venv-futu\Scripts\python.exe" ".\option_flow_monitor.py" --mode backfill --start 2026-05-01 --end 2026-05-25 --chain-request-pause 3.3 --kline-request-pause 0.55 --max-kline-requests 20
