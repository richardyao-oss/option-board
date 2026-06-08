from __future__ import annotations

import argparse
import csv
import json
import shutil
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from runtime_env import PYTHON, ROOT, clean_env_for_child, configure_runtime


TRACKED_OUTPUTS = [
    ROOT / "data" / "option_screen_underlying_snapshot.csv",
    ROOT / "data" / "daily_option_signals.csv",
    ROOT / "data" / "option_screen_contract_snapshot.csv",
    ROOT / "data" / "option_screen_volume_contract_snapshot.csv",
    ROOT / "data" / "option_unusual_snapshot.csv",
    ROOT / "data" / "current_quote_snapshot.json",
    ROOT / "data" / "option_screen_snapshot_status.json",
    ROOT / "reports" / "options_anomaly_report.html",
]


def run_git(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", "-c", f"safe.directory={ROOT}", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(detail or f"git {' '.join(args)} failed")
    return proc


def is_git_repo() -> bool:
    return run_git(["rev-parse", "--is-inside-work-tree"], check=False).returncode == 0


def ensure_clean_worktree() -> None:
    status = run_git(["status", "--porcelain"]).stdout.strip()
    if status:
        raise RuntimeError(
            "Git worktree is not clean. Commit, push, or discard local changes before updating.\n"
            + status
        )


def current_branch() -> str:
    branch = run_git(["branch", "--show-current"]).stdout.strip()
    return branch or "main"


def pull_latest() -> None:
    if not is_git_repo():
        raise RuntimeError("Git repository is not initialized. Run setup_git_sync.cmd first.")
    ensure_clean_worktree()
    run_git(["fetch", "origin"])
    run_git(["pull", "--ff-only"])


def check_opend() -> None:
    configure_runtime()
    host = "127.0.0.1"
    port = 11111
    try:
        with socket.create_connection((host, port), timeout=2.0):
            pass
    except OSError as exc:
        raise RuntimeError("OpenD is not reachable at 127.0.0.1:11111.") from exc

    from futu import OpenQuoteContext, RET_OK

    qot = OpenQuoteContext(host=host, port=port)
    try:
        ret, data = qot.get_global_state()
    finally:
        qot.close()
    if ret != RET_OK or not isinstance(data, dict):
        raise RuntimeError(f"OpenD global state failed: {data}")
    if not data.get("qot_logined"):
        raise RuntimeError("OpenD quote context is not logged in.")
    if str(data.get("program_status_type", "")).upper() != "READY":
        raise RuntimeError(f"OpenD is not READY: {data.get('program_status_desc') or data}")


def make_backup(mode: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = ROOT / f"backup_before_git_{mode}_{stamp}"
    backup.mkdir(parents=True, exist_ok=False)
    for name in ("data", "reports"):
        src = ROOT / name
        if src.exists():
            shutil.copytree(src, backup / name)
    return backup


def run_report_update(args: argparse.Namespace) -> subprocess.CompletedProcess[str]:
    cmd = [
        str(PYTHON),
        str(ROOT / "daily_option_report.py"),
        "--mode",
        args.mode,
        "--pages",
        str(args.pages),
        "--page-count",
        str(args.page_count),
        "--volume-page-count",
        str(args.volume_page_count),
        "--request-pause",
        str(args.request_pause),
        "--data-dir",
        str(ROOT / "data"),
        "--html",
        str(ROOT / "reports" / "options_anomaly_report.html"),
    ]
    if args.symbols:
        cmd.extend(["--symbols", *args.symbols])
        if args.merge_partial:
            cmd.append("--merge-partial")
    else:
        cmd.extend([
            "--watchlist-source",
            "futu-user",
            "--group-type",
            "CUSTOM",
            "--group-name",
            args.group_name,
        ])
    if args.snapshot_date:
        cmd.extend(["--snapshot-date", args.snapshot_date])
    if args.allow_market_hours_preopen:
        cmd.append("--allow-market-hours-preopen")

    return subprocess.run(
        cmd,
        cwd=ROOT,
        env=clean_env_for_child(),
        text=True,
        capture_output=True,
        timeout=args.timeout,
    )


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def validate_outputs(expected_mode: str) -> tuple[str, str, int, int, int]:
    status_path = ROOT / "data" / "option_screen_snapshot_status.json"
    report_path = ROOT / "reports" / "options_anomaly_report.html"
    unusual_path = ROOT / "data" / "option_unusual_snapshot.csv"
    if not status_path.exists():
        raise RuntimeError("Missing option_screen_snapshot_status.json after update.")
    if not report_path.exists():
        raise RuntimeError("Missing options_anomaly_report.html after update.")
    if not unusual_path.exists():
        raise RuntimeError("Missing option_unusual_snapshot.csv after update.")

    status = json.loads(status_path.read_text(encoding="utf-8-sig"))
    snapshot_date = str(status.get("snapshot_date") or status.get("trade_date") or "")
    snapshot_type = str(status.get("snapshot_type") or "")
    expected_type = "intraday" if expected_mode == "intraday" else "complete"
    if snapshot_type != expected_type:
        raise RuntimeError(f"Expected snapshot_type={expected_type}, got {snapshot_type}.")
    if not snapshot_date:
        raise RuntimeError("Snapshot date is empty after update.")

    agg_rows = [row for row in read_csv(ROOT / "data" / "option_screen_underlying_snapshot.csv") if row.get("snapshot_date") == snapshot_date]
    contract_rows = [row for row in read_csv(ROOT / "data" / "option_screen_contract_snapshot.csv") if row.get("snapshot_date") == snapshot_date]
    unusual_rows = [row for row in read_csv(unusual_path) if row.get("snapshot_date") == snapshot_date]
    if not agg_rows:
        raise RuntimeError(f"No aggregate rows for {snapshot_date}.")
    if not contract_rows:
        raise RuntimeError(f"No contract rows for {snapshot_date}.")

    html_text = report_path.read_text(encoding="utf-8", errors="ignore")
    if snapshot_date not in html_text:
        raise RuntimeError(f"Report HTML does not contain snapshot date {snapshot_date}.")
    return snapshot_date, snapshot_type, len(agg_rows), len(contract_rows), len(unusual_rows)


def commit_and_push(snapshot_date: str, snapshot_type: str) -> bool:
    existing = [path for path in TRACKED_OUTPUTS if path.exists()]
    run_git(["add", "--", *[str(path.relative_to(ROOT)) for path in existing]])
    diff = run_git(["diff", "--cached", "--quiet"], check=False)
    if diff.returncode == 0:
        print("No data/report changes to commit.")
        return False
    if diff.returncode not in (0, 1):
        raise RuntimeError((diff.stderr or diff.stdout).strip() or "git diff --cached failed")
    run_git(["commit", "-m", f"data: update options dashboard {snapshot_date} {snapshot_type}"])
    run_git(["push", "-u", "origin", current_branch()])
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an option dashboard update inside a Git sync transaction.")
    parser.add_argument("--mode", choices=["preopen", "intraday"], required=True)
    parser.add_argument("--snapshot-date")
    parser.add_argument("--symbols", nargs="+")
    parser.add_argument("--group-name", default="To be A8")
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--page-count", type=int, default=200)
    parser.add_argument("--volume-page-count", type=int, default=10)
    parser.add_argument("--request-pause", type=float, default=3.8)
    parser.add_argument("--timeout", type=int, default=2400)
    parser.add_argument("--allow-market-hours-preopen", action="store_true")
    parser.add_argument("--merge-partial", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pull_latest()
    check_opend()
    backup = make_backup(args.mode)
    print(f"Backup created: {backup}")

    proc = run_report_update(args)
    if proc.stdout:
        print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    if proc.returncode != 0:
        raise RuntimeError(f"daily_option_report.py failed with exit code {proc.returncode}.")

    snapshot_date, snapshot_type, agg_count, contract_count, unusual_count = validate_outputs(args.mode)
    print(
        f"Validated {snapshot_date} {snapshot_type}: "
        f"{agg_count} symbols, {contract_count} contracts, "
        f"{unusual_count} unusual rows."
    )
    committed = commit_and_push(snapshot_date, snapshot_type)
    print("Git push completed." if committed else "Git already up to date.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Git-synced update failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
