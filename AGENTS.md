# Project Agent Memory

This project is an options anomaly dashboard backed by Futu OpenAPI data. Treat it as a semi-production workflow: it uses real data, limited quotas, local files, and Git sync across computers.

## Hard Rules

- Before any Futu data fetch, check OpenD first. Stop and ask Richard to open/log in OpenD if `127.0.0.1:11111` is unreachable, `qot_logined` is not true, or status is not `READY`.
- Do not call historical K-line APIs unless Richard explicitly allows it for that task. Before using historical K-line data, state the symbols, date range, and expected request count.
- Distinguish intraday snapshots from complete post-close reviews. Never label intraday data as complete. A complete same-date review may overwrite the intraday snapshot for that date.
- If Richard says stop, stop the running work as the first priority.
- Before writing option data or regenerating the dashboard, create a local backup under `backup_before_*`.
- Daily updates must use the Git transaction wrapper (`git_sync_update.py` or `git_sync_update.cmd`) so pull, local update, validation, commit, and push happen together.
- Any user-facing `.cmd`/PowerShell script that Richard is asked to double-click must pause on failure and show both the log path and the error text before exiting. Do not provide scripts that fail and immediately close the window.
- When Codex runs updates from this project, explicitly write to the local project `data` and `reports` paths.
- After a successful local data/dashboard update, confirm that Git push completed. Do not ask Richard to run Google Drive sync for routine updates.
- Do not use `legacy/google_drive/initialize_google_drive_sync.cmd` or `legacy/google_drive/sync_latest_snapshot_to_google_drive.cmd` for routine sync. They are legacy Google Drive tools only.

## Permissions And Skills

- Current Codex sessions usually have write access only inside `C:\Users\yaoru\Documents\New project`.
- Do not assume Google Drive, Desktop, Windows Startup, or global Codex directories are writable from this session.
- Installing a new skill is a global write operation. If global write permission is not clearly available, do not repeatedly try installing it. After one failed attempt, stop and tell Richard it must be handled in a session/environment with global Codex write permission.
- If a requested skill is unavailable in the current session, say so briefly and use the best local fallback instead of attempting repeated installs.

## Dashboard Workflow

- Single-symbol refreshes are temporary inspection views. A full complete review can replace same-date single-symbol temporary data.
- Full reviews should scan the complete current watchlist and regenerate the full dashboard.
- Git worktree must be clean before running synced updates. Do not auto-merge option data conflicts.
- VIX is special: Futu code is `US..VIX`, option screen category is `US_INDEX`, and normal US stock snapshot logic may not provide current price/change.
- Top contracts should preserve the current mixed logic: turnover top 5 plus volume top 10 after removing duplicates until 10 rows.
- P/C remains volume-based. Top-contract tables should show both volume and turnover.
- `option_screen_snapshot_status.json` should keep non-destructive collection metadata: screen sort/page counts, P/C basis, Top10 basis, and unusual time range.

## Frontend Design

- When Richard asks for frontend pages, HTML pages, dashboard visual redesigns, UI mockups, or visual prototypes, prefer the `huashu-design` skill as the design workflow and quality bar when it is available.
- If `huashu-design` is not available, state that and continue by following the existing dashboard visual system.
