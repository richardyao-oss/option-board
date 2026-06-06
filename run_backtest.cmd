@echo off
cd /d "%~dp0"
python ".\option_flow_monitor.py" --mode backfill --start 2026-05-01 --end 2026-05-25 --max-kline-requests 20
