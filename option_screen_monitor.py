#!/usr/bin/env python3
"""
Quota-safe option contract-rank monitor.

This script does not call request_history_kline. It uses Futu's option-rank
endpoint to collect the requested day's top option contracts.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import socket
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

from runtime_env import configure_runtime


DEFAULT_WATCHLIST = Path("config/watchlist.json")
# OpenD allows 60 first-page option-rank requests per rolling 30 seconds.
DEFAULT_REQUEST_PAUSE = 0.51
CONTRACT_COLUMNS = [
    "snapshot_date",
    "underlying",
    "option_code",
    "option_type",
    "strike",
    "volume",
    "turnover",
    "open_interest",
    "implied_volatility",
    "premium",
]
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
    "volume_basis",
]


def add_local_packages() -> None:
    return


def prepare_futu_import_environment() -> None:
    configure_runtime()


add_local_packages()


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
    return [normalize_symbol(str(symbol)) for symbol in symbols]


def safe_float(value: Any, default: float = 0.0) -> float:
    if value in (None, "", "N/A"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def option_type_name(value: Any) -> str:
    text = str(value).upper()
    if text in {"1", "CALL"} or "CALL" in text:
        return "CALL"
    if text in {"2", "PUT"} or "PUT" in text:
        return "PUT"
    return text


def option_strike(option_code: str) -> float:
    match = re.search(r"\d{6}[CP](\d+)$", option_code)
    return safe_float(match.group(1)) / 1000 if match else 0.0


def rank_contract_row(item: Any, snapshot_date: str, underlying: str) -> dict[str, Any]:
    code = str(item.get("code", ""))
    return {
        "snapshot_date": snapshot_date,
        "underlying": underlying,
        "option_code": code,
        "option_type": option_type_name(item.get("option_type")),
        "strike": option_strike(code),
        "volume": safe_int(item.get("volume")),
        "turnover": safe_float(item.get("turnover")),
        "open_interest": safe_int(item.get("open_interest")),
        "implied_volatility": safe_float(item.get("iv")) / 100,
        "premium": "",
    }


def write_rows(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in columns})


def append_dedup(path: Path, columns: list[str], rows: list[dict[str, Any]], key_cols: list[str]) -> None:
    existing: dict[tuple[str, ...], dict[str, Any]] = {}
    if path.exists():
        with path.open("r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                existing[tuple(str(row.get(col, "")) for col in key_cols)] = row
    for row in rows:
        existing[tuple(str(row.get(col, "")) for col in key_cols)] = row
    write_rows(path, columns, list(existing.values()))


def opend_endpoint() -> tuple[str, int]:
    host = os.getenv("FUTU_OPEND_HOST", "127.0.0.1")
    port = int(os.getenv("FUTU_OPEND_PORT", "11111"))
    return host, port


def ensure_opend_port(timeout: float = 1.5) -> None:
    host, port = opend_endpoint()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return
    except OSError as exc:
        raise RuntimeError(
            f"OpenD is not reachable at {host}:{port}. Please start and log in to Futu OpenD first."
        ) from exc


def create_quote_context():
    prepare_futu_import_environment()
    ensure_opend_port()
    from futu import OpenQuoteContext

    host, port = opend_endpoint()
    return OpenQuoteContext(host=host, port=port)


def collect_rank_rows(
    watchlist: list[str],
    pages: int,
    page_count: int,
    snapshot_date: str,
    request_pause: float,
    sort_by: str = "turnover",
) -> tuple[list[dict[str, Any]], int]:
    prepare_futu_import_environment()
    from futu import (
        OptionMarket,
        OptionRankFilter,
        OptionRankIndicatorType,
        OptionRankType,
        RET_OK,
    )

    if pages < 1 or not 1 <= page_count <= 200:
        raise ValueError("option rank pages must be positive and page_count must be in [1, 200]")

    rows: list[dict[str, Any]] = []
    total_seen = 0
    rank_type = OptionRankType.VOLUME if sort_by.lower() == "volume" else OptionRankType.TURNOVER
    ctx = create_quote_context()
    try:
        for symbol_index, underlying in enumerate(watchlist):
            if symbol_index:
                time.sleep(request_pause)
            page = None
            symbol_rows = 0
            for _ in range(pages):
                owner = OptionRankFilter(
                    OptionRankIndicatorType.OWNER_LIST,
                    security_list=[underlying],
                )
                market = OptionMarket.US_INDEX if underlying == "US..VIX" else OptionMarket.US_SECURITY
                for attempt in range(3):
                    ret, df, next_page, _all_count = ctx.get_option_rank(
                        market,
                        rank_type,
                        count=page_count,
                        trading_date=snapshot_date,
                        page=page,
                        filter_list=[owner],
                    )
                    if ret == RET_OK:
                        page = next_page
                        break
                    if attempt < 2:
                        time.sleep(1)
                if ret != RET_OK:
                    raise RuntimeError(f"get_option_rank({underlying}, sort_by={sort_by}) failed: {df}")
                if df is None or df.empty:
                    break
                total_seen += len(df)
                symbol_rows += len(df)
                for _, item in df.iterrows():
                    rows.append(rank_contract_row(item, snapshot_date, underlying))
                if not page:
                    break
            if not symbol_rows:
                raise RuntimeError(f"get_option_rank returned no {sort_by} contracts for {underlying}")
    finally:
        ctx.close()
    return rows, total_seen


def aggregate_overview_records(
    records: list[dict[str, Any]],
    snapshot_date: str,
    watchlist: list[str],
    contracts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    requested = {str(symbol).upper() for symbol in watchlist}
    by_code: dict[str, dict[str, Any]] = {}
    for record in records:
        code = str(record.get("code") or "").upper()
        if code in by_code:
            raise RuntimeError(f"get_option_underlying_overview returned duplicate symbol: {code}")
        by_code[code] = record
    returned = set(by_code)
    if returned != requested:
        missing = sorted(requested - returned)
        unexpected = sorted(returned - requested)
        raise RuntimeError(
            "get_option_underlying_overview coverage mismatch: "
            f"missing={missing or 'none'}, unexpected={unexpected or 'none'}"
        )

    contracts_seen: dict[str, int] = {symbol: 0 for symbol in requested}
    for contract in contracts:
        symbol = str(contract.get("underlying") or "").upper()
        if symbol in contracts_seen:
            contracts_seen[symbol] += 1

    aggregates: list[dict[str, Any]] = []
    for symbol in watchlist:
        code = str(symbol).upper()
        record = by_code[code]
        call_volume = safe_int(record.get("call_volume"))
        put_volume = safe_int(record.get("put_volume"))
        total = call_volume + put_volume
        aggregates.append(
            {
                "snapshot_date": snapshot_date,
                "underlying": code,
                "call_volume": call_volume,
                "put_volume": put_volume,
                "total_volume": total,
                "call_share": round(call_volume / total, 4) if total else 0.0,
                "put_share": round(put_volume / total, 4) if total else 0.0,
                "put_call_ratio": round(put_volume / call_volume, 4) if call_volume else (999.0 if put_volume else 0.0),
                "contracts_seen": contracts_seen[code],
                "volume_basis": "underlying_overview",
            }
        )
    return aggregates


def collect_overview_aggregates(
    watchlist: list[str],
    snapshot_date: str,
    contracts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    prepare_futu_import_environment()
    from futu import RET_OK

    normalized = [str(symbol).upper() for symbol in watchlist]
    ctx = create_quote_context()
    try:
        ret, frame = ctx.get_option_underlying_overview(normalized)
    finally:
        ctx.close()
    if ret != RET_OK:
        raise RuntimeError(f"get_option_underlying_overview failed: {frame}")
    if frame is None:
        raise RuntimeError("get_option_underlying_overview returned no DataFrame")
    return aggregate_overview_records(frame.to_dict("records"), snapshot_date, normalized, contracts)


def fetch_market_snapshot_quotes(symbols: list[str]) -> dict[str, dict[str, Any]]:
    codes = sorted(
        {
            str(symbol).upper()
            for symbol in symbols
            if str(symbol).upper().startswith("US.") and not str(symbol).upper().startswith("US..")
        }
    )
    if not codes:
        return {}

    prepare_futu_import_environment()
    from futu import RET_OK

    quotes: dict[str, dict[str, Any]] = {}
    ctx = create_quote_context()
    try:
        for start in range(0, len(codes), 100):
            batch = codes[start : start + 100]
            ret, df = ctx.get_market_snapshot(batch)
            if ret != RET_OK:
                print(f"[warn] get_market_snapshot failed: {df}", file=sys.stderr)
                continue
            for _, item in df.iterrows():
                code = str(item.get("code", ""))
                last_price = safe_float(item.get("last_price"))
                prev_close = safe_float(item.get("prev_close_price"))
                change_ratio = (last_price - prev_close) / prev_close * 100 if last_price and prev_close else 0.0
                if code and last_price:
                    quotes[code] = {
                        "stock_price": round(last_price, 4),
                        "change_ratio": round(change_ratio, 4),
                        "update_time": str(item.get("update_time", "")),
                    }
    finally:
        ctx.close()
    return quotes


def print_table(rows: list[dict[str, Any]], total_seen: int) -> None:
    print(f"Scanned top-turnover option rank contracts: {total_seen}")
    print("underlying | contracts | call_vol | put_vol | total | call_share | put/call")
    print("-" * 82)
    for row in rows:
        print(
            f"{row['underlying']:<10} | {row['contracts_seen']:>9} | "
            f"{row['call_volume']:>8} | {row['put_volume']:>7} | "
            f"{row['total_volume']:>5} | {row['call_share']:>10.2%} | "
            f"{row['put_call_ratio']:>8}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Quota-safe option contract-rank monitor")
    parser.add_argument("--watchlist", type=Path, default=DEFAULT_WATCHLIST)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--pages", type=int, default=5, help="Number of option-rank pages per underlying")
    parser.add_argument("--page-count", type=int, default=200, help="Contracts per option-rank page")
    parser.add_argument("--request-pause", type=float, default=DEFAULT_REQUEST_PAUSE)
    parser.add_argument("--snapshot-date", default=date.today().isoformat())
    parser.add_argument("--json", action="store_true", dest="output_json")
    args = parser.parse_args()

    watchlist = load_watchlist(args.watchlist)
    contracts, total_seen = collect_rank_rows(
        watchlist=watchlist,
        pages=args.pages,
        page_count=args.page_count,
        snapshot_date=args.snapshot_date,
        request_pause=args.request_pause,
    )
    aggregates = collect_overview_aggregates(watchlist, args.snapshot_date, contracts)

    contract_path = args.data_dir / "option_screen_contract_snapshot.csv"
    aggregate_path = args.data_dir / "option_screen_underlying_snapshot.csv"
    append_dedup(contract_path, CONTRACT_COLUMNS, contracts, ["snapshot_date", "option_code"])
    append_dedup(aggregate_path, AGG_COLUMNS, aggregates, ["snapshot_date", "underlying"])

    if args.output_json:
        print(json.dumps({"total_seen": total_seen, "aggregates": aggregates, "contracts": contracts}, ensure_ascii=False, indent=2))
    else:
        print(f"Saved contract snapshot:  {contract_path}")
        print(f"Saved aggregate snapshot: {aggregate_path}")
        print_table(aggregates, total_seen)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
