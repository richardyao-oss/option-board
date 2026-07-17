#!/usr/bin/env python3
"""Collect structured option events from Futu for one US trading date."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from runtime_env import configure_runtime


UNUSUAL_COLUMNS = [
    "snapshot_date",
    "underlying",
    "option_code",
    "option_type",
    "strike",
    "expiry",
    "volume",
    "turnover",
    "direction",
    "event_time",
    "raw_text",
    "open_interest",
    "contract_volume",
    "vo_ratio",
    "strategy_type",
]

ET = ZoneInfo("America/New_York")
VALID_DIRECTIONS = {"BUY", "SELL", "NEUTRAL"}
VALID_OPTION_TYPES = {"CALL", "PUT"}


def safe_float(value: Any) -> float:
    try:
        if value in (None, "", "N/A"):
            return 0.0
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def safe_int(value: Any) -> int:
    try:
        return int(float(str(value or 0).replace(",", "")))
    except (TypeError, ValueError):
        return 0


def optional_number(value: Any, *, integer: bool = False) -> int | float | str:
    if value in (None, "", "N/A"):
        return ""
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return ""
    return int(number) if integer else number


def event_time_bounds(snapshot_date: str) -> tuple[float, float]:
    target = datetime.strptime(snapshot_date, "%Y-%m-%d").date()
    start = datetime.combine(target, time.min, tzinfo=ET)
    end = start + timedelta(days=1)
    return start.timestamp(), end.timestamp()


def normalize_event_records(
    records: Iterable[dict[str, Any]],
    snapshot_date: str,
    watchlist: list[str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    target = datetime.strptime(snapshot_date, "%Y-%m-%d").date()
    requested = {str(symbol).upper() for symbol in watchlist}
    seen: set[tuple[Any, ...]] = set()
    rows: list[dict[str, Any]] = []
    stats = {
        "exact_duplicates_removed": 0,
        "excluded_other_trade_date": 0,
        "invalid_records": 0,
        "neutral_records_kept": 0,
    }

    for record in records:
        option_code = str(record.get("option_code") or "").upper()
        underlying = str(record.get("owner_code") or "").upper()
        direction = str(record.get("ticker_type") or "").upper()
        option_type = str(record.get("option_type") or "").upper()
        timestamp = safe_float(record.get("fill_timestamp"))
        volume = safe_int(record.get("volume"))
        turnover = safe_float(record.get("turnover"))
        price = safe_float(record.get("price"))
        if (
            not option_code
            or underlying not in requested
            or direction not in VALID_DIRECTIONS
            or option_type not in VALID_OPTION_TYPES
            or timestamp <= 0
            or volume <= 0
        ):
            stats["invalid_records"] += 1
            continue

        event_et = datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone(ET)
        if event_et.date() != target:
            stats["excluded_other_trade_date"] += 1
            continue

        key = (option_code, timestamp, direction, price, volume, turnover)
        if key in seen:
            stats["exact_duplicates_removed"] += 1
            continue
        seen.add(key)
        if direction == "NEUTRAL":
            stats["neutral_records_kept"] += 1
        rows.append(
            {
                "snapshot_date": snapshot_date,
                "underlying": underlying,
                "option_code": option_code,
                "option_type": option_type,
                "strike": safe_float(record.get("strike_price")),
                "expiry": str(record.get("strike_time") or ""),
                "volume": volume,
                "turnover": turnover,
                "direction": direction,
                "event_time": event_et.strftime("%Y-%m-%d %H:%M:%S"),
                "raw_text": "",
                "open_interest": optional_number(record.get("total_open_interest"), integer=True),
                "contract_volume": optional_number(record.get("total_volume"), integer=True),
                "vo_ratio": optional_number(record.get("vo_ratio")),
                "strategy_type": str(record.get("strategy_type") or "").upper(),
            }
        )
    rows.sort(key=lambda row: (row["underlying"], row["event_time"], row["option_code"], row["direction"]))
    return rows, stats


def create_quote_context():
    configure_runtime()
    from futu import OpenQuoteContext

    return OpenQuoteContext(host="127.0.0.1", port=11111)


def collect_event_pages(ctx: Any, watchlist: list[str], snapshot_date: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    configure_runtime()
    from futu import EventIndicatorType, OptionEventFilter, OptionMarket, RET_OK

    start_ts, end_ts = event_time_bounds(snapshot_date)
    filters = [
        OptionEventFilter(EventIndicatorType.OWNER_LIST, security_list=watchlist),
        OptionEventFilter(
            EventIndicatorType.TIME,
            interval_min=start_ts,
            interval_max=end_ts,
            min_inclusive=True,
            max_inclusive=False,
        ),
    ]
    page: str | None = None
    seen_pages: set[str] = set()
    records: list[dict[str, Any]] = []
    expected_count: int | None = None
    pages = 0
    while True:
        ret, payload = ctx.get_option_event(
            OptionMarket.US_SECURITY,
            count=300,
            page=page,
            filter_list=filters,
        )
        if ret != RET_OK:
            raise RuntimeError(f"get_option_event failed on page {pages + 1}: {payload}")
        if not isinstance(payload, dict):
            raise RuntimeError(f"get_option_event returned {type(payload).__name__}, expected dict")
        page_count = safe_int(payload.get("all_count"))
        if expected_count is None:
            expected_count = page_count
        elif page_count != expected_count:
            raise RuntimeError(f"get_option_event all_count changed during pagination: {expected_count} -> {page_count}")
        frame = payload.get("event_list")
        if frame is None:
            raise RuntimeError("get_option_event payload is missing event_list")
        records.extend(frame.to_dict("records"))
        pages += 1
        next_page = str(payload.get("next_page") or "")
        if not next_page:
            break
        if next_page in seen_pages:
            raise RuntimeError(f"get_option_event repeated pagination cursor: {next_page}")
        seen_pages.add(next_page)
        page = next_page

    expected_count = expected_count or 0
    if len(records) != expected_count:
        raise RuntimeError(f"get_option_event pagination incomplete: received {len(records)} of {expected_count}")
    return records, {"pages": pages, "all_count": expected_count, "rows_received": len(records)}


def collect_unusual_rows_with_stats(
    watchlist: list[str],
    snapshot_date: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    normalized_watchlist = sorted({str(symbol).upper() for symbol in watchlist})
    if not normalized_watchlist:
        raise RuntimeError("get_option_event requires at least one symbol")
    ctx = create_quote_context()
    try:
        records, page_stats = collect_event_pages(ctx, normalized_watchlist, snapshot_date)
    finally:
        ctx.close()
    rows, normalize_stats = normalize_event_records(records, snapshot_date, normalized_watchlist)
    stats: dict[str, Any] = {
        "symbols_requested": len(normalized_watchlist),
        **page_stats,
        **normalize_stats,
        "rows_stored": len(rows),
    }
    return rows, stats


def collect_unusual_rows(watchlist: list[str], snapshot_date: str) -> list[dict[str, Any]]:
    rows, _stats = collect_unusual_rows_with_stats(watchlist, snapshot_date)
    return rows


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=UNUSUAL_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in UNUSUAL_COLUMNS})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect structured option events from Futu.")
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--snapshot-date", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    rows, stats = collect_unusual_rows_with_stats(
        watchlist=[str(symbol).upper() for symbol in args.symbols],
        snapshot_date=args.snapshot_date,
    )
    if args.output:
        write_rows(args.output, rows)
    if args.json:
        print(json.dumps({"rows": rows, "stats": stats}, ensure_ascii=False, indent=2))
    else:
        print(f"Collected structured option events: {len(rows)}")
        print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
