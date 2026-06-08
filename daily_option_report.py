#!/usr/bin/env python3
"""
Daily options anomaly report.

Safe by design: uses option screen snapshots only and never calls historical
K-line APIs. It can load symbols from a local file or from Futu user watchlists.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import option_screen_monitor as osm
import option_unusual_monitor as oum
import dashboard_renderer
import report_groups as rg
import sync_settings


AGG_COLUMNS = [
    "snapshot_date",
    "underlying",
    "call_volume",
    "put_volume",
    "total_volume",
    "call_share",
    "put_share",
    "put_call_ratio",
    "contracts_seen",
]
SIGNAL_COLUMNS = [
    "snapshot_date",
    "underlying",
    "direction",
    "score",
    "reason",
    "call_volume",
    "put_volume",
    "total_volume",
    "call_share",
    "put_share",
    "put_call_ratio",
    "direction_x_base",
    "total_x_base",
    "prior_direction",
    "direction_share_base",
    "reversal_bonus",
    "history_days",
]
INTRADAY_META_COLUMNS = [
    "snapshot_time",
    "snapshot_type",
    "trade_date",
    "as_of_et",
    "as_of_bjt",
]
INTRADAY_CONTRACT_COLUMNS = INTRADAY_META_COLUMNS + osm.CONTRACT_COLUMNS
INTRADAY_AGG_COLUMNS = INTRADAY_META_COLUMNS + AGG_COLUMNS
INTRADAY_SIGNAL_COLUMNS = INTRADAY_META_COLUMNS + SIGNAL_COLUMNS
SNAPSHOT_STATUS_FILE = "option_screen_snapshot_status.json"
VOLUME_CONTRACT_SNAPSHOT_FILE = "option_screen_volume_contract_snapshot.csv"
UNUSUAL_SNAPSHOT_FILE = "option_unusual_snapshot.csv"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in columns})


def quote_symbols_from_groups(report_groups: dict[str, list[str]]) -> list[str]:
    return unique_symbols([symbol for symbols in report_groups.values() for symbol in symbols])


def write_quote_snapshot(path: Path, symbols: list[str]) -> dict[str, Any]:
    bjt_now = datetime.now(ZoneInfo("Asia/Shanghai"))
    et_now = bjt_now.astimezone(ZoneInfo("America/New_York"))
    quotes = osm.fetch_market_snapshot_quotes(symbols)
    payload: dict[str, Any] = {
        "snapshot_time": bjt_now.strftime("%Y-%m-%d %H:%M:%S"),
        "as_of_bjt": bjt_now.strftime("%Y-%m-%d %H:%M:%S BJT"),
        "as_of_et": et_now.strftime("%Y-%m-%d %H:%M:%S ET"),
        "source": "get_market_snapshot",
        "quotes": quotes,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def read_quote_snapshot(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"quotes": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"quotes": {}}


def read_snapshot_status(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_snapshot_status(path: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def safe_float(value: Any) -> float:
    try:
        if value in (None, "", "N/A"):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def safe_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def load_file_watchlist(path: Path) -> list[str]:
    return osm.load_watchlist(path)


def load_futu_watchlist(group_type: str = "CUSTOM", include_system: bool = False, group_name: str | None = None) -> list[str]:
    osm.prepare_futu_import_environment()
    from futu import RET_OK, UserSecurityGroupType

    type_map = {
        "ALL": UserSecurityGroupType.ALL,
        "CUSTOM": UserSecurityGroupType.CUSTOM,
        "SYSTEM": UserSecurityGroupType.SYSTEM,
    }
    group_enum = type_map.get(group_type.upper(), UserSecurityGroupType.CUSTOM)
    ctx = osm.create_quote_context()
    codes: set[str] = set()
    try:
        ret, groups = ctx.get_user_security_group(group_enum)
        if ret != RET_OK:
            raise RuntimeError(f"get_user_security_group failed: {groups}")
        if groups is None or groups.empty:
            return []
        matched_group = False
        for _, group in groups.iterrows():
            actual_group_name = str(group.get("group_name", ""))
            actual_type = str(group.get("group_type", ""))
            if not include_system and actual_type.upper() == "SYSTEM":
                continue
            if group_name and actual_group_name.strip().casefold() != group_name.strip().casefold():
                continue
            matched_group = True
            ret, securities = ctx.get_user_security(actual_group_name)
            if ret != RET_OK:
                print(f"[warn] get_user_security({actual_group_name}) failed: {securities}", file=sys.stderr)
                continue
            if securities is None or securities.empty:
                continue
            for _, item in securities.iterrows():
                code = str(item.get("code", "")).upper()
                stock_type = str(item.get("stock_type", "")).upper()
                if code.startswith("US.") and stock_type in {"", "STOCK", "ETF"}:
                    codes.add(code)
            time.sleep(0.2)
        if group_name and not matched_group:
            available = ", ".join(str(row.get("group_name", "")) for _, row in groups.iterrows())
            raise RuntimeError(f"Futu watchlist group not found: {group_name}. Available groups: {available}")
    finally:
        ctx.close()
    return sorted(codes)


def unique_symbols(symbols: list[str]) -> list[str]:
    return sorted({str(symbol).upper() for symbol in symbols if str(symbol).strip()})


def remove_excluded_symbols(symbols: list[str]) -> list[str]:
    excluded = {str(symbol).upper() for symbol in getattr(rg, "LOW_LIQUIDITY_EXCLUDED_SYMBOLS", set())}
    return [symbol for symbol in unique_symbols(symbols) if symbol not in excluded]


def resolve_group_name(name: str, report_groups: dict[str, list[str]]) -> str:
    requested = rg.STATIC_GROUP_ALIASES.get(name.strip().casefold(), name.strip())
    for group_name in report_groups:
        if group_name.strip().casefold() == requested.casefold():
            return group_name
    available = ", ".join(report_groups)
    raise RuntimeError(f"Report group not found: {name}. Available groups: {available}")


def choose_watchlist(args: argparse.Namespace) -> tuple[list[str], dict[str, list[str]]]:
    explicit_symbols = unique_symbols(getattr(args, "symbols", None) or [])
    if explicit_symbols:
        symbols = remove_excluded_symbols(explicit_symbols)
        return symbols, {rg.COMBINED_GROUP_NAME: symbols}

    if args.watchlist_source == "file":
        primary_symbols = load_file_watchlist(args.watchlist)
        primary_name = args.group_name or rg.PRIMARY_GROUP_NAME
    else:
        primary_name = args.group_name or rg.PRIMARY_GROUP_NAME
        primary_symbols = load_futu_watchlist(
            group_type=args.group_type,
            include_system=args.include_system_groups,
            group_name=args.group_name,
        )
        if not primary_symbols:
            print("[warn] Futu watchlist is empty; falling back to config/watchlist.json", file=sys.stderr)
            primary_symbols = load_file_watchlist(args.watchlist)

    combined_symbols: list[str] = list(primary_symbols)
    for symbols in rg.STATIC_REPORT_GROUPS.values():
        combined_symbols.extend(symbols)

    report_groups: dict[str, list[str]] = {
        rg.COMBINED_GROUP_NAME: remove_excluded_symbols(combined_symbols)
    }

    scan_group_request = getattr(args, "scan_group_name", None)
    if scan_group_request:
        scan_group_name = resolve_group_name(scan_group_request, report_groups)
        scan_symbols = remove_excluded_symbols(report_groups[scan_group_name])
    else:
        scan_symbols = remove_excluded_symbols([symbol for symbols in report_groups.values() for symbol in symbols])
    return scan_symbols, report_groups


def upsert_rows(path: Path, columns: list[str], rows: list[dict[str, Any]], key_cols: list[str]) -> None:
    merged: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in read_csv(path):
        merged[tuple(str(row.get(col, "")) for col in key_cols)] = row
    for row in rows:
        merged[tuple(str(row.get(col, "")) for col in key_cols)] = row
    write_csv(path, columns, list(merged.values()))


def replace_rows_for_key(path: Path, columns: list[str], rows: list[dict[str, Any]], key_col: str, key_value: str) -> None:
    kept = [row for row in read_csv(path) if str(row.get(key_col, "")) != key_value]
    write_csv(path, columns, kept + rows)


def collection_scope(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "option_screen_turnover_sort": "turnover",
        "option_screen_turnover_pages": int(args.pages),
        "option_screen_turnover_page_count": int(args.page_count),
        "option_screen_volume_sort": "volume",
        "option_screen_volume_pages": 1,
        "option_screen_volume_page_count": int(args.volume_page_count),
        "aggregate_volume_basis": "turnover_screen_rows",
        "pcr_basis": "volume_from_turnover_screen_rows",
        "top_contract_basis": "turnover_top5_plus_volume_top10_dedup_to_10",
        "option_unusual_source": "get_derivative_unusual(option_unusual)",
        "option_unusual_time_range_days": 1,
    }


def collect_option_unusual_rows(
    watchlist: list[str],
    snapshot_date: str,
    request_pause: float,
) -> list[dict[str, Any]]:
    try:
        rows, warnings, stats = oum.collect_unusual_rows_with_stats(
            watchlist=watchlist,
            snapshot_date=snapshot_date,
            request_pause=request_pause,
            time_range=1,
            language_id=0,
        )
    except Exception as exc:
        print(f"[warn] option unusual collection failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return []
    for warning in warnings:
        print(f"[warn] option unusual: {warning}", file=sys.stderr)
    print(
        "Option unusual parse stats: "
        f"{stats['parsed_records']}/{stats['raw_records']} parsed, "
        f"{stats['unparsed_records']} unparsed, "
        f"{stats['symbols_failed']} symbols failed"
    )
    return rows


def average(rows: list[dict[str, Any]], key: str) -> float:
    return sum(safe_float(row.get(key)) for row in rows) / len(rows) if rows else 0.0


def multiplier(value: float, base: float) -> float:
    return value / max(base, 1.0)


def direction_from_shares(call_share: float, put_share: float) -> tuple[str, float]:
    if call_share >= put_share:
        return "CALL", call_share
    return "PUT", put_share


def build_signals(all_agg_rows: list[dict[str, Any]], min_total: int, min_history_days: int) -> list[dict[str, Any]]:
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in all_agg_rows:
        by_symbol[str(row["underlying"])].append(row)

    signals: list[dict[str, Any]] = []
    for symbol, rows in by_symbol.items():
        rows = sorted(rows, key=lambda row: row["snapshot_date"])
        for idx, row in enumerate(rows):
            prior = rows[max(0, idx - 6):idx]
            call_volume = safe_int(row.get("call_volume"))
            put_volume = safe_int(row.get("put_volume"))
            total_volume = safe_int(row.get("total_volume"))
            call_share = safe_float(row.get("call_share"))
            put_share = safe_float(row.get("put_share"))
            pcr = safe_float(row.get("put_call_ratio"))

            if total_volume <= 0:
                signals.append({
                    "snapshot_date": row["snapshot_date"],
                    "underlying": symbol,
                    "direction": "NONE",
                    "score": 0,
                    "reason": "暂无期权成交",
                    "call_volume": call_volume,
                    "put_volume": put_volume,
                    "total_volume": total_volume,
                    "call_share": round(call_share, 4),
                    "put_share": round(put_share, 4),
                    "put_call_ratio": round(pcr, 4),
                    "direction_x_base": "",
                    "total_x_base": "",
                    "prior_direction": "",
                    "direction_share_base": "",
                    "reversal_bonus": "",
                    "history_days": len(prior),
                })
                continue

            direction, direction_share = direction_from_shares(call_share, put_share)
            if direction == "CALL":
                direction_volume = call_volume
                direction_base = average(prior, "call_volume")
            else:
                direction_volume = put_volume
                direction_base = average(prior, "put_volume")

            total_base = average(prior, "total_volume")
            direction_x = multiplier(direction_volume, direction_base)
            total_x = multiplier(total_volume, total_base)
            history_days = len(prior)
            prior_call_share = average(prior, "call_share")
            prior_put_share = average(prior, "put_share")
            prior_direction = ""
            prior_direction_share = 0.0
            direction_share_base = 0.0
            if history_days:
                prior_direction, prior_direction_share = direction_from_shares(prior_call_share, prior_put_share)
                direction_share_base = prior_call_share if direction == "CALL" else prior_put_share

            reasons: list[str] = [f"{direction} 占比 {direction_share:.0%}"]
            if history_days:
                reasons.append(f"{direction} 较基线 {direction_x:.1f}x")
                reasons.append(f"总量较基线 {total_x:.1f}x")
            else:
                reasons.append("暂无历史基线")

            reversal_bonus = 0.0
            reversal_shift = max(0.0, direction_share - direction_share_base)
            has_direction_reversal = (
                history_days
                and total_volume >= min_total
                and prior_direction in {"CALL", "PUT"}
                and prior_direction != direction
                and prior_direction_share >= 0.55
                and direction_share >= 0.60
                and reversal_shift >= 0.20
            )
            if has_direction_reversal:
                reversal_bonus = min(15.0, reversal_shift * 30)
                reasons.append(f"方向反转 {prior_direction}->{direction}, 占比提升 {reversal_shift:.0%}")

            score = direction_share * 40
            if history_days:
                score += min(direction_x, 5.0) * 8 + min(total_x, 5.0) * 6
            else:
                score += 20
            score += reversal_bonus
            signals.append({
                "snapshot_date": row["snapshot_date"],
                "underlying": symbol,
                "direction": direction,
                "score": round(score, 2),
                "reason": "; ".join(reasons),
                "call_volume": call_volume,
                "put_volume": put_volume,
                "total_volume": total_volume,
                "call_share": round(call_share, 4),
                "put_share": round(put_share, 4),
                "put_call_ratio": round(pcr, 4),
                "direction_x_base": round(direction_x, 3) if history_days else "",
                "total_x_base": round(total_x, 3) if history_days else "",
                "prior_direction": prior_direction if history_days else "",
                "direction_share_base": round(direction_share_base, 4) if history_days else "",
                "reversal_bonus": round(reversal_bonus, 2) if reversal_bonus else "",
                "history_days": history_days,
            })
    return sorted(signals, key=lambda row: (row["snapshot_date"], safe_float(row["score"])), reverse=True)


def nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    current = date(year, month, 1)
    while current.weekday() != weekday:
        current += timedelta(days=1)
    return current + timedelta(days=7 * (nth - 1))


def last_weekday(year: int, month: int, weekday: int) -> date:
    current = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    while current.weekday() != weekday:
        current -= timedelta(days=1)
    return current


def observed_fixed_holiday(year: int, month: int, day: int) -> date:
    holiday = date(year, month, day)
    if holiday.weekday() == 5:
        return holiday - timedelta(days=1)
    if holiday.weekday() == 6:
        return holiday + timedelta(days=1)
    return holiday


def easter_sunday(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def us_market_holidays(year: int) -> set[date]:
    holidays = {
        observed_fixed_holiday(year, 1, 1),
        nth_weekday(year, 1, 0, 3),
        nth_weekday(year, 2, 0, 3),
        easter_sunday(year) - timedelta(days=2),
        last_weekday(year, 5, 0),
        observed_fixed_holiday(year, 6, 19),
        observed_fixed_holiday(year, 7, 4),
        nth_weekday(year, 9, 0, 1),
        nth_weekday(year, 11, 3, 4),
        observed_fixed_holiday(year, 12, 25),
    }
    return holidays


def is_us_trading_day(day: date) -> bool:
    return day.weekday() < 5 and day not in us_market_holidays(day.year)


def previous_weekday(day: date) -> date:
    current = day - timedelta(days=1)
    while not is_us_trading_day(current):
        current -= timedelta(days=1)
    return current


def trailing_weekdays(end_day: str, count: int = 7) -> list[str]:
    current = datetime.strptime(end_day, "%Y-%m-%d").date()
    days: list[str] = []
    while len(days) < count:
        if is_us_trading_day(current):
            days.append(current.isoformat())
        current -= timedelta(days=1)
    return list(reversed(days))


def current_us_trade_date(now: datetime | None = None) -> date:
    et_now = now.astimezone(ZoneInfo("America/New_York")) if now else datetime.now(ZoneInfo("America/New_York"))
    current = et_now.date()
    while not is_us_trading_day(current):
        current -= timedelta(days=1)
    return current


def us_market_minutes(now: datetime | None = None) -> tuple[date, int, bool]:
    et_now = now.astimezone(ZoneInfo("America/New_York")) if now else datetime.now(ZoneInfo("America/New_York"))
    minutes = et_now.hour * 60 + et_now.minute
    return et_now.date(), minutes, is_us_trading_day(et_now.date())


def is_us_regular_session(now: datetime | None = None) -> bool:
    trade_day, minutes, is_trading_day = us_market_minutes(now)
    return is_trading_day and (9 * 60 + 30) <= minutes < (16 * 60)


def last_completed_us_trade_date(now: datetime | None = None) -> date:
    trade_day, minutes, is_trading_day = us_market_minutes(now)
    if is_trading_day and minutes >= 16 * 60:
        return trade_day
    return previous_weekday(trade_day)


def ensure_preopen_collection_window(allow_market_hours: bool = False) -> None:
    if allow_market_hours or not is_us_regular_session():
        return
    et_now = datetime.now(ZoneInfo("America/New_York"))
    raise RuntimeError(
        "Refusing preopen collection during the US regular session "
        f"({et_now:%Y-%m-%d %H:%M:%S ET}). Use --mode intraday now, "
        "or run --mode preopen before the next US open / after the US close."
    )


def intraday_metadata(trade_date: str, scope: dict[str, Any] | None = None) -> dict[str, Any]:
    return collection_metadata(trade_date, "intraday", scope)


def collection_metadata(
    trade_date: str,
    snapshot_type: str,
    scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    bjt_now = datetime.now(ZoneInfo("Asia/Shanghai"))
    et_now = bjt_now.astimezone(ZoneInfo("America/New_York"))
    snapshot_time = bjt_now.strftime("%Y-%m-%d %H:%M:%S")
    metadata: dict[str, Any] = {
        "snapshot_time": snapshot_time,
        "snapshot_type": snapshot_type,
        "trade_date": trade_date,
        "snapshot_date": trade_date,
        "as_of_et": et_now.strftime("%Y-%m-%d %H:%M:%S ET"),
        "as_of_bjt": bjt_now.strftime("%Y-%m-%d %H:%M:%S BJT"),
    }
    if scope:
        metadata["collection_scope"] = scope
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description="Build daily option anomaly HTML report")
    parser.add_argument("--mode", choices=["preopen", "intraday"], default="preopen")
    parser.add_argument("--watchlist-source", choices=["file", "futu-user"], default="file")
    parser.add_argument("--watchlist", type=Path, default=Path("config/watchlist.json"))
    parser.add_argument("--group-type", choices=["ALL", "CUSTOM", "SYSTEM"], default="CUSTOM")
    parser.add_argument("--group-name", default=None)
    parser.add_argument("--scan-group-name", default=None)
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--include-system-groups", action="store_true")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--snapshot-date", default=None)
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--page-count", type=int, default=200)
    parser.add_argument("--volume-page-count", type=int, default=10)
    parser.add_argument("--request-pause", type=float, default=3.2)
    parser.add_argument("--min-total", type=int, default=10000)
    parser.add_argument("--min-history-days", type=int, default=3)
    parser.add_argument("--html", type=Path, default=Path("reports/options_anomaly_report.html"))
    parser.add_argument("--allow-market-hours-preopen", action="store_true")
    args = parser.parse_args()
    args.data_dir = sync_settings.resolve_data_dir(args.data_dir)
    args.html = sync_settings.resolve_report_path(args.html)

    watchlist, report_groups = choose_watchlist(args)

    daily_contract_path = args.data_dir / "option_screen_contract_snapshot.csv"
    daily_volume_contract_path = args.data_dir / VOLUME_CONTRACT_SNAPSHOT_FILE
    unusual_path = args.data_dir / UNUSUAL_SNAPSHOT_FILE
    daily_agg_path = args.data_dir / "option_screen_underlying_snapshot.csv"
    daily_signal_path = args.data_dir / "daily_option_signals.csv"
    quote_snapshot_path = args.data_dir / "current_quote_snapshot.json"
    snapshot_status_path = args.data_dir / SNAPSHOT_STATUS_FILE
    scope = collection_scope(args)

    if args.mode == "intraday":
        snapshot_date = args.snapshot_date or current_us_trade_date().isoformat()
        metadata = intraday_metadata(snapshot_date, scope)
        contracts, total_seen = osm.collect_screen_rows(
            watchlist=watchlist,
            pages=args.pages,
            page_count=args.page_count,
            snapshot_date=snapshot_date,
            request_pause=args.request_pause,
        )
        volume_contracts, volume_total_seen = osm.collect_screen_rows(
            watchlist=watchlist,
            pages=1,
            page_count=args.volume_page_count,
            snapshot_date=snapshot_date,
            request_pause=args.request_pause,
            sort_by="volume",
        )
        unusual_rows = collect_option_unusual_rows(watchlist, snapshot_date, args.request_pause)
        aggregates = osm.aggregate_contracts(contracts, snapshot_date, watchlist)

        replace_rows_for_key(daily_contract_path, osm.CONTRACT_COLUMNS, contracts, "snapshot_date", snapshot_date)
        replace_rows_for_key(daily_volume_contract_path, osm.CONTRACT_COLUMNS, volume_contracts, "snapshot_date", snapshot_date)
        replace_rows_for_key(unusual_path, oum.UNUSUAL_COLUMNS, unusual_rows, "snapshot_date", snapshot_date)
        replace_rows_for_key(daily_agg_path, osm.AGG_COLUMNS, aggregates, "snapshot_date", snapshot_date)

        all_daily_agg = read_csv(daily_agg_path)
        all_daily_signals = build_signals(all_daily_agg, min_total=args.min_total, min_history_days=args.min_history_days)
        write_csv(daily_signal_path, SIGNAL_COLUMNS, all_daily_signals)
        all_daily_contracts = read_csv(daily_contract_path)
        all_daily_volume_contracts = read_csv(daily_volume_contract_path)
        all_unusual_rows = read_csv(unusual_path)
        snapshot_status = write_snapshot_status(snapshot_status_path, metadata)
        quote_snapshot = write_quote_snapshot(quote_snapshot_path, quote_symbols_from_groups(report_groups))
        dashboard_renderer.render_html(
            args.html,
            all_daily_agg,
            all_daily_signals,
            all_daily_contracts,
            snapshot_date,
            trailing_weekdays(snapshot_date, 7),
            volume_contract_rows=all_daily_volume_contracts,
            option_unusual_rows=all_unusual_rows,
            report_groups=report_groups,
            quote_map=quote_snapshot.get("quotes", {}),
            snapshot_status=snapshot_status,
        )

        print(f"Mode:                     intraday")
        print(f"Trade date:               {snapshot_date}")
        print(f"As of:                    {metadata['as_of_bjt']} / {metadata['as_of_et']}")
        print(f"Watchlist symbols scanned: {len(watchlist)}")
        print(f"Option contracts scanned:  {total_seen}")
        print(f"Volume top contracts scanned: {volume_total_seen}")
        print(f"Option unusual rows parsed:   {len(unusual_rows)}")
        print(f"Saved daily slot:          {daily_agg_path}")
        print(f"Saved HTML report:         {args.html}")
        return 0

    ensure_preopen_collection_window(args.allow_market_hours_preopen)
    snapshot_date = args.snapshot_date or last_completed_us_trade_date().isoformat()
    contracts, total_seen = osm.collect_screen_rows(
        watchlist=watchlist,
        pages=args.pages,
        page_count=args.page_count,
        snapshot_date=snapshot_date,
        request_pause=args.request_pause,
    )
    volume_contracts, volume_total_seen = osm.collect_screen_rows(
        watchlist=watchlist,
        pages=1,
        page_count=args.volume_page_count,
        snapshot_date=snapshot_date,
        request_pause=args.request_pause,
        sort_by="volume",
    )
    unusual_rows = collect_option_unusual_rows(watchlist, snapshot_date, args.request_pause)
    aggregates = osm.aggregate_contracts(contracts, snapshot_date, watchlist)

    replace_rows_for_key(daily_contract_path, osm.CONTRACT_COLUMNS, contracts, "snapshot_date", snapshot_date)
    replace_rows_for_key(daily_volume_contract_path, osm.CONTRACT_COLUMNS, volume_contracts, "snapshot_date", snapshot_date)
    replace_rows_for_key(unusual_path, oum.UNUSUAL_COLUMNS, unusual_rows, "snapshot_date", snapshot_date)
    replace_rows_for_key(daily_agg_path, osm.AGG_COLUMNS, aggregates, "snapshot_date", snapshot_date)

    all_agg = read_csv(daily_agg_path)
    signals = build_signals(all_agg, min_total=args.min_total, min_history_days=args.min_history_days)
    write_csv(daily_signal_path, SIGNAL_COLUMNS, signals)
    all_contracts = read_csv(daily_contract_path)
    all_volume_contracts = read_csv(daily_volume_contract_path)
    all_unusual_rows = read_csv(unusual_path)
    metadata = collection_metadata(snapshot_date, "complete", scope)
    snapshot_status = write_snapshot_status(snapshot_status_path, metadata)
    quote_snapshot = write_quote_snapshot(quote_snapshot_path, quote_symbols_from_groups(report_groups))
    dashboard_renderer.render_html(
        args.html,
        all_agg,
        signals,
        all_contracts,
        snapshot_date,
        trailing_weekdays(snapshot_date, 7),
        volume_contract_rows=all_volume_contracts,
        option_unusual_rows=all_unusual_rows,
        report_groups=report_groups,
        quote_map=quote_snapshot.get("quotes", {}),
        snapshot_status=snapshot_status,
    )

    print(f"Mode:                     preopen")
    print(f"Watchlist symbols scanned: {len(watchlist)}")
    print(f"Option contracts scanned:  {total_seen}")
    print(f"Volume top contracts scanned: {volume_total_seen}")
    print(f"Option unusual rows parsed:   {len(unusual_rows)}")
    print(f"Saved signals:            {daily_signal_path}")
    print(f"Saved HTML report:        {args.html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
