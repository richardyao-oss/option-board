#!/usr/bin/env python3
"""
Daily option-flow anomaly monitor.

This prototype pulls option-contract daily volume from Futu OpenAPI, aggregates
Call/Put volume by underlying, and scores directional volume anomalies.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime_env import configure_runtime

DEFAULT_WATCHLIST = Path("config/watchlist.json")
RAW_COLUMNS = [
    "date",
    "underlying",
    "option_code",
    "option_type",
    "expiry",
    "strike",
    "volume",
    "turnover",
    "close",
]
AGG_COLUMNS = [
    "date",
    "underlying",
    "call_volume",
    "put_volume",
    "total_volume",
    "call_share",
    "put_share",
    "put_call_ratio",
]
SIGNAL_COLUMNS = [
    "date",
    "underlying",
    "direction",
    "score",
    "alert",
    "call_volume",
    "put_volume",
    "total_volume",
    "call_share",
    "put_share",
    "put_call_ratio",
    "call_mult_7d",
    "put_mult_7d",
    "call_mult_15d",
    "put_mult_15d",
    "total_mult_15d",
    "pcr_rel_15d",
    "history_days",
]


@dataclass(frozen=True)
class OptionContract:
    code: str
    option_type: str
    expiry: str
    strike: float | None


class QuotaBudgetExceeded(RuntimeError):
    pass


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def date_range(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def normalize_symbol(symbol: str) -> str:
    value = symbol.strip().upper()
    if value.endswith(".US"):
        return "US." + value[:-3]
    if value.endswith(".HK"):
        return "HK." + value[:-3]
    if "." not in value:
        return "US." + value
    return value


def load_watchlist(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    symbols = payload.get("symbols", payload if isinstance(payload, list) else [])
    if not symbols:
        raise ValueError(f"No symbols found in {path}")
    return [normalize_symbol(str(symbol)) for symbol in symbols]


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def append_rows(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    ensure_parent(path)
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def write_rows(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    ensure_parent(path)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def safe_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        if isinstance(value, float) and math.isnan(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def row_get(row: Any, key: str, default: Any = None) -> Any:
    if hasattr(row, "get"):
        return row.get(key, default)
    return getattr(row, key, default)


def normalize_option_type(value: Any) -> str:
    text = str(value).upper()
    if "CALL" in text or text in {"C", "1"} or "认购" in text or "购" in text:
        return "CALL"
    if "PUT" in text or text in {"P", "2"} or "认沽" in text or "沽" in text:
        return "PUT"
    return text


def generated_friday_expiries(start: date, end: date, lookahead_days: int) -> list[str]:
    first = start - timedelta(days=7)
    last = end + timedelta(days=lookahead_days)
    expiries = []
    for day in date_range(first, last):
        if day.weekday() == 4:
            expiries.append(day.isoformat())
    return expiries


def create_quote_context():
    prepare_futu_import_environment()
    try:
        from futu import OpenQuoteContext
    except ImportError as exc:
        raise RuntimeError("futu-api is not installed. Install/upgrade OpenD and futu-api first.") from exc

    host = os.getenv("FUTU_OPEND_HOST", "127.0.0.1")
    port = int(os.getenv("FUTU_OPEND_PORT", "11111"))
    return OpenQuoteContext(host=host, port=port)


def prepare_futu_import_environment() -> None:
    configure_runtime()


def check_ret(ret: int, data: Any, action: str) -> None:
    from futu import RET_OK

    if ret != RET_OK:
        raise RuntimeError(f"{action} failed: {data}")


def df_is_empty(data: Any) -> bool:
    return data is None or (hasattr(data, "empty") and data.empty) or len(data) == 0


def fetch_listed_expiries(ctx: Any, underlying: str) -> list[str]:
    ret, data = ctx.get_option_expiration_date(underlying)
    check_ret(ret, data, f"get_option_expiration_date({underlying})")
    if df_is_empty(data):
        return []
    values = []
    for _, row in data.iterrows():
        for key in ("strike_time", "expiry_date", "date", "time"):
            value = row_get(row, key)
            if value:
                values.append(str(value)[:10])
                break
    return values


def fetch_option_chain(ctx: Any, underlying: str, expiry: str) -> list[OptionContract]:
    ret, data = ctx.get_option_chain(underlying, start=expiry, end=expiry)
    check_ret(ret, data, f"get_option_chain({underlying}, {expiry})")
    if df_is_empty(data):
        return []

    contracts: list[OptionContract] = []
    for _, row in data.iterrows():
        code = str(row_get(row, "code", ""))
        option_type = normalize_option_type(row_get(row, "option_type", ""))
        if not code or option_type not in {"CALL", "PUT"}:
            continue
        contracts.append(
            OptionContract(
                code=code,
                option_type=option_type,
                expiry=str(row_get(row, "strike_time", expiry))[:10],
                strike=safe_float(row_get(row, "strike_price", ""), default=float("nan")),
            )
        )
    return contracts


def fetch_contract_kline(ctx: Any, contract: OptionContract, start: date, end: date) -> list[dict[str, Any]]:
    from futu import AuType, KLType

    records: list[dict[str, Any]] = []
    page_req_key = None
    while True:
        kwargs = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "ktype": KLType.K_DAY,
            "autype": AuType.NONE,
            "max_count": 1000,
        }
        if page_req_key is not None:
            kwargs["page_req_key"] = page_req_key
        ret, data, page_req_key = ctx.request_history_kline(contract.code, **kwargs)
        check_ret(ret, data, f"request_history_kline({contract.code})")
        if not df_is_empty(data):
            for _, row in data.iterrows():
                records.append(
                    {
                        "date": str(row_get(row, "time_key", ""))[:10],
                        "option_code": contract.code,
                        "option_type": contract.option_type,
                        "expiry": contract.expiry,
                        "strike": "" if contract.strike is None or math.isnan(contract.strike) else contract.strike,
                        "volume": safe_int(row_get(row, "volume", 0)),
                        "turnover": safe_float(row_get(row, "turnover", 0)),
                        "close": safe_float(row_get(row, "close", 0)),
                    }
                )
        if page_req_key is None:
            break
    return records


def raw_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return str(row["date"]), str(row["underlying"]), str(row["option_code"])


def collect_backfill(
    watchlist: list[str],
    start: date,
    end: date,
    raw_path: Path,
    expiry_lookahead_days: int,
    chain_request_pause: float,
    kline_request_pause: float,
    max_contracts_per_underlying: int | None,
    max_kline_requests: int,
) -> None:
    existing = {raw_key(row) for row in read_rows(raw_path)}
    ctx = create_quote_context()
    last_chain_call_at = 0.0
    kline_requests_used = 0

    def wait_for_chain_slot() -> None:
        nonlocal last_chain_call_at
        elapsed = time.monotonic() - last_chain_call_at
        if elapsed < chain_request_pause:
            time.sleep(chain_request_pause - elapsed)
        last_chain_call_at = time.monotonic()

    try:
        for underlying in watchlist:
            min_expiry = start - timedelta(days=7)
            max_expiry = end + timedelta(days=expiry_lookahead_days)
            candidate_expiries = set(generated_friday_expiries(start, end, expiry_lookahead_days))
            try:
                wait_for_chain_slot()
                candidate_expiries.update(fetch_listed_expiries(ctx, underlying))
            except Exception as exc:
                print(f"[warn] Could not fetch listed expiries for {underlying}: {exc}", file=sys.stderr)
            candidate_expiries = {
                expiry
                for expiry in candidate_expiries
                if min_expiry <= parse_date(expiry) <= max_expiry
            }

            contracts_by_code: dict[str, OptionContract] = {}
            for expiry in sorted(candidate_expiries):
                try:
                    wait_for_chain_slot()
                    for contract in fetch_option_chain(ctx, underlying, expiry):
                        contracts_by_code[contract.code] = contract
                    if max_contracts_per_underlying and len(contracts_by_code) >= max_contracts_per_underlying:
                        break
                except Exception as exc:
                    print(f"[warn] Skipping {underlying} expiry {expiry}: {exc}", file=sys.stderr)

            contracts = list(contracts_by_code.values())
            if max_contracts_per_underlying:
                contracts = contracts[:max_contracts_per_underlying]
            print(f"[info] {underlying}: {len(contracts)} option contracts to scan")

            pending_rows: list[dict[str, Any]] = []
            for idx, contract in enumerate(contracts, start=1):
                try:
                    if kline_requests_used >= max_kline_requests:
                        raise QuotaBudgetExceeded(
                            f"Stopped before {contract.code}: max historical K-line request budget "
                            f"reached ({max_kline_requests})."
                        )
                    kline_requests_used += 1
                    rows = fetch_contract_kline(ctx, contract, start, end)
                    for row in rows:
                        full_row = {"underlying": underlying, **row}
                        if raw_key(full_row) not in existing:
                            pending_rows.append(full_row)
                            existing.add(raw_key(full_row))
                    if len(pending_rows) >= 1000:
                        append_rows(raw_path, RAW_COLUMNS, pending_rows)
                        pending_rows = []
                    if idx % 50 == 0:
                        print(f"[info] {underlying}: scanned {idx}/{len(contracts)} contracts")
                    time.sleep(kline_request_pause)
                except Exception as exc:
                    if isinstance(exc, QuotaBudgetExceeded):
                        raise
                    print(f"[warn] Skipping {contract.code}: {exc}", file=sys.stderr)
            append_rows(raw_path, RAW_COLUMNS, pending_rows)
    finally:
        ctx.close()


def aggregate_raw(raw_rows: list[dict[str, str]], start: date | None = None, end: date | None = None) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in raw_rows:
        row_date = parse_date(row["date"])
        if start and row_date < start:
            continue
        if end and row_date > end:
            continue
        key = (row["date"], row["underlying"])
        item = grouped.setdefault(
            key,
            {
                "date": row["date"],
                "underlying": row["underlying"],
                "call_volume": 0,
                "put_volume": 0,
            },
        )
        volume = safe_int(row.get("volume"))
        if row.get("option_type") == "CALL":
            item["call_volume"] += volume
        elif row.get("option_type") == "PUT":
            item["put_volume"] += volume

    results = []
    for item in grouped.values():
        call_volume = int(item["call_volume"])
        put_volume = int(item["put_volume"])
        total = call_volume + put_volume
        results.append(
            {
                "date": item["date"],
                "underlying": item["underlying"],
                "call_volume": call_volume,
                "put_volume": put_volume,
                "total_volume": total,
                "call_share": call_volume / total if total else 0.0,
                "put_share": put_volume / total if total else 0.0,
                "put_call_ratio": put_volume / call_volume if call_volume else (float("inf") if put_volume else 0.0),
            }
        )
    return sorted(results, key=lambda r: (r["underlying"], r["date"]))


def average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def multiplier(value: float, baseline: float) -> float:
    return value / max(baseline, 1.0)


def log_component(value: float, scale: float) -> float:
    return max(0.0, math.log2(max(value, 1.0))) * scale


def score_aggregates(agg_rows: list[dict[str, Any]], min_history_days: int) -> list[dict[str, Any]]:
    by_underlying: dict[str, list[dict[str, Any]]] = {}
    for row in agg_rows:
        by_underlying.setdefault(row["underlying"], []).append(row)

    signals: list[dict[str, Any]] = []
    for underlying, rows in by_underlying.items():
        rows = sorted(rows, key=lambda r: r["date"])
        for idx, row in enumerate(rows):
            history = rows[:idx]
            history_7 = history[-7:]
            history_15 = history[-15:]
            if len(history_15) < min_history_days:
                continue

            call_volume = safe_float(row["call_volume"])
            put_volume = safe_float(row["put_volume"])
            total_volume = safe_float(row["total_volume"])
            call_share = safe_float(row["call_share"])
            put_share = safe_float(row["put_share"])
            pcr = safe_float(row["put_call_ratio"])

            call_avg_7 = average([safe_float(r["call_volume"]) for r in history_7])
            put_avg_7 = average([safe_float(r["put_volume"]) for r in history_7])
            call_avg_15 = average([safe_float(r["call_volume"]) for r in history_15])
            put_avg_15 = average([safe_float(r["put_volume"]) for r in history_15])
            total_avg_15 = average([safe_float(r["total_volume"]) for r in history_15])
            pcr_avg_15 = average([safe_float(r["put_call_ratio"]) for r in history_15 if safe_float(r["put_call_ratio"]) < 1000])

            call_mult_7 = multiplier(call_volume, call_avg_7)
            put_mult_7 = multiplier(put_volume, put_avg_7)
            call_mult_15 = multiplier(call_volume, call_avg_15)
            put_mult_15 = multiplier(put_volume, put_avg_15)
            total_mult_15 = multiplier(total_volume, total_avg_15)
            call_pcr_rel = multiplier(pcr_avg_15, pcr) if pcr else multiplier(pcr_avg_15, 0.1)
            put_pcr_rel = multiplier(pcr, pcr_avg_15)

            call_score = (
                log_component(call_mult_15, 35)
                + log_component(total_mult_15, 20)
                + call_share * 35
                + log_component(call_pcr_rel, 10)
            )
            put_score = (
                log_component(put_mult_15, 35)
                + log_component(total_mult_15, 20)
                + put_share * 35
                + log_component(put_pcr_rel, 10)
            )

            if call_score >= put_score:
                direction = "CALL"
                score = call_score
                alert = total_mult_15 >= 2.0 and call_share >= 0.65 and call_mult_15 >= 3.0
                pcr_rel = call_pcr_rel
            else:
                direction = "PUT"
                score = put_score
                alert = total_mult_15 >= 2.0 and put_share >= 0.65 and put_mult_15 >= 3.0
                pcr_rel = put_pcr_rel

            signals.append(
                {
                    "date": row["date"],
                    "underlying": underlying,
                    "direction": direction,
                    "score": round(score, 2),
                    "alert": "YES" if alert else "NO",
                    "call_volume": int(call_volume),
                    "put_volume": int(put_volume),
                    "total_volume": int(total_volume),
                    "call_share": round(call_share, 4),
                    "put_share": round(put_share, 4),
                    "put_call_ratio": round(pcr, 4),
                    "call_mult_7d": round(call_mult_7, 3),
                    "put_mult_7d": round(put_mult_7, 3),
                    "call_mult_15d": round(call_mult_15, 3),
                    "put_mult_15d": round(put_mult_15, 3),
                    "total_mult_15d": round(total_mult_15, 3),
                    "pcr_rel_15d": round(pcr_rel, 3),
                    "history_days": len(history_15),
                }
            )
    return sorted(signals, key=lambda r: (r["date"], safe_float(r["score"])), reverse=True)


def print_signal_table(signals: list[dict[str, Any]], target_date: str | None, limit: int) -> None:
    rows = [row for row in signals if not target_date or row["date"] == target_date]
    rows = rows[:limit]
    if not rows:
        print("No signals available. Backfill more dates or lower --min-history-days.")
        return
    headers = ["date", "underlying", "dir", "score", "alert", "C vol", "P vol", "C share", "P/C", "x15"]
    print(" | ".join(headers))
    print("-" * 104)
    for row in rows:
        direction_mult = row["call_mult_15d"] if row["direction"] == "CALL" else row["put_mult_15d"]
        print(
            f"{row['date']} | {row['underlying']:<7} | {row['direction']:<4} | "
            f"{row['score']:>6} | {row['alert']:<5} | {row['call_volume']:>8} | "
            f"{row['put_volume']:>8} | {row['call_share']:>7.2%} | "
            f"{row['put_call_ratio']:>5} | {direction_mult:>5}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Option-flow anomaly monitor")
    parser.add_argument("--mode", choices=["backfill", "score-only"], default="backfill")
    parser.add_argument("--watchlist", type=Path, default=DEFAULT_WATCHLIST)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--start", required=True, help="Start date, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date, YYYY-MM-DD")
    parser.add_argument("--expiry-lookahead-days", type=int, default=90)
    parser.add_argument("--chain-request-pause", type=float, default=3.1)
    parser.add_argument("--kline-request-pause", type=float, default=0.55)
    parser.add_argument("--min-history-days", type=int, default=5)
    parser.add_argument(
        "--max-kline-requests",
        type=int,
        default=0,
        help="Hard cap for historical K-line requests. Default 0 prevents quota usage.",
    )
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--target-date", default=None, help="Print only one signal date, default is --end")
    parser.add_argument("--max-contracts-per-underlying", type=int, default=None)
    parser.add_argument("--json", action="store_true", dest="output_json")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    start = parse_date(args.start)
    end = parse_date(args.end)
    if end < start:
        raise ValueError("--end must be on or after --start")

    raw_path = args.data_dir / "option_contract_daily.csv"
    agg_path = args.data_dir / "underlying_option_daily.csv"
    signal_path = args.data_dir / "option_flow_signals.csv"

    watchlist = load_watchlist(args.watchlist)
    if args.mode == "backfill":
        if args.max_kline_requests <= 0:
            raise SystemExit(
                "Refusing to use historical K-line quota without --max-kline-requests. "
                "Example: --max-kline-requests 20"
            )
        collect_backfill(
            watchlist=watchlist,
            start=start,
            end=end,
            raw_path=raw_path,
            expiry_lookahead_days=args.expiry_lookahead_days,
            chain_request_pause=args.chain_request_pause,
            kline_request_pause=args.kline_request_pause,
            max_contracts_per_underlying=args.max_contracts_per_underlying,
            max_kline_requests=args.max_kline_requests,
        )

    raw_rows = read_rows(raw_path)
    agg_rows = aggregate_raw(raw_rows)
    signals = score_aggregates(agg_rows, min_history_days=args.min_history_days)
    write_rows(agg_path, AGG_COLUMNS, agg_rows)
    write_rows(signal_path, SIGNAL_COLUMNS, signals)

    target_date = args.target_date or args.end
    if args.output_json:
        rows = [row for row in signals if row["date"] == target_date][: args.limit]
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(f"Saved raw data:      {raw_path}")
        print(f"Saved aggregates:   {agg_path}")
        print(f"Saved signal table: {signal_path}")
        print()
        print_signal_table(signals, target_date=target_date, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
