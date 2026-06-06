#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime_env import configure_runtime


DEFAULT_SIGNAL_DATES = ["2026-05-28", "2026-05-29", "2026-06-01"]
DEFAULT_PRICE_START = "2026-05-28"
DEFAULT_PRICE_END = "2026-06-02"
LOW_LIQUIDITY_EXCLUDED = {
    "US.XYF",
    "US.AIFC",
    "US.REMX",
    "US.CLBT",
    "US.KC",
    "US.MANH",
    "US.FICO",
    "US.GWRE",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in columns})


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


def fmt_pct(value: Any) -> str:
    if value in (None, ""):
        return "pending"
    return f"{safe_float(value):+.2f}%"


def fmt_num(value: Any, digits: int = 3) -> str:
    if value in (None, ""):
        return "n/a"
    return f"{safe_float(value):.{digits}f}"


def quota_summary(value: Any) -> str:
    if isinstance(value, tuple) and len(value) >= 2:
        return f"used={value[0]}, remain={value[1]}"
    return str(value)


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def ranks(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    result = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        rank = (i + j + 2) / 2.0
        for k in range(i, j + 1):
            result[indexed[k][0]] = rank
        i = j + 1
    return result


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return cov / math.sqrt(vx * vy)


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    return pearson(ranks(xs), ranks(ys))


def option_date_rows(data_dir: Path, signal_dates: list[str]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    agg_rows = read_csv(data_dir / "option_screen_underlying_snapshot.csv")
    signal_rows = read_csv(data_dir / "daily_option_signals.csv")
    return (
        [row for row in agg_rows if row.get("snapshot_date") in signal_dates],
        [row for row in signal_rows if row.get("snapshot_date") in signal_dates],
    )


def current_pool_symbols(report_path: Path) -> set[str]:
    if not report_path.exists():
        return set()
    text = report_path.read_text(encoding="utf-8")
    import re

    return set(re.findall(r'data-symbol="([^"]+)"', text))


def top_symbols_by_latest_volume(
    data_dir: Path,
    report_path: Path,
    latest_date: str,
    top_n: int,
) -> list[str]:
    current_pool = current_pool_symbols(report_path)
    agg_rows = read_csv(data_dir / "option_screen_underlying_snapshot.csv")
    rows = [
        row
        for row in agg_rows
        if row.get("snapshot_date") == latest_date
        and row.get("underlying") in current_pool
        and row.get("underlying") not in LOW_LIQUIDITY_EXCLUDED
    ]
    rows.sort(key=lambda row: safe_int(row.get("total_volume")), reverse=True)
    return [str(row.get("underlying")) for row in rows[:top_n]]


def get_opend_state() -> dict[str, Any]:
    configure_runtime()
    from futu import OpenQuoteContext, RET_OK

    ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
    try:
        ret, data = ctx.get_global_state()
        if ret != RET_OK:
            raise RuntimeError(f"get_global_state failed: {data}")
        return dict(data)
    finally:
        ctx.close()


def fetch_daily_klines(symbols: list[str], start: str, end: str) -> tuple[dict[str, dict[str, float]], dict[str, str]]:
    configure_runtime()
    from futu import AuType, KLType, OpenQuoteContext, RET_OK

    prices: dict[str, dict[str, float]] = {}
    failures: dict[str, str] = {}
    ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
    try:
        for symbol in symbols:
            ret, data, page_req_key = ctx.request_history_kline(
                symbol,
                start=start,
                end=end,
                ktype=KLType.K_DAY,
                autype=AuType.QFQ,
                max_count=1000,
            )
            if ret != RET_OK:
                failures[symbol] = str(data)
                continue
            if page_req_key is not None:
                failures[symbol] = "unexpected pagination for short date range"
                continue
            symbol_prices: dict[str, float] = {}
            if data is not None and not data.empty:
                for _, item in data.iterrows():
                    day = str(item.get("time_key", ""))[:10]
                    close = safe_float(item.get("close"))
                    if day and close > 0:
                        symbol_prices[day] = close
            prices[symbol] = symbol_prices
    finally:
        ctx.close()
    return prices, failures


def next_trade_date_from_prices(price_dates: list[str], option_date: str) -> str:
    for day in sorted(price_dates):
        if day > option_date:
            return day
    return ""


def is_current_us_day_incomplete(market_us: str) -> bool:
    return str(market_us).upper() not in {"CLOSED"}


def build_analysis_rows(
    symbols: list[str],
    signal_dates: list[str],
    agg_rows: list[dict[str, str]],
    signal_rows: list[dict[str, str]],
    prices: dict[str, dict[str, float]],
    incomplete_us_day: str | None,
) -> list[dict[str, Any]]:
    agg_by_key = {(row.get("underlying"), row.get("snapshot_date")): row for row in agg_rows}
    signal_by_key = {(row.get("underlying"), row.get("snapshot_date")): row for row in signal_rows}
    output: list[dict[str, Any]] = []
    for symbol in symbols:
        symbol_prices = prices.get(symbol, {})
        price_dates = sorted(symbol_prices)
        for option_date in signal_dates:
            agg = agg_by_key.get((symbol, option_date), {})
            signal = signal_by_key.get((symbol, option_date), {})
            direction = str(signal.get("direction") or "")
            has_signal = direction in {"CALL", "PUT"} and safe_int(agg.get("total_volume")) > 0
            next_date = next_trade_date_from_prices(price_dates, option_date)
            next_return: float | None = None
            status = "pending" if has_signal else "no_signal"
            if (
                has_signal
                and next_date
                and option_date in symbol_prices
                and next_date in symbol_prices
                and next_date != incomplete_us_day
            ):
                base = symbol_prices[option_date]
                next_close = symbol_prices[next_date]
                if base > 0 and next_close > 0:
                    next_return = (next_close / base - 1.0) * 100
                    status = "ready"
            elif (
                next_date
                and option_date in symbol_prices
                and next_date in symbol_prices
                and next_date != incomplete_us_day
            ):
                base = symbol_prices[option_date]
                next_close = symbol_prices[next_date]
                if base > 0 and next_close > 0:
                    next_return = (next_close / base - 1.0) * 100
            adjusted_return: float | None = None
            hit: str | int = ""
            if status == "ready" and next_return is not None and direction in {"CALL", "PUT"}:
                adjusted_return = next_return if direction == "CALL" else -next_return
                hit = int(adjusted_return > 0)
            output.append(
                {
                    "option_date": option_date,
                    "underlying": symbol,
                    "direction": direction,
                    "score": safe_float(signal.get("score")),
                    "total_volume": safe_int(agg.get("total_volume")),
                    "call_share": safe_float(agg.get("call_share")),
                    "put_share": safe_float(agg.get("put_share")),
                    "put_call_ratio": safe_float(agg.get("put_call_ratio")),
                    "next_trade_date": next_date,
                    "next_close_return_pct": round(next_return, 4) if next_return is not None else "",
                    "direction_adjusted_return_pct": round(adjusted_return, 4) if adjusted_return is not None else "",
                    "hit": hit,
                    "status": status,
                }
            )
    return output


def correlation_table(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ready = [row for row in rows if row.get("status") == "ready" and row.get("direction_adjusted_return_pct") != ""]
    metrics = [
        ("score", "异常分"),
        ("total_volume", "期权总成交量"),
        ("put_call_ratio", "P/C"),
        ("call_share", "Call占比"),
        ("put_share", "Put占比"),
    ]
    output = []
    ys = [safe_float(row["direction_adjusted_return_pct"]) for row in ready]
    for key, label in metrics:
        pairs = [
            (safe_float(row.get(key)), safe_float(row["direction_adjusted_return_pct"]))
            for row in ready
            if row.get(key) not in (None, "")
        ]
        xs = [pair[0] for pair in pairs]
        ys2 = [pair[1] for pair in pairs]
        output.append(
            {
                "metric": label,
                "n": len(pairs),
                "pearson": pearson(xs, ys2),
                "spearman": spearman(xs, ys2),
            }
        )
    output.append(
        {
            "metric": "整体方向调整收益",
            "n": len(ready),
            "pearson": "",
            "spearman": mean(ys),
        }
    )
    return output


def score_buckets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ready = [row for row in rows if row.get("status") == "ready" and row.get("direction_adjusted_return_pct") != ""]
    ready.sort(key=lambda row: safe_float(row.get("score")), reverse=True)
    if not ready:
        return []
    bucket_count = min(3, len(ready))
    buckets: list[dict[str, Any]] = []
    for index in range(bucket_count):
        bucket = ready[index::bucket_count]
        returns = [safe_float(row["direction_adjusted_return_pct"]) for row in bucket]
        hits = [safe_int(row["hit"]) for row in bucket if row.get("hit") != ""]
        buckets.append(
            {
                "bucket": f"score_rank_{index + 1}",
                "n": len(bucket),
                "score_min": min(safe_float(row.get("score")) for row in bucket),
                "score_max": max(safe_float(row.get("score")) for row in bucket),
                "avg_adjusted_return_pct": mean(returns),
                "hit_rate": mean(hits),
            }
        )
    return buckets


def write_markdown(
    path: Path,
    symbols: list[str],
    rows: list[dict[str, Any]],
    correlations: list[dict[str, Any]],
    buckets: list[dict[str, Any]],
    failures: dict[str, str],
    quota_before: Any,
    quota_after: Any,
) -> None:
    ready = [row for row in rows if row.get("status") == "ready"]
    pending = [row for row in rows if row.get("status") != "ready"]
    waiting_next_close = [row for row in rows if row.get("status") == "pending"]
    no_signal = [row for row in rows if row.get("status") == "no_signal"]
    hits = [safe_int(row["hit"]) for row in ready if row.get("hit") != ""]
    adjusted = [safe_float(row["direction_adjusted_return_pct"]) for row in ready if row.get("direction_adjusted_return_pct") != ""]
    lines = [
        "# 前25高成交标的：期权信号与次日涨跌幅关联性",
        "",
        f"- 标的数：{len(symbols)}",
        f"- 可计算样本：{len(ready)}",
        f"- 待补次日收盘样本：{len(waiting_next_close)}",
        f"- 无有效期权信号样本：{len(no_signal)}",
        f"- 方向命中率：{fmt_num(mean(hits) * 100 if hits else None, 1)}%",
        f"- 平均方向调整后收益：{fmt_pct(mean(adjusted))}",
        f"- 历史K额度：before {quota_summary(quota_before)} / after {quota_summary(quota_after)}",
        "",
        "## 相关性",
        "",
        "| 指标 | N | Pearson | Spearman / 均值 |",
        "|---|---:|---:|---:|",
    ]
    for row in correlations:
        lines.append(
            f"| {row['metric']} | {row['n']} | {fmt_num(row['pearson'])} | {fmt_num(row['spearman'])} |"
        )
    lines.extend(["", "## 异常分分桶", "", "| 分桶 | N | 分数区间 | 平均方向调整收益 | 命中率 |", "|---|---:|---:|---:|---:|"])
    for row in buckets:
        lines.append(
            f"| {row['bucket']} | {row['n']} | {row['score_min']:.1f}-{row['score_max']:.1f} | {fmt_pct(row['avg_adjusted_return_pct'])} | {fmt_num(row['hit_rate'] * 100 if row['hit_rate'] is not None else None, 1)}% |"
        )
    lines.extend(["", "## 明细", "", "| 日期 | 标的 | 方向 | 分数 | 量 | P/C | 次日 | 次日涨跌幅 | 方向调整收益 | 命中 | 状态 |", "|---|---|---|---:|---:|---:|---|---:|---:|---:|---|"])
    for row in rows:
        lines.append(
            "| {option_date} | {underlying} | {direction} | {score:.1f} | {total_volume:,} | {put_call_ratio:.2f} | {next_trade_date} | {next_return} | {adjusted_return} | {hit} | {status} |".format(
                **row,
                next_return=fmt_pct(row.get("next_close_return_pct")),
                adjusted_return=fmt_pct(row.get("direction_adjusted_return_pct")),
            )
        )
    if failures:
        lines.extend(["", "## 取数失败", "", "| 标的 | 原因 |", "|---|---|"])
        for symbol, reason in failures.items():
            lines.append(f"| {symbol} | {reason} |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def get_quota(ctx: Any) -> Any:
    ret, data = ctx.get_history_kl_quota(get_detail=True)
    if ret != 0:
        return str(data)
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze option signal vs next-day stock returns for top-volume symbols.")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--report-html", type=Path, default=Path("reports/options_anomaly_report.html"))
    parser.add_argument("--latest-date", default="2026-06-01")
    parser.add_argument("--signal-dates", nargs="+", default=DEFAULT_SIGNAL_DATES)
    parser.add_argument("--price-start", default=DEFAULT_PRICE_START)
    parser.add_argument("--price-end", default=DEFAULT_PRICE_END)
    parser.add_argument("--top-n", type=int, default=25)
    parser.add_argument("--output-prefix", type=Path, default=Path("reports/top25_option_return_correlation_20260601"))
    args = parser.parse_args()

    state = get_opend_state()
    market_us = str(state.get("market_us", ""))
    today_us = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    incomplete_day = today_us if is_current_us_day_incomplete(market_us) else None

    symbols = top_symbols_by_latest_volume(args.data_dir, args.report_html, args.latest_date, args.top_n)
    if len(symbols) != args.top_n:
        raise RuntimeError(f"Expected {args.top_n} symbols, got {len(symbols)}")

    configure_runtime()
    from futu import OpenQuoteContext

    quota_ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
    try:
        quota_before = get_quota(quota_ctx)
    finally:
        quota_ctx.close()

    prices, failures = fetch_daily_klines(symbols, args.price_start, args.price_end)

    quota_ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
    try:
        quota_after = get_quota(quota_ctx)
    finally:
        quota_ctx.close()

    agg_rows, signal_rows = option_date_rows(args.data_dir, args.signal_dates)
    rows = build_analysis_rows(symbols, args.signal_dates, agg_rows, signal_rows, prices, incomplete_day)
    correlations = correlation_table(rows)
    buckets = score_buckets(rows)

    detail_columns = [
        "option_date",
        "underlying",
        "direction",
        "score",
        "total_volume",
        "call_share",
        "put_share",
        "put_call_ratio",
        "next_trade_date",
        "next_close_return_pct",
        "direction_adjusted_return_pct",
        "hit",
        "status",
    ]
    write_csv(args.output_prefix.with_suffix(".csv"), rows, detail_columns)
    write_csv(
        Path(str(args.output_prefix) + "_correlations.csv"),
        correlations,
        ["metric", "n", "pearson", "spearman"],
    )
    write_csv(
        Path(str(args.output_prefix) + "_buckets.csv"),
        buckets,
        ["bucket", "n", "score_min", "score_max", "avg_adjusted_return_pct", "hit_rate"],
    )
    write_markdown(args.output_prefix.with_suffix(".md"), symbols, rows, correlations, buckets, failures, quota_before, quota_after)

    print(f"Market US: {market_us}; incomplete day ignored: {incomplete_day or 'none'}")
    print(f"Symbols: {len(symbols)}")
    print(f"Ready rows: {sum(1 for row in rows if row['status'] == 'ready')}")
    print(f"Pending rows: {sum(1 for row in rows if row['status'] == 'pending')}")
    print(f"No-signal rows: {sum(1 for row in rows if row['status'] == 'no_signal')}")
    print(f"CSV: {args.output_prefix.with_suffix('.csv')}")
    print(f"Markdown: {args.output_prefix.with_suffix('.md')}")
    if failures:
        print(f"Failures: {failures}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
