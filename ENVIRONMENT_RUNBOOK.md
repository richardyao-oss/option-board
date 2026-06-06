# Local Environment Runbook

## Golden Path

Use only this Python:

```cmd
C:\Users\yaoru\Documents\New project\.venv-futu\Scripts\python.exe
```

Use these entry points:

```cmd
check_environment.cmd
setup_venv.cmd
START_HERE_期权监控.cmd
stop_report_console.cmd
```

Do not use `pip install --target .python-packages`. `setup_deps.cmd` is now a legacy wrapper that calls `setup_venv.cmd`.

## What Was Fixed

- `.python-packages` was isolated as `.legacy-python-packages-20260526-223538`.
- Project scripts no longer insert `.python-packages`, `.python-packages-fixed`, or `.python-packages-local` into `sys.path`.
- Futu SDK logs are redirected to project-local `.futu-appdata`.
- Child processes receive a normalized environment with only one `Path` key, avoiding the `Path` vs `PATH` Windows conflict.
- `report_server.py` now runs updates with the same `.venv-futu` Python and the same project-local Futu environment.

## Local Report Console

To open the report console manually:

```cmd
START_HERE_期权监控.cmd
```

`START_HERE_期权监控.cmd` writes `reports\start_here.log` and pauses on failure.
It opens the browser only after Git pull, local server startup, and
`http://127.0.0.1:8765/api/info` all succeed.

To stop the local report console:

```cmd
stop_report_console.cmd
```

The browser should use:

```text
http://127.0.0.1:8765/
```

Avoid using the old `file://` report URL for buttons that need to update local files.

## Git Sync

Cross-device sync now uses a private Git repository:

```text
git@github.com:richardyao-oss/option-board.git
```

Daily updates should run through:

```cmd
git_sync_update.cmd preopen
git_sync_update.cmd intraday
```

First-time publish on this computer:

```cmd
publish_initial_git_sync.cmd
```

The wrapper performs the full transaction:

- checks the Git worktree is clean;
- pulls the latest remote state with `git pull --ff-only`;
- checks OpenD before any Futu data fetch;
- writes local `data\` and `reports\options_anomaly_report.html`;
- validates the updated snapshot and HTML;
- commits and pushes to Git.

Opening the local console runs a Git pull before opening the report page. If pull fails, fix the Git state before using the dashboard.

On a new computer:

```cmd
git clone git@github.com:richardyao-oss/option-board.git
setup_venv.cmd
check_environment.cmd
START_HERE_期权监控.cmd
```

Keep these local on each computer and do not commit them:

```text
.venv-futu
.futu-appdata
OpenD logs
temporary folders
backup_before_*
```

## Legacy Google Drive Sync

Google Drive sync scripts are legacy fallback tools only:

```cmd
legacy\google_drive\initialize_google_drive_sync.cmd
legacy\google_drive\sync_latest_snapshot_to_google_drive.cmd
```

Do not use them for routine sync. Routine cross-device sync should use Git commit/push and pull.

`sync_settings.py` now defaults to `git`. `OPTION_MONITOR_SYNC_DIR` is intentionally ignored by the daily workflow; Google Drive tools must explicitly read `legacy_google_drive_sync_dir` from `sync_config.json`.

## Archived Tools

Historical backfill, old option-flow prototypes, NOW/FUTU case validation, and Google Drive recovery utilities were moved under:

```text
legacy\
```

The one-off return-correlation research helper remains useful and lives under:

```text
tools\research\analyze_option_return_correlation.py
```

Do not use archived tools as daily entry points.

## Start On Login

To make the local console start when Windows logs in, run:

```cmd
install_startup_shortcut.cmd
```

This creates a shortcut in the current user's Windows Startup folder. Starting from Windows login is more reliable than starting long-running services from Codex, because Codex may clean up child processes after a tool command finishes.

## Health Check

Run:

```cmd
check_environment.cmd
```

Expected healthy signals:

- `.venv-futu` exists and is the active Python.
- `futu-api` loads from `.venv-futu`.
- no active legacy target package dirs are found.
- `127.0.0.1:11111` is listening.
- Futu API quote context connects and reports `qot_logined: true`.
- after starting the console, `127.0.0.1:8765` is listening.

The duplicate `PATH`/`Path` warning may still appear inside Codex. That is okay for this project because the launchers normalize the child environment before starting Python.

## Codex Operating Rules

Treat this dashboard as a semi-production workflow. It uses real Futu data, limited quotas, local files, and Git sync across computers.

- Before any Futu data fetch, check OpenD first. Stop if `127.0.0.1:11111` is unreachable, `qot_logined` is not true, or status is not `READY`.
- Do not call historical K-line APIs unless Richard explicitly allows it. Before using historical K-line data, state the symbols, date range, and expected request count.
- Keep intraday snapshots and complete post-close reviews separate. Never label intraday data as complete.
- If Richard says stop, stop the running work first.
- Before writing option data or regenerating the dashboard, create a local `backup_before_*` backup.
- Use the Git transaction wrapper for routine updates. Confirm that Git push completed after a successful local update.
- Do not use Google Drive sync scripts for routine sync. They are archived legacy fallback tools only.

Codex permissions are intentionally limited:

- Current Codex sessions usually write only inside `C:\Users\yaoru\Documents\New project`.
- Do not assume Google Drive, Desktop, Windows Startup, or global Codex directories are writable.
- Installing a new skill is a global write operation. If global write permission is not clearly available, do not repeatedly try installing it. After one failed attempt, stop and tell Richard it must be handled in a session/environment with global Codex write permission.
- If a requested skill is unavailable in the current session, state that and use the best local fallback.

Project-specific data notes:

- Single-symbol refreshes are temporary inspection views. A full complete review can replace same-date single-symbol temporary data.
- Git worktree must be clean before synced updates. Do not auto-merge option data conflicts.
- VIX is special: Futu code is `US..VIX`, option screen category is `US_INDEX`, and normal US stock snapshot logic may not provide current price/change.
- Top contracts use the mixed logic: turnover top 5 plus volume top 10 after removing duplicates until 10 rows.
- P/C remains volume-based. Top-contract tables show both volume and turnover.
- `option_screen_snapshot_status.json` records the active collection scope, including page counts, P/C basis, Top10 basis, and unusual time range.
