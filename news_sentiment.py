from __future__ import annotations

import csv
import io
import json
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from gemini_ai import evaluate_news_risk


@dataclass
class NewsResult:
    risk: str = "NEUTRAL"
    score: float = 50.0
    reason: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


CURRENCY_NAMES = {
    "USD": "US dollar",
    "EUR": "euro",
    "GBP": "pound sterling",
    "JPY": "Japanese yen",
    "CHF": "Swiss franc",
    "CAD": "Canadian dollar",
    "AUD": "Australian dollar",
    "NZD": "New Zealand dollar",
    "CNH": "Chinese yuan",
    "CNY": "Chinese yuan",
    "HKD": "Hong Kong dollar",
    "SGD": "Singapore dollar",
    "NOK": "Norwegian krone",
    "SEK": "Swedish krona",
    "DKK": "Danish krone",
    "PLN": "Polish zloty",
    "HUF": "Hungarian forint",
    "CZK": "Czech koruna",
    "TRY": "Turkish lira",
    "ZAR": "South African rand",
    "MXN": "Mexican peso",
    "BRL": "Brazilian real",
    "CLP": "Chilean peso",
    "COP": "Colombian peso",
    "ARS": "Argentine peso",
    "THB": "Thai baht",
    "IDR": "Indonesian rupiah",
    "INR": "Indian rupee",
    "KRW": "Korean won",
    "ILS": "Israeli shekel",
}

POSITIVE_WORDS = {
    "beat", "beats", "higher", "strong", "stronger", "growth", "hawkish", "hike",
    "raised", "rises", "rally", "surge", "optimism", "improves", "expands",
}
NEGATIVE_WORDS = {
    "miss", "misses", "lower", "weak", "weaker", "contraction", "dovish", "cut",
    "cuts", "falls", "slump", "recession", "risk", "crisis", "inflation shock",
}


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def split_pair(symbol: str) -> tuple[str, str] | None:
    cleaned = "".join(ch for ch in symbol.upper().replace("/", "") if ch.isalpha())
    if len(cleaned) < 6:
        return None
    base, quote = cleaned[:3], cleaned[3:6]
    if base not in CURRENCY_NAMES or quote not in CURRENCY_NAMES:
        return None
    return base, quote


def cache_path() -> Path:
    return Path(os.getenv("NEWS_CACHE_PATH", "data/news_cache.json"))


def load_cache() -> dict[str, Any]:
    path = cache_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cache(cache: dict[str, Any]) -> None:
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")


def cached_json(key: str, max_age_seconds: float) -> Any | None:
    if not env_bool("NEWS_USE_CACHE", True):
        return None
    cache = load_cache()
    item = cache.get(key, {})
    try:
        if time.time() - float(item.get("timestamp", 0.0)) <= max_age_seconds:
            return item.get("value")
    except (TypeError, ValueError):
        return None
    return None


def set_cached_json(key: str, value: Any) -> None:
    if not env_bool("NEWS_USE_CACHE", True):
        return
    cache = load_cache()
    cache[key] = {"timestamp": time.time(), "value": value}
    save_cache(cache)


def parse_event_time(raw: str) -> datetime | None:
    if not raw:
        return None
    text = raw.strip().replace("Z", "+00:00")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m-%d-%Y %H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            pass
    try:
        value = datetime.fromisoformat(text)
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    except ValueError:
        return None


def local_calendar_events() -> list[dict[str, Any]]:
    path = Path(os.getenv("NEWS_CALENDAR_PATH", "data/economic_calendar.csv"))
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rows.append(row)
    return rows


def external_calendar_events() -> list[dict[str, Any]]:
    url = os.getenv("FOREX_FACTORY_CALENDAR_URL", "").strip()
    if not url:
        return []
    cache_key = f"calendar:{url}"
    max_age = env_float("NEWS_CALENDAR_CACHE_SECONDS", 900.0)
    cached = cached_json(cache_key, max_age)
    if cached is not None:
        return cached
    response = requests.get(url, timeout=env_float("NEWS_HTTP_TIMEOUT_SECONDS", 10.0))
    response.raise_for_status()
    if "json" in response.headers.get("content-type", "").lower() or response.text.strip().startswith("["):
        rows = response.json()
    else:
        rows = list(csv.DictReader(io.StringIO(response.text)))
    if isinstance(rows, list):
        set_cached_json(cache_key, rows)
        return rows
    return []


def scheduled_news_block(currencies: tuple[str, str]) -> tuple[bool, str, list[dict[str, Any]]]:
    now = datetime.now(UTC)
    lookahead = timedelta(hours=env_float("NEWS_LOOKAHEAD_HOURS", 4.0))
    buffer_minutes = env_float("NEWS_BLOCK_MINUTES_AROUND_EVENT", 30.0)
    impacts = {"high", "red", "3", "important"}
    relevant: list[dict[str, Any]] = []
    for row in local_calendar_events() + external_calendar_events():
        currency = str(row.get("currency") or row.get("Currency") or row.get("ccy") or "").upper()
        impact = str(row.get("impact") or row.get("Impact") or row.get("importance") or "").strip().lower()
        event_time = parse_event_time(str(row.get("time") or row.get("datetime") or row.get("date") or row.get("Date") or ""))
        if currency not in currencies or event_time is None:
            continue
        if impact not in impacts:
            continue
        relevant.append(row)
        if now - timedelta(minutes=buffer_minutes) <= event_time <= now + lookahead:
            if abs((event_time - now).total_seconds()) <= lookahead.total_seconds():
                return True, f"high-impact {currency} event within {lookahead}", relevant
    return False, "no blocking high-impact event", relevant


def simple_sentiment(text: str) -> float:
    lowered = text.lower()
    score = 0.0
    for word in POSITIVE_WORDS:
        if word in lowered:
            score += 1.0
    for word in NEGATIVE_WORDS:
        if word in lowered:
            score -= 1.0
    return score


def gdelt_news(currency: str, hours: int) -> list[dict[str, Any]]:
    query = CURRENCY_NAMES.get(currency, currency)
    cache_key = f"gdelt:{currency}:{hours}"
    cached = cached_json(cache_key, env_float("NEWS_CACHE_MAX_AGE_SECONDS", 3600.0))
    if cached is not None:
        return cached
    response = requests.get(
        "https://api.gdeltproject.org/api/v2/doc/doc",
        params={
            "query": query,
            "mode": "ArtList",
            "format": "json",
            "timespan": f"{hours}h",
            "maxrecords": int(env_float("GDELT_MAX_RECORDS", 25)),
        },
        timeout=env_float("NEWS_HTTP_TIMEOUT_SECONDS", 10.0),
    )
    response.raise_for_status()
    rows = response.json().get("articles", [])
    set_cached_json(cache_key, rows)
    return rows


def newsapi_news(currency: str, hours: int) -> list[dict[str, Any]]:
    key = os.getenv("NEWSAPI_KEY", "").strip()
    if not key or not env_bool("LAYER8_USE_NEWSAPI", False):
        return []
    query = CURRENCY_NAMES.get(currency, currency)
    cache_key = f"newsapi:{currency}:{hours}"
    cached = cached_json(cache_key, env_float("NEWS_CACHE_MAX_AGE_SECONDS", 3600.0))
    if cached is not None:
        return cached
    from_time = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    response = requests.get(
        "https://newsapi.org/v2/everything",
        params={
            "q": query,
            "from": from_time,
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": int(env_float("NEWSAPI_PAGE_SIZE", 20)),
            "apiKey": key,
        },
        timeout=env_float("NEWS_HTTP_TIMEOUT_SECONDS", 10.0),
    )
    response.raise_for_status()
    rows = response.json().get("articles", [])
    set_cached_json(cache_key, rows)
    return rows


def alpha_vantage_news(currency: str, hours: int) -> list[dict[str, Any]]:
    key = os.getenv("ALPHA_VANTAGE_API_KEY", "").strip()
    if not key or not env_bool("LAYER8_USE_ALPHA_VANTAGE_NEWS", True):
        return []
    query = CURRENCY_NAMES.get(currency, currency)
    cache_key = f"alpha_news:{currency}:{hours}"
    cached = cached_json(cache_key, env_float("NEWS_CACHE_MAX_AGE_SECONDS", 3600.0))
    if cached is not None:
        return cached
    response = requests.get(
        "https://www.alphavantage.co/query",
        params={
            "function": "NEWS_SENTIMENT",
            "topics": "forex",
            "apikey": key,
            "limit": int(env_float("ALPHA_NEWS_LIMIT", 25)),
        },
        timeout=env_float("NEWS_HTTP_TIMEOUT_SECONDS", 10.0),
    )
    response.raise_for_status()
    feed = response.json().get("feed", [])
    rows = [row for row in feed if query.lower() in json.dumps(row).lower() or currency.lower() in json.dumps(row).lower()]
    set_cached_json(cache_key, rows)
    return rows


def currency_news_score(currency: str, hours: int) -> tuple[float, dict[str, Any]]:
    evidence: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    for source, fn in (
        ("gdelt", gdelt_news),
        ("alpha_vantage", alpha_vantage_news),
        ("newsapi", newsapi_news),
    ):
        try:
            source_rows = fn(currency, hours)
            evidence[f"{source}_count"] = len(source_rows)
            rows.extend(source_rows)
        except Exception as exc:
            evidence[f"{source}_error"] = str(exc)

    if not rows:
        return 50.0, evidence

    sentiment_total = 0.0
    sentiment_count = 0
    headlines = []
    for row in rows[:50]:
        title = str(row.get("title") or row.get("headline") or row.get("summary") or "")
        summary = str(row.get("description") or row.get("summary") or "")
        text = f"{title} {summary}"
        if "overall_sentiment_score" in row:
            try:
                sentiment_total += float(row["overall_sentiment_score"]) * 5.0
                sentiment_count += 1
            except (TypeError, ValueError):
                pass
        score = simple_sentiment(text)
        if score:
            sentiment_total += score
            sentiment_count += 1
        if title:
            headlines.append(title[:160])

    evidence["sample_headlines"] = headlines[:8]
    if sentiment_count == 0:
        return 50.0, evidence
    avg = sentiment_total / sentiment_count
    return max(0.0, min(100.0, 50.0 + avg * 10.0)), evidence


def ai_news_layer_result(
    symbol: str,
    side: str,
    base: str,
    quote: str,
    evidence: dict[str, Any],
    fallback_risk: str,
    fallback_score: float,
    fallback_reason: str,
) -> NewsResult | None:
    if not env_bool("AI_USE_FOR_LAYER8", True):
        return None

    context = {
        "symbol": symbol,
        "requested_trade_side": side.upper(),
        "base_currency": base,
        "quote_currency": quote,
        "rules": {
            "BLOCKED": "Use only when a high-impact event is near or supplied news indicates abnormal event risk.",
            "AGAINST_TRADE": "Use when news bias clearly conflicts with requested trade side.",
            "BIASED": "Use when news creates a directional bias but does not block the trade.",
            "CLEAR": "Use when there is enough evidence and no meaningful news risk.",
            "NEUTRAL": "Use when evidence is weak, mixed, stale, or not decisive.",
        },
        "fallback_model": {
            "risk": fallback_risk,
            "score": fallback_score,
            "reason": fallback_reason,
            "note": "This fallback is simple keyword/API scoring only. AI should override it when evidence supports a stronger judgement.",
        },
        "evidence": evidence,
    }
    ai_result = evaluate_news_risk(context)
    evidence["ai_layer8"] = {
        "enabled": ai_result.enabled,
        "decision": ai_result.decision,
        "score": ai_result.score,
        "pattern": ai_result.pattern,
        "reason": ai_result.reason,
    }
    if not ai_result.enabled or ai_result.decision in {"ERROR", "NOT_USED"}:
        return None

    decision = ai_result.decision if ai_result.decision in {"BLOCKED", "CLEAR", "AGAINST_TRADE", "BIASED", "NEUTRAL"} else "NEUTRAL"
    risk = "CLEAR" if decision == "NEUTRAL" else decision
    reason = f"AI Layer 8 {decision}: {ai_result.reason or ai_result.pattern or 'news evidence assessed'}"
    return NewsResult(
        risk=risk,
        score=ai_result.score,
        reason=reason,
        evidence=evidence,
    )


def evaluate_news(symbol: str, side: str = "") -> NewsResult:
    pair = split_pair(symbol)
    if pair is None:
        return NewsResult(reason="Unsupported symbol for news layer")
    base, quote = pair
    blocked, block_reason, events = scheduled_news_block(pair)
    evidence: dict[str, Any] = {"base": base, "quote": quote, "blocking_events": events[:5]}
    if blocked:
        return NewsResult(risk="BLOCKED", score=0.0, reason=block_reason, evidence=evidence)

    hours = int(env_float("NEWS_LOOKBACK_HOURS", 72.0))
    base_score, base_evidence = currency_news_score(base, hours)
    quote_score, quote_evidence = currency_news_score(quote, hours)
    evidence["base_news"] = base_evidence
    evidence["quote_news"] = quote_evidence
    evidence["base_score"] = base_score
    evidence["quote_score"] = quote_score

    directional = base_score - quote_score
    score = 50.0 + max(-35.0, min(35.0, directional))
    side = side.upper()
    if side == "BUY" and directional < -env_float("NEWS_BIAS_THRESHOLD", 8.0):
        risk = "AGAINST_TRADE"
        reason = f"news bias against BUY: base={base_score:.1f}, quote={quote_score:.1f}"
    elif side == "SELL" and directional > env_float("NEWS_BIAS_THRESHOLD", 8.0):
        risk = "AGAINST_TRADE"
        reason = f"news bias against SELL: base={base_score:.1f}, quote={quote_score:.1f}"
    else:
        risk = "CLEAR" if abs(directional) < env_float("NEWS_BIAS_THRESHOLD", 8.0) else "BIASED"
        reason = f"news bias score base={base_score:.1f}, quote={quote_score:.1f}"
    score = max(0.0, min(100.0, score))

    ai_result = ai_news_layer_result(symbol, side, base, quote, evidence, risk, score, reason)
    if ai_result is not None:
        return ai_result

    evidence["fallback_note"] = "AI Layer 8 unavailable; using simple keyword/API sentiment fallback."
    return NewsResult(risk=risk, score=score, reason=reason, evidence=evidence)
