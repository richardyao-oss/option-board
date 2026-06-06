from __future__ import annotations

import html
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


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


def fmt_int(value: Any) -> str:
    number = safe_int(value)
    return f"{number:,}" if number else ""


def fmt_ratio(value: Any) -> str:
    number = safe_float(value)
    return f"{number:.2f}" if number else ""


def fmt_price(value: Any) -> str:
    number = safe_float(value)
    if number <= 0:
        return "--"
    return f"{number:.2f}"


def fmt_change_pct(value: Any) -> str:
    if value in (None, "", "N/A"):
        return "--"
    number = safe_float(value)
    if not number:
        return "--"
    return f"{number:+.2f}%"


def trend_class(value: Any, invert: bool = False) -> str:
    number = safe_float(value)
    if invert:
        number = -number
    if number > 0:
        return "trend-up"
    if number < 0:
        return "trend-down"
    return "trend-flat"


def delta_trend_class(current: Any, previous: Any, invert: bool = False) -> str:
    current_value = safe_float(current)
    previous_value = safe_float(previous)
    if previous_value <= 0 or current_value <= 0:
        return "trend-flat"
    return trend_class(current_value - previous_value, invert=invert)


def compact_delta(value: str) -> str:
    return value.replace("（", "(").replace("）", ")")


def volume_delta_html(value: str) -> str:
    text = compact_delta(value)
    if text.endswith("|up"):
        return f"{html.escape(text[:-3])} <span class='delta-arrow'>&uarr;</span>"
    if text.endswith("|down"):
        return f"{html.escape(text[:-5])} <span class='delta-arrow'>&darr;</span>"
    return html.escape(text)


def fmt_strike(value: Any) -> str:
    number = safe_float(value)
    if not number:
        return ""
    return f"{number:.3f}".rstrip("0").rstrip(".")


def fmt_musd(value: Any) -> str:
    amount = safe_float(value)
    if amount <= 0:
        return ""
    millions = amount / 1_000_000
    return f"{millions:.2f}" if millions < 1 else f"{millions:.1f}"


def fmt_volume_delta(current: Any, previous: Any) -> str:
    current_value = safe_float(current)
    previous_value = safe_float(previous)
    if previous_value <= 0 or current_value <= 0:
        return ""
    change = (current_value - previous_value) / previous_value * 100
    if change > 0:
        return f"{change:.0f}%|up"
    if change < 0:
        return f"{abs(change):.0f}%|down"
    return "0%"
    if change > 0:
        return f"{change:.0f}% ↑"
    if change < 0:
        return f"{abs(change):.0f}% ↓"
    return "0%"
    return f"（{change:+.0f}%）"


def fmt_ratio_delta(current: Any, previous: Any) -> str:
    current_value = safe_float(current)
    previous_value = safe_float(previous)
    if previous_value <= 0 or current_value <= 0:
        return ""
    change = current_value - previous_value
    return f"（{change:+.2f}）"


def latest_snapshot_value(rows: list[dict[str, Any]], key: str) -> str:
    values = sorted({str(row.get(key, "")) for row in rows if row.get(key)})
    return values[-1] if values else ""


def dominant_direction(row: dict[str, Any]) -> str:
    total = safe_int(row.get("total_volume"))
    if total <= 0:
        return "NONE"
    return "CALL" if safe_float(row.get("call_share")) >= safe_float(row.get("put_share")) else "PUT"


def latest_signals_by_symbol(signal_rows: list[dict[str, Any]], allowed_dates: set[str]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for signal in signal_rows:
        if str(signal.get("snapshot_date", "")) in allowed_dates:
            grouped[str(signal.get("underlying", ""))].append(signal)

    result: dict[str, dict[str, Any]] = {}
    for symbol, rows in grouped.items():
        rows.sort(key=lambda row: (str(row.get("snapshot_date", "")), safe_float(row.get("score"))), reverse=True)
        if rows:
            result[symbol] = rows[0]
    return result


def normalize_report_groups(report_groups: dict[str, list[str]] | None) -> list[tuple[str, set[str]]]:
    if not report_groups:
        return []
    groups: list[tuple[str, set[str]]] = []
    for name, symbols in report_groups.items():
        clean_name = str(name).strip()
        clean_symbols = {str(symbol).upper() for symbol in symbols if str(symbol).strip()}
        if clean_name and clean_symbols:
            groups.append((clean_name, clean_symbols))
    return groups


def all_group_symbols(groups: list[tuple[str, set[str]]]) -> set[str]:
    symbols: set[str] = set()
    for _, group_symbols in groups:
        symbols.update(group_symbols)
    return symbols


def groups_for_symbol(symbol: str, groups: list[tuple[str, set[str]]]) -> list[str]:
    memberships = [name for name, symbols in groups if symbol in symbols]
    if memberships:
        return memberships
    return [groups[0][0]] if groups else []


def group_attr(symbol: str, groups: list[tuple[str, set[str]]]) -> str:
    return html.escape("|".join(groups_for_symbol(symbol, groups)), quote=True)


def group_switch_html(groups: list[tuple[str, set[str]]]) -> str:
    if len(groups) <= 1:
        return ""
    buttons = []
    for index, (name, symbols) in enumerate(groups):
        active = " active" if index == 0 else ""
        buttons.append(
            f"<button class='group-button{active}' data-report-group='{html.escape(name, quote=True)}' type='button'>"
            f"{html.escape(name)} <span>{len(symbols)}</span>"
            "</button>"
        )
    return f"<section class='group-switch' aria-label='report groups'>{''.join(buttons)}</section>"


def share_stack(call_share: float, put_share: float) -> str:
    return (
        f"<div class='stack' title='Call {call_share:.0%}, Put {put_share:.0%}'>"
        f"<i class='call-fill' style='width:{max(0.0, min(1.0, call_share)) * 100:.2f}%'></i>"
        f"<i class='put-fill' style='width:{max(0.0, min(1.0, put_share)) * 100:.2f}%'></i>"
        "</div>"
    )


def option_expiry(option_code: str) -> str:
    match = re.search(r"(\d{6})([CP])", option_code)
    if not match:
        return ""
    raw = match.group(1)
    return f"20{raw[:2]}-{raw[2:4]}-{raw[4:6]}"


def top_contract_rows(
    contract_rows: list[dict[str, str]],
    snapshot_date: str,
    symbol: str,
    limit: int = 10,
    snapshot_time: str | None = None,
    volume_contract_rows: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    amount_rows = [
        row for row in contract_rows
        if row.get("snapshot_date") == snapshot_date
        and row.get("underlying") == symbol
        and (snapshot_time is None or row.get("snapshot_time") == snapshot_time)
    ]
    volume_source = volume_contract_rows if volume_contract_rows is not None else contract_rows
    volume_rows = [
        row for row in volume_source
        if row.get("snapshot_date") == snapshot_date
        and row.get("underlying") == symbol
        and (snapshot_time is None or row.get("snapshot_time") == snapshot_time)
    ] or amount_rows

    amount_top = sorted(
        amount_rows,
        key=lambda row: (safe_float(row.get("turnover")), safe_int(row.get("volume"))),
        reverse=True,
    )[:5]
    amount_codes = {str(row.get("option_code", "")) for row in amount_top}
    volume_top = sorted(
        volume_rows,
        key=lambda row: (safe_int(row.get("volume")), safe_float(row.get("turnover"))),
        reverse=True,
    )[:10]
    mixed = amount_top + [row for row in volume_top if str(row.get("option_code", "")) not in amount_codes][:5]
    return mixed[:limit]


def option_moneyness_marker(option_type: str, strike: Any, stock_price: Any) -> tuple[str, str]:
    price = safe_float(stock_price)
    strike_value = safe_float(strike)
    if price <= 0 or strike_value <= 0:
        return "", ""

    if option_type.upper() == "CALL":
        distance = strike_value / price - 1
        symbol = "+"
        css_class = "call-otm-mark"
    elif option_type.upper() == "PUT":
        distance = 1 - strike_value / price
        symbol = "-"
        css_class = "put-otm-mark"
    else:
        return "", ""

    if 0.05 <= distance < 0.10:
        return symbol, css_class
    if 0.10 <= distance < 0.20:
        return symbol * 2, css_class
    if distance >= 0.20:
        return symbol * 3, css_class
    return "", ""


def contract_table(contracts: list[dict[str, str]], stock_price: Any = None) -> str:
    if not contracts:
        return "<div class='contract-empty'>暂无合约明细</div>"

    rows = []
    max_turnover = max((safe_float(contract.get("turnover")) for contract in contracts), default=0.0)
    max_volume = max((safe_int(contract.get("volume")) for contract in contracts), default=0)

    def display_key(contract: dict[str, str]) -> tuple[str, int, float, int]:
        expiry = option_expiry(str(contract.get("option_code", ""))) or "9999-99-99"
        option_type = str(contract.get("option_type", "")).upper()
        type_rank = 0 if option_type == "CALL" else 1 if option_type == "PUT" else 2
        return (expiry, type_rank, -safe_float(contract.get("strike")), -safe_int(contract.get("volume")))

    previous_expiry = ""
    for contract in sorted(contracts, key=display_key):
        expiry = option_expiry(str(contract.get("option_code", ""))) or "--"
        option_type = str(contract.get("option_type", "")).upper()
        type_class = "call-text" if option_type == "CALL" else "put-text" if option_type == "PUT" else ""
        row_class = " class='expiry-break'" if previous_expiry and expiry != previous_expiry else ""
        expiry_label = expiry if expiry != previous_expiry else ""
        turnover = safe_float(contract.get("turnover"))
        bar_width = min(100.0, turnover / max_turnover * 100) if max_turnover > 0 else 0.0
        volume = safe_int(contract.get("volume"))
        volume_bar_width = min(100.0, volume / max_volume * 100) if max_volume > 0 else 0.0
        marker, marker_class = option_moneyness_marker(option_type, contract.get("strike"), stock_price)
        marker_html = f" <span class='otm-mark {marker_class}'>{html.escape(marker)}</span>" if marker else ""
        type_label = html.escape(option_type[:1] or "--")
        strike_label = html.escape(fmt_strike(contract.get("strike")))
        previous_expiry = expiry
        rows.append(
            f"<tr{row_class}>"
            f"<td class='expiry-cell'>{html.escape(expiry_label)}</td>"
            f"<td class='contract-kind'><span class='{type_class}'>{type_label}</span> <span>{strike_label}</span>{marker_html}</td>"
            f"<td class='volume-cell'><span class='volume-bar' style='width:{volume_bar_width:.1f}%'></span><span class='volume-value'>{fmt_int(volume)}</span></td>"
            f"<td class='turnover-cell'><span class='turnover-bar' style='width:{bar_width:.1f}%'></span><span class='turnover-value'>{html.escape(fmt_musd(turnover))}</span></td>"
            "</tr>"
        )

    return (
        "<table class='contract-table'>"
        "<thead><tr><th>到期</th><th>型/行权</th><th>量</th><th>额($M)</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def contract_identity(row: dict[str, Any]) -> tuple[str, str, str]:
    expiry = str(row.get("expiry") or option_expiry(str(row.get("option_code", ""))) or "")
    option_type = str(row.get("option_type", "")).upper()
    strike = f"{safe_float(row.get('strike')):.4f}"
    return expiry, option_type, strike


def matched_unusual_rows(
    contracts: list[dict[str, str]],
    unusual_rows: list[dict[str, str]],
    snapshot_date: str,
    symbol: str,
    limit: int = 10,
) -> list[dict[str, str]]:
    if not contracts or not unusual_rows:
        return []
    top_codes = {str(row.get("option_code", "")) for row in contracts if row.get("option_code")}
    top_identities = {contract_identity(row) for row in contracts}
    matched: list[dict[str, str]] = []
    for row in unusual_rows:
        if str(row.get("snapshot_date", "")) != snapshot_date:
            continue
        if str(row.get("underlying", "")) != symbol:
            continue
        if str(row.get("direction", "")).upper() not in {"BUY", "SELL"}:
            continue
        option_code = str(row.get("option_code", ""))
        if option_code and option_code in top_codes:
            matched.append(row)
            continue
        if contract_identity(row) in top_identities:
            matched.append(row)
    matched.sort(key=lambda row: (safe_float(row.get("turnover")), safe_int(row.get("volume"))), reverse=True)
    return matched[:limit]


def unusual_match_table(rows: list[dict[str, str]], stock_price: Any = None) -> str:
    if not rows:
        return ""

    max_turnover = max((safe_float(row.get("turnover")) for row in rows), default=0.0)
    max_volume = max((safe_int(row.get("volume")) for row in rows), default=0)
    body = []
    for row in rows:
        expiry = str(row.get("expiry") or option_expiry(str(row.get("option_code", ""))) or "--")
        option_type = str(row.get("option_type", "")).upper()
        type_class = "call-text" if option_type == "CALL" else "put-text" if option_type == "PUT" else ""
        type_label = html.escape(option_type[:1] or "--")
        strike_label = html.escape(fmt_strike(row.get("strike")))
        marker, marker_class = option_moneyness_marker(option_type, row.get("strike"), stock_price)
        marker_html = f" <span class='otm-mark {marker_class}'>{html.escape(marker)}</span>" if marker else ""
        volume = safe_int(row.get("volume"))
        turnover = safe_float(row.get("turnover"))
        volume_width = min(100.0, volume / max_volume * 100) if max_volume > 0 else 0.0
        turnover_width = min(100.0, turnover / max_turnover * 100) if max_turnover > 0 else 0.0
        direction = str(row.get("direction", "")).upper()
        direction_label = "主动买入" if direction == "BUY" else "主动卖出" if direction == "SELL" else ""
        direction_class = "buy" if direction == "BUY" else "sell" if direction == "SELL" else ""
        body.append(
            "<tr>"
            f"<td class='expiry-cell'>{html.escape(expiry)}</td>"
            f"<td class='contract-kind'><span class='{type_class}'>{type_label}</span> <span>{strike_label}</span>{marker_html}</td>"
            f"<td class='volume-cell'><span class='volume-bar' style='width:{volume_width:.1f}%'></span><span class='volume-value'>{fmt_int(volume)}</span></td>"
            f"<td class='turnover-cell'><span class='turnover-bar' style='width:{turnover_width:.1f}%'></span><span class='turnover-value'>{html.escape(fmt_musd(turnover))}</span></td>"
            f"<td class='unusual-direction {direction_class}'>{html.escape(direction_label)}</td>"
            "</tr>"
        )

    return (
        "<section class='unusual-matches'>"
        "<table class='contract-table unusual-table'>"
        "<thead><tr><th>到期</th><th>型/行权</th><th>量</th><th>额($M)</th><th>方向</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody>"
        "</table>"
        "</section>"
    )


def daily_symbol_infos(
    agg_rows: list[dict[str, Any]],
    signal_rows: list[dict[str, Any]],
    snapshot_date: str,
    display_dates: list[str],
    group_symbols: set[str] | None = None,
) -> list[dict[str, Any]]:
    by_symbol_date = {(str(row.get("underlying", "")), str(row.get("snapshot_date", ""))): row for row in agg_rows}
    current_symbols = {
        str(row.get("underlying", ""))
        for row in agg_rows
        if str(row.get("snapshot_date", "")) == snapshot_date and row.get("underlying")
    }
    if group_symbols is not None:
        current_symbols = set(group_symbols)
    elif not current_symbols:
        current_symbols = {str(row.get("underlying", "")) for row in agg_rows if row.get("underlying")}

    signal_map = latest_signals_by_symbol(signal_rows, set(display_dates))
    infos = []
    for symbol in sorted(current_symbols):
        latest_row = by_symbol_date.get((symbol, snapshot_date), {})
        signal = signal_map.get(symbol, {})
        score = safe_float(signal.get("score"))
        direction = str(signal.get("direction") or dominant_direction(latest_row))
        infos.append(
            {
                "symbol": symbol,
                "latest_row": latest_row,
                "signal": signal,
                "score": score,
                "direction": direction if direction in {"CALL", "PUT"} else "NONE",
                "total": safe_int(latest_row.get("total_volume")),
                "pcr": safe_float(latest_row.get("put_call_ratio")),
            }
        )

    infos.sort(key=lambda item: (item["score"], item["total"], item["symbol"]), reverse=True)
    return infos


def render_daily_rows(
    agg_rows: list[dict[str, Any]],
    signal_rows: list[dict[str, Any]],
    contract_rows: list[dict[str, str]],
    snapshot_date: str,
    display_dates: list[str],
    groups: list[tuple[str, set[str]]] | None = None,
    quote_map: dict[str, Any] | None = None,
    snapshot_status: dict[str, Any] | None = None,
    volume_contract_rows: list[dict[str, str]] | None = None,
    option_unusual_rows: list[dict[str, str]] | None = None,
) -> tuple[str, dict[str, Any]]:
    groups = groups or []
    quote_map = quote_map or {}
    snapshot_status = snapshot_status or {}
    option_unusual_rows = option_unusual_rows or []
    is_intraday_snapshot = (
        str(snapshot_status.get("snapshot_type", "")).lower() == "intraday"
        and str(snapshot_status.get("snapshot_date") or snapshot_status.get("trade_date") or "") == snapshot_date
    )
    by_symbol_date = {(str(row.get("underlying", "")), str(row.get("snapshot_date", ""))): row for row in agg_rows}
    infos = daily_symbol_infos(agg_rows, signal_rows, snapshot_date, display_dates, all_group_symbols(groups))
    rows_html = []

    def day_label(day: str) -> str:
        label = html.escape(day[5:])
        if is_intraday_snapshot and day == snapshot_date:
            return f"{label}<em class='day-tag'>盘中</em>"
        return label

    for index, info in enumerate(infos, start=1):
        symbol = str(info["symbol"])
        latest_row = info["latest_row"]
        score = safe_float(info["score"])
        direction = str(info["direction"])
        total = safe_int(info["total"])
        pcr = safe_float(info["pcr"])
        total_text = f"{total:,}" if total else "暂无数据"
        quote = quote_map.get(symbol, {})
        price_text = fmt_price(quote.get("stock_price"))
        change_text = fmt_change_pct(quote.get("change_ratio"))
        stock_trend = trend_class(quote.get("change_ratio"))

        visible_dates = display_dates[-4:]
        date_index = {day: index for index, day in enumerate(display_dates)}
        day_cells = []
        for day in visible_dates:
            row = by_symbol_date.get((symbol, day))
            day_index = date_index.get(day, 0)
            previous_day = display_dates[day_index - 1] if day_index else ""
            previous_row = by_symbol_date.get((symbol, previous_day)) if previous_day else None
            if not row:
                day_cells.append(
                    f"<div class='day-cell empty'><span class='day-date'>{day_label(day)}</span><div class='ghost-line'></div><div class='day-metrics'><span></span><span></span></div></div>"
                )
                continue
            call_share = safe_float(row.get("call_share"))
            put_share = safe_float(row.get("put_share"))
            dominant = dominant_direction(row).lower()
            pcr_delta = fmt_ratio_delta(row.get("put_call_ratio"), previous_row.get("put_call_ratio") if previous_row else "")
            volume_delta = fmt_volume_delta(row.get("total_volume"), previous_row.get("total_volume") if previous_row else "")
            pcr_delta_class = delta_trend_class(
                row.get("put_call_ratio"),
                previous_row.get("put_call_ratio") if previous_row else "",
                invert=True,
            )
            day_cells.append(
                f"<div class='day-cell {dominant}'>"
                f"<span class='day-date'>{day_label(day)}</span>"
                f"{share_stack(call_share, put_share)}"
                "<div class='day-metrics'>"
                f"<span class='day-metric'><strong>P/C {fmt_ratio(row.get('put_call_ratio'))}</strong><em class='{pcr_delta_class}'>{html.escape(compact_delta(pcr_delta))}</em></span>"
                f"<span class='day-metric'><strong>{fmt_int(row.get('total_volume'))}</strong><em class='volume-delta'>{volume_delta_html(volume_delta)}</em></span>"
                "</div>"
                "</div>"
            )
        day_cells = list(reversed(day_cells))

        contracts = top_contract_rows(
            contract_rows,
            snapshot_date,
            symbol,
            limit=10,
            volume_contract_rows=volume_contract_rows,
        )
        unusual_matches = matched_unusual_rows(contracts, option_unusual_rows, snapshot_date, symbol, limit=10)
        unusual_html = unusual_match_table(unusual_matches, quote.get("stock_price"))
        rows_html.append(f"""
        <article class="scan-row" data-symbol="{html.escape(symbol)}" data-groups="{group_attr(symbol, groups)}" data-direction="{html.escape(direction)}" data-score="{score:.2f}" data-total="{total}" data-pcr="{pcr:.4f}">
          <section class="identity">
            <div class="rank">{index:02d}</div>
            <div class="identity-main">
              <h2>{html.escape(symbol)}</h2>
              <p><span>{html.escape(snapshot_date[5:])}</span><b>{html.escape(total_text)}</b><span>P/C {pcr:.2f}</span></p>
            </div>
          </section>
          <section class="score-line">
            <div class="score-badge"><em>异常分</em><b>{score:.1f}</b></div>
            <div class="quote-metrics">
              <span><em>当前价</em><b class="{stock_trend}">{html.escape(price_text)}</b></span>
              <span><em>涨跌幅</em><b class="{stock_trend}">{html.escape(change_text)}</b></span>
            </div>
          </section>
          <section class="card-body">
            <div class="rail-frame"><section class="week-strip">{''.join(day_cells)}</section></div>
            <aside class="contracts">
              <div class="contracts-title">Top 10 成交额</div>
              {contract_table(contracts, quote.get("stock_price"))}
            </aside>
          </section>
          {unusual_html}
        </article>
        """)

    if not rows_html:
        rows_html.append("<article class='empty-board'><h2>当前分组没有可展示标的</h2><p>请确认自选分组或数据快照。</p></article>")

    current_agg = [row for row in agg_rows if str(row.get("snapshot_date", "")) == snapshot_date]
    summary = {
        "count": len(infos),
        "scored": sum(1 for item in infos if item["score"] > 0),
        "scanned": len({str(row.get("underlying", "")) for row in current_agg if row.get("underlying")}),
        "volume": sum(safe_int(row.get("total_volume")) for row in current_agg),
    }
    return "".join(rows_html), summary


def render_intraday_rows(
    intraday_agg_rows: list[dict[str, Any]],
    intraday_signal_rows: list[dict[str, Any]],
    intraday_contract_rows: list[dict[str, str]],
    allowed_symbols: set[str] | None = None,
    groups: list[tuple[str, set[str]]] | None = None,
    quote_map: dict[str, Any] | None = None,
    intraday_volume_contract_rows: list[dict[str, str]] | None = None,
    option_unusual_rows: list[dict[str, str]] | None = None,
) -> tuple[str, dict[str, Any]]:
    groups = groups or []
    quote_map = quote_map or {}
    option_unusual_rows = option_unusual_rows or []
    latest_time = latest_snapshot_value(intraday_agg_rows + intraday_signal_rows, "snapshot_time")
    agg_rows = list(intraday_agg_rows)
    signal_rows = list(intraday_signal_rows)
    if allowed_symbols:
        agg_rows = [row for row in agg_rows if str(row.get("underlying", "")) in allowed_symbols]
        signal_rows = [row for row in signal_rows if str(row.get("underlying", "")) in allowed_symbols]

    by_symbol: dict[str, dict[str, Any]] = {}
    for row in agg_rows:
        symbol = str(row.get("underlying", ""))
        if not symbol:
            continue
        current = by_symbol.get(symbol)
        if current is None or str(row.get("snapshot_time", "")) > str(current.get("snapshot_time", "")):
            by_symbol[symbol] = row

    signal_map: dict[str, dict[str, Any]] = {}
    for row in signal_rows:
        symbol = str(row.get("underlying", ""))
        if not symbol:
            continue
        current = signal_map.get(symbol)
        if current is None or str(row.get("snapshot_time", "")) > str(current.get("snapshot_time", "")):
            signal_map[symbol] = row

    infos = []
    symbols = set(by_symbol.keys()) | all_group_symbols(groups)
    for symbol in sorted(symbols):
        row = by_symbol.get(symbol, {})
        signal = signal_map.get(symbol, {})
        direction = str(signal.get("direction") or dominant_direction(row))
        infos.append(
            {
                "symbol": symbol,
                "row": row,
                "score": safe_float(signal.get("score")),
                "direction": direction if direction in {"CALL", "PUT"} else "NONE",
                "total": safe_int(row.get("total_volume")),
                "pcr": safe_float(row.get("put_call_ratio")),
            }
        )
    infos.sort(key=lambda item: (item["score"], item["total"], item["symbol"]), reverse=True)

    cards = []
    for index, info in enumerate(infos, start=1):
        row = info["row"]
        symbol = str(info["symbol"])
        score = safe_float(info["score"])
        total = safe_int(info["total"])
        pcr = safe_float(info["pcr"])
        direction = str(info["direction"])
        call_share = safe_float(row.get("call_share"))
        put_share = safe_float(row.get("put_share"))
        snapshot_date = str(row.get("snapshot_date", ""))
        snapshot_time = str(row.get("snapshot_time", ""))
        contracts = top_contract_rows(
            intraday_contract_rows,
            snapshot_date,
            symbol,
            limit=10,
            snapshot_time=snapshot_time or None,
            volume_contract_rows=intraday_volume_contract_rows,
        )
        unusual_matches = matched_unusual_rows(contracts, option_unusual_rows, snapshot_date, symbol, limit=10)
        unusual_html = unusual_match_table(unusual_matches, quote_map.get(symbol, {}).get("stock_price"))
        total_text = f"{total:,}" if total else "暂无数据"
        quote = quote_map.get(symbol, {})
        price_text = fmt_price(quote.get("stock_price"))
        change_text = fmt_change_pct(quote.get("change_ratio"))
        stock_trend = trend_class(quote.get("change_ratio"))

        cards.append(f"""
        <article class="intraday-row" data-symbol="{html.escape(symbol)}" data-groups="{group_attr(symbol, groups)}" data-direction="{html.escape(direction)}" data-score="{score:.2f}" data-total="{total}" data-pcr="{pcr:.4f}">
          <section class="identity">
            <div class="rank">{index:02d}</div>
            <div class="identity-main">
              <h2>{html.escape(symbol)}</h2>
              <p><span>{html.escape(snapshot_date[5:])}</span><b>{html.escape(total_text)}</b><span>P/C {pcr:.2f}</span></p>
            </div>
          </section>
          <section class="score-line">
            <div class="score-badge"><em>异常分</em><b>{score:.1f}</b></div>
            <div class="quote-metrics">
              <span><em>当前价</em><b class="{stock_trend}">{html.escape(price_text)}</b></span>
              <span><em>涨跌幅</em><b class="{stock_trend}">{html.escape(change_text)}</b></span>
            </div>
          </section>
          <section class="card-body">
            <div class="rail-frame"><section class="current-strip">
                <div class="current-cell">
                  <span>Call / Put</span>
                  {share_stack(call_share, put_share)}
                  <b>{fmt_int(row.get('call_volume'))} / {fmt_int(row.get('put_volume'))}</b>
                </div>
                <div class="current-cell">
                  <span>Score</span>
                  <b>{score:.1f}</b>
                  <em>{'已评分' if score else '暂无成交'}</em>
                </div>
              </section></div>
            <aside class="contracts">
              <div class="contracts-title">Top 10 成交额</div>
              {contract_table(contracts, quote.get("stock_price"))}
            </aside>
          </section>
          {unusual_html}
        </article>
        """)

    if not cards:
        cards.append("<article class='empty-board'><h2>当前没有盘中快照</h2><p>盘中数据单独存储，不写入正式 7 日基线。</p></article>")

    summary = {
        "snapshot_time": latest_time,
        "as_of_et": latest_snapshot_value(signal_rows + agg_rows, "as_of_et"),
        "trade_date": latest_snapshot_value(signal_rows + agg_rows, "trade_date"),
        "count": len(infos),
        "scored": sum(1 for item in infos if item["score"] > 0),
        "scanned": len(by_symbol),
        "volume": sum(safe_int(row.get("total_volume")) for row in by_symbol.values()),
    }
    return "".join(cards), summary


def render_html(
    html_path: Path,
    agg_rows: list[dict[str, Any]],
    signal_rows: list[dict[str, Any]],
    contract_rows: list[dict[str, str]],
    snapshot_date: str,
    display_dates: list[str],
    volume_contract_rows: list[dict[str, str]] | None = None,
    intraday_agg_rows: list[dict[str, Any]] | None = None,
    intraday_signal_rows: list[dict[str, Any]] | None = None,
    intraday_contract_rows: list[dict[str, str]] | None = None,
    intraday_volume_contract_rows: list[dict[str, str]] | None = None,
    option_unusual_rows: list[dict[str, str]] | None = None,
    report_groups: dict[str, list[str]] | None = None,
    quote_map: dict[str, Any] | None = None,
    snapshot_status: dict[str, Any] | None = None,
) -> None:
    volume_contract_rows = volume_contract_rows or []
    intraday_agg_rows = intraday_agg_rows or []
    intraday_signal_rows = intraday_signal_rows or []
    intraday_contract_rows = intraday_contract_rows or []
    intraday_volume_contract_rows = intraday_volume_contract_rows or []
    option_unusual_rows = option_unusual_rows or []
    groups = normalize_report_groups(report_groups)
    quote_map = quote_map or {}
    snapshot_status = snapshot_status or {}

    daily_rows, daily_summary = render_daily_rows(
        agg_rows,
        signal_rows,
        contract_rows,
        snapshot_date,
        display_dates,
        groups,
        quote_map,
        snapshot_status,
        volume_contract_rows,
        option_unusual_rows,
    )
    first_day = display_dates[0]
    last_day = display_dates[-1]
    group_switch = group_switch_html(groups)
    status_type = str(snapshot_status.get("snapshot_type") or "complete").lower()
    status_label = "盘中快照" if status_type == "intraday" else "完整复盘"
    status_time = str(snapshot_status.get("as_of_et") or snapshot_status.get("snapshot_time") or "本地已存数据")
    status_note = "最新日期为盘中快照；完整复盘会覆盖同一天数据。" if status_type == "intraday" else "最新日期为完整复盘数据。"

    doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>期权异动扫描单</title>
  <style>
    :root {{
      --paper: #eef4fb;
      --panel: #ffffff;
      --ink: #13233d;
      --text: #243149;
      --muted: #66748a;
      --faint: #b5c1d2;
      --hair: #d9e2ee;
      --hair-strong: #aebbd0;
      --blue: #1f6feb;
      --call: #d7434a;
      --call-soft: #fff5f5;
      --put: #2f9360;
      --put-soft: #f1faf5;
      --rise: #d7434a;
      --fall: #2f9360;
      --void: #f7faff;
    }}
    * {{ box-sizing: border-box; }}
    html {{ background: var(--paper); }}
    body {{ margin: 0; color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif; line-height: 1.4; background:
      radial-gradient(circle at 18% 0%, rgba(255,255,255,.95), rgba(255,255,255,0) 28%),
      linear-gradient(180deg, #f8fbff 0%, var(--paper) 34%, #edf3fb 100%); }}
    button, input {{ font: inherit; }}
    .sheet {{ max-width: none; margin: 0; padding: 0 28px 44px; }}
    .mast {{ background: rgba(255,255,255,.88); border-bottom: 1px solid var(--hair); padding: 14px 28px 12px; margin: 0 -28px; position: sticky; top: 0; z-index: 100; display: flex; align-items: baseline; justify-content: space-between; gap: 18px; box-shadow: 0 1px 10px rgba(27, 55, 92, .06); backdrop-filter: blur(14px); }}
    .eyebrow {{ display: none; }}
    h1 {{ margin: 0; font-size: 22px; line-height: 1.2; letter-spacing: 0; color: #0f1f38; font-weight: 850; }}
    h2 {{ margin: 0; font-size: 14px; line-height: 1.15; letter-spacing: 0; color: var(--ink); }}
    .meta {{ display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 6px 18px; margin: 0; color: var(--muted); font-size: 12px; }}
    .meta span {{ white-space: nowrap; }}
    .group-switch {{ display: flex; flex-wrap: wrap; gap: 0; background: rgba(255,255,255,.82); border-bottom: 1px solid #dfe7f2; margin: 0 -28px; padding: 0 28px; position: sticky; top: 52px; z-index: 80; backdrop-filter: blur(14px); }}
    .group-button {{ border: 0; border-bottom: 2px solid transparent; background: transparent; color: var(--muted); min-width: 0; padding: 8px 16px; cursor: pointer; text-align: left; font-size: 12px; transition: color .15s, border-color .15s; }}
    .group-button:hover {{ color: var(--text); }}
    .group-button span {{ color: var(--faint); font-size: 11px; margin-left: 4px; }}
    .group-button.active {{ color: var(--blue); border-bottom-color: var(--blue); font-weight: 700; }}
    .group-button.active span {{ color: var(--blue); }}
    .toolbar {{ display: flex; align-items: center; gap: 12px; position: static; background: rgba(248,251,255,.92); padding: 12px 28px; margin: 0 -28px; border-bottom: 1px solid #dfe7f2; backdrop-filter: blur(12px); }}
    .search {{ width: 176px; border: 1px solid #d1dceb; border-radius: 7px; background: rgba(255,255,255,.92); padding: 7px 11px; outline: none; color: var(--text); font-size: 12px; box-shadow: inset 0 1px 0 rgba(255,255,255,.8); }}
    .search:focus {{ border-color: var(--blue); box-shadow: 0 0 0 2px rgba(26,115,232,.12); }}
    .search:focus, .seg button:focus-visible, .group-button:focus-visible {{ outline: none; }}
    .seg {{ display: inline-flex; gap: 3px; border: 0; background: transparent; }}
    .seg[data-sorter] {{ margin-left: auto; }}
    .seg button {{ border: 1px solid #d9e2ee; border-radius: 6px; background: rgba(255,255,255,.9); min-width: 0; padding: 6px 13px; cursor: pointer; color: var(--muted); font-size: 12px; transition: all .12s; }}
    .seg button:hover {{ color: var(--text); border-color: var(--hair-strong); }}
    .seg button.active {{ color: #fff; background: var(--blue); border-color: var(--blue); font-weight: 600; }}
    .seg[data-sorter] button.active {{ background: #173563; border-color: #173563; }}
    .count {{ text-align: right; color: var(--faint); font-size: 12px; white-space: nowrap; }}
    .board {{ margin-top: 22px; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 20px 26px; overflow: visible; }}
    .scan-row {{ position: relative; display: flex; flex-direction: column; border: 1px solid #dbe5f1; border-radius: 12px; background: rgba(255,255,255,.90); min-height: 520px; padding: 22px 22px 20px; overflow: hidden; box-shadow: 0 12px 32px rgba(29, 55, 91, .08), 0 1px 0 rgba(255,255,255,.9) inset; }}
    .score-line {{ display: flex; align-items: center; justify-content: center; gap: 14px; margin: 8px 0 20px; min-height: 32px; }}
    .quote-metrics {{ display: flex; align-items: center; gap: 10px; color: #5f6f86; font-variant-numeric: tabular-nums; }}
    .score-badge, .quote-metrics span {{ display: grid; grid-template-columns: auto auto; gap: 4px; align-items: baseline; padding: 4px 7px; border: 1px solid #e5edf7; border-radius: 7px; background: rgba(255,255,255,.72); font-variant-numeric: tabular-nums; }}
    .score-badge em, .quote-metrics em {{ color: #7d8ca3; font-size: 10px; font-style: normal; font-weight: 700; }}
    .score-badge b, .quote-metrics b {{ color: #10213d; font-size: 12px; font-weight: 850; }}
    .quote-metrics b.trend-up, .day-metric em.trend-up {{ color: var(--rise); }}
    .quote-metrics b.trend-down, .day-metric em.trend-down {{ color: var(--fall); }}
    .quote-metrics b.trend-flat, .day-metric em.trend-flat {{ color: inherit; }}
    .identity {{ display: flex; justify-content: center; padding: 4px 0 0; border-right: 0; }}
    .rank {{ display: none; }}
    .identity-main {{ display: flex; justify-content: center; min-width: 0; }}
    .identity-main h2 {{ padding: 5px 10px; background: transparent; border: 0; border-radius: 0; font-size: 24px; font-weight: 900; color: #0d1f55; box-shadow: none; }}
    .identity-main p {{ display: none; }}
    .card-body {{ display: grid; grid-template-columns: 178px minmax(0, 1fr); gap: 18px; align-items: stretch; min-width: 0; }}
    .rail-frame {{ --rail-scale: .88; width: 178px; min-height: 360px; overflow: hidden; display: flex; }}
    .week-strip {{ flex: 1; display: grid; grid-template-rows: repeat(4, minmax(0, 1fr)); gap: 12px; border: 1px solid #e8eef7; border-right: 0; border-radius: 10px; background: linear-gradient(180deg, #ffffff, #fbfdff); padding: 12px; width: 100%; min-height: 360px; overflow: hidden; box-shadow: 0 10px 20px rgba(19, 52, 91, .035); }}
    .day-cell {{ min-height: 0; min-width: 0; padding: 0 3px; border-right: 0; text-align: center; display: grid; grid-template-rows: auto 12px auto; align-content: center; gap: 8px; }}
    .day-cell:last-child {{ border-right: 0; }}
    .day-cell .day-date, .current-cell span {{ color: #7f8ea7; font-size: 15px; font-weight: 850; }}
    .day-date {{ display: inline-flex; justify-content: center; align-items: center; gap: 5px; }}
    .day-tag {{ color: #1f6feb; border: 1px solid rgba(31,111,235,.22); border-radius: 5px; padding: 1px 4px; background: rgba(31,111,235,.08); font-size: 10px; font-style: normal; font-weight: 800; }}
    .day-metrics {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 7px; align-items: start; }}
    .day-metric {{ display: grid; gap: 4px; min-width: 0; }}
    .day-metric strong {{ color: #081426; font-size: 17px; font-weight: 900; line-height: 1.02; white-space: nowrap; }}
    .day-metric em {{ color: #66748a; font-size: 15px; font-style: normal; font-weight: 850; line-height: 1; white-space: nowrap; }}
    .volume-delta .delta-arrow {{ display: inline-block; margin-left: 1px; font-size: 1.18em; font-weight: 950; line-height: .75; transform: translateY(-1px); }}
    .current-cell em {{ color: var(--faint); font-size: 10px; font-style: normal; }}
    .day-cell.call, .day-cell.put {{ background: transparent; }}
    .day-cell.empty {{ background: transparent; opacity: .28; }}
    .stack, .ghost-line {{ width: 100%; height: 12px; display: flex; overflow: hidden; border: 0; border-radius: 5px; background: #eef2f7; box-shadow: inset 0 1px 0 rgba(255,255,255,.8); }}
    .ghost-line {{ border-style: dashed; }}
    .call-fill {{ background: var(--call); }}
    .put-fill {{ background: var(--put); }}
    .call-text {{ color: var(--call); font-weight: 700; }}
    .put-text {{ color: var(--put); font-weight: 700; }}
    .current-strip {{ display: grid; grid-template-rows: repeat(2, minmax(0, 1fr)); gap: 16px; border-right: 0; border-radius: 10px; background: linear-gradient(180deg, #ffffff, #fbfdff); width: calc(100% / var(--rail-scale)); height: calc(360px / var(--rail-scale)); padding: 18px 12px; box-shadow: 0 10px 20px rgba(19, 52, 91, .035); transform: scale(var(--rail-scale)); transform-origin: top left; }}
    .current-cell {{ padding: 0 10px; border-right: 0; display: grid; align-content: center; gap: 8px; text-align: center; }}
    .current-cell:last-child {{ border-right: 0; }}
    .current-cell b {{ color: var(--ink); font-size: 15px; }}
    .contracts {{ margin-top: 0; background: linear-gradient(180deg, #ffffff, #fcfdff); border: 1px solid #e1e9f4; border-radius: 10px; padding: 12px 14px 10px; overflow-x: hidden; box-shadow: 0 8px 18px rgba(21, 55, 91, .035); min-width: 0; min-height: 360px; }}
    .contracts-title {{ display: none; }}
    .contract-table {{ width: 100%; max-width: 100%; border-collapse: collapse; table-layout: fixed; font-size: 13px; font-variant-numeric: tabular-nums; }}
    .contract-table th {{ background: #f6f9fd; color: #33435c; font-weight: 850; text-align: left; border-bottom: 1px solid #dce5f0; padding: 8px 7px; font-size: 12px; white-space: nowrap; }}
    .contract-table td {{ border-bottom: 1px solid #edf2f7; padding: 6px 7px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: #081426; }}
    .contract-table tr:last-child td {{ border-bottom: 0; }}
    .contract-table tr.expiry-break td {{ border-top: 1px solid #e9ecef; }}
    .expiry-cell {{ color: var(--ink); font-weight: 600; font-variant-numeric: tabular-nums; }}
    .contract-table th:nth-child(1), .contract-table td:nth-child(1) {{ width: 38%; }}
    .contract-table th:nth-child(2), .contract-table td:nth-child(2) {{ width: 25%; }}
    .contract-table th:nth-child(3), .contract-table td:nth-child(3) {{ width: 18%; padding-left: 2px; }}
    .contract-table th:nth-child(4), .contract-table td:nth-child(4) {{ width: 19%; }}
    .contract-kind {{ text-align: left; color: var(--ink); font-weight: 650; }}
    .otm-mark {{ font-weight: 900; }}
    .call-otm-mark {{ color: var(--call); }}
    .put-otm-mark {{ color: var(--put); }}
    .contract-table td:nth-child(3) {{ color: var(--ink); font-weight: 700; }}
    .contract-table td:nth-child(4) {{ color: var(--ink); font-weight: 800; }}
    .contract-table th:nth-child(3), .contract-table td:nth-child(3) {{ text-align: right; }}
    .contract-table th:nth-child(4), .contract-table td:nth-child(4) {{ text-align: right; }}
    .volume-cell {{ position: relative; overflow: hidden; }}
    .volume-bar {{ position: absolute; left: 4px; top: 4px; bottom: 4px; border-radius: 2px; background: rgba(76, 113, 214, .22); z-index: 0; }}
    .volume-value {{ position: relative; z-index: 1; }}
    .turnover-cell {{ position: relative; overflow: hidden; }}
    .turnover-bar {{ position: absolute; left: 4px; top: 4px; bottom: 4px; border-radius: 2px; background: rgba(76, 113, 214, .22); z-index: 0; }}
    .turnover-value {{ position: relative; z-index: 1; }}
    .unusual-matches {{ margin-top: 14px; border: 1px solid #e1e9f4; border-radius: 10px; background: linear-gradient(180deg, rgba(255,255,255,.82), rgba(250,253,255,.92)); padding: 10px 12px; box-shadow: 0 8px 18px rgba(21,55,91,.025); }}
    .unusual-table {{ font-size: 12px; }}
    .unusual-table th {{ padding-top: 7px; padding-bottom: 7px; }}
    .unusual-table td {{ padding-top: 5px; padding-bottom: 5px; }}
    .unusual-table th:nth-child(1), .unusual-table td:nth-child(1) {{ width: 28%; }}
    .unusual-table th:nth-child(2), .unusual-table td:nth-child(2) {{ width: 22%; }}
    .unusual-table th:nth-child(3), .unusual-table td:nth-child(3) {{ width: 18%; text-align: right; }}
    .unusual-table th:nth-child(4), .unusual-table td:nth-child(4) {{ width: 18%; text-align: right; }}
    .unusual-table th:nth-child(5), .unusual-table td:nth-child(5) {{ width: 14%; text-align: right; }}
    .unusual-direction {{ font-size: 11px; font-weight: 850; white-space: nowrap; }}
    .unusual-direction.buy {{ color: var(--call); }}
    .unusual-direction.sell {{ color: var(--put); }}
    .contract-empty, .empty-board {{ color: #ced4da; border: 1px solid var(--hair); border-radius: 5px; background: var(--panel); padding: 14px; text-align: center; font-size: 12px; }}
    .hidden, .tab-panel.hidden {{ display: none; }}
    ::-webkit-scrollbar {{ width: 5px; height: 5px; }}
    ::-webkit-scrollbar-track {{ background: transparent; }}
    ::-webkit-scrollbar-thumb {{ background: #ced4da; border-radius: 3px; }}
    ::-webkit-scrollbar-thumb:hover {{ background: var(--hair-strong); }}
    @media (max-width: 980px) {{
      .sheet {{ padding: 14px; }}
      .mast, .group-switch, .toolbar {{ position: static; margin-left: 0; margin-right: 0; }}
      .mast {{ display: block; }}
      .meta {{ justify-content: flex-start; margin-top: 8px; }}
      .toolbar {{ flex-wrap: wrap; }}
      .count {{ text-align: left; }}
      .board {{ grid-template-columns: 1fr; overflow: visible; padding-bottom: 8px; }}
      .scan-row {{ min-width: 0; }}
      .card-body {{ grid-template-columns: 1fr; }}
      .rail-frame {{ width: 100%; min-height: 300px; }}
      .week-strip {{ min-height: 300px; }}
      .current-strip {{ height: calc(220px / var(--rail-scale)); }}
    }}
    @media (max-width: 1700px) and (min-width: 981px) {{
      .board {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
  </style>
</head>
<body>
  <main class="sheet">
    <header class="mast">
      <p class="eyebrow">Options Anomaly Sheet</p>
      <h1>期权异动扫描单</h1>
      <p class="meta">
        <span>数据日期：{html.escape(snapshot_date)}</span>
        <span>数据状态：{html.escape(status_label)}</span>
        <span>观察窗口：{html.escape(first_day)} 至 {html.escape(last_day)}</span>
        <span>更新时间：{html.escape(status_time)}</span>
      </p>
    </header>

    {group_switch}

    <section class="report-panel" id="preopenPanel" data-panel="preopen">
      <p class="meta"><span>{html.escape(status_note)}</span><span>展示当前自选分组全部已扫描标的。</span><span>展示标的：{daily_summary["count"]}</span><span>已评分：{daily_summary["scored"]}</span><span>总成交量：{daily_summary["volume"]:,}</span></p>
      <section class="toolbar" aria-label="daily filters">
        <input class="search" data-search="preopen" type="search" placeholder="搜索股票代码" autocomplete="off">
        <div class="seg" data-direction-filter="preopen">
          <button class="active" data-filter="ALL" type="button">全部</button>
          <button data-filter="CALL" type="button">CALL</button>
          <button data-filter="PUT" type="button">PUT</button>
        </div>
        <div class="seg" data-sorter="preopen">
          <button class="active" data-sort="score" type="button">分数</button>
          <button data-sort="total" type="button">成交</button>
          <button data-sort="pcr" type="button">P/C</button>
        </div>
        <div class="count"><span data-visible-count="preopen">{daily_summary["count"]}</span> / <span data-total-count="preopen">{daily_summary["count"]}</span></div>
      </section>
      <section class="board" data-board="preopen">{daily_rows}</section>
    </section>
  </main>

  <script>
    const groupButtons = Array.from(document.querySelectorAll('[data-report-group]'));
    let activeGroup = groupButtons.length ? (localStorage.getItem('option-report-group') || groupButtons[0].dataset.reportGroup || '') : '';
    if (!groupButtons.length) {{
      localStorage.removeItem('option-report-group');
    }}
    const states = {{
      preopen: {{ direction: 'ALL', sort: 'score' }}
    }};

    function rowsFor(tab) {{
      return Array.from(document.querySelectorAll(`[data-board="${{tab}}"] [data-symbol]`));
    }}

    function setActive(container, button) {{
      container.querySelectorAll('button').forEach((item) => item.classList.toggle('active', item === button));
    }}

    function rowInGroup(row) {{
      if (!groupButtons.length) return true;
      if (!activeGroup) return true;
      return (row.dataset.groups || '').split('|').includes(activeGroup);
    }}

    function refreshRows(tab) {{
      const search = document.querySelector(`[data-search="${{tab}}"]`);
      const query = search.value.trim().toUpperCase();
      let visible = 0;
      let total = 0;
      rowsFor(tab).forEach((row) => {{
        const symbol = row.dataset.symbol.toUpperCase();
        const direction = row.dataset.direction;
        const groupMatched = rowInGroup(row);
        if (groupMatched) total += 1;
        const matched = groupMatched && (!query || symbol.includes(query)) && (states[tab].direction === 'ALL' || direction === states[tab].direction);
        row.classList.toggle('hidden', !matched);
        if (matched) visible += 1;
      }});
      document.querySelector(`[data-visible-count="${{tab}}"]`).textContent = visible;
      document.querySelector(`[data-total-count="${{tab}}"]`).textContent = total;
    }}

    function sortRows(tab) {{
      const board = document.querySelector(`[data-board="${{tab}}"]`);
      rowsFor(tab).sort((a, b) => Number(b.dataset[states[tab].sort]) - Number(a.dataset[states[tab].sort])).forEach((row) => board.appendChild(row));
      refreshRows(tab);
    }}

    function scrollWeekStripsToLatest() {{
      document.querySelectorAll('.week-strip').forEach((strip) => {{
        strip.scrollTop = 0;
        strip.scrollLeft = 0;
      }});
    }}

    document.querySelectorAll('[data-direction-filter]').forEach((container) => {{
      const tab = container.dataset.directionFilter;
      container.addEventListener('click', (event) => {{
        const button = event.target.closest('button');
        if (!button) return;
        states[tab].direction = button.dataset.filter;
        setActive(container, button);
        refreshRows(tab);
      }});
    }});

    document.querySelectorAll('[data-sorter]').forEach((container) => {{
      const tab = container.dataset.sorter;
      container.addEventListener('click', (event) => {{
        const button = event.target.closest('button');
        if (!button) return;
        states[tab].sort = button.dataset.sort;
        setActive(container, button);
        sortRows(tab);
      }});
    }});

    document.querySelectorAll('[data-search]').forEach((input) => {{
      input.addEventListener('input', () => refreshRows(input.dataset.search));
    }});

    groupButtons.forEach((button) => {{
      button.addEventListener('click', () => {{
        activeGroup = button.dataset.reportGroup;
        groupButtons.forEach((item) => item.classList.toggle('active', item === button));
        localStorage.setItem('option-report-group', activeGroup);
        refreshRows('preopen');
      }});
    }});

    if (groupButtons.length) {{
      const selected = groupButtons.find((button) => button.dataset.reportGroup === activeGroup) || groupButtons[0];
      activeGroup = selected.dataset.reportGroup;
      groupButtons.forEach((button) => button.classList.toggle('active', button === selected));
    }}
    sortRows('preopen');
    requestAnimationFrame(scrollWeekStripsToLatest);
  </script>
</body>
</html>"""
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(doc, encoding="utf-8")
