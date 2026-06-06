#!/usr/bin/env python3
"""
Historical case validator for option-flow signals.

Default mode is read-only: it analyzes local CSV data and never calls Futu.
Quota-consuming fetches must be done with option_flow_monitor.py and an explicit
--max-kline-requests budget.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


RAW_PATH = Path("data/option_contract_daily.csv")
REPORT_COLUMNS = [
    "date",
    "underlying",
    "call_volume",
    "put_volume",
    "total_volume",
    "call_share",
    "put_share",
    "put_call_ratio",
    "call_x7",
    "put_x7",
    "total_x7",
    "call_x15",
    "put_x15",
    "total_x15",
    "direction",
    "note",
]


@dataclass
class DailyFlow:
    date: str
    underlying: str
    call_volume: int
    put_volume: int
    contracts_seen: int

    @property
    def total_volume(self) -> int:
        return self.call_volume + self.put_volume

    @property
    def call_share(self) -> float:
        return self.call_volume / self.total_volume if self.total_volume else 0.0

    @property
    def put_share(self) -> float:
        return self.put_volume / self.total_volume if self.total_volume else 0.0

    @property
    def put_call_ratio(self) -> float:
        if self.call_volume:
            return self.put_volume / self.call_volume
        return 999.0 if self.put_volume else 0.0


def safe_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def load_daily_flows(path: Path, underlying: str) -> list[DailyFlow]:
    grouped: dict[str, dict[str, Any]] = defaultdict(lambda: {"CALL": 0, "PUT": 0, "contracts": set()})
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("underlying") != underlying:
                continue
            date = row["date"]
            option_type = row.get("option_type")
            if option_type in ("CALL", "PUT"):
                grouped[date][option_type] += safe_int(row.get("volume"))
                grouped[date]["contracts"].add(row.get("option_code"))
    return [
        DailyFlow(
            date=date,
            underlying=underlying,
            call_volume=int(values["CALL"]),
            put_volume=int(values["PUT"]),
            contracts_seen=len(values["contracts"]),
        )
        for date, values in sorted(grouped.items())
    ]


def avg(rows: list[DailyFlow], attr: str) -> float:
    if not rows:
        return 0.0
    return sum(float(getattr(row, attr)) for row in rows) / len(rows)


def mult(value: float, baseline: float) -> float:
    return value / max(baseline, 1.0)


def direction_note(row: DailyFlow, call_x7: float, put_x7: float, total_x7: float) -> tuple[str, str]:
    if row.call_share >= 0.65 and call_x7 >= 2.0 and total_x7 >= 2.0:
        return "CALL", "call-volume breakout"
    if row.put_share >= 0.65 and put_x7 >= 2.0 and total_x7 >= 2.0:
        return "PUT", "put-volume breakout"
    if row.call_share >= 0.75 and call_x7 >= 1.5:
        return "CALL", "call-heavy"
    if row.put_share >= 0.75 and put_x7 >= 1.5:
        return "PUT", "put-heavy"
    return "", ""


def build_report(rows: list[DailyFlow]) -> list[dict[str, Any]]:
    report: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        h7 = rows[max(0, idx - 7):idx]
        h15 = rows[max(0, idx - 15):idx]
        call_x7 = mult(row.call_volume, avg(h7, "call_volume"))
        put_x7 = mult(row.put_volume, avg(h7, "put_volume"))
        total_x7 = mult(row.total_volume, avg(h7, "total_volume"))
        call_x15 = mult(row.call_volume, avg(h15, "call_volume"))
        put_x15 = mult(row.put_volume, avg(h15, "put_volume"))
        total_x15 = mult(row.total_volume, avg(h15, "total_volume"))
        direction, note = direction_note(row, call_x7, put_x7, total_x7)
        report.append({
            "date": row.date,
            "underlying": row.underlying,
            "call_volume": row.call_volume,
            "put_volume": row.put_volume,
            "total_volume": row.total_volume,
            "call_share": round(row.call_share, 4),
            "put_share": round(row.put_share, 4),
            "put_call_ratio": round(row.put_call_ratio, 4),
            "call_x7": round(call_x7, 3),
            "put_x7": round(put_x7, 3),
            "total_x7": round(total_x7, 3),
            "call_x15": round(call_x15, 3),
            "put_x15": round(put_x15, 3),
            "total_x15": round(total_x15, 3),
            "direction": direction,
            "note": note,
            "contracts_seen": row.contracts_seen,
        })
    return report


def write_report(path: Path, report: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=REPORT_COLUMNS)
        writer.writeheader()
        for row in report:
            writer.writerow({col: row.get(col, "") for col in REPORT_COLUMNS})


def print_report(report: list[dict[str, Any]], dates: set[str] | None = None) -> None:
    selected = [row for row in report if not dates or row["date"] in dates]
    if not selected:
        print("No local case data found.")
        return
    print("date | call | put | total | call% | p/c | call_x7 | put_x7 | total_x7 | note")
    print("-" * 96)
    for row in selected:
        print(
            f"{row['date']} | {row['call_volume']:>6} | {row['put_volume']:>6} | "
            f"{row['total_volume']:>6} | {row['call_share']:>5.1%} | "
            f"{row['put_call_ratio']:>5} | {row['call_x7']:>7} | "
            f"{row['put_x7']:>6} | {row['total_x7']:>8} | {row['note']}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate historical option-flow cases from local data")
    parser.add_argument("--underlying", required=True, help="Underlying code, for example US.NOW")
    parser.add_argument("--dates", nargs="*", help="Specific dates to print, YYYY-MM-DD")
    parser.add_argument("--raw", type=Path, default=RAW_PATH)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--json", action="store_true", dest="output_json")
    args = parser.parse_args()

    rows = load_daily_flows(args.raw, args.underlying)
    report = build_report(rows)
    output = args.output or Path("data") / f"case_validation_{args.underlying.replace('.', '_')}.csv"
    write_report(output, report)
    if args.output_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Saved report: {output}")
        print_report(report, set(args.dates or []))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
