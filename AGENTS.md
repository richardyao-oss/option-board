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

## Change Methodology

- When Richard introduces a new requirement or changes an existing requirement, first map the impact scope before editing code or running data workflows. Explicitly consider data fetching, write/merge behavior, rendering/display scope, quote scope, validation, Git sync, user-facing scripts, and cross-device behavior.
- Treat scope-changing features such as partial refresh, single-symbol refresh, remaining-symbol refresh, or workflow mode changes as end-to-end data-flow changes, not as local parameter tweaks.
- Before implementing a scope-changing change, identify which symbols/files/views are scanned, overwritten, preserved, displayed, quoted, validated, committed, and pushed.

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

## Complete Review Intent Analysis

- When Richard says only “复盘几月几号数据” or equivalent, treat the date as the target US trading date, infer an omitted year from the current project/conversation context, run the full complete post-close review for the current dashboard watchlist, then analyze and summarize it with the option-trading intent model below. Ask about the year only when it is genuinely ambiguous; Richard does not need to repeat these instructions.
- Interpret option activity as evidence about intent, not as a mechanical `CALL=bullish` / `PUT=bearish` rule. Separate the result into position action (open/close/roll/unknown), directional intent (bullish/bearish/neutral/unknown), volatility intent (long/short/unknown), time horizon, and confidence.
- Apply the model in this order:
  1. Keep only unusual trades whose BJT event time converts to the target US trading date.
  2. Merge likely split executions so one parent order does not count as repeated confirmation.
  3. Detect multi-leg structures before judging individual legs, including verticals, calendars/diagonals, straddles/strangles, risk reversals, butterflies/condors, and rolls when evidence supports them.
  4. Infer opening, closing, or rolling with V/OI and, when later data exists, next-day OI change. V/OI alone is supporting evidence, not proof.
  5. Treat BUY CALL / SELL PUT as usually bullish and BUY PUT / SELL CALL as usually bearish or upside-capping, but lower confidence when covered positions, hedges, or unseen spread legs could explain the trade.
  6. Include moneyness and time to expiry to distinguish short-term event/gamma trades, swing positioning, LEAPS, stock substitution, and hedging.
  7. Separate directional intent from volatility intent and cross-check IV behavior, P/C versus baseline, concentration, underlying price action, repeated expiries, and contradictory evidence.
  8. Allow `unknown` or `suspected structure`; never force a bullish/bearish conclusion when order IDs, stock legs, opening/closing flags, or complete strategy legs are unavailable.
- Summaries should rank A (clear structure/direction plus opening and size evidence), B (at least two independent supporting conditions without a strong contradiction), and C/downgraded (large activity but ambiguous, low V/OI, closing/rolling risk, incomplete structure, or conflicting evidence). Explain notable exclusions and contradictions.

## Low-Token Complete Review Workflow

- For `复盘某日数据`, first inspect the local snapshot status and Git state. If that date is already `complete`, the worktree is clean, and local `HEAD` matches its upstream, do not check OpenD or fetch again; run `dashboard_analysis.py --date YYYY-MM-DD --intent --top 15 --json` against local files.
- If the date is not complete, run exactly one Git transaction update, then run the compact intent analysis. Do not perform exploratory duplicate fetches.
- For project reviews, use the project-local workflow directly. Do not load the generic FutuAPI reference unless Richard asks a standalone API question.
- During long collection, poll about every 55 seconds. Suppress empty poll payloads and use only `仍在抓取，无错误。` unless the phase changes, an error occurs, or collection finishes.
- Read raw CSV rows only when the compact report identifies missing or ambiguous evidence that cannot be judged from its bounded structures, contracts, and contradictions.
- The final answer is bounded to one core conclusion, at most three group judgments, at most five symbols in each A/B/C tier, data status, at most two limitations, and the dashboard link.

## Frontend Design

- When Richard asks for frontend pages, HTML pages, dashboard visual redesigns, UI mockups, or visual prototypes, prefer the `huashu-design` skill as the design workflow and quality bar when it is available.
- If `huashu-design` is not available, state that and continue by following the existing dashboard visual system.
