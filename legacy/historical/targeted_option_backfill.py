#!/usr/bin/env python3
"""
Targeted option-contract historical backfill with an explicit quota budget.

This script is for small historical case checks only. It first selects contracts
from option chains, then requests historical daily K data only for the selected
contracts. It defaults to dry-run; --execute is required to consume quota.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime_env import configure_runtime


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


@dataclass(frozen=True)
class Contract:
    code: str
    option_type: str
    expiry: str
    strike: float


def prepare_futu_import_environment() -> None:
    configure_runtime()


def create_quote_context():
    prepare_futu_import_environment()
    from futu import OpenQuoteContext

    host = os.getenv("FUTU_OPEND_HOST", "127.0.0.1")
    port = int(os.getenv("FUTU_OPEND_PORT", "11111"))
    return OpenQuoteContext(host=host, port=port)


def check_ret(ret: int, data: Any, action: str) -> None:
    from futu import RET_OK

    if ret != RET_OK:
        raise RuntimeError(f"{action} failed: {data}")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, "", "N/A"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return default


def norm_option_type(value: Any) -> str:
    text = str(value).upper()
    if text in ("CALL", "C", "1") or "CALL" in text:
        return "CALL"
    if text in ("PUT", "P", "2") or "PUT" in text:
        return "PUT"
    return text


def append_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_keys = set()
    if path.exists():
        with path.open("r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                existing_keys.add((row["date"], row["underlying"], row["option_code"]))
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RAW_COLUMNS)
        if not exists:
            writer.writeheader()
        for row in rows:
            key = (str(row["date"]), str(row["underlying"]), str(row["option_code"]))
            if key not in existing_keys:
                writer.writerow({col: row.get(col, "") for col in RAW_COLUMNS})
                existing_keys.add(key)


def select_contracts(
    ctx: Any,
    underlying: str,
    expiries: list[str],
    option_type: str,
    min_strike: float | None,
    max_strike: float | None,
    max_contracts: int,
    chain_pause: float,
) -> list[Contract]:
    selected: dict[str, Contract] = {}
    for expiry in expiries:
        ret, df = ctx.get_option_chain(underlying, start=expiry, end=expiry)
        check_ret(ret, df, f"get_option_chain({underlying}, {expiry})")
        if df is None or df.empty:
            continue
        for _, row in df.iterrows():
            row_type = norm_option_type(row.get("option_type"))
            if row_type != option_type:
                continue
            strike = safe_float(row.get("strike_price"), default=float("nan"))
            if math.isnan(strike):
                continue
            if min_strike is not None and strike < min_strike:
                continue
            if max_strike is not None and strike > max_strike:
                continue
            code = str(row.get("code", ""))
            if code:
                selected[code] = Contract(code=code, option_type=row_type, expiry=expiry, strike=strike)
                if len(selected) >= max_contracts:
                    return list(selected.values())
        time.sleep(chain_pause)
    return list(selected.values())[:max_contracts]


def fetch_kline(ctx: Any, underlying: str, contract: Contract, start: str, end: str) -> list[dict[str, Any]]:
    from futu import AuType, KLType

    ret, df, _page_req_key = ctx.request_history_kline(
        contract.code,
        start=start,
        end=end,
        ktype=KLType.K_DAY,
        autype=AuType.NONE,
        max_count=1000,
    )
    check_ret(ret, df, f"request_history_kline({contract.code})")
    rows = []
    if df is None or df.empty:
        return rows
    for _, row in df.iterrows():
        rows.append({
            "date": str(row.get("time_key", ""))[:10],
            "underlying": underlying,
            "option_code": contract.code,
            "option_type": contract.option_type,
            "expiry": contract.expiry,
            "strike": contract.strike,
            "volume": safe_int(row.get("volume")),
            "turnover": safe_float(row.get("turnover")),
            "close": safe_float(row.get("close")),
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Targeted option backfill with explicit K-line budget")
    parser.add_argument("--underlying", required=True)
    parser.add_argument("--option-type", choices=["CALL", "PUT"], required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--expiries", nargs="+", required=True)
    parser.add_argument("--min-strike", type=float)
    parser.add_argument("--max-strike", type=float)
    parser.add_argument("--max-contracts", type=int, required=True)
    parser.add_argument("--max-kline-requests", type=int, required=True)
    parser.add_argument("--chain-pause", type=float, default=3.3)
    parser.add_argument("--kline-pause", type=float, default=0.6)
    parser.add_argument("--output", type=Path, default=Path("data/option_contract_daily.csv"))
    parser.add_argument("--execute", action="store_true", help="Actually consume historical K-line quota")
    args = parser.parse_args()

    if args.max_contracts > args.max_kline_requests:
        raise SystemExit("--max-contracts cannot exceed --max-kline-requests")
    datetime.strptime(args.start, "%Y-%m-%d")
    datetime.strptime(args.end, "%Y-%m-%d")

    ctx = create_quote_context()
    try:
        contracts = select_contracts(
            ctx=ctx,
            underlying=args.underlying,
            expiries=args.expiries,
            option_type=args.option_type,
            min_strike=args.min_strike,
            max_strike=args.max_strike,
            max_contracts=args.max_contracts,
            chain_pause=args.chain_pause,
        )
        print(f"Selected contracts: {len(contracts)}")
        for contract in contracts[:20]:
            print(f"  {contract.code} {contract.option_type} strike={contract.strike} expiry={contract.expiry}")
        if len(contracts) > 20:
            print(f"  ... {len(contracts) - 20} more")
        print(f"Historical K-line requests planned: {len(contracts)} / budget {args.max_kline_requests}")
        if not args.execute:
            print("Dry-run only. Add --execute to consume quota and write data.")
            return 0

        all_rows: list[dict[str, Any]] = []
        for index, contract in enumerate(contracts, start=1):
            print(f"[{index}/{len(contracts)}] {contract.code}")
            all_rows.extend(fetch_kline(ctx, args.underlying, contract, args.start, args.end))
            time.sleep(args.kline_pause)
        append_rows(args.output, all_rows)
        print(f"Saved {len(all_rows)} rows to {args.output}")
    finally:
        ctx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
