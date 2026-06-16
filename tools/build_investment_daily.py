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

from futu import (  # noqa: E402
    OpenQuoteContext,
    OpenSecTradeContext,
    RET_OK,
    TrdEnv,
    TrdMarket,
)

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


def now_bjt() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, float) and math.isnan(value):
            return default
        text = str(value).replace(",", "").strip()
        if not text or text.upper() in {"N/A", "NONE", "NAN"}:
            return default
        return float(text)
    except (TypeError, ValueError):
        return default


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


def pct(value: Any, digits: int = 1, signed: bool = True) -> str:
    number = safe_float(value)
    sign = "+" if signed and number > 0 else ""
    return f"{sign}{number:.{digits}f}%"


def money(value: Any, currency: str = "") -> str:
    number = safe_float(value)
    suffix = f" {currency}" if currency else ""
    if abs(number) >= 1_000_000:
        return f"{number / 1_000_000:.2f}M{suffix}"
    if abs(number) >= 1_000:
        return f"{number / 1_000:.1f}K{suffix}"
    return f"{number:.0f}{suffix}"


def fmt_num(value: Any) -> str:
    return f"{safe_int(value):,}"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


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


def df_records(value: Any) -> list[dict[str, Any]]:
    if hasattr(value, "to_dict"):
        return value.to_dict(orient="records")
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    return []


def get_col(row: dict[str, Any], *names: str, default: Any = "") -> Any:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return default


def parse_us_option(code: str) -> dict[str, Any] | None:
    match = re.match(r"^(US\.[A-Z.]+?)(\d{6})([CP])(\d+)$", code)
    if not match:
        return None
    underlying, expiry_raw, option_type, strike_raw = match.groups()
    expiry = f"20{expiry_raw[:2]}-{expiry_raw[2:4]}-{expiry_raw[4:6]}"
    strike = safe_float(strike_raw) / 1000
    return {
        "underlying": underlying,
        "expiry": expiry,
        "option_type": option_type,
        "strike": strike,
        "kind": "option",
    }


def parse_hk_option(code: str) -> dict[str, Any] | None:
    match = re.match(r"^(HK\.[A-Z]+)(\d{6})([CP])(\d+)$", code)
    if not match:
        return None
    option_root, expiry_raw, option_type, strike_raw = match.groups()
    underlying = HK_OPTION_UNDERLYING.get(option_root, option_root)
    expiry = f"20{expiry_raw[:2]}-{expiry_raw[2:4]}-{expiry_raw[4:6]}"
    strike = safe_float(strike_raw) / 1000
    return {
        "underlying": underlying,
        "expiry": expiry,
        "option_type": option_type,
        "strike": strike,
        "kind": "option",
    }


def enrich_position(row: dict[str, Any]) -> dict[str, Any]:
    code = str(get_col(row, "code", "stock_code")).strip()
    parsed = parse_us_option(code) or parse_hk_option(code)
    if parsed is None:
        parsed = {"underlying": code, "expiry": "", "option_type": "", "strike": 0.0, "kind": "stock"}
    qty = safe_float(get_col(row, "qty", "position_qty", "can_sell_qty", default=0))
    market_val = safe_float(get_col(row, "market_val", "market_value", "nominal_price", default=0))
    pl_val = safe_float(get_col(row, "pl_val", "pl", "pl_val_valid", default=0))
    pl_ratio = safe_float(get_col(row, "pl_ratio", "pl_ratio_valid", default=0))
    currency = str(get_col(row, "currency", "currency_type", default="")).replace("Currency.", "")
    enriched = {
        "code": code,
        "name": str(get_col(row, "stock_name", "name", default=code)),
        "qty": qty,
        "market_val": market_val,
        "pl_val": pl_val,
        "pl_ratio": pl_ratio,
        "currency": currency,
        **parsed,
    }
    return enriched


def query_positions() -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    raw_positions: list[dict[str, Any]] = []
    seen: set[tuple[str, float, float, str]] = set()
    for market in (TrdMarket.US, TrdMarket.HK):
        ctx = OpenSecTradeContext(filter_trdmarket=market, host=HOST, port=PORT)
        try:
            ret, accounts = ctx.get_acc_list()
            account_rows = df_records(accounts) if ret == RET_OK else []
            if ret != RET_OK:
                warnings.append(f"{market}: get_acc_list failed: {accounts}")
            acc_ids: list[int] = []
            for account in account_rows:
                env_text = str(account.get("trd_env", "")).upper()
                if env_text and "REAL" not in env_text:
                    continue
                acc_id = safe_account_id(account.get("acc_id"))
                if acc_id:
                    acc_ids.append(acc_id)
            if not acc_ids:
                acc_ids = [0]
            for acc_id in dict.fromkeys(acc_ids):
                ret, positions = ctx.position_list_query(
                    trd_env=TrdEnv.REAL,
                    acc_id=acc_id,
                    refresh_cache=True,
                )
                if ret != RET_OK:
                    warnings.append(f"{market} acc {acc_id}: position query failed: {positions}")
                    continue
                for row in df_records(positions):
                    enriched = enrich_position(row)
                    if abs(enriched["qty"]) < 0.00001:
                        continue
                    key = (
                        enriched["code"],
                        round(enriched["qty"], 6),
                        round(enriched["market_val"], 2),
                        enriched["currency"],
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    raw_positions.append(enriched)
        finally:
            ctx.close()
    return raw_positions, warnings


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
            records = df_records(data)
            if not records:
                warnings.append(f"{symbol}: empty snapshot")
                continue
            row = records[0]
            last = safe_float(get_col(row, "last_price", default=0))
            prev = safe_float(get_col(row, "prev_close_price", default=0))
            change_rate = safe_float(get_col(row, "change_rate", default=0))
            quotes[symbol] = {
                "code": symbol,
                "name": str(get_col(row, "stock_name", "name", default=symbol)),
                "last_price": last,
                "prev_close_price": prev,
                "change_rate": change_rate,
                "volume": safe_float(get_col(row, "volume", default=0)),
                "turnover": safe_float(get_col(row, "turnover", default=0)),
                "update_time": str(get_col(row, "update_time", default="")),
            }
    finally:
        ctx.close()
    return quotes, warnings


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
            results[symbol] = {
                "error": (completed.stderr or completed.stdout).strip()[-500:],
            }
            continue
        try:
            results[symbol] = parse_json_from_mixed_output(completed.stdout)
        except json.JSONDecodeError:
            results[symbol] = {"error": "invalid json", "raw": completed.stdout[-500:]}
        time.sleep(0.15)
    return results


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


def fetch_json(endpoint: str, params: dict[str, Any], user_agent: str) -> dict[str, Any]:
    url = f"https://ai-news-search.futunn.com/{endpoint}?{urlencode(params)}"
    request = Request(url, headers={"User-Agent": user_agent})
    with urlopen(request, timeout=8) as response:
        raw = response.read().decode("utf-8", errors="replace")
    return json.loads(raw)


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
                {"keyword": keyword, "size": 5, "news_type": 1, "lang": "zh-CN", "sort_type": 2},
                "futunn-news-search/0.0.2 (Skill)",
            )
            items = payload.get("data") if payload.get("code") == 0 else []
            normalized = []
            for item in items or []:
                normalized.append(
                    {
                        "title": clean_text(item.get("title")),
                        "publish_time": normalize_time(item.get("publish_time")),
                        "url": str(item.get("url") or ""),
                    }
                )
            results[symbol] = {"items": normalized, "error": "" if payload.get("code") == 0 else payload.get("message", "")}
        except Exception as exc:  # noqa: BLE001
            results[symbol] = {"items": [], "error": f"{type(exc).__name__}: {exc}"}
        time.sleep(0.1)
    return results


BULL_CUES = [
    "涨",
    "拉升",
    "突破",
    "看多",
    "买入",
    "利好",
    "强势",
    "beat",
    "bull",
    "buy",
    "long",
    "breakout",
    "upside",
    "surge",
]
BEAR_CUES = [
    "跌",
    "下跌",
    "回落",
    "看空",
    "卖出",
    "利空",
    "风险",
    "bear",
    "sell",
    "short",
    "downside",
    "miss",
    "dump",
]


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
            payload = fetch_json(
                "stock_feed",
                {"keyword": keyword, "size": 30},
                "futunn-comment-sentiment/0.0.2 (Skill)",
            )
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


def flatten_snippets(value: Any, limit: int = 3) -> list[str]:
    snippets: list[str] = []

    def walk(obj: Any) -> None:
        if len(snippets) >= limit:
            return
        if isinstance(obj, dict):
            values = []
            for key, item in obj.items():
                if isinstance(item, (dict, list)):
                    walk(item)
                elif item not in (None, ""):
                    key_text = str(key)
                    if any(token in key_text.lower() for token in ("desc", "summary", "content", "name", "title", "direction", "date", "time")):
                        values.append(str(item))
            text = clean_text(" / ".join(values))
            if len(text) >= 12:
                snippets.append(text[:180])
        elif isinstance(obj, list):
            for item in obj:
                walk(item)
        elif isinstance(obj, str) and len(obj.strip()) >= 12:
            snippets.append(clean_text(obj)[:180])

    walk(value)
    return snippets[:limit]


def load_local_dashboard(symbols: list[str]) -> tuple[dict[str, dict[str, Any]], str]:
    rows = dashboard_analysis.build_analysis()
    by_symbol = {str(row.get("underlying")): row for row in rows}
    latest_date = rows[0]["snapshot_date"] if rows else ""
    return {symbol: by_symbol.get(symbol, {}) for symbol in symbols}, latest_date


def days_to_expiry(expiry: str) -> int | None:
    if not expiry:
        return None
    try:
        target = datetime.strptime(expiry, "%Y-%m-%d").date()
        return (target - now_bjt().date()).days
    except ValueError:
        return None


def moneyness(position: dict[str, Any], quote: dict[str, Any]) -> float | None:
    if position.get("kind") != "option":
        return None
    strike = safe_float(position.get("strike"))
    price = safe_float(quote.get("last_price"))
    if strike <= 0 or price <= 0:
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
    parts = []
    for currency, value in group.get("market_val_by_currency", {}).items():
        parts.append(money(value, currency))
    return " / ".join(parts) if parts else "-"


def build_attention_items(
    groups: dict[str, dict[str, Any]],
    quotes: dict[str, dict[str, Any]],
    dashboard: dict[str, dict[str, Any]],
    sentiment: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for symbol, group in groups.items():
        quote = quotes.get(symbol, {})
        signal = dashboard.get(symbol, {})
        tags: list[str] = []
        reasons: list[str] = []
        score = 0.0

        direction = str(signal.get("direction") or "NONE")
        strength = str(signal.get("strength") or "none")
        if strength in {"strong", "medium"}:
            tags.append(f"期权{direction}/{strength}")
            reasons.append(
                f"看板方向 {direction}，异常分 {safe_float(signal.get('score')):.1f}，"
                f"成交量变化 {pct((signal.get('volume_change_pct') or 0) * 100, 0)}"
            )
            score += 2.0 if strength == "strong" else 1.2
        if direction == "PUT":
            tags.append("与多头仓位相反")
            score += 2.0
        elif direction == "CALL":
            tags.append("与多头仓位同向")
            score += 0.8

        change = safe_float(quote.get("change_rate"))
        if abs(change) >= 4:
            tags.append("正股大幅波动")
            reasons.append(f"当前涨跌幅 {pct(change)}")
            score += 1.0

        for position in group.get("positions", []):
            dte = days_to_expiry(str(position.get("expiry") or ""))
            if dte is not None and dte <= 14:
                tags.append("近到期")
                reasons.append(f"{position['code']} 距到期 {dte} 天")
                score += 1.8
            mny = moneyness(position, quote)
            if mny is not None and position.get("option_type") == "C" and mny <= -0.15:
                tags.append("Call价外")
                reasons.append(f"{position['code']} 距行权价约 {abs(mny) * 100:.0f}%")
                score += 0.9

        senti = sentiment.get(symbol, {})
        if senti.get("post_count"):
            bull = safe_float(senti.get("bullish"))
            bear = safe_float(senti.get("bearish"))
            if abs(bull - bear) >= 25:
                tags.append("社区情绪偏向")
                reasons.append(f"社区看多 {bull:.0f}% / 看空 {bear:.0f}%")
                score += 0.5

        abs_exposure = sum(abs(safe_float(v)) for v in group.get("market_val_by_currency", {}).values())
        if abs_exposure >= 50_000:
            tags.append("仓位较大")
            score += 1.0

        if not reasons:
            reasons.append("暂无特别异常，放在例行观察。")

        items.append(
            {
                "symbol": symbol,
                "score": score,
                "tags": list(dict.fromkeys(tags))[:5],
                "reasons": reasons[:3],
                "exposure": exposure_text(group),
                "quote": quote,
                "signal": signal,
            }
        )
    return sorted(items, key=lambda item: (-item["score"], item["symbol"]))


def quote_class(value: Any) -> str:
    number = safe_float(value)
    if number > 0:
        return "up"
    if number < 0:
        return "down"
    return "flat"


def render_badge(text: str, cls: str = "") -> str:
    return f'<span class="badge {cls}">{html.escape(text)}</span>'


def render_html(payload: dict[str, Any]) -> str:
    generated_at = payload["generated_at_bjt"]
    state = payload["opend_state"]
    groups = payload["groups"]
    quotes = payload["quotes"]
    dashboard = payload["dashboard"]
    sentiment = payload["sentiment"]
    news = payload["news"]
    derivatives = payload["derivatives"]
    attention = payload["attention"]

    top_attention = attention[:6]
    benchmarks = [quotes.get(symbol, {"code": symbol}) for symbol in BENCHMARKS]

    def h(text: Any) -> str:
        return html.escape(str(text if text is not None else ""))

    def quote_price(symbol: str) -> str:
        quote = quotes.get(symbol, {})
        if not quote:
            return "-"
        cls = quote_class(quote.get("change_rate"))
        return f'<span class="{cls}">{safe_float(quote.get("last_price")):.2f} / {pct(quote.get("change_rate"))}</span>'

    attention_cards = []
    for item in top_attention:
        symbol = item["symbol"]
        signal = item.get("signal") or {}
        tags = "".join(render_badge(tag) for tag in item["tags"])
        reasons = "".join(f"<li>{h(reason)}</li>" for reason in item["reasons"])
        attention_cards.append(
            f"""
            <article class="attention-card">
              <div class="card-head">
                <h3>{h(symbol)}</h3>
                <span class="priority">{item['score']:.1f}</span>
              </div>
              <div class="metric-row">
                <span>敞口</span><b>{h(item['exposure'])}</b>
                <span>当前</span><b>{quote_price(symbol)}</b>
              </div>
              <div class="metric-row">
                <span>期权信号</span><b>{h(signal.get('direction', 'NONE'))} {safe_float(signal.get('score')):.1f}</b>
                <span>P/C</span><b>{safe_float(signal.get('put_call_ratio')):.2f}</b>
              </div>
              <div class="badges">{tags or render_badge("例行观察")}</div>
              <ul>{reasons}</ul>
            </article>
            """
        )

    position_rows = []
    for symbol, group in groups.items():
        quote = quotes.get(symbol, {})
        signal = dashboard.get(symbol, {})
        pos_bits = []
        for position in group["positions"]:
            if position["kind"] == "option":
                dte = days_to_expiry(str(position.get("expiry")))
                mny = moneyness(position, quote)
                mny_text = "" if mny is None else f" / {'价内' if mny >= 0 else '价外'} {abs(mny) * 100:.0f}%"
                dte_text = "" if dte is None else f" / {dte}天"
                pos_bits.append(
                    f"{h(position['option_type'])}{safe_float(position['strike']):g} {h(position['expiry'])}"
                    f" x{safe_float(position['qty']):g}{dte_text}{mny_text}"
                )
            else:
                pos_bits.append(f"正股 x{safe_float(position['qty']):g}")
        position_rows.append(
            f"""
            <tr>
              <td><b>{h(symbol)}</b><small>{h(quote.get('name', ''))}</small></td>
              <td>{quote_price(symbol)}</td>
              <td>{h(exposure_text(group))}</td>
              <td>{h('；'.join(pos_bits))}</td>
              <td>{h(signal.get('direction', 'NONE'))}<small>异常分 {safe_float(signal.get('score')):.1f}</small></td>
            </tr>
            """
        )

    option_rows = []
    for symbol in groups:
        signal = dashboard.get(symbol, {})
        if not signal:
            continue
        option_rows.append(
            f"""
            <tr>
              <td><b>{h(symbol)}</b></td>
              <td>{h(signal.get('direction'))}</td>
              <td>{safe_float(signal.get('score')):.1f}</td>
              <td>{fmt_num(signal.get('total_volume'))}</td>
              <td>{safe_float(signal.get('put_call_ratio')):.2f}</td>
              <td>{pct((signal.get('volume_change_pct') or 0) * 100, 0)}</td>
              <td>{h(signal.get('matched_unusual_count', 0))}</td>
              <td>{'是' if signal.get('is_concentrated') else '否'}</td>
            </tr>
            """
        )

    derivatives_rows = []
    for symbol in groups:
        data = derivatives.get(symbol, {})
        snippets = flatten_snippets(data.get("data", data), 2)
        text = "；".join(snippets) if snippets else (data.get("error") or "无明显返回内容")
        derivatives_rows.append(
            f"""
            <tr>
              <td><b>{h(symbol)}</b></td>
              <td>{h(text)}</td>
            </tr>
            """
        )

    news_blocks = []
    for symbol in groups:
        items = news.get(symbol, {}).get("items", [])[:3]
        if not items:
            err = news.get(symbol, {}).get("error") or "暂无新闻"
            body = f"<li>{h(err)}</li>"
        else:
            body = "".join(
                f'<li><a href="{h(item["url"])}">{h(item["title"])}</a><small>{h(item["publish_time"])}</small></li>'
                for item in items
            )
        senti = sentiment.get(symbol, {})
        news_blocks.append(
            f"""
            <article class="news-card">
              <div class="card-head">
                <h3>{h(symbol)}</h3>
                <span>社区 {safe_int(senti.get('post_count'))} 条</span>
              </div>
              <div class="sentiment">
                <span class="up">看多 {safe_float(senti.get('bullish')):.0f}%</span>
                <span class="down">看空 {safe_float(senti.get('bearish')):.0f}%</span>
                <span>中性 {safe_float(senti.get('neutral')):.0f}%</span>
              </div>
              <ul>{body}</ul>
            </article>
            """
        )

    benchmark_html = "".join(
        f"""
        <div class="bench">
          <span>{h(item.get('code'))}</span>
          <b class="{quote_class(item.get('change_rate'))}">{safe_float(item.get('last_price')):.2f}</b>
          <small class="{quote_class(item.get('change_rate'))}">{pct(item.get('change_rate'))}</small>
        </div>
        """
        for item in benchmarks
        if item
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>投资日报</title>
  <style>
    :root {{
      --bg: #eef4fb;
      --panel: #ffffff;
      --ink: #071933;
      --muted: #66758c;
      --line: #dbe5f1;
      --blue: #12305f;
      --soft-blue: #f4f8fd;
      --up: #d63b4c;
      --down: #198a55;
      --bar: #d8e1f5;
      --shadow: 0 16px 42px rgba(20, 45, 76, .08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
      line-height: 1.45;
    }}
    .page {{ max-width: 1740px; margin: 0 auto; padding: 22px 28px 34px; }}
    header {{
      background: rgba(255,255,255,.92);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 22px 26px;
      box-shadow: var(--shadow);
    }}
    .topline {{ display: flex; justify-content: space-between; gap: 22px; align-items: flex-start; }}
    h1 {{ margin: 0; font-size: 36px; letter-spacing: 0; color: var(--blue); }}
    .sub {{ color: var(--muted); margin-top: 8px; }}
    .source {{ text-align: right; color: var(--muted); font-size: 14px; }}
    .benchmarks {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-top: 20px; }}
    .bench {{ background: var(--soft-blue); border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px; }}
    .bench span, .bench small {{ display: block; color: var(--muted); }}
    .bench b {{ font-size: 22px; }}
    section {{ margin-top: 22px; }}
    .section-head {{ display: flex; align-items: baseline; justify-content: space-between; margin: 0 2px 12px; }}
    h2 {{ margin: 0; font-size: 22px; color: var(--blue); }}
    .grid-3 {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; }}
    .attention-card, .table-card, .news-card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      box-shadow: var(--shadow);
    }}
    .attention-card {{ padding: 18px; }}
    .card-head {{ display: flex; justify-content: space-between; align-items: center; gap: 12px; }}
    h3 {{ margin: 0; font-size: 24px; color: var(--blue); }}
    .priority {{
      border: 1px solid var(--line);
      background: var(--soft-blue);
      color: var(--blue);
      font-weight: 800;
      border-radius: 10px;
      padding: 5px 10px;
    }}
    .metric-row {{ display: grid; grid-template-columns: auto 1fr auto 1fr; gap: 8px 12px; margin-top: 12px; align-items: baseline; }}
    .metric-row span {{ color: var(--muted); font-size: 13px; }}
    .metric-row b {{ font-size: 15px; }}
    .badges {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 14px 0 8px; }}
    .badge {{ border: 1px solid var(--line); background: var(--soft-blue); color: var(--blue); border-radius: 999px; padding: 4px 9px; font-weight: 700; font-size: 12px; }}
    ul {{ margin: 10px 0 0; padding-left: 18px; }}
    li {{ margin: 4px 0; }}
    .table-card {{ padding: 16px; overflow: hidden; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th {{ text-align: left; color: var(--muted); background: var(--soft-blue); font-weight: 800; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 10px 12px; vertical-align: top; }}
    td small {{ display: block; color: var(--muted); margin-top: 2px; }}
    tr:last-child td {{ border-bottom: 0; }}
    .up {{ color: var(--up); }}
    .down {{ color: var(--down); }}
    .flat {{ color: var(--ink); }}
    .two-col {{ display: grid; grid-template-columns: 1.35fr .95fr; gap: 18px; }}
    .news-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; }}
    .news-card {{ padding: 16px; }}
    .news-card h3 {{ font-size: 20px; }}
    .sentiment {{ display: flex; gap: 12px; color: var(--muted); margin: 10px 0; font-weight: 800; }}
    a {{ color: var(--blue); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .note {{ color: var(--muted); font-size: 13px; margin-top: 14px; }}
    @media (max-width: 1100px) {{
      .grid-3, .news-grid, .two-col, .benchmarks {{ grid-template-columns: 1fr; }}
      .topline {{ flex-direction: column; }}
      .source {{ text-align: left; }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <header>
      <div class="topline">
        <div>
          <h1>投资日报</h1>
          <div class="sub">以真实持仓为中心，综合 Futu 持仓/行情、期权看板、衍生品异动、新闻和社区情绪。</div>
        </div>
        <div class="source">
          <div>生成时间：{h(generated_at)} BJT</div>
          <div>OpenD：{h(state.get('program_status_type'))} / 美股状态 {h(state.get('market_us'))}</div>
          <div>期权看板日期：{h(payload.get('dashboard_date'))}</div>
        </div>
      </div>
      <div class="benchmarks">{benchmark_html}</div>
    </header>

    <section>
      <div class="section-head"><h2>今天优先看</h2><span class="sub">排序综合仓位、期权方向、近到期、正股波动和社区情绪</span></div>
      <div class="grid-3">{''.join(attention_cards)}</div>
    </section>

    <section class="two-col">
      <div class="table-card">
        <div class="section-head"><h2>持仓视角</h2></div>
        <table>
          <thead><tr><th>标的</th><th>当前</th><th>敞口</th><th>持仓理解</th><th>看板信号</th></tr></thead>
          <tbody>{''.join(position_rows)}</tbody>
        </table>
      </div>
      <div class="table-card">
        <div class="section-head"><h2>期权信号交叉验证</h2></div>
        <table>
          <thead><tr><th>标的</th><th>方向</th><th>分</th><th>量</th><th>P/C</th><th>量变</th><th>异动</th><th>集中</th></tr></thead>
          <tbody>{''.join(option_rows) or '<tr><td colspan="8">持仓标的暂无本地看板信号。</td></tr>'}</tbody>
        </table>
      </div>
    </section>

    <section>
      <div class="section-head"><h2>衍生品异动返回摘要</h2><span class="sub">来自 get_derivative_unusual，保留短摘用于核对</span></div>
      <div class="table-card">
        <table>
          <thead><tr><th style="width: 150px;">标的</th><th>摘要</th></tr></thead>
          <tbody>{''.join(derivatives_rows)}</tbody>
        </table>
      </div>
    </section>

    <section>
      <div class="section-head"><h2>新闻与社区情绪</h2></div>
      <div class="news-grid">{''.join(news_blocks)}</div>
    </section>

    <p class="note">说明：本页为信息整理，不构成投资建议。期权看板的成交量/P-C 口径沿用当前项目规则：筛选器第一页成交额口径聚合，P/C 仍按成交量计算；衍生品异动与社区讨论为实时接口返回，可能受权限、时段和平台收录影响。</p>
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

    print("[7/7] Rendering HTML...")
    attention = build_attention_items(groups, quotes, dashboard, sentiment)
    payload = {
        "generated_at_bjt": now_bjt().strftime("%Y-%m-%d %H:%M:%S"),
        "opend_state": state,
        "positions": positions,
        "groups": groups,
        "quotes": quotes,
        "dashboard": dashboard,
        "dashboard_date": dashboard_date,
        "derivatives": derivatives,
        "news": news,
        "sentiment": sentiment,
        "attention": attention,
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
    print("Top attention:")
    for item in attention[:6]:
        print(f" - {item['symbol']}: {item['score']:.1f} {'/'.join(item['tags'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
