#!/usr/bin/env python3
"""
Quota-safe option volume monitor.

This script does not call request_history_kline. It uses Futu's option screen
endpoint to collect the current day's top-turnover option contracts, filters them
to the watchlist, and aggregates Call/Put volume by underlying.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import socket
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

from runtime_env import configure_runtime


DEFAULT_WATCHLIST = Path("config/watchlist.json")
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


def match_underlying(option_code: str, watchlist: list[str]) -> str | None:
    for symbol in watchlist:
        # Futu US option code format: US.TICKERYYMMDDC/P...
        if option_code.startswith(symbol):
            rest = option_code[len(symbol):]
            if rest[:1].isdigit():
                return symbol
    return None


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


def get_stock_ids(ctx: Any, watchlist: list[str]) -> dict[str, int]:
    from futu import Market, RET_OK, SecurityType

    us_codes = [code for code in watchlist if code.startswith("US.")]
    if not us_codes:
        return {}
    ret, df = ctx.get_stock_basicinfo(Market.US, SecurityType.STOCK, us_codes)
    if ret != RET_OK:
        raise RuntimeError(f"get_stock_basicinfo failed: {df}")
    result: dict[str, int] = {}
    for _, row in df.iterrows():
        code = str(row.get("code", ""))
        stock_id = safe_int(row.get("stock_id"))
        if code and stock_id:
            result[code] = stock_id
    return result


def build_screen_request(page_from: int, page_count: int, stock_id: int | None = None, sort_by: str = "turnover"):
    from futu import OptionScreenRequest, OptIndicator, OptMarketCategory, OptUnderlyingIndicator

    req = OptionScreenRequest(market_categories=[OptMarketCategory.US_STOCK])
    req.page_from = page_from
    req.page_count = page_count
    if stock_id is not None:
        req.add_underlying_filter(indicator_type=OptUnderlyingIndicator.STOCK_LIST, values=[stock_id])
    req.add_option_filter(indicator_type=OptIndicator.OPTION_TYPE, values=[1])
    req.add_option_filter(indicator_type=OptIndicator.OPTION_TYPE, values=[2], or_with_previous=True)
    sort_indicator = OptIndicator.VOLUME if sort_by.lower() == "volume" else OptIndicator.TURNOVER
    req.add_sort(indicator_type=sort_indicator, desc=True)
    for field in (
        "OPTION_TYPE",
        "STRIKE_PRICE",
        "VOLUME",
        "TURNOVER",
        "OPEN_INTEREST",
        "IMPLIED_VOLATILITY",
        "PREMIUM",
    ):
        req.add_option_retrieve(getattr(OptIndicator, field))
    return req


def collect_screen_rows(
    watchlist: list[str],
    pages: int,
    page_count: int,
    snapshot_date: str,
    request_pause: float,
    sort_by: str = "turnover",
) -> tuple[list[dict[str, Any]], int]:
    prepare_futu_import_environment()
    from futu import RET_OK

    rows: list[dict[str, Any]] = []
    total_seen = 0
    ctx = create_quote_context()
    try:
        stock_ids = get_stock_ids(ctx, watchlist)
        missing = [code for code in watchlist if code.startswith("US.") and code not in stock_ids]
        if missing:
            print(f"[warn] Missing stock_id for: {', '.join(missing)}", file=sys.stderr)
        for underlying in watchlist:
            stock_id = stock_ids.get(underlying)
            if stock_id is None:
                continue
            for page in range(pages):
                if total_seen:
                    time.sleep(request_pause)
                req = build_screen_request(
                    page_from=page * page_count,
                    page_count=page_count,
                    stock_id=stock_id,
                    sort_by=sort_by,
                )
                ret, data = ctx.get_option_screen(req)
                if ret != RET_OK:
                    print(f"[warn] get_option_screen({underlying}, sort_by={sort_by}) failed: {data}", file=sys.stderr)
                    break
                last_page, _all_count, df = data
                if df is None or df.empty:
                    break
                total_seen += len(df)
                for _, item in df.iterrows():
                    code = str(item.get("code", ""))
                    rows.append(
                        {
                            "snapshot_date": snapshot_date,
                            "underlying": underlying,
                            "option_code": code,
                            "option_type": option_type_name(item.get("option_type")),
                            "strike": safe_float(item.get("strike_price")),
                            "volume": safe_int(item.get("volume")),
                            "turnover": safe_float(item.get("turnover")),
                            "open_interest": safe_int(item.get("open_interest")),
                            "implied_volatility": safe_float(item.get("implied_volatility")),
                            "premium": safe_float(item.get("premium")),
                        }
                    )
                if last_page:
                    break
    finally:
        ctx.close()
    return rows, total_seen


def aggregate_contracts(rows: list[dict[str, Any]], snapshot_date: str, watchlist: list[str]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for symbol in watchlist:
        grouped[symbol] = {
            "snapshot_date": snapshot_date,
            "underlying": symbol,
            "call_volume": 0,
            "put_volume": 0,
            "contracts_seen": 0,
        }
    for row in rows:
        item = grouped[row["underlying"]]
        item["contracts_seen"] += 1
        if row["option_type"] == "CALL":
            item["call_volume"] += safe_int(row["volume"])
        elif row["option_type"] == "PUT":
            item["put_volume"] += safe_int(row["volume"])

    output = []
    for item in grouped.values():
        call_volume = item["call_volume"]
        put_volume = item["put_volume"]
        total = call_volume + put_volume
        output.append(
            {
                **item,
                "total_volume": total,
                "call_share": round(call_volume / total, 4) if total else 0.0,
                "put_share": round(put_volume / total, 4) if total else 0.0,
                "put_call_ratio": round(put_volume / call_volume, 4) if call_volume else (999.0 if put_volume else 0.0),
            }
        )
    return output


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
    print(f"Scanned top-turnover option contracts: {total_seen}")
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
    parser = argparse.ArgumentParser(description="Quota-safe option screen monitor")
    parser.add_argument("--watchlist", type=Path, default=DEFAULT_WATCHLIST)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--pages", type=int, default=5, help="Number of option-screen pages to scan per underlying")
    parser.add_argument("--page-count", type=int, default=200, help="Contracts per option-screen page")
    parser.add_argument("--request-pause", type=float, default=3.2)
    parser.add_argument("--snapshot-date", default=date.today().isoformat())
    parser.add_argument("--json", action="store_true", dest="output_json")
    args = parser.parse_args()

    watchlist = load_watchlist(args.watchlist)
    contracts, total_seen = collect_screen_rows(
        watchlist=watchlist,
        pages=args.pages,
        page_count=args.page_count,
        snapshot_date=args.snapshot_date,
        request_pause=args.request_pause,
    )
    aggregates = aggregate_contracts(contracts, args.snapshot_date, watchlist)

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
