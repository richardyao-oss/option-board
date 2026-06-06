#!/usr/bin/env python3
"""
Daily options anomaly report.

Safe by design: uses option screen snapshots only and never calls historical
K-line APIs. It can load symbols from a local file or from Futu user watchlists.
"""

from __future__ import annotations

import argparse
import csv
import html
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


def write_snapshot_status(path: Path, metadata: dict[str, str]) -> dict[str, str]:
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


def recent_signal_symbols(signals: list[dict[str, Any]], display_dates: list[str]) -> set[str]:
    recent_dates = set(display_dates)
    return {str(row["underlying"]) for row in signals if row["snapshot_date"] in recent_dates}


def top_contracts(contract_rows: list[dict[str, str]], snapshot_date: str, symbol: str, direction: str, limit: int = 5) -> list[dict[str, str]]:
    rows = [
        row for row in contract_rows
        if row.get("snapshot_date") == snapshot_date
        and row.get("underlying") == symbol
        and row.get("option_type") == direction
    ]
    rows.sort(key=lambda row: safe_int(row.get("volume")), reverse=True)
    return rows[:limit]


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


def intraday_metadata(trade_date: str) -> dict[str, str]:
    return collection_metadata(trade_date, "intraday")


def collection_metadata(trade_date: str, snapshot_type: str) -> dict[str, str]:
    bjt_now = datetime.now(ZoneInfo("Asia/Shanghai"))
    et_now = bjt_now.astimezone(ZoneInfo("America/New_York"))
    snapshot_time = bjt_now.strftime("%Y-%m-%d %H:%M:%S")
    return {
        "snapshot_time": snapshot_time,
        "snapshot_type": snapshot_type,
        "trade_date": trade_date,
        "snapshot_date": trade_date,
        "as_of_et": et_now.strftime("%Y-%m-%d %H:%M:%S ET"),
        "as_of_bjt": bjt_now.strftime("%Y-%m-%d %H:%M:%S BJT"),
    }


def with_metadata(rows: list[dict[str, Any]], metadata: dict[str, str]) -> list[dict[str, Any]]:
    return [{**metadata, **row} for row in rows]


def read_intraday_files(data_dir: Path) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    return (
        read_csv(data_dir / "option_screen_intraday_underlying_snapshot.csv"),
        read_csv(data_dir / "intraday_option_signals.csv"),
        read_csv(data_dir / "option_screen_intraday_contract_snapshot.csv"),
    )


def bar_html(call_share: float, put_share: float) -> str:
    if call_share <= 0 and put_share <= 0:
        return "<div class='bar empty-bar'></div>"
    call_pct = max(0.0, min(1.0, call_share)) * 100
    put_pct = max(0.0, min(1.0, put_share)) * 100
    return (
        "<div class='bar'>"
        f"<div class='bar-call' style='width:{call_pct:.2f}%'></div>"
        f"<div class='bar-put' style='width:{put_pct:.2f}%'></div>"
        "</div>"
    )


def render_html(
    html_path: Path,
    agg_rows: list[dict[str, Any]],
    signal_rows: list[dict[str, Any]],
    contract_rows: list[dict[str, str]],
    snapshot_date: str,
) -> None:
    display_dates = trailing_weekdays(snapshot_date, 7)
    symbols = sorted(recent_signal_symbols(signal_rows, display_dates))
    by_symbol_date = {(str(row["underlying"]), str(row["snapshot_date"])): row for row in agg_rows}
    signal_by_symbol = defaultdict(list)
    for sig in signal_rows:
        signal_by_symbol[str(sig["underlying"])].append(sig)

    cards = []
    for symbol in symbols:
        latest_signal = next((sig for sig in signal_by_symbol[symbol] if sig["snapshot_date"] in display_dates), None)
        direction = latest_signal["direction"] if latest_signal else ""
        latest_date = latest_signal["snapshot_date"] if latest_signal else display_dates[-1]
        contracts = top_contracts(contract_rows, latest_date, symbol, direction)
        trend_rows = []
        for day in display_dates:
            row = by_symbol_date.get((symbol, day))
            if row:
                call_share = safe_float(row.get("call_share"))
                put_share = safe_float(row.get("put_share"))
                dominant = "CALL" if call_share >= put_share else "PUT"
                share = max(call_share, put_share)
                call_volume = safe_int(row.get("call_volume"))
                put_volume = safe_int(row.get("put_volume"))
                total_volume = safe_int(row.get("total_volume"))
                trend_rows.append(
                    f"<tr class='{dominant.lower()}-row'>"
                    f"<td>{html.escape(day[5:])}</td>"
                    f"<td>{call_volume:,}</td>"
                    f"<td>{put_volume:,}</td>"
                    f"<td>{total_volume:,}</td>"
                    f"<td>{bar_html(call_share, put_share)}"
                    f"<div class='bar-labels'><span>Call {call_share:.0%}</span><span>Put {put_share:.0%}</span></div></td>"
                    f"<td>{safe_float(row.get('put_call_ratio')):.2f}</td>"
                    f"<td><b>{dominant}</b> {share:.0%}</td>"
                    "</tr>"
                )
            else:
                trend_rows.append(
                    f"<tr class='empty-row'><td>{html.escape(day[5:])}</td>"
                    "<td></td><td></td><td></td><td><div class='bar empty-bar'></div></td><td></td><td></td></tr>"
                )
        contract_items = "".join(
            f"<li><code>{html.escape(c.get('option_code',''))}</code> "
            f"vol {safe_int(c.get('volume')):,}, OI {safe_int(c.get('open_interest')):,}</li>"
            for c in contracts
        ) or "<li>暂无合约明细</li>"
        cards.append(f"""
        <section class="card">
          <div class="card-head">
            <h2>{html.escape(symbol)}</h2>
            <div class="badge {html.escape(direction.lower())}">{html.escape(direction or 'SIGNAL')}</div>
          </div>
          <p class="reason">{html.escape(latest_signal.get('reason','') if latest_signal else '')}</p>
          <table class="trend">
            <thead>
              <tr>
                <th>日期</th><th>Call量</th><th>Put量</th><th>总量</th><th>Call / Put 占比</th><th>P/C</th><th>主方向</th>
              </tr>
            </thead>
            <tbody>{''.join(trend_rows)}</tbody>
          </table>
          <h3>Top Contracts</h3>
          <ul>{contract_items}</ul>
        </section>
        """)

    if not cards:
        cards.append("<section class='card'><h2>最近 7 天没有评分数据</h2><p>数据已更新，继续观察。</p></section>")

    doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>期权异动日报</title>
  <style>
    body {{ margin: 0; font-family: Arial, 'Microsoft YaHei', sans-serif; background: #f5f7fb; color: #172033; }}
    header {{ padding: 28px 36px 18px; background: #182131; color: white; }}
    header h1 {{ margin: 0 0 8px; font-size: 28px; }}
    header p {{ margin: 0; color: #b8c3d6; }}
    main {{ padding: 24px 36px 44px; max-width: 1280px; margin: auto; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(520px, 1fr)); gap: 18px; }}
    .card {{ background: white; border: 1px solid #dfe5ef; border-radius: 8px; padding: 18px; box-shadow: 0 1px 2px rgba(20,30,50,.04); }}
    .card-head {{ display: flex; justify-content: space-between; align-items: center; gap: 12px; }}
    h2 {{ margin: 0; font-size: 22px; }}
    h3 {{ margin: 18px 0 8px; font-size: 15px; color: #526071; }}
    .badge {{ padding: 6px 10px; border-radius: 6px; font-weight: 700; font-size: 13px; }}
    .badge.call {{ background: #fdeaea; color: #9d2020; }}
    .badge.put {{ background: #e7f6ed; color: #126b37; }}
    .reason {{ color: #526071; min-height: 22px; }}
    table.trend {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
    .trend th {{ background: #f3f6fa; color: #526071; font-size: 12px; padding: 8px 6px; border: 1px solid #e1e7f0; }}
    .trend td {{ border: 1px solid #e1e7f0; padding: 8px 6px; text-align: center; font-size: 13px; line-height: 1.35; }}
    .trend .call-row {{ background: #fff7f7; }}
    .trend .put-row {{ background: #f5fbf7; }}
    .trend .empty-row {{ background: #fafbfd; color: #a2acb8; }}
    .bar {{ height: 16px; width: 100%; display: flex; overflow: hidden; border-radius: 4px; background: #edf1f5; border: 1px solid #d9e1eb; }}
    .bar-call {{ background: #d94b4b; }}
    .bar-put {{ background: #2aa65a; }}
    .empty-bar {{ border-style: dashed; }}
    .bar-labels {{ display: flex; justify-content: space-between; gap: 8px; margin-top: 4px; color: #59687a; font-size: 11px; }}
    ul {{ margin: 0; padding-left: 20px; }}
    li {{ margin: 6px 0; }}
    code {{ background: #f1f4f8; padding: 2px 4px; border-radius: 4px; }}
  </style>
</head>
<body>
  <header>
    <h1>期权异动日报</h1>
    <p>更新日期：{html.escape(snapshot_date)} · 仅展示最近 7 天内出现过异常信号的自选股</p>
  </header>
  <main><div class="grid">{''.join(cards)}</div></main>
</body>
</html>"""
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(doc, encoding="utf-8")


def render_html(
    html_path: Path,
    agg_rows: list[dict[str, Any]],
    signal_rows: list[dict[str, Any]],
    contract_rows: list[dict[str, str]],
    snapshot_date: str,
) -> None:
    display_dates = trailing_weekdays(snapshot_date, 7)
    by_symbol_date = {(str(row["underlying"]), str(row["snapshot_date"])): row for row in agg_rows}

    signal_by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sig in signal_rows:
        if str(sig.get("snapshot_date", "")) in display_dates:
            signal_by_symbol[str(sig["underlying"])].append(sig)

    symbol_infos = []
    for symbol, rows in signal_by_symbol.items():
        rows.sort(key=lambda row: (str(row["snapshot_date"]), safe_float(row["score"])), reverse=True)
        symbol_infos.append((symbol, rows[0]))
    symbol_infos.sort(key=lambda item: (str(item[1]["snapshot_date"]), safe_float(item[1]["score"])), reverse=True)

    cards = []
    for symbol, latest_signal in symbol_infos:
        direction = str(latest_signal.get("direction", ""))
        latest_date = str(latest_signal.get("snapshot_date", display_dates[-1]))
        contracts = top_contracts(contract_rows, latest_date, symbol, direction)
        trend_rows = []

        for day in display_dates:
            row = by_symbol_date.get((symbol, day))
            if not row:
                trend_rows.append(
                    f"<tr class='empty-row'><td>{html.escape(day[5:])}</td>"
                    "<td></td><td></td><td></td>"
                    "<td><div class='bar empty-bar'></div></td><td></td><td></td></tr>"
                )
                continue

            call_share = safe_float(row.get("call_share"))
            put_share = safe_float(row.get("put_share"))
            dominant = "CALL" if call_share >= put_share else "PUT"
            share = max(call_share, put_share)
            call_volume = safe_int(row.get("call_volume"))
            put_volume = safe_int(row.get("put_volume"))
            total_volume = safe_int(row.get("total_volume"))
            trend_rows.append(
                f"<tr class='{dominant.lower()}-row'>"
                f"<td>{html.escape(day[5:])}</td>"
                f"<td>{call_volume:,}</td>"
                f"<td>{put_volume:,}</td>"
                f"<td>{total_volume:,}</td>"
                f"<td>{bar_html(call_share, put_share)}"
                f"<div class='bar-labels'><span>Call {call_share:.0%}</span><span>Put {put_share:.0%}</span></div></td>"
                f"<td>{safe_float(row.get('put_call_ratio')):.2f}</td>"
                f"<td><b>{dominant}</b> {share:.0%}</td>"
                "</tr>"
            )

        contract_items = "".join(
            f"<li><code>{html.escape(c.get('option_code', ''))}</code> "
            f"成交 {safe_int(c.get('volume')):,}，持仓 {safe_int(c.get('open_interest')):,}</li>"
            for c in contracts
        ) or "<li>暂无合约明细</li>"

        cards.append(f"""
        <section class="card">
          <div class="card-head">
            <h2>{html.escape(symbol)}</h2>
            <div class="badge {html.escape(direction.lower())}">{html.escape(direction or 'SIGNAL')}</div>
          </div>
          <p class="reason">{html.escape(str(latest_signal.get('reason', '')))}</p>
          <table class="trend">
            <thead>
              <tr>
                <th>日期</th><th>Call 量</th><th>Put 量</th><th>总量</th><th>Call / Put 占比</th><th>P/C</th><th>主方向</th>
              </tr>
            </thead>
            <tbody>{''.join(trend_rows)}</tbody>
          </table>
          <h3>最新信号方向 Top 合约</h3>
          <ul>{contract_items}</ul>
        </section>
        """)

    if not cards:
        cards.append("<section class='card'><h2>最近 7 个交易日没有评分数据</h2><p>数据已更新，继续观察。</p></section>")

    first_day = display_dates[0]
    last_day = display_dates[-1]
    doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>期权异动日报</title>
  <style>
    body {{ margin: 0; font-family: Arial, 'Microsoft YaHei', sans-serif; background: #f5f7fb; color: #172033; }}
    header {{ padding: 28px 36px 18px; background: #182131; color: white; }}
    header h1 {{ margin: 0 0 8px; font-size: 28px; letter-spacing: 0; }}
    header p {{ margin: 0; color: #b8c3d6; }}
    main {{ padding: 24px 36px 44px; max-width: 1320px; margin: auto; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(560px, 1fr)); gap: 18px; }}
    .card {{ background: white; border: 1px solid #dfe5ef; border-radius: 8px; padding: 18px; box-shadow: 0 1px 2px rgba(20,30,50,.04); }}
    .card-head {{ display: flex; justify-content: space-between; align-items: center; gap: 12px; }}
    h2 {{ margin: 0; font-size: 22px; letter-spacing: 0; }}
    h3 {{ margin: 18px 0 8px; font-size: 15px; color: #526071; letter-spacing: 0; }}
    .badge {{ padding: 6px 10px; border-radius: 6px; font-weight: 700; font-size: 13px; }}
    .badge.call {{ background: #fdeaea; color: #9d2020; }}
    .badge.put {{ background: #e7f6ed; color: #126b37; }}
    .badge.signal {{ background: #eef3f9; color: #526071; }}
    .reason {{ color: #526071; min-height: 22px; }}
    table.trend {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
    .trend th {{ background: #f3f6fa; color: #526071; font-size: 12px; padding: 8px 6px; border: 1px solid #e1e7f0; }}
    .trend td {{ border: 1px solid #e1e7f0; padding: 8px 6px; text-align: center; font-size: 13px; line-height: 1.35; }}
    .trend .call-row {{ background: #fff7f7; }}
    .trend .put-row {{ background: #f5fbf7; }}
    .trend .empty-row {{ background: #fafbfd; color: #a2acb8; }}
    .bar {{ height: 16px; width: 100%; display: flex; overflow: hidden; border-radius: 4px; background: #edf1f5; border: 1px solid #d9e1eb; }}
    .bar-call {{ background: #d94b4b; }}
    .bar-put {{ background: #2aa65a; }}
    .empty-bar {{ border-style: dashed; }}
    .bar-labels {{ display: flex; justify-content: space-between; gap: 8px; margin-top: 4px; color: #59687a; font-size: 11px; }}
    ul {{ margin: 0; padding-left: 20px; }}
    li {{ margin: 6px 0; }}
    code {{ background: #f1f4f8; padding: 2px 4px; border-radius: 4px; }}
    @media (max-width: 700px) {{
      header {{ padding: 22px 16px 14px; }}
      main {{ padding: 16px; }}
      .grid {{ grid-template-columns: 1fr; }}
      .card {{ overflow-x: auto; }}
      table.trend {{ min-width: 620px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>期权异动日报</h1>
    <p>更新日期：{html.escape(snapshot_date)} · 趋势区间：{html.escape(first_day)} 至 {html.escape(last_day)} · 仅展示最近 7 个交易日内出现过异常信号的自选股</p>
  </header>
  <main><div class="grid">{''.join(cards)}</div></main>
</body>
</html>"""
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(doc, encoding="utf-8")


def render_html(
    html_path: Path,
    agg_rows: list[dict[str, Any]],
    signal_rows: list[dict[str, Any]],
    contract_rows: list[dict[str, str]],
    snapshot_date: str,
) -> None:
    def fmt_int(value: Any) -> str:
        number = safe_int(value)
        return f"{number:,}" if number else ""

    def fmt_pct(value: Any) -> str:
        return f"{safe_float(value):.0%}" if safe_float(value) else ""

    def fmt_ratio(value: Any) -> str:
        number = safe_float(value)
        return f"{number:.2f}" if number else ""

    display_dates = trailing_weekdays(snapshot_date, 7)
    by_symbol_date = {(str(row["underlying"]), str(row["snapshot_date"])): row for row in agg_rows}
    current_agg = [row for row in agg_rows if str(row.get("snapshot_date", "")) == snapshot_date]

    signal_by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sig in signal_rows:
        if str(sig.get("snapshot_date", "")) in display_dates:
            signal_by_symbol[str(sig["underlying"])].append(sig)

    symbol_infos: list[tuple[str, dict[str, Any]]] = []
    for symbol, rows in signal_by_symbol.items():
        rows.sort(key=lambda row: (str(row["snapshot_date"]), safe_float(row["score"])), reverse=True)
        symbol_infos.append((symbol, rows[0]))
    symbol_infos.sort(key=lambda item: (str(item[1]["snapshot_date"]), safe_float(item[1]["score"])), reverse=True)

    call_signals = sum(1 for _, sig in symbol_infos if str(sig.get("direction")) == "CALL")
    put_signals = sum(1 for _, sig in symbol_infos if str(sig.get("direction")) == "PUT")
    scanned_count = len({str(row.get("underlying", "")) for row in current_agg if row.get("underlying")})
    total_volume = sum(safe_int(row.get("total_volume")) for row in current_agg)
    top_signal = symbol_infos[0][1] if symbol_infos else {}

    metric_html = f"""
      <section class="metrics" aria-label="summary">
        <div class="metric"><span>信号股票</span><strong>{len(symbol_infos)}</strong></div>
        <div class="metric"><span>CALL 主导</span><strong class="call-text">{call_signals}</strong></div>
        <div class="metric"><span>PUT 主导</span><strong class="put-text">{put_signals}</strong></div>
        <div class="metric"><span>扫描自选</span><strong>{scanned_count}</strong></div>
        <div class="metric wide"><span>期权成交量</span><strong>{total_volume:,}</strong></div>
      </section>
    """

    cards = []
    for symbol, latest_signal in symbol_infos:
        direction = str(latest_signal.get("direction", ""))
        latest_date = str(latest_signal.get("snapshot_date", display_dates[-1]))
        direction_class = direction.lower() if direction else "signal"
        latest_row = by_symbol_date.get((symbol, latest_date), {})
        latest_total = safe_int(latest_row.get("total_volume"))
        latest_pcr = safe_float(latest_row.get("put_call_ratio"))
        score = safe_float(latest_signal.get("score"))
        contracts = top_contracts(contract_rows, latest_date, symbol, direction)

        trend_rows = []
        for day in display_dates:
            row = by_symbol_date.get((symbol, day))
            if not row:
                trend_rows.append(
                    f"<tr class='empty-row'><td>{html.escape(day[5:])}</td>"
                    "<td></td><td></td><td></td><td><div class='share-track empty-track'></div></td><td></td><td></td></tr>"
                )
                continue

            call_share = safe_float(row.get("call_share"))
            put_share = safe_float(row.get("put_share"))
            dominant = "CALL" if call_share >= put_share else "PUT"
            dominant_class = dominant.lower()
            trend_rows.append(
                f"<tr class='{dominant_class}-row'>"
                f"<td>{html.escape(day[5:])}</td>"
                f"<td>{fmt_int(row.get('call_volume'))}</td>"
                f"<td>{fmt_int(row.get('put_volume'))}</td>"
                f"<td>{fmt_int(row.get('total_volume'))}</td>"
                f"<td><div class='share-track' aria-label='Call {call_share:.0%}, Put {put_share:.0%}'>"
                f"<span class='share-call' style='width:{call_share * 100:.2f}%'></span>"
                f"<span class='share-put' style='width:{put_share * 100:.2f}%'></span></div>"
                f"<div class='share-labels'><span>Call {call_share:.0%}</span><span>Put {put_share:.0%}</span></div></td>"
                f"<td>{fmt_ratio(row.get('put_call_ratio'))}</td>"
                f"<td><span class='dir-pill {dominant_class}'>{dominant}</span></td>"
                "</tr>"
            )

        contract_rows_html = "".join(
            f"<tr><td><code>{html.escape(c.get('option_code', ''))}</code></td>"
            f"<td>{fmt_int(c.get('volume'))}</td><td>{fmt_int(c.get('open_interest'))}</td>"
            f"<td>{fmt_ratio(c.get('implied_volatility'))}</td></tr>"
            for c in contracts
        ) or "<tr><td colspan='4' class='muted'>暂无合约明细</td></tr>"

        cards.append(f"""
        <article class="signal-panel" data-symbol="{html.escape(symbol)}" data-direction="{html.escape(direction)}" data-score="{score:.2f}" data-total="{latest_total}" data-pcr="{latest_pcr:.4f}">
          <div class="panel-head">
            <div>
              <div class="symbol-line">
                <h2>{html.escape(symbol)}</h2>
                <span class="badge {direction_class}">{html.escape(direction or "SIGNAL")}</span>
              </div>
              <p>{html.escape(str(latest_signal.get("reason", "")))}</p>
            </div>
            <div class="panel-stats">
              <span><b>{score:.1f}</b><small>Score</small></span>
              <span><b>{latest_total:,}</b><small>Volume</small></span>
              <span><b>{latest_pcr:.2f}</b><small>P/C</small></span>
            </div>
          </div>
          <div class="table-wrap">
            <table class="trend">
              <thead>
                <tr><th>日期</th><th>Call</th><th>Put</th><th>总量</th><th>方向占比</th><th>P/C</th><th>主导</th></tr>
              </thead>
              <tbody>{''.join(trend_rows)}</tbody>
            </table>
          </div>
          <div class="contracts">
            <div class="section-label">最新信号方向 Top 合约</div>
            <table>
              <thead><tr><th>合约</th><th>成交</th><th>持仓</th><th>IV</th></tr></thead>
              <tbody>{contract_rows_html}</tbody>
            </table>
          </div>
        </article>
        """)

    empty_state = """
      <section class="empty-state">
        <h2>最近 7 个交易日没有评分数据</h2>
        <p>数据已更新，继续观察。</p>
      </section>
    """

    top_symbol = html.escape(str(top_signal.get("underlying", "--")))
    top_reason = html.escape(str(top_signal.get("reason", "暂无评分说明")))
    first_day = display_dates[0]
    last_day = display_dates[-1]

    doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>期权异动监控</title>
  <style>
    :root {{
      --bg: #f6f7f8;
      --paper: #ffffff;
      --ink: #171717;
      --muted: #606873;
      --line: #dce2e8;
      --call: #c73535;
      --call-soft: #fff0f0;
      --put: #16834a;
      --put-soft: #ecf8f1;
      --amber: #5d6778;
      --shadow: 0 12px 28px rgba(35, 40, 48, .08);
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Arial, 'Microsoft YaHei', sans-serif; background: var(--bg); color: var(--ink); }}
    button, input {{ font: inherit; }}
    .shell {{ max-width: 1440px; margin: 0 auto; padding: 24px; }}
    .topbar {{ display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(280px, .8fr); gap: 18px; align-items: stretch; margin-bottom: 18px; }}
    .masthead, .focus-box, .toolbar, .signal-panel, .empty-state {{ background: var(--paper); border: 1px solid var(--line); border-radius: 8px; box-shadow: var(--shadow); }}
    .masthead {{ padding: 22px; min-height: 156px; display: flex; flex-direction: column; justify-content: space-between; }}
    .kicker {{ margin: 0 0 8px; color: var(--muted); font-size: 13px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }}
    h1 {{ margin: 0; font-size: 30px; line-height: 1.1; letter-spacing: 0; }}
    .date-line {{ margin: 12px 0 0; color: var(--muted); font-size: 14px; display: flex; flex-wrap: wrap; gap: 6px 12px; }}
    .date-line span {{ white-space: nowrap; }}
    .focus-box {{ padding: 18px; display: grid; gap: 12px; align-content: space-between; }}
    .focus-box span {{ color: var(--muted); font-size: 13px; }}
    .focus-box strong {{ display: block; font-size: 28px; line-height: 1.1; }}
    .focus-box p {{ margin: 0; color: var(--muted); line-height: 1.5; }}
    .metrics {{ display: grid; grid-template-columns: repeat(5, minmax(130px, 1fr)); gap: 10px; margin-bottom: 12px; }}
    .metric {{ background: rgba(255,255,255,.78); border: 1px solid var(--line); border-radius: 8px; padding: 12px; }}
    .metric span {{ display: block; color: var(--muted); font-size: 12px; margin-bottom: 5px; }}
    .metric strong {{ font-size: 22px; }}
    .call-text {{ color: var(--call); }}
    .put-text {{ color: var(--put); }}
    .toolbar {{ position: sticky; top: 0; z-index: 5; padding: 12px; display: grid; grid-template-columns: minmax(220px, 1fr) auto auto; gap: 10px; align-items: center; margin-bottom: 14px; }}
    .search {{ width: 100%; border: 1px solid var(--line); border-radius: 6px; padding: 10px 12px; background: #ffffff; outline: none; }}
    .search:focus, .seg button:focus-visible {{ outline: 2px solid #687384; outline-offset: 2px; }}
    .seg {{ display: inline-grid; grid-auto-flow: column; gap: 4px; padding: 4px; border: 1px solid var(--line); border-radius: 8px; background: #eef1f4; }}
    .seg button {{ border: 0; border-radius: 6px; padding: 8px 12px; color: var(--muted); background: transparent; cursor: pointer; transition: transform .15s ease, background .15s ease, color .15s ease; }}
    .seg button:active {{ transform: scale(.98); }}
    .seg button.active {{ background: var(--paper); color: var(--ink); box-shadow: 0 1px 2px rgba(0,0,0,.08); }}
    .count {{ color: var(--muted); font-size: 13px; text-align: right; min-width: 72px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(620px, 1fr)); gap: 14px; align-items: start; }}
    .signal-panel {{ overflow: hidden; }}
    .panel-head {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 18px; align-items: start; padding: 16px 16px 12px; border-bottom: 1px solid var(--line); }}
    .symbol-line {{ display: flex; align-items: center; gap: 10px; }}
    h2 {{ margin: 0; font-size: 22px; letter-spacing: 0; }}
    .panel-head p {{ margin: 8px 0 0; color: var(--muted); font-size: 13px; }}
    .badge, .dir-pill {{ display: inline-flex; align-items: center; justify-content: center; border-radius: 6px; font-weight: 700; }}
    .badge {{ padding: 5px 8px; font-size: 12px; }}
    .badge.call, .dir-pill.call {{ background: var(--call-soft); color: var(--call); }}
    .badge.put, .dir-pill.put {{ background: var(--put-soft); color: var(--put); }}
    .panel-stats {{ display: grid; grid-template-columns: repeat(3, 86px); gap: 8px; }}
    .panel-stats span {{ border-left: 1px solid var(--line); padding-left: 10px; }}
    .panel-stats b {{ display: block; font-size: 18px; }}
    .panel-stats small {{ display: block; color: var(--muted); font-size: 11px; margin-top: 2px; }}
    .table-wrap {{ overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
    th {{ color: var(--muted); background: #f3f5f7; font-size: 12px; font-weight: 700; }}
    th, td {{ padding: 9px 8px; border-bottom: 1px solid #e5e9ee; text-align: right; white-space: nowrap; }}
    th:first-child, td:first-child {{ text-align: left; }}
    .trend td {{ font-size: 13px; }}
    .trend .call-row {{ background: linear-gradient(90deg, rgba(199,53,53,.06), transparent 34%); }}
    .trend .put-row {{ background: linear-gradient(90deg, rgba(22,131,74,.07), transparent 34%); }}
    .empty-row td {{ color: #a3abb5; background: #fafbfc; }}
    .share-track {{ width: 100%; min-width: 150px; height: 14px; display: flex; overflow: hidden; border-radius: 5px; background: #edf0f3; border: 1px solid #dce2e8; }}
    .share-call {{ background: var(--call); }}
    .share-put {{ background: var(--put); }}
    .empty-track {{ border-style: dashed; }}
    .share-labels {{ display: flex; justify-content: space-between; gap: 8px; margin-top: 4px; color: var(--muted); font-size: 11px; }}
    .dir-pill {{ min-width: 48px; padding: 4px 6px; font-size: 11px; }}
    .contracts {{ border-top: 1px solid var(--line); padding: 12px 16px 16px; }}
    .section-label {{ color: var(--muted); font-size: 12px; font-weight: 700; margin-bottom: 6px; }}
    .contracts table {{ font-size: 12px; }}
    .contracts code {{ display: inline-block; max-width: 210px; overflow: hidden; text-overflow: ellipsis; vertical-align: bottom; background: #f0f2f5; border-radius: 4px; padding: 2px 5px; }}
    .muted {{ color: var(--muted); text-align: left; }}
    .empty-state {{ padding: 22px; }}
    .hidden {{ display: none; }}
    @keyframes rise {{ from {{ opacity: 0; transform: translateY(8px); }} to {{ opacity: 1; transform: translateY(0); }} }}
    @media (prefers-reduced-motion: reduce) {{ .signal-panel, .seg button {{ animation: none; transition: none; }} }}
    @media (max-width: 900px) {{
      .shell {{ padding: 14px; }}
      .topbar {{ grid-template-columns: 1fr; }}
      .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .toolbar {{ grid-template-columns: 1fr; position: static; }}
      .count {{ text-align: left; }}
      .grid {{ grid-template-columns: 1fr; }}
      .signal-panel {{ overflow-x: auto; }}
      .panel-head {{ grid-template-columns: 1fr; }}
      .panel-stats {{ grid-template-columns: repeat(3, minmax(80px, 1fr)); }}
      .trend {{ min-width: 680px; }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <div class="topbar">
      <section class="masthead">
        <div>
          <p class="kicker">Options Flow Monitor</p>
          <h1>期权异动监控</h1>
          <p class="date-line"><span>更新日期：{html.escape(snapshot_date)}</span><span>趋势区间：{html.escape(first_day)} 至 {html.escape(last_day)}</span></p>
        </div>
      </section>
      <aside class="focus-box">
        <span>当前最强信号</span>
        <strong>{top_symbol}</strong>
        <p>{top_reason}</p>
      </aside>
    </div>
    {metric_html}
    <section class="toolbar" aria-label="filters">
      <input id="search" class="search" type="search" placeholder="搜索股票代码" autocomplete="off">
      <div class="seg" id="directionFilter">
        <button class="active" data-filter="ALL" type="button">全部</button>
        <button data-filter="CALL" type="button">CALL</button>
        <button data-filter="PUT" type="button">PUT</button>
      </div>
      <div class="seg" id="sorter">
        <button class="active" data-sort="score" type="button">分数</button>
        <button data-sort="total" type="button">成交</button>
        <button data-sort="pcr" type="button">P/C</button>
      </div>
      <div class="count"><span id="visibleCount">{len(symbol_infos)}</span> / {len(symbol_infos)}</div>
    </section>
    <section class="grid" id="panelGrid">
      {''.join(cards) if cards else empty_state}
    </section>
  </main>
  <script>
    const grid = document.getElementById('panelGrid');
    const panels = Array.from(document.querySelectorAll('.signal-panel'));
    const search = document.getElementById('search');
    const visibleCount = document.getElementById('visibleCount');
    let activeDirection = 'ALL';
    let activeSort = 'score';

    function setActive(container, button) {{
      container.querySelectorAll('button').forEach((item) => item.classList.toggle('active', item === button));
    }}

    function refreshPanels() {{
      const query = search.value.trim().toUpperCase();
      let visible = 0;
      panels.forEach((panel) => {{
        const symbol = panel.dataset.symbol.toUpperCase();
        const direction = panel.dataset.direction;
        const matched = (!query || symbol.includes(query)) && (activeDirection === 'ALL' || direction === activeDirection);
        panel.classList.toggle('hidden', !matched);
        if (matched) visible += 1;
      }});
      visibleCount.textContent = visible;
    }}

    function sortPanels() {{
      const sorted = panels.slice().sort((a, b) => Number(b.dataset[activeSort]) - Number(a.dataset[activeSort]));
      sorted.forEach((panel) => grid.appendChild(panel));
      refreshPanels();
    }}

    document.getElementById('directionFilter').addEventListener('click', (event) => {{
      const button = event.target.closest('button');
      if (!button) return;
      activeDirection = button.dataset.filter;
      setActive(event.currentTarget, button);
      refreshPanels();
    }});

    document.getElementById('sorter').addEventListener('click', (event) => {{
      const button = event.target.closest('button');
      if (!button) return;
      activeSort = button.dataset.sort;
      setActive(event.currentTarget, button);
      sortPanels();
    }});

    search.addEventListener('input', refreshPanels);
  </script>
</body>
</html>"""
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(doc, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build daily option anomaly HTML report")
    parser.add_argument("--mode", choices=["preopen", "intraday"], default="preopen")
    parser.add_argument("--watchlist-source", choices=["file", "futu-user"], default="file")
    parser.add_argument("--watchlist", type=Path, default=Path("config/watchlist.json"))
    parser.add_argument("--group-type", choices=["ALL", "CUSTOM", "SYSTEM"], default="CUSTOM")
    parser.add_argument("--group-name", default=None)
    parser.add_argument("--scan-group-name", default=None)
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
    daily_agg_path = args.data_dir / "option_screen_underlying_snapshot.csv"
    daily_signal_path = args.data_dir / "daily_option_signals.csv"
    quote_snapshot_path = args.data_dir / "current_quote_snapshot.json"
    snapshot_status_path = args.data_dir / SNAPSHOT_STATUS_FILE

    if args.mode == "intraday":
        snapshot_date = args.snapshot_date or current_us_trade_date().isoformat()
        metadata = intraday_metadata(snapshot_date)
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
        aggregates = osm.aggregate_contracts(contracts, snapshot_date, watchlist)

        replace_rows_for_key(daily_contract_path, osm.CONTRACT_COLUMNS, contracts, "snapshot_date", snapshot_date)
        replace_rows_for_key(daily_volume_contract_path, osm.CONTRACT_COLUMNS, volume_contracts, "snapshot_date", snapshot_date)
        replace_rows_for_key(daily_agg_path, osm.AGG_COLUMNS, aggregates, "snapshot_date", snapshot_date)

        all_daily_agg = read_csv(daily_agg_path)
        all_daily_signals = build_signals(all_daily_agg, min_total=args.min_total, min_history_days=args.min_history_days)
        write_csv(daily_signal_path, SIGNAL_COLUMNS, all_daily_signals)
        all_daily_contracts = read_csv(daily_contract_path)
        all_daily_volume_contracts = read_csv(daily_volume_contract_path)
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
    aggregates = osm.aggregate_contracts(contracts, snapshot_date, watchlist)

    replace_rows_for_key(daily_contract_path, osm.CONTRACT_COLUMNS, contracts, "snapshot_date", snapshot_date)
    replace_rows_for_key(daily_volume_contract_path, osm.CONTRACT_COLUMNS, volume_contracts, "snapshot_date", snapshot_date)
    replace_rows_for_key(daily_agg_path, osm.AGG_COLUMNS, aggregates, "snapshot_date", snapshot_date)

    all_agg = read_csv(daily_agg_path)
    signals = build_signals(all_agg, min_total=args.min_total, min_history_days=args.min_history_days)
    write_csv(daily_signal_path, SIGNAL_COLUMNS, signals)
    all_contracts = read_csv(daily_contract_path)
    all_volume_contracts = read_csv(daily_volume_contract_path)
    metadata = collection_metadata(snapshot_date, "complete")
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
        report_groups=report_groups,
        quote_map=quote_snapshot.get("quotes", {}),
        snapshot_status=snapshot_status,
    )

    print(f"Mode:                     preopen")
    print(f"Watchlist symbols scanned: {len(watchlist)}")
    print(f"Option contracts scanned:  {total_seen}")
    print(f"Volume top contracts scanned: {volume_total_seen}")
    print(f"Saved signals:            {daily_signal_path}")
    print(f"Saved HTML report:        {args.html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
