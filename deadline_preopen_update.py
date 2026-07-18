from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import daily_option_report as dor
import dashboard_renderer
import option_screen_monitor as osm
import option_unusual_monitor as oum
import sync_settings


ROOT = Path(__file__).resolve().parent
PROGRESS_FILE = "deadline_preopen_progress.json"


def bjt_now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def parse_bjt_deadline(value: str) -> datetime:
    text = value.strip()
    if len(text) == 8 and text.count(":") == 2:
        today = bjt_now().date().isoformat()
        text = f"{today} {text}"
    if "T" in text:
        dt = datetime.fromisoformat(text)
    else:
        dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return dt.astimezone(ZoneInfo("Asia/Shanghai"))


def has_time(deadline: datetime, guard_seconds: float) -> bool:
    return bjt_now() + timedelta(seconds=guard_seconds) < deadline


def write_progress(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def collect_turnover_for_symbol(
    symbol: str,
    snapshot_date: str,
    pages: int,
    page_count: int,
) -> tuple[list[dict[str, Any]], int]:
    return osm.collect_rank_rows([symbol], pages, page_count, snapshot_date, 0, "turnover")


def collect_volume_for_symbol(symbol: str, snapshot_date: str, page_count: int) -> tuple[list[dict[str, Any]], int]:
    return osm.collect_rank_rows([symbol], 1, page_count, snapshot_date, 0, "volume")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deadline-aware preopen option dashboard update.")
    parser.add_argument("--deadline-bjt", required=True, help="BJT deadline, e.g. 21:29:59 or YYYY-MM-DD HH:MM:SS")
    parser.add_argument("--snapshot-date")
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--page-count", type=int, default=200)
    parser.add_argument("--volume-page-count", type=int, default=10)
    parser.add_argument("--request-pause", type=float, default=osm.DEFAULT_REQUEST_PAUSE)
    parser.add_argument("--min-total", type=int, default=1000)
    parser.add_argument("--min-history-days", type=int, default=3)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--html", type=Path, default=Path("reports/options_anomaly_report.html"))
    parser.add_argument("--allow-market-hours-preopen", action="store_true")
    parser.add_argument("--deadline-guard-seconds", type=float, default=12.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.data_dir = sync_settings.resolve_data_dir(args.data_dir)
    args.html = sync_settings.resolve_report_path(args.html)
    deadline = parse_bjt_deadline(args.deadline_bjt)

    dor.ensure_preopen_collection_window(args.allow_market_hours_preopen)
    watchlist, report_groups = dor.choose_watchlist(args)
    snapshot_date = args.snapshot_date or dor.last_completed_us_trade_date().isoformat()
    dor.ensure_overview_target_date("preopen", snapshot_date)
    scope = dor.collection_scope(args, watchlist)
    progress_path = args.data_dir / PROGRESS_FILE
    progress: dict[str, Any] = {
        "started_at_bjt": bjt_now().strftime("%Y-%m-%d %H:%M:%S BJT"),
        "deadline_bjt": deadline.strftime("%Y-%m-%d %H:%M:%S BJT"),
        "snapshot_date": snapshot_date,
        "status": "running",
        "completed_symbols": [],
        "remaining_symbols": watchlist,
        "total_symbols": len(watchlist),
        "turnover_contracts_seen": 0,
        "volume_contracts_seen": 0,
        "unusual_rows": 0,
    }
    write_progress(progress_path, progress)
    if not has_time(deadline, args.deadline_guard_seconds):
        progress["status"] = "stopped_before_deadline"
        progress["stop_reason"] = "deadline reached before resolving stock ids"
        write_progress(progress_path, progress)
        print(
            "Deadline stop: "
            f"0/{len(watchlist)} symbols completed. "
            f"Progress file: {progress_path}"
        )
        return 2

    contracts: list[dict[str, Any]] = []
    volume_contracts: list[dict[str, Any]] = []
    unusual_rows: list[dict[str, Any]] = []
    total_seen = 0
    volume_total_seen = 0

    stopped_reason = ""
    for index, symbol in enumerate(watchlist):
        if not has_time(deadline, args.deadline_guard_seconds):
            stopped_reason = f"deadline reached before starting {symbol}"
            break
        if index:
            if not has_time(deadline, args.deadline_guard_seconds + args.request_pause):
                stopped_reason = f"deadline reached before pause for {symbol}"
                break
            time.sleep(args.request_pause)
            if not has_time(deadline, args.deadline_guard_seconds):
                stopped_reason = f"deadline reached before starting {symbol}"
                break
        turnover_rows, seen = collect_turnover_for_symbol(symbol, snapshot_date, args.pages, args.page_count)
        contracts.extend(turnover_rows)
        total_seen += seen
        if has_time(deadline, args.deadline_guard_seconds):
            time.sleep(args.request_pause)
            volume_rows, volume_seen = collect_volume_for_symbol(symbol, snapshot_date, args.volume_page_count)
            volume_contracts.extend(volume_rows)
            volume_total_seen += volume_seen
        else:
            stopped_reason = f"deadline reached after turnover for {symbol}"
        if stopped_reason:
            progress["partial_symbol"] = symbol
            progress["remaining_symbols"] = watchlist[index:]
            progress["turnover_contracts_seen"] = total_seen
            progress["volume_contracts_seen"] = volume_total_seen
            progress["unusual_rows"] = len(unusual_rows)
            progress["last_progress_at_bjt"] = bjt_now().strftime("%Y-%m-%d %H:%M:%S BJT")
            write_progress(progress_path, progress)
            break

        progress["completed_symbols"].append(symbol)
        progress["remaining_symbols"] = watchlist[index + 1 :]
        progress["turnover_contracts_seen"] = total_seen
        progress["volume_contracts_seen"] = volume_total_seen
        progress["unusual_rows"] = len(unusual_rows)
        progress["last_completed_at_bjt"] = bjt_now().strftime("%Y-%m-%d %H:%M:%S BJT")
        write_progress(progress_path, progress)
        if stopped_reason:
            break

    completed_symbols = list(progress["completed_symbols"])
    if len(completed_symbols) < len(watchlist):
        progress["status"] = "stopped_before_deadline"
        progress["stop_reason"] = stopped_reason or "deadline guard reached"
        write_progress(progress_path, progress)
        print(
            "Deadline stop: "
            f"{len(completed_symbols)}/{len(watchlist)} symbols completed. "
            f"Progress file: {progress_path}"
        )
        return 2

    if not has_time(deadline, args.deadline_guard_seconds):
        progress["status"] = "stopped_before_deadline"
        progress["stop_reason"] = "deadline reached before global option event fetch"
        write_progress(progress_path, progress)
        return 2
    dor.add_contract_collection_stats(scope, contracts, volume_contracts)
    unusual_rows, event_stats = dor.collect_option_unusual_rows(watchlist, snapshot_date)
    if not has_time(deadline, args.deadline_guard_seconds):
        progress["status"] = "stopped_before_deadline"
        progress["stop_reason"] = "deadline reached before underlying overview fetch"
        write_progress(progress_path, progress)
        return 2
    aggregates = osm.collect_overview_aggregates(watchlist, snapshot_date, contracts)
    dor.add_collection_stats(scope, event_stats, len(aggregates))
    metadata = dor.collection_metadata(snapshot_date, "complete", scope)
    progress["unusual_rows"] = len(unusual_rows)
    progress["option_event_pages"] = event_stats["pages"]
    progress["option_overview_symbol_count"] = len(aggregates)
    daily_contract_path = args.data_dir / "option_screen_contract_snapshot.csv"
    daily_volume_contract_path = args.data_dir / dor.VOLUME_CONTRACT_SNAPSHOT_FILE
    unusual_path = args.data_dir / dor.UNUSUAL_SNAPSHOT_FILE
    daily_agg_path = args.data_dir / "option_screen_underlying_snapshot.csv"
    daily_signal_path = args.data_dir / "daily_option_signals.csv"
    quote_snapshot_path = args.data_dir / "current_quote_snapshot.json"
    snapshot_status_path = args.data_dir / dor.SNAPSHOT_STATUS_FILE

    dor.write_snapshot_rows(daily_contract_path, osm.CONTRACT_COLUMNS, contracts, snapshot_date, watchlist, False)
    dor.write_snapshot_rows(daily_volume_contract_path, osm.CONTRACT_COLUMNS, volume_contracts, snapshot_date, watchlist, False)
    dor.write_snapshot_rows(unusual_path, oum.UNUSUAL_COLUMNS, unusual_rows, snapshot_date, watchlist, False)
    dor.write_snapshot_rows(daily_agg_path, osm.AGG_COLUMNS, aggregates, snapshot_date, watchlist, False)
    all_agg = dor.read_csv(daily_agg_path)
    signals = dor.build_signals(all_agg, min_total=args.min_total, min_history_days=args.min_history_days)
    dor.write_csv(daily_signal_path, dor.SIGNAL_COLUMNS, signals)
    all_contracts = dor.read_csv(daily_contract_path)
    all_volume_contracts = dor.read_csv(daily_volume_contract_path)
    all_unusual_rows = dor.read_csv(unusual_path)
    snapshot_status = dor.write_snapshot_status(snapshot_status_path, metadata)
    quote_snapshot = dor.write_quote_snapshot(quote_snapshot_path, dor.quote_symbols_from_groups(report_groups))
    dashboard_renderer.render_html(
        args.html,
        all_agg,
        signals,
        all_contracts,
        snapshot_date,
        dor.trailing_weekdays(snapshot_date, 7),
        volume_contract_rows=all_volume_contracts,
        option_unusual_rows=all_unusual_rows,
        report_groups=report_groups,
        quote_map=quote_snapshot.get("quotes", {}),
        snapshot_status=snapshot_status,
    )
    progress["status"] = "complete"
    progress["completed_at_bjt"] = bjt_now().strftime("%Y-%m-%d %H:%M:%S BJT")
    write_progress(progress_path, progress)
    print(f"Mode:                     preopen-deadline")
    print(f"Watchlist symbols scanned: {len(watchlist)}")
    print(f"Option contracts scanned:  {total_seen}")
    print(f"Volume top contracts scanned: {volume_total_seen}")
    print(f"Option unusual rows parsed:   {len(unusual_rows)}")
    print(f"Saved progress:           {progress_path}")
    print(f"Saved HTML report:        {args.html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
