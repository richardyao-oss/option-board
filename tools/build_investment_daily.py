from __future__ import annotations

import csv
import html
import json
import math
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from runtime_env import clean_env_for_child, configure_runtime  # noqa: E402

configure_runtime()

from futu import OpenQuoteContext, OpenSecTradeContext, RET_OK, TrdEnv, TrdMarket  # noqa: E402

import dashboard_analysis  # noqa: E402


HOST = "127.0.0.1"
PORT = 11111
REPORT_PATH = ROOT / "reports" / "investment_daily_report.html"
DATA_PATH = ROOT / "reports" / "investment_daily_source.json"
DERIVATIVE_SCRIPT = (
    Path.home()
    / ".codex"
    / "skills"
    / "futu-derivatives-anomaly"
    / "scripts"
    / "handle_derivatives_anomaly.py"
)

DERIVATIVE_DIMS = [
    "option_unusual",
    "option_volatility",
    "option_volume_price",
    "option_sentiment",
    "option_comprehensive",
]

HK_OPTION_UNDERLYING = {
    "HK.MIU": "HK.01810",
    "HK.SMC": "HK.00981",
    "HK.TCH": "HK.00700",
}

COMPANY_KEYWORDS = {
    "HK.01810": "小米",
    "HK.00981": "中芯国际",
    "HK.00700": "腾讯",
    "US.QCOM": "QCOM",
    "US.NOK": "NOK",
    "US.MSTR": "MSTR",
    "US.IREN": "IREN",
    "US.HOOD": "HOOD",
    "US.GFS": "GFS",
    "US.FOTO": "FOTO",
    "US.EUV": "EUV",
}

BENCHMARKS = ["US.SPY", "US.QQQ", "US.IWM", "US..VIX"]
MAX_PRIMARY_CARDS = 10


def now_bjt() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def optional_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        if isinstance(value, float) and math.isnan(value):
            return None
        text = str(value).replace(",", "").strip()
        if not text or text.upper() in {"N/A", "NONE", "NAN"}:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def safe_float(value: Any, default: float = 0.0) -> float:
    number = optional_float(value)
    return default if number is None else number


def safe_int(value: Any) -> int:
    return int(round(safe_float(value)))


def safe_account_id(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        return int(text)
    except ValueError:
        return int(float(text))


def fmt_pct(value: Any, digits: int = 1, signed: bool = True, missing: str = "未返回") -> str:
    number = optional_float(value)
    if number is None:
        return missing
    sign = "+" if signed and number > 0 else ""
    return f"{sign}{number:.{digits}f}%"


def fmt_num(value: Any, missing: str = "未返回") -> str:
    number = optional_float(value)
    if number is None:
        return missing
    return f"{int(round(number)):,}"


def fmt_money(value: Any, currency: str = "", missing: str = "未返回") -> str:
    number = optional_float(value)
    if number is None:
        return missing
    suffix = f" {currency}" if currency else ""
    if abs(number) >= 1_000_000:
        return f"{number / 1_000_000:.2f}M{suffix}"
    if abs(number) >= 1_000:
        return f"{number / 1_000:.1f}K{suffix}"
    return f"{number:.0f}{suffix}"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def get_col(row: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return default


def df_records(value: Any) -> list[dict[str, Any]]:
    if hasattr(value, "to_dict"):
        return value.to_dict(orient="records")
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    return []


def check_opend() -> dict[str, Any]:
    ctx = OpenQuoteContext(host=HOST, port=PORT)
    try:
        ret, data = ctx.get_global_state()
    finally:
        ctx.close()
    if ret != RET_OK:
        raise RuntimeError(f"OpenD global state failed: {data}")
    state = data if isinstance(data, dict) else data.iloc[0].to_dict()
    if not state.get("qot_logined") or state.get("program_status_type") != "READY":
        raise RuntimeError(f"OpenD not ready: {state}")
    return state


def parse_us_option(code: str) -> dict[str, Any] | None:
    match = re.match(r"^(US\.[A-Z.]+?)(\d{6})([CP])(\d+)$", code)
    if not match:
        return None
    underlying, expiry_raw, option_type, strike_raw = match.groups()
    return {
        "underlying": underlying,
        "expiry": f"20{expiry_raw[:2]}-{expiry_raw[2:4]}-{expiry_raw[4:6]}",
        "option_type": option_type,
        "strike": safe_float(strike_raw) / 1000,
        "kind": "option",
    }


def parse_hk_option(code: str) -> dict[str, Any] | None:
    match = re.match(r"^(HK\.[A-Z]+)(\d{6})([CP])(\d+)$", code)
    if not match:
        return None
    option_root, expiry_raw, option_type, strike_raw = match.groups()
    return {
        "underlying": HK_OPTION_UNDERLYING.get(option_root, option_root),
        "expiry": f"20{expiry_raw[:2]}-{expiry_raw[2:4]}-{expiry_raw[4:6]}",
        "option_type": option_type,
        "strike": safe_float(strike_raw) / 1000,
        "kind": "option",
    }


def enrich_position(row: dict[str, Any]) -> dict[str, Any]:
    code = str(get_col(row, "code", "stock_code", default="")).strip()
    parsed = parse_us_option(code) or parse_hk_option(code)
    if parsed is None:
        parsed = {"underlying": code, "expiry": "", "option_type": "", "strike": None, "kind": "stock"}
    return {
        "code": code,
        "name": str(get_col(row, "stock_name", "name", default=code)),
        "qty": safe_float(get_col(row, "qty", "position_qty", "can_sell_qty", default=0)),
        "market_val": safe_float(get_col(row, "market_val", "market_value", default=0)),
        "pl_val": safe_float(get_col(row, "pl_val", "pl", default=0)),
        "pl_ratio": optional_float(get_col(row, "pl_ratio", "pl_ratio_valid", default=None)),
        "currency": str(get_col(row, "currency", "currency_type", default="")).replace("Currency.", ""),
        **parsed,
    }


def query_positions() -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    positions: list[dict[str, Any]] = []
    seen: set[tuple[str, float, float, str]] = set()
    for market in (TrdMarket.US, TrdMarket.HK):
        ctx = OpenSecTradeContext(filter_trdmarket=market, host=HOST, port=PORT)
        try:
            ret, accounts = ctx.get_acc_list()
            if ret != RET_OK:
                warnings.append(f"{market}: get_acc_list failed: {accounts}")
                continue
            acc_ids: list[int] = []
            for account in df_records(accounts):
                env_text = str(account.get("trd_env", "")).upper()
                if env_text and "REAL" not in env_text:
                    continue
                acc_id = safe_account_id(account.get("acc_id"))
                if acc_id:
                    acc_ids.append(acc_id)
            for acc_id in dict.fromkeys(acc_ids or [0]):
                ret, data = ctx.position_list_query(trd_env=TrdEnv.REAL, acc_id=acc_id, refresh_cache=True)
                if ret != RET_OK:
                    warnings.append(f"{market} acc {acc_id}: position query failed: {data}")
                    continue
                for row in df_records(data):
                    position = enrich_position(row)
                    if abs(position["qty"]) < 0.00001:
                        continue
                    key = (
                        position["code"],
                        round(position["qty"], 6),
                        round(position["market_val"], 2),
                        position["currency"],
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    positions.append(position)
        finally:
            ctx.close()
    return positions, warnings


def query_quotes(symbols: list[str]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    warnings: list[str] = []
    quotes: dict[str, dict[str, Any]] = {}
    ctx = OpenQuoteContext(host=HOST, port=PORT)
    try:
        for symbol in symbols:
            ret, data = ctx.get_market_snapshot([symbol])
            if ret != RET_OK:
                warnings.append(f"{symbol}: get_market_snapshot failed: {data}")
                continue
            rows = df_records(data)
            if not rows:
                warnings.append(f"{symbol}: empty snapshot")
                continue
            row = rows[0]
            last = optional_float(get_col(row, "last_price"))
            prev = optional_float(get_col(row, "prev_close_price"))
            change_rate = optional_float(get_col(row, "change_rate"))
            if last is not None and prev and (change_rate is None or (abs(change_rate) < 0.0001 and abs(last - prev) > 0.0001)):
                change_rate = (last / prev - 1) * 100
            quotes[symbol] = {
                "code": symbol,
                "name": str(get_col(row, "stock_name", "name", default=symbol)),
                "last_price": last,
                "prev_close_price": prev,
                "change_rate": change_rate,
                "volume": optional_float(get_col(row, "volume")),
                "turnover": optional_float(get_col(row, "turnover")),
                "update_time": str(get_col(row, "update_time", default="")),
            }
    finally:
        ctx.close()
    return quotes, warnings


def parse_json_from_mixed_output(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    starts = [text.find('{\n  "method"'), text.find('{"method"'), text.find("{")]
    for start in starts:
        if start < 0:
            continue
        try:
            value, _end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise json.JSONDecodeError("no JSON object found", text, 0)


def query_derivative_anomalies(symbols: list[str]) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    if not DERIVATIVE_SCRIPT.exists():
        return {symbol: {"error": "derivative skill script missing"} for symbol in symbols}
    for symbol in symbols:
        command = [
            sys.executable,
            str(DERIVATIVE_SCRIPT),
            symbol,
            "--time-range",
            "7",
            "--analysis-dimensions",
            *DERIVATIVE_DIMS,
            "--language-id",
            "0",
            "--json",
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=str(ROOT),
                env=clean_env_for_child(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=25,
            )
        except subprocess.TimeoutExpired:
            results[symbol] = {"error": "timeout"}
            continue
        if completed.returncode != 0:
            results[symbol] = {"error": (completed.stderr or completed.stdout).strip()[-500:]}
            continue
        try:
            results[symbol] = parse_json_from_mixed_output(completed.stdout)
        except json.JSONDecodeError:
            results[symbol] = {"error": "invalid json", "raw": completed.stdout[-500:]}
        time.sleep(0.15)
    return results


def fetch_json(endpoint: str, params: dict[str, Any], user_agent: str) -> dict[str, Any]:
    url = f"https://ai-news-search.futunn.com/{endpoint}?{urlencode(params)}"
    request = Request(url, headers={"User-Agent": user_agent})
    with urlopen(request, timeout=8) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def normalize_time(value: Any) -> str:
    try:
        ts = float(value)
        if ts > 1e12:
            ts /= 1000
        return datetime.fromtimestamp(ts, timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return str(value or "")


def clean_text(value: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def query_news(symbols: list[str]) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        keyword = COMPANY_KEYWORDS.get(symbol, symbol.split(".")[-1])
        try:
            payload = fetch_json(
                "news_search",
                {"keyword": keyword, "size": 8, "news_type": 1, "lang": "zh-CN", "sort_type": 2},
                "futunn-news-search/0.0.2 (Skill)",
            )
            items = payload.get("data") if payload.get("code") == 0 else []
            normalized = [
                {
                    "title": clean_text(item.get("title")),
                    "publish_time": normalize_time(item.get("publish_time")),
                    "url": str(item.get("url") or ""),
                }
                for item in items or []
            ]
            results[symbol] = {"items": normalized, "error": "" if payload.get("code") == 0 else payload.get("message", "")}
        except Exception as exc:  # noqa: BLE001
            results[symbol] = {"items": [], "error": f"{type(exc).__name__}: {exc}"}
        time.sleep(0.1)
    return results


BULL_CUES = ["涨", "拉升", "突破", "看多", "买入", "利好", "强势", "beat", "bull", "buy", "long", "breakout", "upside", "surge"]
BEAR_CUES = ["跌", "下跌", "回落", "看空", "卖出", "利空", "风险", "bear", "sell", "short", "downside", "miss", "dump"]


def classify_sentiment(text: str) -> str:
    lowered = text.lower()
    bull = sum(1 for cue in BULL_CUES if cue.lower() in lowered)
    bear = sum(1 for cue in BEAR_CUES if cue.lower() in lowered)
    if bull > bear:
        return "bullish"
    if bear > bull:
        return "bearish"
    return "neutral"


def query_sentiment(symbols: list[str]) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        keyword = COMPANY_KEYWORDS.get(symbol, symbol.split(".")[-1])
        try:
            payload = fetch_json("stock_feed", {"keyword": keyword, "size": 30}, "futunn-comment-sentiment/0.0.2 (Skill)")
            items = payload.get("data") if payload.get("code") == 0 else []
        except Exception as exc:  # noqa: BLE001
            results[symbol] = {"post_count": 0, "bullish": 0, "bearish": 0, "neutral": 0, "views": [], "error": f"{type(exc).__name__}: {exc}"}
            continue

        counts = {"bullish": 0, "bearish": 0, "neutral": 0}
        views: list[dict[str, str]] = []
        for item in items or []:
            text = clean_text(f"{item.get('title', '')} {item.get('desc', '')}")
            if len(text) < 8:
                continue
            label = classify_sentiment(text)
            counts[label] += 1
            if len(views) < 3 and label != "neutral":
                views.append(
                    {
                        "label": label,
                        "text": text[:160],
                        "time": normalize_time(item.get("publish_time")),
                        "url": str(item.get("url") or ""),
                    }
                )
        total = sum(counts.values())
        results[symbol] = {
            "post_count": total,
            "bullish": round(counts["bullish"] / total * 100, 1) if total else 0,
            "bearish": round(counts["bearish"] / total * 100, 1) if total else 0,
            "neutral": round(counts["neutral"] / total * 100, 1) if total else 0,
            "views": views,
            "error": "",
        }
        time.sleep(0.1)
    return results


def load_local_dashboard(symbols: list[str]) -> tuple[dict[str, dict[str, Any]], str]:
    rows = dashboard_analysis.build_analysis()
    by_symbol = {str(row.get("underlying")): row for row in rows}
    latest_date = rows[0]["snapshot_date"] if rows else ""
    return {symbol: by_symbol.get(symbol, {}) for symbol in symbols}, latest_date


def days_to_expiry(expiry: str) -> int | None:
    if not expiry:
        return None
    try:
        return (datetime.strptime(expiry, "%Y-%m-%d").date() - now_bjt().date()).days
    except ValueError:
        return None


def moneyness(position: dict[str, Any], quote: dict[str, Any]) -> float | None:
    if position.get("kind") != "option":
        return None
    strike = optional_float(position.get("strike"))
    price = optional_float(quote.get("last_price"))
    if not strike or not price:
        return None
    if position.get("option_type") == "P":
        return strike / price - 1
    return price / strike - 1


def aggregate_by_underlying(positions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for position in positions:
        symbol = position["underlying"]
        item = grouped.setdefault(
            symbol,
            {
                "underlying": symbol,
                "positions": [],
                "market_val_by_currency": defaultdict(float),
                "pl_by_currency": defaultdict(float),
            },
        )
        item["positions"].append(position)
        item["market_val_by_currency"][position.get("currency", "")] += safe_float(position.get("market_val"))
        item["pl_by_currency"][position.get("currency", "")] += safe_float(position.get("pl_val"))
    for item in grouped.values():
        item["market_val_by_currency"] = dict(item["market_val_by_currency"])
        item["pl_by_currency"] = dict(item["pl_by_currency"])
    return grouped


def exposure_text(group: dict[str, Any]) -> str:
    parts = [fmt_money(value, currency) for currency, value in group.get("market_val_by_currency", {}).items()]
    return " / ".join(parts) if parts else "未返回"


def position_direction(group: dict[str, Any]) -> str:
    directions: set[str] = set()
    for position in group.get("positions", []):
        if position.get("kind") == "stock":
            directions.add("CALL")
        elif position.get("option_type") in {"C", "P"}:
            directions.add("CALL" if position["option_type"] == "C" else "PUT")
    if len(directions) == 1:
        return next(iter(directions))
    if len(directions) > 1:
        return "MIXED"
    return "NONE"


def exposure_priority(group: dict[str, Any]) -> float:
    score = 0.0
    for currency, value in group.get("market_val_by_currency", {}).items():
        value = abs(safe_float(value))
        if currency == "HKD":
            if value >= 300_000:
                score += 2.0
            elif value >= 60_000:
                score += 1.0
        else:
            if value >= 50_000:
                score += 2.0
            elif value >= 10_000:
                score += 1.0
    return score


UNUSUAL_TRADE_RE = re.compile(
    r"(?P<time>\d{1,2}\.\d{1,2}\s+\d{2}:\d{2})，出现一笔(?P<side>买入|卖出|中性)"
    r"(?P<type>看涨|看跌)期权交易，成交量为(?P<volume>[\d,]+)张，未平仓数为(?P<oi>[\d,]+)张，"
    r"V/OI值为(?P<voi>[\d.]+)，交易金额为(?P<turnover>[\d,]+)(?P<currency>[A-Z]+)，"
    r"合约行权价是(?P<strike>[\d.]+)，到期日为(?P<expiry>\d{4}/\d{2}/\d{2})"
)


def extract_derivative_facts(raw: dict[str, Any], limit: int = 3) -> list[dict[str, Any]]:
    content = str((raw.get("data") or {}).get("content") or raw.get("content") or "")
    facts: list[dict[str, Any]] = []
    for match in UNUSUAL_TRADE_RE.finditer(content):
        item = match.groupdict()
        if item["side"] == "中性":
            continue
        item["volume_num"] = safe_int(item["volume"])
        item["turnover_num"] = safe_float(item["turnover"])
        facts.append(item)
    facts.sort(key=lambda row: (row["turnover_num"], row["volume_num"]), reverse=True)
    return facts[:limit]


IMPORTANT_NEWS_WORDS = [
    "财报",
    "业绩",
    "营收",
    "盈利",
    "亏损",
    "指引",
    "评级",
    "目标价",
    "上调",
    "下调",
    "监管",
    "调查",
    "诉讼",
    "禁令",
    "制裁",
    "订单",
    "合作",
    "收购",
    "并购",
    "融资",
    "发行",
    "芯片",
    "AI",
    "比特币",
    "earnings",
    "guidance",
    "revenue",
    "upgrade",
    "downgrade",
    "target",
    "lawsuit",
    "crypto",
    "bitcoin",
]


def parse_news_time(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=timezone(timedelta(hours=8)))
    except ValueError:
        return None


def filter_relevant_news(items: list[dict[str, Any]], generated_at: datetime, limit: int = 2) -> list[dict[str, Any]]:
    relevant: list[dict[str, Any]] = []
    for item in items:
        title = clean_text(item.get("title"))
        if not title:
            continue
        strong = any(word.lower() in title.lower() for word in IMPORTANT_NEWS_WORDS)
        published = parse_news_time(str(item.get("publish_time") or ""))
        recent = published is not None and generated_at - published <= timedelta(hours=72)
        if strong and (recent or published is None):
            relevant.append(item)
        if len(relevant) >= limit:
            break
    return relevant


def quote_class(value: Any) -> str:
    number = optional_float(value)
    if number is None:
        return "missing"
    if number > 0:
        return "up"
    if number < 0:
        return "down"
    return "flat"


def format_quote_value(quote: dict[str, Any]) -> str:
    price = optional_float(quote.get("last_price"))
    if price is None:
        return "未返回"
    return f"{price:.2f}"


def build_position_facts(group: dict[str, Any], quote: dict[str, Any]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for position in group.get("positions", []):
        if position.get("kind") == "option":
            dte = days_to_expiry(str(position.get("expiry") or ""))
            mny = moneyness(position, quote)
            if mny is None:
                mny_text = "价内/价外未返回"
            else:
                mny_text = f"{'价内' if mny >= 0 else '价外'} {abs(mny) * 100:.0f}%"
            facts.append(
                {
                    "contract": f"{position.get('option_type')} {safe_float(position.get('strike')):g}",
                    "code": position["code"],
                    "expiry": position.get("expiry") or "未返回",
                    "dte": dte,
                    "qty": position.get("qty"),
                    "moneyness": mny,
                    "moneyness_text": mny_text,
                    "market_val": position.get("market_val"),
                    "currency": position.get("currency", ""),
                }
            )
        else:
            facts.append(
                {
                    "contract": "正股",
                    "code": position["code"],
                    "expiry": "-",
                    "dte": None,
                    "qty": position.get("qty"),
                    "moneyness": None,
                    "moneyness_text": "-",
                    "market_val": position.get("market_val"),
                    "currency": position.get("currency", ""),
                }
            )
    return sorted(facts, key=lambda row: (9999 if row["dte"] is None else row["dte"], -abs(safe_float(row["market_val"]))))


def option_signal_fact(signal: dict[str, Any], holding_direction: str) -> dict[str, Any]:
    direction = str(signal.get("direction") or "NONE")
    if not signal:
        relation = "本地看板无该标的信号"
    elif holding_direction in {"CALL", "PUT"} and direction in {"CALL", "PUT"}:
        relation = "同向" if holding_direction == direction else "冲突"
    elif direction in {"CALL", "PUT"}:
        relation = "方向混合，需人工核对"
    else:
        relation = "无明确方向"
    return {
        "direction": direction,
        "relation": relation,
        "score": optional_float(signal.get("score")),
        "strength": signal.get("strength") or "none",
        "total_volume": optional_float(signal.get("total_volume")),
        "put_call_ratio": optional_float(signal.get("put_call_ratio")),
        "volume_change_pct": None if signal.get("volume_change_pct") is None else safe_float(signal.get("volume_change_pct")) * 100,
        "matched_unusual_count": safe_int(signal.get("matched_unusual_count")),
        "is_concentrated": bool(signal.get("is_concentrated")),
    }


def card_priority_score(
    group: dict[str, Any],
    quote: dict[str, Any],
    positions: list[dict[str, Any]],
    signal: dict[str, Any],
    derivative_facts: list[dict[str, Any]],
    relevant_news: list[dict[str, Any]],
    sentiment: dict[str, Any],
) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = exposure_priority(group)
    if score:
        reasons.append(f"持仓敞口 {exposure_text(group)}")

    for position in positions:
        dte = position["dte"]
        if dte is not None and dte <= 7:
            score += 3.0
            reasons.append(f"{position['code']} 距到期 {dte} 天")
        elif dte is not None and dte <= 30:
            score += 1.5
            reasons.append(f"{position['code']} 距到期 {dte} 天")
        mny = position["moneyness"]
        if mny is not None and mny <= -0.3:
            score += 1.5
            reasons.append(f"{position['code']} {position['moneyness_text']}")
        elif mny is not None and mny <= -0.15:
            score += 1.0
            reasons.append(f"{position['code']} {position['moneyness_text']}")

    signal_fact = option_signal_fact(signal, position_direction(group))
    if signal_fact["relation"] == "冲突":
        score += 3.0
        reasons.append(f"期权看板方向 {signal_fact['direction']} 与持仓方向冲突")
    elif signal_fact["relation"] == "同向" and signal_fact["strength"] in {"strong", "medium"}:
        score += 1.2
        reasons.append(f"期权看板方向 {signal_fact['direction']} 与持仓同向")

    if abs(safe_float(quote.get("change_rate"))) >= 4:
        score += 1.5
        reasons.append(f"正股涨跌幅 {fmt_pct(quote.get('change_rate'))}")
    if derivative_facts:
        score += 1.0
        top = derivative_facts[0]
        reasons.append(f"衍生品异动最大金额 {fmt_money(top['turnover_num'], top['currency'])}")
    if relevant_news:
        score += 1.0
        reasons.append("有强相关新闻")

    bull = safe_float(sentiment.get("bullish"))
    bear = safe_float(sentiment.get("bearish"))
    if safe_int(sentiment.get("post_count")) >= 10 and abs(bull - bear) >= 35:
        score += 0.6
        reasons.append(f"社区情绪偏向：看多 {bull:.0f}% / 看空 {bear:.0f}%")

    if not reasons:
        reasons.append("持仓例行核对")
    return score, list(dict.fromkeys(reasons))[:4]


def build_symbol_cards(
    groups: dict[str, dict[str, Any]],
    quotes: dict[str, dict[str, Any]],
    dashboard: dict[str, dict[str, Any]],
    derivatives: dict[str, dict[str, Any]],
    news: dict[str, dict[str, Any]],
    sentiment: dict[str, dict[str, Any]],
    generated_at: datetime,
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for symbol, group in groups.items():
        quote = quotes.get(symbol, {})
        position_facts = build_position_facts(group, quote)
        signal = dashboard.get(symbol, {})
        derivative_facts = extract_derivative_facts(derivatives.get(symbol, {}))
        relevant_news = filter_relevant_news(news.get(symbol, {}).get("items", []), generated_at)
        score, reasons = card_priority_score(
            group,
            quote,
            position_facts,
            signal,
            derivative_facts,
            relevant_news,
            sentiment.get(symbol, {}),
        )
        level = "高" if score >= 5 else "中" if score >= 2.5 else "低"
        cards.append(
            {
                "symbol": symbol,
                "name": quote.get("name") or symbol,
                "priority_score": round(score, 2),
                "attention_level": level,
                "quote": quote,
                "exposure": exposure_text(group),
                "position_facts": position_facts,
                "signal": option_signal_fact(signal, position_direction(group)),
                "derivative_facts": derivative_facts,
                "news": relevant_news,
                "sentiment": sentiment.get(symbol, {}),
                "reasons": reasons,
                "summary": "；".join(reasons[:2]),
            }
        )
    return sorted(cards, key=lambda item: (-item["priority_score"], item["symbol"]))


def h(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def render_badge(text: str, cls: str = "") -> str:
    return f'<span class="badge {cls}">{h(text)}</span>'


def render_metric(label: str, value: str, cls: str = "") -> str:
    return f'<div class="metric"><span>{h(label)}</span><b class="{cls}">{value}</b></div>'


def render_position_rows(rows: list[dict[str, Any]]) -> str:
    html_rows: list[str] = []
    for row in rows:
        dte_text = "未返回" if row["dte"] is None else f"{row['dte']}天"
        html_rows.append(
            f"""
            <tr>
              <td><b>{h(row['contract'])}</b><small>{h(row['code'])}</small></td>
              <td>{h(row['expiry'])}</td>
              <td>{h(dte_text)}</td>
              <td>{fmt_num(row['qty'])}</td>
              <td>{h(row['moneyness_text'])}</td>
              <td>{h(fmt_money(row['market_val'], row['currency']))}</td>
            </tr>
            """
        )
    return "".join(html_rows)


def render_derivative_rows(facts: list[dict[str, Any]]) -> str:
    if not facts:
        return '<div class="muted-block">无强相关衍生品异动</div>'
    rows = []
    for fact in facts:
        opt_type = "C" if fact["type"] == "看涨" else "P"
        rows.append(
            f"""
            <tr>
              <td>{h(fact['time'])}</td>
              <td>{h(fact['side'])}{h(fact['type'])}</td>
              <td>{opt_type} {h(fact['strike'])}</td>
              <td>{h(fact['expiry'].replace('/', '-'))}</td>
              <td>{fmt_num(fact['volume_num'])}</td>
              <td>{h(fmt_money(fact['turnover_num'], fact['currency']))}</td>
            </tr>
            """
        )
    return f"""
      <table class="compact-table">
        <thead><tr><th>时间</th><th>方向</th><th>型/行权</th><th>到期</th><th>量</th><th>额</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    """


def render_news(items: list[dict[str, Any]], sentiment: dict[str, Any]) -> str:
    blocks: list[str] = []
    if items:
        blocks.append("<ul class=\"news-list\">")
        for item in items:
            blocks.append(
                f'<li><a href="{h(item.get("url"))}">{h(item.get("title"))}</a><small>{h(item.get("publish_time"))}</small></li>'
            )
        blocks.append("</ul>")
    bull = safe_float(sentiment.get("bullish"))
    bear = safe_float(sentiment.get("bearish"))
    post_count = safe_int(sentiment.get("post_count"))
    if post_count >= 10 and abs(bull - bear) >= 35:
        blocks.append(f'<div class="sentiment-line">社区讨论：看多 {bull:.0f}% / 看空 {bear:.0f}% / 样本 {post_count} 条</div>')
    if not blocks:
        return '<div class="muted-block">无强相关新闻或社区偏向</div>'
    return "".join(blocks)


def render_symbol_card(card: dict[str, Any]) -> str:
    quote = card["quote"]
    signal = card["signal"]
    change_cls = quote_class(quote.get("change_rate"))
    badges = [
        render_badge(f"关注{card['attention_level']}", f"level-{card['attention_level']}"),
        render_badge(signal["relation"]),
    ]
    if signal["strength"] in {"strong", "medium"}:
        badges.append(render_badge(f"期权{signal['strength']}"))
    reasons = "".join(f"<li>{h(reason)}</li>" for reason in card["reasons"])
    signal_cells = "".join(
        [
            render_metric("方向", h(signal["direction"])),
            render_metric("异常分", "未返回" if signal["score"] is None else f"{signal['score']:.1f}"),
            render_metric("P/C", "未返回" if signal["put_call_ratio"] is None else f"{signal['put_call_ratio']:.2f}"),
            render_metric("成交变化", fmt_pct(signal["volume_change_pct"], 0)),
            render_metric("匹配异动", str(signal["matched_unusual_count"])),
            render_metric("集中大单", "是" if signal["is_concentrated"] else "否"),
        ]
    )
    return f"""
    <article class="symbol-card">
      <div class="symbol-top">
        <div>
          <h2>{h(card['symbol'])}</h2>
          <div class="company">{h(card['name'])}</div>
        </div>
        <div class="top-metrics">
          {render_metric("当前价", h(format_quote_value(quote)))}
          {render_metric("涨跌幅", h(fmt_pct(quote.get('change_rate'))), change_cls)}
          {render_metric("敞口", h(card['exposure']))}
          {render_metric("关注分", f"{card['priority_score']:.1f}")}
        </div>
      </div>
      <div class="badges">{''.join(badges)}</div>
      <div class="reason-box">
        <b>为什么今天上榜</b>
        <ul>{reasons}</ul>
      </div>
      <div class="card-grid">
        <section class="card-section span-2">
          <h3>持仓事实</h3>
          <table class="compact-table">
            <thead><tr><th>持仓</th><th>到期</th><th>DTE</th><th>数量</th><th>价内/价外</th><th>市值</th></tr></thead>
            <tbody>{render_position_rows(card['position_facts'])}</tbody>
          </table>
        </section>
        <section class="card-section">
          <h3>期权看板事实</h3>
          <div class="signal-grid">{signal_cells}</div>
        </section>
        <section class="card-section span-2">
          <h3>异动事实</h3>
          {render_derivative_rows(card['derivative_facts'])}
        </section>
        <section class="card-section">
          <h3>新闻/社区</h3>
          {render_news(card['news'], card['sentiment'])}
        </section>
      </div>
      <div class="summary-line">事实小结：{h(card['summary'])}</div>
    </article>
    """


def render_other_holdings(cards: list[dict[str, Any]]) -> str:
    if not cards:
        return ""
    rows = []
    for card in cards:
        rows.append(
            f"""
            <tr>
              <td><b>{h(card['symbol'])}</b><small>{h(card['name'])}</small></td>
              <td>{h(card['attention_level'])}</td>
              <td>{card['priority_score']:.1f}</td>
              <td>{h(card['exposure'])}</td>
              <td>{h(card['summary'])}</td>
            </tr>
            """
        )
    return f"""
    <section class="appendix">
      <div class="section-head"><h2>其他持仓</h2><span>未进入前 {MAX_PRIMARY_CARDS}，保留核对</span></div>
      <div class="table-shell">
        <table class="compact-table">
          <thead><tr><th>标的</th><th>关注</th><th>分</th><th>敞口</th><th>事实摘要</th></tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
      </div>
    </section>
    """


def render_html(payload: dict[str, Any]) -> str:
    generated_at = payload["generated_at_bjt"]
    state = payload["opend_state"]
    cards = payload["symbol_cards"]
    primary_cards = cards[:MAX_PRIMARY_CARDS]
    other_cards = cards[MAX_PRIMARY_CARDS:]
    warnings = payload.get("warnings", [])
    available_benchmarks = [payload["quotes"][symbol] for symbol in BENCHMARKS if symbol in payload["quotes"]]
    unavailable = [symbol for symbol in BENCHMARKS if symbol not in payload["quotes"]]

    benchmark_html = "".join(
        f"""
        <div class="bench">
          <span>{h(item.get('code'))}</span>
          <b>{h(format_quote_value(item))}</b>
          <small class="{quote_class(item.get('change_rate'))}">{h(fmt_pct(item.get('change_rate')))}</small>
        </div>
        """
        for item in available_benchmarks
    )
    if not benchmark_html:
        benchmark_html = '<div class="muted-block">市场快照未返回</div>'

    warning_lines = "".join(f"<li>{h(warning)}</li>" for warning in warnings)
    unavailable_line = f"<li>不可用基准：{h(', '.join(unavailable))}</li>" if unavailable else ""

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>投资日报 - 持仓风控</title>
  <style>
    :root {{
      --bg: #edf3f8;
      --panel: #ffffff;
      --ink: #06152b;
      --muted: #637189;
      --line: #d9e3ef;
      --soft: #f5f8fc;
      --blue: #112e5e;
      --red: #d63b4c;
      --green: #198a55;
      --amber: #b7791f;
      --shadow: 0 18px 42px rgba(22, 43, 72, .08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
      line-height: 1.45;
    }}
    .page {{ max-width: 1540px; margin: 0 auto; padding: 24px 28px 40px; }}
    header, .symbol-card, .appendix, .data-note {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: var(--shadow);
    }}
    header {{ padding: 24px 28px; }}
    .topline {{ display: flex; justify-content: space-between; gap: 24px; align-items: flex-start; }}
    h1 {{ margin: 0; font-size: 34px; color: var(--blue); letter-spacing: 0; }}
    .sub, .company, .section-head span, small, .muted-block {{ color: var(--muted); }}
    .source {{ text-align: right; color: var(--muted); font-size: 14px; }}
    .benchmarks {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 20px; }}
    .bench, .metric {{
      background: var(--soft);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 10px 12px;
    }}
    .bench span, .metric span {{ display: block; color: var(--muted); font-size: 12px; }}
    .bench b, .metric b {{ display: block; margin-top: 3px; font-size: 17px; }}
    .symbol-stack {{ display: grid; gap: 18px; margin-top: 22px; }}
    .symbol-card {{ padding: 22px 24px; }}
    .symbol-top {{ display: flex; justify-content: space-between; gap: 20px; align-items: flex-start; }}
    h2 {{ margin: 0; font-size: 30px; color: var(--blue); }}
    h3 {{ margin: 0 0 10px; font-size: 16px; color: var(--blue); }}
    .top-metrics {{ display: grid; grid-template-columns: repeat(4, minmax(120px, 1fr)); gap: 10px; min-width: 620px; }}
    .badges {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 14px 0; }}
    .badge {{ border: 1px solid var(--line); background: var(--soft); border-radius: 999px; padding: 4px 10px; font-size: 12px; font-weight: 800; color: var(--blue); }}
    .level-高 {{ color: var(--red); }}
    .level-中 {{ color: var(--amber); }}
    .level-低 {{ color: var(--green); }}
    .reason-box {{ background: #fffaf2; border: 1px solid #ead9bd; border-radius: 14px; padding: 12px 14px; }}
    .reason-box ul {{ margin: 6px 0 0; padding-left: 18px; }}
    .card-grid {{ display: grid; grid-template-columns: 1.35fr .8fr; gap: 14px; margin-top: 14px; }}
    .card-section {{ border: 1px solid var(--line); border-radius: 14px; padding: 14px; background: #fff; }}
    .span-2 {{ grid-column: span 1; }}
    .signal-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th {{ text-align: left; background: var(--soft); color: var(--muted); font-weight: 800; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 8px 10px; vertical-align: top; }}
    tr:last-child td {{ border-bottom: 0; }}
    td small {{ display: block; margin-top: 2px; }}
    .summary-line {{ color: var(--muted); margin-top: 12px; font-size: 14px; }}
    .news-list {{ margin: 0; padding-left: 18px; }}
    .news-list li {{ margin-bottom: 8px; }}
    a {{ color: var(--blue); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .sentiment-line, .muted-block {{ border: 1px dashed var(--line); border-radius: 12px; padding: 10px 12px; background: var(--soft); }}
    .appendix, .data-note {{ margin-top: 20px; padding: 18px; }}
    .section-head {{ display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 12px; }}
    .section-head h2 {{ font-size: 22px; }}
    .up {{ color: var(--red); }}
    .down {{ color: var(--green); }}
    .missing {{ color: var(--muted); }}
    .flat {{ color: var(--ink); }}
    @media (max-width: 1080px) {{
      .topline, .symbol-top {{ flex-direction: column; }}
      .source {{ text-align: left; }}
      .top-metrics, .benchmarks, .card-grid {{ grid-template-columns: 1fr; min-width: 0; width: 100%; }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <header>
      <div class="topline">
        <div>
          <h1>投资日报</h1>
          <div class="sub">按标的组织的持仓风控视图。只展示经过筛选的事实，不给交易动作建议。</div>
        </div>
        <div class="source">
          <div>生成时间：{h(generated_at)} BJT</div>
          <div>OpenD：{h(state.get('program_status_type'))} / 美股状态 {h(state.get('market_us'))}</div>
          <div>期权看板日期：{h(payload.get('dashboard_date'))}</div>
        </div>
      </div>
      <div class="benchmarks">{benchmark_html}</div>
    </header>

    <section class="symbol-stack">
      {''.join(render_symbol_card(card) for card in primary_cards)}
    </section>

    {render_other_holdings(other_cards)}

    <section class="data-note">
      <div class="section-head"><h2>数据状态</h2><span>用于核对，不作为结论</span></div>
      <ul>
        <li>正文展示 {len(primary_cards)} 个重点标的；完整持仓标的 {len(cards)} 个。</li>
        <li>新闻和社区只在强相关时进入对应标的卡。</li>
        <li>未调用历史 K 线。</li>
        {unavailable_line}
        {warning_lines}
      </ul>
    </section>
  </main>
</body>
</html>"""


def main() -> int:
    print("[1/7] Checking OpenD...")
    state = check_opend()

    print("[2/7] Reading positions...")
    positions, position_warnings = query_positions()
    if not positions:
        raise RuntimeError("No real positions returned from OpenD.")
    groups = aggregate_by_underlying(positions)
    symbols = sorted(groups)

    print(f"[3/7] Reading quotes for {len(symbols)} holdings and benchmarks...")
    quotes, quote_warnings = query_quotes(list(dict.fromkeys(symbols + BENCHMARKS)))

    print("[4/7] Reading local option dashboard signals...")
    dashboard, dashboard_date = load_local_dashboard(symbols)

    print("[5/7] Querying derivatives anomaly skill...")
    derivatives = query_derivative_anomalies(symbols)

    print("[6/7] Querying Futu news and community sentiment...")
    news = query_news(symbols)
    sentiment = query_sentiment(symbols)

    print("[7/7] Building symbol cards and rendering HTML...")
    generated_dt = now_bjt()
    symbol_cards = build_symbol_cards(groups, quotes, dashboard, derivatives, news, sentiment, generated_dt)
    payload = {
        "generated_at_bjt": generated_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "opend_state": state,
        "positions": positions,
        "groups": groups,
        "quotes": quotes,
        "dashboard": dashboard,
        "dashboard_date": dashboard_date,
        "derivatives": derivatives,
        "news": news,
        "sentiment": sentiment,
        "symbol_cards": symbol_cards,
        "primary_symbols": [card["symbol"] for card in symbol_cards[:MAX_PRIMARY_CARDS]],
        "other_symbols": [card["symbol"] for card in symbol_cards[MAX_PRIMARY_CARDS:]],
        "warnings": position_warnings + quote_warnings,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    REPORT_PATH.write_text(render_html(payload), encoding="utf-8")

    print(f"Report written: {REPORT_PATH}")
    print(f"Source written: {DATA_PATH}")
    if payload["warnings"]:
        print("Warnings:")
        for warning in payload["warnings"]:
            print(f" - {warning}")
    print("Top symbols:")
    for card in symbol_cards[:MAX_PRIMARY_CARDS]:
        print(f" - {card['symbol']}: {card['priority_score']:.1f} {card['attention_level']} | {card['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
