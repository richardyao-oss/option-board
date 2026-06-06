#!/usr/bin/env python3
"""
Read-only dashboard analysis helpers.

This module intentionally reads local CSV/JSON snapshots only. It does not call
Futu APIs, so it can be used for quick follow-up questions without consuming
market data quota.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import dashboard_renderer


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def safe_float(value: Any) -> float:
    try:
        text = str(value).replace(",", "").strip()
        return float(text) if text else 0.0
    except (TypeError, ValueError):
        return 0.0


def safe_int(value: Any) -> int:
    try:
        text = str(value).replace(",", "").strip()
        return int(float(text)) if text else 0
    except (TypeError, ValueError):
        return 0


def latest_snapshot_date(agg_rows: list[dict[str, str]]) -> str:
    status_path = DATA_DIR / "option_screen_snapshot_status.json"
    if status_path.exists():
        try:
            status = json.loads(status_path.read_text(encoding="utf-8-sig"))
            date = str(status.get("snapshot_date") or status.get("trade_date") or "").strip()
            if date:
                return date
        except (OSError, json.JSONDecodeError):
            pass
    dates = sorted({str(row.get("snapshot_date", "")) for row in agg_rows if row.get("snapshot_date")})
    return dates[-1] if dates else ""


def prior_row(rows: list[dict[str, str]], snapshot_date: str) -> dict[str, str] | None:
    prior = [row for row in rows if str(row.get("snapshot_date", "")) < snapshot_date]
    return sorted(prior, key=lambda row: str(row.get("snapshot_date", "")))[-1] if prior else None


def unusual_turnover_summary(rows: list[dict[str, str]]) -> dict[str, float]:
    summary = {
        "buy_call": 0.0,
        "sell_call": 0.0,
        "buy_put": 0.0,
        "sell_put": 0.0,
    }
    for row in rows:
        direction = str(row.get("direction", "")).upper()
        option_type = str(row.get("option_type", "")).upper()
        key = f"{direction.lower()}_{option_type.lower()}"
        if key in summary:
            summary[key] += safe_float(row.get("turnover"))
    summary["bullish_net"] = summary["buy_call"] + summary["sell_put"] - summary["buy_put"] - summary["sell_call"]
    return summary


def concentration_summary(contracts: list[dict[str, str]]) -> dict[str, Any]:
    turnovers = sorted((safe_float(row.get("turnover")) for row in contracts), reverse=True)
    volumes = sorted((safe_int(row.get("volume")) for row in contracts), reverse=True)
    top_turnover = turnovers[0] if turnovers else 0.0
    second_turnover = turnovers[1] if len(turnovers) > 1 else 0.0
    top_volume = volumes[0] if volumes else 0
    second_volume = volumes[1] if len(volumes) > 1 else 0
    total_turnover = sum(turnovers)
    total_volume = sum(volumes)
    turnover_share = top_turnover / total_turnover if total_turnover > 0 else 0.0
    volume_share = top_volume / total_volume if total_volume > 0 else 0.0
    return {
        "top_turnover": top_turnover,
        "top_turnover_share": turnover_share,
        "top_turnover_vs_second": top_turnover / second_turnover if second_turnover > 0 else 0.0,
        "top_volume": top_volume,
        "top_volume_share": volume_share,
        "top_volume_vs_second": top_volume / second_volume if second_volume > 0 else 0.0,
        "is_concentrated": turnover_share >= 0.35 or volume_share >= 0.35,
    }


def build_analysis(snapshot_date: str | None = None) -> list[dict[str, Any]]:
    agg_rows = read_csv(DATA_DIR / "option_screen_underlying_snapshot.csv")
    signal_rows = read_csv(DATA_DIR / "daily_option_signals.csv")
    contract_rows = read_csv(DATA_DIR / "option_screen_contract_snapshot.csv")
    volume_contract_rows = read_csv(DATA_DIR / "option_screen_volume_contract_snapshot.csv")
    unusual_rows = read_csv(DATA_DIR / "option_unusual_snapshot.csv")

    date = snapshot_date or latest_snapshot_date(agg_rows)
    signals_by_key = {(row.get("snapshot_date"), row.get("underlying")): row for row in signal_rows}

    agg_by_symbol: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in agg_rows:
        agg_by_symbol[str(row.get("underlying", ""))].append(row)

    analysis: list[dict[str, Any]] = []
    for row in agg_rows:
        if str(row.get("snapshot_date", "")) != date:
            continue
        symbol = str(row.get("underlying", ""))
        signal = signals_by_key.get((date, symbol), {})
        previous = prior_row(agg_by_symbol[symbol], date)
        direction = str(signal.get("direction") or "").upper()
        call_share = safe_float(row.get("call_share"))
        put_share = safe_float(row.get("put_share"))
        direction_share = call_share if direction == "CALL" else put_share if direction == "PUT" else 0.0
        prev_direction_share = 0.0
        if previous:
            prev_direction_share = (
                safe_float(previous.get("call_share"))
                if direction == "CALL"
                else safe_float(previous.get("put_share"))
                if direction == "PUT"
                else 0.0
            )
        total_volume = safe_int(row.get("total_volume"))
        prev_total_volume = safe_int(previous.get("total_volume")) if previous else 0
        pcr = safe_float(row.get("put_call_ratio"))
        prev_pcr = safe_float(previous.get("put_call_ratio")) if previous else 0.0

        top_contracts = dashboard_renderer.top_contract_rows(
            contract_rows,
            date,
            symbol,
            volume_contract_rows=volume_contract_rows,
        )
        matched_unusual = dashboard_renderer.matched_unusual_rows(top_contracts, unusual_rows, date, symbol)
        concentration = concentration_summary(top_contracts)
        unusual_summary = unusual_turnover_summary(matched_unusual)

        direction_x_base = safe_float(signal.get("direction_x_base"))
        total_x_base = safe_float(signal.get("total_x_base"))
        direction_share_delta = direction_share - prev_direction_share
        strength = "none"
        if direction in {"CALL", "PUT"} and direction_x_base >= 1.25 and total_x_base >= 1.50 and direction_share_delta >= 0.05:
            strength = "strong"
        elif direction in {"CALL", "PUT"} and direction_x_base >= 1.10 and total_x_base >= 1.20:
            strength = "medium"

        analysis.append(
            {
                "snapshot_date": date,
                "underlying": symbol,
                "direction": direction or "NONE",
                "strength": strength,
                "score": safe_float(signal.get("score")),
                "total_volume": total_volume,
                "volume_change_pct": (total_volume / prev_total_volume - 1) if prev_total_volume > 0 else None,
                "put_call_ratio": pcr,
                "pcr_delta": pcr - prev_pcr if previous else None,
                "call_share": call_share,
                "put_share": put_share,
                "direction_share_delta": direction_share_delta if previous else None,
                "direction_x_base": direction_x_base,
                "total_x_base": total_x_base,
                "matched_unusual_count": len(matched_unusual),
                **concentration,
                **unusual_summary,
            }
        )
    return sorted(analysis, key=lambda item: (item["strength"] != "strong", -item["score"], -item["total_volume"]))


def pct(value: Any) -> str:
    if value is None:
        return "--"
    return f"{safe_float(value) * 100:+.0f}%"


def print_table(rows: list[dict[str, Any]], limit: int) -> None:
    columns = [
        ("underlying", "symbol"),
        ("direction", "dir"),
        ("strength", "strength"),
        ("score", "score"),
        ("direction_x_base", "dir_x"),
        ("total_x_base", "vol_x"),
        ("direction_share_delta", "share_chg"),
        ("volume_change_pct", "vol_chg"),
        ("put_call_ratio", "P/C"),
        ("pcr_delta", "P/C_chg"),
        ("is_concentrated", "large"),
        ("matched_unusual_count", "unusual"),
    ]
    print(" | ".join(title for _key, title in columns))
    print(" | ".join("---" for _key, _title in columns))
    for row in rows[:limit]:
        values: list[str] = []
        for key, _title in columns:
            value = row.get(key)
            if key in {"score", "direction_x_base", "total_x_base", "put_call_ratio", "pcr_delta"}:
                values.append(f"{safe_float(value):.2f}")
            elif key in {"direction_share_delta", "volume_change_pct"}:
                values.append(pct(value))
            elif key == "is_concentrated":
                values.append("Y" if value else "")
            else:
                values.append(str(value))
        print(" | ".join(values))


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze local option dashboard snapshots without fetching data.")
    parser.add_argument("--date", default=None)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    rows = build_analysis(args.date)
    if args.json:
        print(json.dumps(rows[: args.top], ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("No local dashboard rows found.")
        return 1
    print(f"Local analysis date: {rows[0]['snapshot_date']}")
    print_table(rows, args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
