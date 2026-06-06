#!/usr/bin/env python3
"""
Collect Futu derivative unusual-option rows for dashboard matching.

The current Futu skill backend returns option-unusual trades as text. This
module normalizes the useful fields so the dashboard can match them against
the mixed Top 10 option contracts.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

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
]

UNUSUAL_PATTERN = re.compile(
    r"(?P<month>\d{1,2})\.(?P<day>\d{1,2})\s+"
    r"(?P<time>\d{1,2}:\d{2}).*?"
    r"出现一笔(?P<side>买入|卖出)(?P<option_kind>看涨|看跌|认购|认沽)期权交易.*?"
    r"成交量为(?P<volume>[\d,]+)张.*?"
    r"交易金额为(?P<turnover>[\d,.]+)USD.*?"
    r"合约行权价是(?P<strike>[\d.]+).*?"
    r"到期日为(?P<expiry>\d{4}/\d{1,2}/\d{1,2})",
    re.S,
)


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


def normalize_expiry(value: str) -> str:
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return value.strip().replace("/", "-")


def normalize_option_type(value: str) -> str:
    text = str(value)
    if text in {"看涨", "认购", "CALL", "C"}:
        return "CALL"
    if text in {"看跌", "认沽", "PUT", "P"}:
        return "PUT"
    return text.upper()


def normalize_direction(value: str) -> str:
    text = str(value)
    if text in {"买入", "主动买入", "BUY"}:
        return "BUY"
    if text in {"卖出", "主动卖出", "SELL"}:
        return "SELL"
    return ""


def split_records(content: str) -> list[str]:
    records: list[str] = []
    for line in str(content or "").splitlines():
        line = line.strip()
        if not line or "出现一笔" not in line:
            continue
        records.append(line.rstrip("。"))
    return records


def parse_unusual_content_with_stats(
    content: str,
    snapshot_date: str,
    underlying: str,
    failed_example_limit: int = 3,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    records = split_records(content)
    failed_examples: list[str] = []
    for record in records:
        match = UNUSUAL_PATTERN.search(record)
        if not match:
            if len(failed_examples) < failed_example_limit:
                failed_examples.append(record)
            continue
        expiry = normalize_expiry(match.group("expiry"))
        option_type = normalize_option_type(match.group("option_kind"))
        direction = normalize_direction(match.group("side"))
        if option_type not in {"CALL", "PUT"} or direction not in {"BUY", "SELL"}:
            if len(failed_examples) < failed_example_limit:
                failed_examples.append(record)
            continue
        rows.append(
            {
                "snapshot_date": snapshot_date,
                "underlying": underlying,
                "option_code": "",
                "option_type": option_type,
                "strike": safe_float(match.group("strike")),
                "expiry": expiry,
                "volume": safe_int(match.group("volume")),
                "turnover": safe_float(match.group("turnover")),
                "direction": direction,
                "event_time": f"{int(match.group('month')):02d}-{int(match.group('day')):02d} {match.group('time')}",
                "raw_text": record,
            }
        )
    stats = {
        "raw_records": len(records),
        "parsed_records": len(rows),
        "unparsed_records": max(0, len(records) - len(rows)),
        "failed_examples": failed_examples,
    }
    return rows, stats


def parse_unusual_content(content: str, snapshot_date: str, underlying: str) -> list[dict[str, Any]]:
    rows, _stats = parse_unusual_content_with_stats(content, snapshot_date, underlying)
    return rows


def create_quote_context():
    configure_runtime()
    from futu import OpenQuoteContext

    return OpenQuoteContext(host="127.0.0.1", port=11111)


def collect_unusual_rows_with_stats(
    watchlist: list[str],
    snapshot_date: str,
    request_pause: float,
    time_range: int = 1,
    language_id: int = 0,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    configure_runtime()
    from futu import RET_OK

    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    stats: dict[str, Any] = {
        "symbols_requested": len(watchlist),
        "symbols_failed": 0,
        "raw_records": 0,
        "parsed_records": 0,
        "unparsed_records": 0,
        "failed_examples": [],
    }
    ctx = create_quote_context()
    try:
        for index, underlying in enumerate(watchlist):
            if index:
                time.sleep(request_pause)
            try:
                ret, data = ctx.get_derivative_unusual(
                    underlying,
                    time_range=time_range,
                    analysis_dimensions=["option_unusual"],
                    language_id=language_id,
                )
            except Exception as exc:
                warnings.append(f"{underlying}: get_derivative_unusual raised {type(exc).__name__}: {exc}")
                stats["symbols_failed"] += 1
                continue
            if ret != RET_OK:
                warnings.append(f"{underlying}: get_derivative_unusual failed: {data}")
                stats["symbols_failed"] += 1
                continue
            if not isinstance(data, dict):
                warnings.append(f"{underlying}: unexpected derivative unusual payload type: {type(data).__name__}")
                stats["symbols_failed"] += 1
                continue
            content = str(data.get("content") or "")
            parsed_rows, parse_stats = parse_unusual_content_with_stats(content, snapshot_date, underlying)
            rows.extend(parsed_rows)
            stats["raw_records"] += int(parse_stats["raw_records"])
            stats["parsed_records"] += int(parse_stats["parsed_records"])
            stats["unparsed_records"] += int(parse_stats["unparsed_records"])
            for example in parse_stats["failed_examples"]:
                if len(stats["failed_examples"]) < 10:
                    stats["failed_examples"].append({"underlying": underlying, "raw_text": example})
            if parse_stats["unparsed_records"]:
                warnings.append(
                    f"{underlying}: {parse_stats['unparsed_records']} option unusual records were not parsed"
                )
    finally:
        ctx.close()
    return rows, warnings, stats


def collect_unusual_rows(
    watchlist: list[str],
    snapshot_date: str,
    request_pause: float,
    time_range: int = 1,
    language_id: int = 0,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows, warnings, _stats = collect_unusual_rows_with_stats(
        watchlist=watchlist,
        snapshot_date=snapshot_date,
        request_pause=request_pause,
        time_range=time_range,
        language_id=language_id,
    )
    return rows, warnings


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=UNUSUAL_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in UNUSUAL_COLUMNS})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect option unusual rows from Futu derivative anomaly API.")
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--snapshot-date", required=True)
    parser.add_argument("--request-pause", type=float, default=3.8)
    parser.add_argument("--time-range", type=int, default=1)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    rows, warnings, stats = collect_unusual_rows_with_stats(
        watchlist=[str(symbol).upper() for symbol in args.symbols],
        snapshot_date=args.snapshot_date,
        request_pause=args.request_pause,
        time_range=args.time_range,
    )
    if args.output:
        write_rows(args.output, rows)
    if args.json:
        print(json.dumps({"rows": rows, "warnings": warnings, "stats": stats}, ensure_ascii=False, indent=2))
    else:
        print(f"Collected option unusual rows: {len(rows)}")
        print(
            "Parse stats: "
            f"{stats['parsed_records']}/{stats['raw_records']} parsed, "
            f"{stats['unparsed_records']} unparsed, "
            f"{stats['symbols_failed']} symbols failed"
        )
        for warning in warnings:
            print(f"[warn] {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
