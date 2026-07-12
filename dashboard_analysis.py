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
from datetime import date
from pathlib import Path
from typing import Any

import dashboard_renderer
import report_groups as rg


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


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def event_minute(value: Any) -> str:
    text = str(value or "").strip()
    prefix, separator, clock = text.rpartition(" ")
    parts = clock.split(":")
    if len(parts) < 2:
        return text
    minute = f"{parts[0]}:{parts[1]}"
    return f"{prefix}{separator}{minute}" if prefix else minute


def merge_parent_orders(
    rows: list[dict[str, str]], snapshot_date: str, symbol: str
) -> list[dict[str, Any]]:
    exact: dict[tuple[str, ...], dict[str, str]] = {}
    for row in rows:
        if row.get("snapshot_date") != snapshot_date or row.get("underlying") != symbol:
            continue
        direction = str(row.get("direction", "")).upper()
        option_type = str(row.get("option_type", "")).upper()
        if direction not in {"BUY", "SELL"} or option_type not in {"CALL", "PUT"}:
            continue
        key = (
            str(row.get("event_time", "")).strip(),
            str(row.get("expiry", "")),
            option_type,
            f"{safe_float(row.get('strike')):.4f}",
            str(safe_int(row.get("volume"))),
            f"{safe_float(row.get('turnover')):.2f}",
            direction,
        )
        exact.setdefault(key, row)

    merged: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in exact.values():
        key = (
            event_minute(row.get("event_time")),
            str(row.get("expiry", "")),
            str(row.get("option_type", "")).upper(),
            f"{safe_float(row.get('strike')):.4f}",
            str(row.get("direction", "")).upper(),
        )
        leg = merged.setdefault(
            key,
            {
                "event_time": key[0],
                "expiry": key[1],
                "option_type": key[2],
                "strike": safe_float(row.get("strike")),
                "direction": key[4],
                "volume": 0,
                "turnover": 0.0,
            },
        )
        leg["volume"] += safe_int(row.get("volume"))
        leg["turnover"] += safe_float(row.get("turnover"))

    legs = list(merged.values())
    for index, leg in enumerate(legs):
        leg["_id"] = index
    return legs


def leg_label(leg: dict[str, Any]) -> str:
    strike = safe_float(leg.get("strike"))
    strike_text = f"{strike:g}"
    return f"{leg.get('direction')} {leg.get('option_type')} {strike_text} {leg.get('expiry')}"


def classify_structure(legs: list[dict[str, Any]]) -> tuple[str, str]:
    if len(legs) != 2:
        return "complex", "unknown"
    first, second = legs
    same_expiry = first["expiry"] == second["expiry"]
    same_type = first["option_type"] == second["option_type"]
    opposite_sides = first["direction"] != second["direction"]

    if same_expiry and same_type and opposite_sides and first["strike"] != second["strike"]:
        bought = first if first["direction"] == "BUY" else second
        sold = second if first["direction"] == "BUY" else first
        if bought["option_type"] == "CALL":
            bullish = bought["strike"] < sold["strike"]
            return ("bull_call_spread", "bullish") if bullish else ("bear_call_spread", "bearish")
        bullish = bought["strike"] < sold["strike"]
        return ("bull_put_spread", "bullish") if bullish else ("bear_put_spread", "bearish")

    if same_expiry and not same_type:
        call = first if first["option_type"] == "CALL" else second
        put = second if first["option_type"] == "CALL" else first
        if call["direction"] == "BUY" and put["direction"] == "SELL":
            return "bullish_risk_reversal", "bullish"
        if call["direction"] == "SELL" and put["direction"] == "BUY":
            return "bearish_risk_reversal", "bearish"
        if call["direction"] == put["direction"] == "BUY":
            return ("long_straddle" if call["strike"] == put["strike"] else "long_strangle"), "volatility"
        if call["direction"] == put["direction"] == "SELL":
            return ("short_straddle" if call["strike"] == put["strike"] else "short_strangle"), "volatility"

    if same_type and not same_expiry and opposite_sides:
        same_strike = first["strike"] == second["strike"]
        return ("calendar_or_roll" if same_strike else "diagonal_or_roll"), "unknown"
    return "suspected_structure", "unknown"


def structure_and_residual_summary(
    rows: list[dict[str, str]], snapshot_date: str, symbol: str
) -> tuple[list[dict[str, Any]], dict[str, Any], int]:
    legs = merge_parent_orders(rows, snapshot_date, symbol)
    by_time_volume: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for leg in legs:
        by_time_volume[(leg["event_time"], leg["volume"])].append(leg)

    used: set[int] = set()
    structures: list[dict[str, Any]] = []
    for (_event_time, volume), candidates in by_time_volume.items():
        identities = {
            (leg["expiry"], leg["option_type"], leg["strike"], leg["direction"])
            for leg in candidates
        }
        if len(identities) < 2:
            continue
        structure_type, bias = classify_structure(candidates)
        used.update(int(leg["_id"]) for leg in candidates)
        structures.append(
            {
                "type": structure_type,
                "confidence": "suspected" if structure_type == "suspected_structure" else "high",
                "bias": bias,
                "event_time": candidates[0]["event_time"],
                "volume": volume,
                "turnover_m": round(sum(safe_float(leg["turnover"]) for leg in candidates) / 1_000_000, 3),
                "legs": [leg_label(leg) for leg in candidates[:6]],
            }
        )
    structures.sort(key=lambda item: item["turnover_m"], reverse=True)

    bullish = bearish = 0.0
    bullish_orders = bearish_orders = 0
    for leg in legs:
        if int(leg["_id"]) in used:
            continue
        is_bullish = (leg["direction"], leg["option_type"]) in {
            ("BUY", "CALL"),
            ("SELL", "PUT"),
        }
        if is_bullish:
            bullish += safe_float(leg["turnover"])
            bullish_orders += 1
        else:
            bearish += safe_float(leg["turnover"])
            bearish_orders += 1
    bias = "bullish" if bullish > bearish * 1.5 else "bearish" if bearish > bullish * 1.5 else "mixed"
    residual = {
        "bias": bias,
        "bullish_turnover_m": round(bullish / 1_000_000, 3),
        "bearish_turnover_m": round(bearish / 1_000_000, 3),
        "bullish_orders": bullish_orders,
        "bearish_orders": bearish_orders,
    }
    return structures, residual, len(legs)


def select_structures(structures: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for bias in ("bullish", "bearish", "volatility", "unknown"):
        match = next((item for item in structures if item["bias"] == bias), None)
        if match is not None:
            selected.append(match)
    for item in structures:
        if item not in selected:
            selected.append(item)
    return sorted(selected[:limit], key=lambda item: item["turnover_m"], reverse=True)


def prior_contract_map(rows: list[dict[str, str]], snapshot_date: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        row_date = str(row.get("snapshot_date", ""))
        code = str(row.get("option_code", ""))
        if not code or not row_date or row_date >= snapshot_date:
            continue
        current = result.get(code)
        if current is None or row_date > str(current.get("snapshot_date", "")):
            result[code] = row
    return result


def compact_contract_evidence(
    contracts: list[dict[str, str]],
    previous_by_code: dict[str, dict[str, str]],
    snapshot_date: str,
    stock_price: float,
    limit: int = 3,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    target_date = date.fromisoformat(snapshot_date)
    for row in contracts[:limit]:
        code = str(row.get("option_code", ""))
        option_type = str(row.get("option_type", "")).upper()
        strike = safe_float(row.get("strike"))
        volume = safe_int(row.get("volume"))
        open_interest = safe_int(row.get("open_interest"))
        v_oi = volume / open_interest if open_interest > 0 else None
        previous = previous_by_code.get(code)
        oi_delta = open_interest - safe_int(previous.get("open_interest")) if previous else None
        if open_interest <= 0:
            opening_support = "unknown"
        elif previous is None:
            opening_support = "unconfirmed"
        elif oi_delta is not None and oi_delta <= 0:
            opening_support = "weak"
        elif v_oi is not None and v_oi >= 2:
            opening_support = "strong"
        elif v_oi is not None and v_oi >= 1:
            opening_support = "supportive"
        else:
            opening_support = "weak"

        expiry = dashboard_renderer.option_expiry(code)
        try:
            dte = (date.fromisoformat(expiry) - target_date).days if expiry else None
        except ValueError:
            dte = None
        if stock_price > 0 and strike > 0 and option_type == "CALL":
            moneyness_pct = (strike / stock_price - 1) * 100
        elif stock_price > 0 and strike > 0 and option_type == "PUT":
            moneyness_pct = (1 - strike / stock_price) * 100
        else:
            moneyness_pct = None
        output.append(
            {
                "code": code,
                "type": option_type,
                "strike": strike,
                "expiry": expiry,
                "dte": dte,
                "volume": volume,
                "turnover_m": round(safe_float(row.get("turnover")) / 1_000_000, 3),
                "open_interest": open_interest,
                "v_oi": round(v_oi, 2) if v_oi is not None else None,
                "oi_delta": oi_delta,
                "opening_support": opening_support,
                "moneyness_pct": round(moneyness_pct, 1) if moneyness_pct is not None else None,
                "iv": round(safe_float(row.get("implied_volatility")), 3),
            }
        )
    return output


def compact_group_metrics(
    agg_rows: list[dict[str, str]], snapshot_date: str
) -> list[dict[str, Any]]:
    by_symbol: dict[str, list[dict[str, str]]] = defaultdict(list)
    current: dict[str, dict[str, str]] = {}
    for row in agg_rows:
        symbol = str(row.get("underlying", ""))
        by_symbol[symbol].append(row)
        if row.get("snapshot_date") == snapshot_date:
            current[symbol] = row

    groups: list[dict[str, Any]] = []
    for group_name, symbols in rg.THEME_REPORT_GROUPS.items():
        rows = [current[symbol] for symbol in symbols if symbol in current]
        prior_rows = [prior_row(by_symbol[symbol], snapshot_date) for symbol in symbols]
        prior_rows = [row for row in prior_rows if row]
        call_volume = sum(safe_int(row.get("call_volume")) for row in rows)
        put_volume = sum(safe_int(row.get("put_volume")) for row in rows)
        total_volume = call_volume + put_volume
        prior_total = sum(safe_int(row.get("total_volume")) for row in prior_rows)
        groups.append(
            {
                "group": group_name,
                "symbols": len(rows),
                "total_volume": total_volume,
                "volume_change_pct": round(total_volume / prior_total - 1, 3) if prior_total else None,
                "call_share": round(call_volume / total_volume, 3) if total_volume else 0.0,
                "put_call_ratio": round(put_volume / call_volume, 3) if call_volume else None,
            }
        )
    return groups


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


def build_intent_report(snapshot_date: str | None = None, limit: int = 15) -> dict[str, Any]:
    agg_rows = read_csv(DATA_DIR / "option_screen_underlying_snapshot.csv")
    contract_rows = read_csv(DATA_DIR / "option_screen_contract_snapshot.csv")
    volume_rows = read_csv(DATA_DIR / "option_screen_volume_contract_snapshot.csv")
    unusual_rows = read_csv(DATA_DIR / "option_unusual_snapshot.csv")
    status = read_json(DATA_DIR / "option_screen_snapshot_status.json")
    quote_snapshot = read_json(DATA_DIR / "current_quote_snapshot.json")
    date_value = snapshot_date or latest_snapshot_date(agg_rows)
    quotes = (
        quote_snapshot.get("quotes", {})
        if str(status.get("snapshot_date", "")) == date_value
        else {}
    )
    base_rows = build_analysis(date_value)
    base_by_symbol = {row["underlying"]: row for row in base_rows}
    previous_contracts = prior_contract_map(contract_rows + volume_rows, date_value)
    group_by_symbol = {
        symbol: group_name
        for group_name, symbols in rg.THEME_REPORT_GROUPS.items()
        for symbol in symbols
    }

    unusual_by_symbol: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in unusual_rows:
        if row.get("snapshot_date") == date_value:
            unusual_by_symbol[str(row.get("underlying", ""))].append(row)

    candidates: list[dict[str, Any]] = []
    for symbol, base in base_by_symbol.items():
        symbol_unusual = unusual_by_symbol.get(symbol, [])
        structures, residual, parent_orders = structure_and_residual_summary(
            unusual_rows, date_value, symbol
        )
        quote = quotes.get(symbol, {}) if isinstance(quotes, dict) else {}
        stock_price = safe_float(quote.get("stock_price"))
        price_change = safe_float(quote.get("change_ratio"))
        top_contracts = dashboard_renderer.top_contract_rows(
            contract_rows,
            date_value,
            symbol,
            volume_contract_rows=volume_rows,
        )
        contracts = compact_contract_evidence(
            top_contracts,
            previous_contracts,
            date_value,
            stock_price,
            limit=3,
        )
        unusual_turnover_m = sum(safe_float(row.get("turnover")) for row in symbol_unusual) / 1_000_000
        directional_biases = {item["bias"] for item in structures} & {"bullish", "bearish"}
        large_structure = any(item["turnover_m"] >= 1 for item in structures)
        opening_support = any(item["opening_support"] in {"strong", "supportive"} for item in contracts)
        high_v_oi_contract = any(
            item["turnover_m"] >= 10 and item["v_oi"] is not None and item["v_oi"] >= 1
            for item in contracts
        )

        attention_score = 0
        evidence: list[str] = []
        if base["strength"] == "strong":
            attention_score += 3
            evidence.append("surface_strong")
        elif base["strength"] == "medium":
            attention_score += 2
            evidence.append("surface_medium")
        if unusual_turnover_m >= 10:
            attention_score += 2
            evidence.append("large_unusual")
        elif unusual_turnover_m >= 1:
            attention_score += 1
            evidence.append("unusual")
        if directional_biases:
            attention_score += 2
            evidence.append("directional_structure")
        if large_structure:
            attention_score += 1
            evidence.append("large_structure")
        if opening_support:
            attention_score += 1
            evidence.append("opening_support")
        if high_v_oi_contract:
            attention_score += 1
            evidence.append("high_v_oi_contract")
        if abs(price_change) >= 3:
            attention_score += 1
            evidence.append("large_price_move")
        if residual["bias"] != "mixed" and (
            residual["bullish_turnover_m"] + residual["bearish_turnover_m"] >= 1
        ):
            attention_score += 1
            evidence.append("residual_direction")
        if attention_score <= 0:
            continue

        contradictions: list[str] = []
        if base["direction"] == "CALL" and "bearish" in directional_biases:
            contradictions.append("CALL_surface_vs_bearish_structure")
        if base["direction"] == "PUT" and "bullish" in directional_biases:
            contradictions.append("PUT_surface_vs_bullish_structure")
        if directional_biases == {"bullish", "bearish"}:
            contradictions.append("opposing_directional_structures")
        if any(
            item["v_oi"] is not None
            and item["v_oi"] >= 2
            and item["opening_support"] == "weak"
            for item in contracts
        ):
            contradictions.append("high_VOI_without_OI_growth")
        if price_change <= -2 and (
            "bullish" in directional_biases or residual["bias"] == "bullish"
        ):
            contradictions.append("price_down_vs_bullish_evidence")
        if price_change >= 2 and (
            "bearish" in directional_biases or residual["bias"] == "bearish"
        ):
            contradictions.append("price_up_vs_bearish_evidence")
        if not symbol_unusual:
            contradictions.append("no_explicit_unusual_direction")

        candidates.append(
            {
                "symbol": symbol,
                "group": group_by_symbol.get(symbol, ""),
                "attention_score": attention_score,
                "price": stock_price or None,
                "price_change_pct": round(price_change, 2),
                "surface": {
                    "direction": base["direction"],
                    "strength": base["strength"],
                    "total_volume": base["total_volume"],
                    "total_x_base": round(safe_float(base["total_x_base"]), 2),
                    "call_share": round(safe_float(base["call_share"]), 3),
                    "put_call_ratio": round(safe_float(base["put_call_ratio"]), 3),
                },
                "unusual": {
                    "rows": len(symbol_unusual),
                    "parent_orders": parent_orders,
                    "turnover_m": round(unusual_turnover_m, 2),
                    "structures": select_structures(structures),
                    "residual": residual,
                },
                "contracts": contracts,
                "evidence": evidence,
                "contradictions": contradictions[:3],
            }
        )

    candidates.sort(
        key=lambda item: (
            item["attention_score"],
            item["unusual"]["turnover_m"],
            item["surface"]["total_volume"],
        ),
        reverse=True,
    )
    current_symbols = {
        str(row.get("underlying", ""))
        for row in agg_rows
        if row.get("snapshot_date") == date_value
    }
    expected_symbols = set(rg.configured_theme_symbols())
    snapshot_type = str(status.get("snapshot_type", "")) if str(status.get("snapshot_date", "")) == date_value else "unknown"
    return {
        "coverage": {
            "trade_date": date_value,
            "snapshot_type": snapshot_type,
            "complete": snapshot_type == "complete" and current_symbols == expected_symbols,
            "symbols": len(current_symbols),
            "expected_symbols": len(expected_symbols),
            "contracts": sum(1 for row in contract_rows if row.get("snapshot_date") == date_value),
            "volume_contracts": sum(1 for row in volume_rows if row.get("snapshot_date") == date_value),
            "unusual_rows": sum(1 for row in unusual_rows if row.get("snapshot_date") == date_value),
        },
        "groups": compact_group_metrics(agg_rows, date_value),
        "candidates": candidates[: min(15, max(1, limit))],
        "limitations": [
            "Structures use exact same-minute and equal-volume matching; fuzzy matches remain unclassified.",
            "Order IDs, stock legs, and complete opening/closing flags are unavailable; final A/B/C judgment remains with Codex.",
        ],
    }


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
    parser.add_argument("--date", default=None, help="Target US trading date (YYYY-MM-DD).")
    parser.add_argument("--top", type=int, default=20, help="Rows to return; intent mode caps this at 15.")
    parser.add_argument("--intent", action="store_true", help="Emit compact option-intent evidence.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.intent:
        report = build_intent_report(args.date, args.top)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

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
