from __future__ import annotations

import csv
import argparse
import json
import os
import lzma
import struct
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv
from adaptive_weights import adaptive_weights_for_mode
from fundamentals import evaluate_fundamentals
from gemini_ai import ai_enabled, evaluate_candle_pattern, evaluate_fundamental_bias, summarize_funnel_status
from news_sentiment import evaluate_news

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover - lets the scanner run without MT5 installed.
    mt5 = None


TRENDING_UP = "TRENDING_UP"
TRENDING_DOWN = "TRENDING_DOWN"
RANGING = "RANGING"
HIGH_PRIORITY = "HIGH_PRIORITY"
WAIT = "WAIT"
NO_BREAKOUT = "NO_BREAKOUT"
PULLBACK_CONFIRMED = "PULLBACK_CONFIRMED"
PULLBACK_WAIT = "PULLBACK_WAIT"
RANGE_BUY = "RANGE_BUY"
RANGE_SELL = "RANGE_SELL"

FALLBACK_CURRENCIES = [
    "USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "CNH", "CNY",
    "HKD", "SGD", "NOK", "SEK", "DKK", "PLN", "HUF", "CZK", "TRY", "ZAR",
    "MXN", "BRL", "CLP", "COP", "ARS", "THB", "IDR", "INR", "KRW", "ILS",
]
FALLBACK_METALS = ["XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD", "XAUEUR", "XAGEUR", "XAUJPY", "XAUGBP", "XAUAUD", "XAUCHF"]


@dataclass
class SourceTrend:
    source: str
    state: str
    reason: str


@dataclass
class Candidate:
    symbol: str
    state: str
    agreement: int
    sources: list[SourceTrend]
    nearest_support: float | None = None
    nearest_resistance: float | None = None
    support_distance_percent: float | None = None
    resistance_distance_percent: float | None = None
    support_tests: int = 0
    resistance_tests: int = 0
    layer2_reason: str = ""
    layer3_state: str = ""
    layer3_reason: str = ""
    breakout_level: float | None = None
    breakout_distance_percent: float | None = None
    breakout_volume_ratio: float | None = None
    breakout_candle_ratio: float | None = None
    breakout_side: str = ""
    layer4_state: str = ""
    layer4_reason: str = ""
    pullback_level: float | None = None
    pullback_distance_percent: float | None = None
    fib_level: float | None = None
    confirmation_pattern: str = ""
    layer5_state: str = ""
    layer5_reason: str = ""
    range_support: float | None = None
    range_resistance: float | None = None
    range_width_pips: float | None = None
    range_width_atr: float | None = None
    range_support_touches: int = 0
    range_resistance_touches: int = 0
    ai_decision: str = ""
    ai_score: float | None = None
    ai_reason: str = ""
    ai_pattern: str = ""
    layer1_score: float = 0.0
    layer2_score: float = 0.0
    layer3_score: float = 0.0
    layer4_score: float = 0.0
    layer5_score: float = 0.0
    layer6_score: float = 0.0
    layer6_pattern: str = ""
    layer6_reason: str = ""
    layer7_bias: str = "NEUTRAL"
    layer7_score: float = 50.0
    layer7_reason: str = "Fundamental layer disabled"
    layer8_risk: str = "NEUTRAL"
    layer8_score: float = 50.0
    layer8_reason: str = "News layer disabled"
    composite_score: float = 0.0
    composite_reason: str = ""
    live_spread_percent: float | None = None
    execution_cost_penalty: float = 0.0
    expected_net_value: float = 0.0


@dataclass
class PriceLevel:
    price: float
    tests: int
    kind: str
    source: str = "MT5"


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass
class RuntimeBudget:
    seconds: float
    started_at: float = field(default_factory=time.monotonic)

    def expired(self) -> bool:
        return self.seconds > 0 and time.monotonic() - self.started_at >= self.seconds

    def remaining(self) -> float:
        if self.seconds <= 0:
            return float("inf")
        return max(0.0, self.seconds - (time.monotonic() - self.started_at))


def http_timeout(default: float = 10.0) -> float:
    return max(1.0, env_float("FUNNEL_HTTP_TIMEOUT_SECONDS", default))


def mt5_timeframe(name: str, default: str = "M5") -> int | None:
    if mt5 is None:
        return None
    key = (name or default).strip().upper()
    mapping = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
    }
    return mapping.get(key, mapping[default])


def normalize_symbol(symbol: str) -> str:
    cleaned = symbol.upper().replace("C:", "").replace("/", "").replace("_", "")
    return "".join(ch for ch in cleaned if ch.isalnum())


def polygon_symbol(symbol: str) -> str:
    return f"C:{normalize_symbol(symbol)}"


def split_forex_symbol(symbol: str) -> tuple[str, str] | None:
    normalized = normalize_symbol(symbol)
    if len(normalized) != 6:
        return None
    return normalized[:3], normalized[3:]


def init_mt5() -> bool:
    if mt5 is None:
        return False

    kwargs = {}
    path = os.getenv("MT5_PATH")
    login = os.getenv("MT5_DEMO_LOGIN") or os.getenv("MT5_LOGIN")
    password = os.getenv("MT5_DEMO_PASSWORD") or os.getenv("MT5_PASSWORD")
    server = os.getenv("MT5_DEMO_SERVER") or os.getenv("MT5_SERVER")
    kwargs["timeout"] = env_int("FUNNEL_MT5_TIMEOUT_MS", 10000)
    if path:
        kwargs["path"] = path
    if login and password and server:
        kwargs.update(login=int(login), password=password, server=server)
    return bool(mt5.initialize(**kwargs))


def mt5_universe(limit: int) -> list[str]:
    if mt5 is None:
        return []

    symbols = mt5.symbols_get()
    if symbols is None:
        return []

    result: list[str] = []
    for item in symbols:
        name = normalize_symbol(item.name)
        if len(name) == 6 and name.isalpha():
            result.append(item.name)
        if len(result) >= limit:
            break
    return result


def massive_universe(api_key: str, api_base: str, limit: int) -> list[str]:
    if not api_key:
        return []
    url = f"{api_base.rstrip('/')}/v2/snapshot/locale/global/markets/forex/tickers"
    try:
        response = requests.get(url, params={"apiKey": api_key}, timeout=http_timeout())
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"MASSIVE universe unavailable: {safe_http_error(exc)}")
        return []
    data = response.json()
    tickers = data.get("tickers") or []
    symbols: list[str] = []
    for row in tickers:
        ticker = row.get("ticker") or ""
        symbol = normalize_symbol(ticker)
        if len(symbol) == 6:
            symbols.append(symbol)
        if len(symbols) >= limit:
            break
    return symbols


def configured_universe(limit: int) -> list[str]:
    raw = os.getenv("WATCHLIST", "")
    symbols = [normalize_symbol(s.strip()) for s in raw.split(",") if s.strip()]
    if env_bool("FUNNEL_EXPAND_FOREX_UNIVERSE", True):
        for base in FALLBACK_CURRENCIES:
            for quote in FALLBACK_CURRENCIES:
                if base != quote:
                    symbols.append(base + quote)
        symbols.extend(FALLBACK_METALS)
    return symbols[:limit]


def unique_symbols(groups: Iterable[Iterable[str]], limit: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for group in groups:
        for symbol in group:
            normalized = normalize_symbol(symbol)
            if normalized in seen or len(normalized) != 6:
                continue
            seen.add(normalized)
            result.append(normalized)
            if len(result) >= limit:
                return result
    return result


def mt5_rates(symbol: str, timeframe: int, count: int = 260) -> pd.DataFrame:
    if mt5 is None:
        return pd.DataFrame()
    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
    if rates is None or len(rates) == 0:
        return pd.DataFrame()
    frame = pd.DataFrame(rates)
    frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
    return frame.rename(columns={"tick_volume": "volume"})


def alpha_rates(symbol: str, interval: str = "60min") -> pd.DataFrame:
    key = os.getenv("ALPHA_VANTAGE_API_KEY", "")
    pair = split_forex_symbol(symbol)
    if not key or pair is None:
        return pd.DataFrame()
    base, quote = pair
    params = {
        "function": "FX_INTRADAY",
        "from_symbol": base,
        "to_symbol": quote,
        "interval": interval,
        "outputsize": "full",
        "apikey": key,
    }
    response = requests.get("https://www.alphavantage.co/query", params=params, timeout=http_timeout())
    response.raise_for_status()
    payload = response.json()
    series_key = next((k for k in payload if k.startswith("Time Series")), "")
    if not series_key:
        return pd.DataFrame()
    rows = []
    for ts, values in payload[series_key].items():
        rows.append(
            {
                "time": pd.Timestamp(ts, tz=UTC),
                "open": float(values["1. open"]),
                "high": float(values["2. high"]),
                "low": float(values["3. low"]),
                "close": float(values["4. close"]),
                "volume": 0.0,
            }
        )
    return pd.DataFrame(rows).sort_values("time").reset_index(drop=True)


def massive_rates(symbol: str) -> pd.DataFrame:
    key = os.getenv("MASSIVE_API_KEY") or os.getenv("POLYGON_API_KEY") or ""
    base = os.getenv("MASSIVE_API_BASE", "https://api.massive.com").rstrip("/")
    if not key:
        return pd.DataFrame()
    end = datetime.now(UTC).date()
    start = end - timedelta(days=420)
    url = f"{base}/v2/aggs/ticker/{polygon_symbol(symbol)}/range/1/hour/{start}/{end}"
    params = {"adjusted": "false", "sort": "asc", "limit": 50000, "apiKey": key}
    try:
        response = requests.get(url, params=params, timeout=http_timeout())
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"{symbol}: MASSIVE rates unavailable: {safe_http_error(exc)}")
        return pd.DataFrame()
    rows = response.json().get("results") or []
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    return pd.DataFrame(
        {
            "time": pd.to_datetime(frame["t"], unit="ms", utc=True),
            "open": frame["o"].astype(float),
            "high": frame["h"].astype(float),
            "low": frame["l"].astype(float),
            "close": frame["c"].astype(float),
            "volume": frame.get("v", pd.Series(np.zeros(len(frame)))).astype(float),
        }
    ).sort_values("time").reset_index(drop=True)


def safe_http_error(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if response is not None:
        return f"HTTP {response.status_code}"
    return exc.__class__.__name__


def resample_h4(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    indexed = frame.set_index("time").sort_index()
    resampled = indexed.resample("4h").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return resampled.dropna().reset_index()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def adx(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    high = frame["high"]
    low = frame["low"]
    close = frame["close"]
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    true_range = pd.concat(
        [(high - low), (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1
    ).max(axis=1)
    atr = true_range.rolling(period).mean()
    plus_di = 100 * plus_dm.rolling(period).mean() / atr
    minus_di = 100 * minus_dm.rolling(period).mean() / atr
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)) * 100
    return dx.rolling(period).mean()


def atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    close = frame["close"].astype(float)
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(period).mean()


def timeframe_trend(frame: pd.DataFrame, label: str) -> SourceTrend:
    if len(frame) < 220:
        return SourceTrend(label, RANGING, "not enough bars")

    data = frame.sort_values("time").reset_index(drop=True).copy()
    data["ema9"] = ema(data["close"], 9)
    data["ema21"] = ema(data["close"], 21)
    data["ema50"] = ema(data["close"], 50)
    data["ema200"] = ema(data["close"], 200)
    data["adx"] = adx(data, env_int("LAYER1_ADX_PERIOD", 14))
    row = data.iloc[-2]
    structure = structure_direction(data)

    min_adx = float(os.getenv("LAYER1_MIN_ADX", "25"))
    if pd.isna(row["adx"]) or row["adx"] < min_adx:
        return SourceTrend(label, RANGING, f"ADX {row['adx']:.2f} below {min_adx:.2f}")

    if structure == TRENDING_UP and row["close"] > row["ema200"] and row["ema9"] > row["ema21"] > row["ema50"]:
        return SourceTrend(label, TRENDING_UP, "HH/HL + EMA200 + EMA stack + ADX")
    if structure == TRENDING_DOWN and row["close"] < row["ema200"] and row["ema9"] < row["ema21"] < row["ema50"]:
        return SourceTrend(label, TRENDING_DOWN, "LH/LL + EMA200 + EMA stack + ADX")
    return SourceTrend(label, RANGING, "structure/EMA alignment failed")


def structure_direction(frame: pd.DataFrame) -> str:
    bars = max(20, env_int("LAYER1_STRUCTURE_WINDOW", 40))
    if len(frame) < bars + 2:
        return RANGING
    completed = frame.iloc[-bars - 1 : -1].copy()
    midpoint = len(completed) // 2
    previous = completed.iloc[:midpoint]
    recent = completed.iloc[midpoint:]
    previous_high = float(previous["high"].max())
    previous_low = float(previous["low"].min())
    recent_high = float(recent["high"].max())
    recent_low = float(recent["low"].min())
    if recent_high > previous_high and recent_low > previous_low:
        return TRENDING_UP
    if recent_high < previous_high and recent_low < previous_low:
        return TRENDING_DOWN
    return RANGING


def source_trend(source: str, h1: pd.DataFrame, h4: pd.DataFrame | None = None) -> SourceTrend:
    if h1.empty:
        return SourceTrend(source, RANGING, "no data")
    if h4 is None:
        h4 = resample_h4(h1)
    h1_state = timeframe_trend(h1, f"{source}:H1")
    h4_state = timeframe_trend(h4, f"{source}:H4")
    if h1_state.state == h4_state.state and h1_state.state != RANGING:
        return SourceTrend(source, h1_state.state, "H1/H4 agreement")
    return SourceTrend(source, RANGING, f"H1={h1_state.state}; H4={h4_state.state}")


def symbol_candidate(symbol: str, use_mt5: bool, use_alpha: bool, use_massive: bool) -> Candidate:
    trends: list[SourceTrend] = []
    if use_mt5 and mt5 is not None:
        trends.append(source_trend("MT5", mt5_rates(symbol, mt5.TIMEFRAME_H1), mt5_rates(symbol, mt5.TIMEFRAME_H4)))
    if use_alpha:
        trends.append(source_trend("ALPHA_VANTAGE", alpha_rates(symbol)))
        time.sleep(max(0.0, env_float("FUNNEL_ALPHA_SLEEP_SECONDS", 12.5)))
    if use_massive:
        trends.append(source_trend("MASSIVE", massive_rates(symbol)))

    up = sum(1 for trend in trends if trend.state == TRENDING_UP)
    down = sum(1 for trend in trends if trend.state == TRENDING_DOWN)
    if up > down:
        return Candidate(symbol, TRENDING_UP, up, trends)
    if down > up:
        return Candidate(symbol, TRENDING_DOWN, down, trends)
    return Candidate(symbol, RANGING, max(up, down), trends)


def mt5_layer1_candidate(symbol: str) -> Candidate:
    if mt5 is None:
        return Candidate(symbol, RANGING, 0, [SourceTrend("MT5", RANGING, "MT5 unavailable")])
    trend = source_trend("MT5", mt5_rates(symbol, mt5.TIMEFRAME_H1), mt5_rates(symbol, mt5.TIMEFRAME_H4))
    agreement = 1 if trend.state != RANGING else 0
    return Candidate(symbol, trend.state, agreement, [trend])


def local_prefilter_rank(candidate: Candidate) -> float:
    score = 0.0
    if candidate.state in {TRENDING_UP, TRENDING_DOWN}:
        score += 60.0
    elif candidate.state == RANGING:
        score += 35.0

    pair = split_forex_symbol(candidate.symbol)
    majors = {"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"}
    if pair is not None:
        major_count = int(pair[0] in majors) + int(pair[1] in majors)
        score += major_count * 10.0

    if mt5 is not None:
        tick = mt5.symbol_info_tick(candidate.symbol)
        if tick is not None and tick.bid > 0 and tick.ask > 0:
            mid = float((tick.bid + tick.ask) / 2.0)
            spread_percent = (float(tick.ask) - float(tick.bid)) / mid * 100.0
            score += max(0.0, 25.0 - spread_percent * 500.0)

    return score


def current_spread_percent(symbol: str) -> float | None:
    if mt5 is None:
        return None
    tick = mt5.symbol_info_tick(symbol)
    if tick is None or tick.bid <= 0 or tick.ask <= 0:
        return None
    mid = float((tick.bid + tick.ask) / 2.0)
    if mid <= 0:
        return None
    return (float(tick.ask) - float(tick.bid)) / mid * 100.0


def externally_confirm_candidate(candidate: Candidate, use_alpha: bool, use_massive: bool) -> Candidate:
    trends = list(candidate.sources)
    if use_alpha:
        trends.append(source_trend("ALPHA_VANTAGE", alpha_rates(candidate.symbol)))
        time.sleep(max(0.0, env_float("FUNNEL_ALPHA_SLEEP_SECONDS", 12.5)))
    if use_massive:
        trends.append(source_trend("MASSIVE", massive_rates(candidate.symbol)))

    agreement = sum(1 for trend in trends if trend.state == candidate.state)
    return Candidate(candidate.symbol, candidate.state, agreement, trends)


def support_resistance_frame(symbol: str) -> pd.DataFrame:
    lookback = env_int("LAYER2_LOOKBACK_CANDLES", 200)
    if env_bool("LAYER2_USE_DUKASCOPY", False):
        frame = dukascopy_rates(symbol, lookback)
        if not frame.empty:
            return frame.tail(lookback).reset_index(drop=True)

    if mt5 is not None:
        frame = mt5_rates(symbol, mt5.TIMEFRAME_H1, max(lookback + 20, 260))
        if not frame.empty:
            return frame.tail(lookback).reset_index(drop=True)
    frame = massive_rates(symbol)
    if not frame.empty:
        return frame.tail(lookback).reset_index(drop=True)
    return alpha_rates(symbol).tail(lookback).reset_index(drop=True)


def dukascopy_rates(symbol: str, hours: int) -> pd.DataFrame:
    normalized = normalize_symbol(symbol)
    if len(normalized) != 6 or not normalized.isalpha():
        return pd.DataFrame()

    rows = []
    max_hours = env_int("DUKASCOPY_MAX_HOURS", max(hours, 240))
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    for offset in range(1, max_hours + 1):
        hour = now - timedelta(hours=offset)
        candle = dukascopy_hour_candle(normalized, hour)
        if candle is not None:
            rows.append(candle)
        if len(rows) >= hours:
            break

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("time").reset_index(drop=True)


def dukascopy_hour_candle(symbol: str, hour: datetime) -> dict[str, float | datetime] | None:
    month = hour.month - 1
    url = (
        "https://datafeed.dukascopy.com/datafeed/"
        f"{symbol}/{hour.year}/{month:02d}/{hour.day:02d}/{hour.hour:02d}h_ticks.bi5"
    )
    try:
        response = requests.get(url, timeout=http_timeout())
        if response.status_code == 404:
            return None
        response.raise_for_status()
        raw = lzma.decompress(response.content)
    except (requests.RequestException, lzma.LZMAError):
        return None

    scale = dukascopy_price_scale(symbol)
    prices: list[float] = []
    volumes: list[float] = []
    for idx in range(0, len(raw) - 19, 20):
        ms, ask, bid, ask_volume, bid_volume = struct.unpack(">IIIff", raw[idx : idx + 20])
        _ = ms
        mid = ((ask / scale) + (bid / scale)) / 2.0
        if mid > 0:
            prices.append(mid)
            volumes.append(float(ask_volume + bid_volume))

    if not prices:
        return None
    return {
        "time": hour,
        "open": prices[0],
        "high": max(prices),
        "low": min(prices),
        "close": prices[-1],
        "volume": sum(volumes),
    }


def dukascopy_price_scale(symbol: str) -> float:
    return 1000.0 if symbol.endswith("JPY") else 100000.0


def swing_levels(frame: pd.DataFrame, symbol: str = "") -> tuple[list[PriceLevel], list[PriceLevel]]:
    min_bars = env_int("LAYER2_MIN_LOOKBACK_CANDLES", 50)
    if len(frame) < min_bars:
        return [], []

    data = frame.sort_values("time").reset_index(drop=True).copy()
    left_right = max(1, env_int("LAYER2_SWING_LEFT_RIGHT", 2))
    swing_lows: list[float] = []
    swing_highs: list[float] = []
    for idx in range(left_right, len(data) - left_right):
        window = data.iloc[idx - left_right : idx + left_right + 1]
        low = data.iloc[idx]["low"]
        high = data.iloc[idx]["high"]
        if low <= window["low"].min():
            swing_lows.append(float(low))
        if high >= window["high"].max():
            swing_highs.append(float(high))

    current = float(data.iloc[-1]["close"])
    tolerance_percent = float(os.getenv("LAYER2_LEVEL_TOLERANCE_PERCENT", "0.10"))
    support = cluster_levels(swing_lows, current, "support", tolerance_percent)
    resistance = cluster_levels(swing_highs, current, "resistance", tolerance_percent)
    tv_support, tv_resistance = tradingview_levels(normalize_symbol(symbol))
    support.extend(tv_support)
    resistance.extend(tv_resistance)
    return support, resistance


def tradingview_levels(symbol: str = "") -> tuple[list[PriceLevel], list[PriceLevel]]:
    if not env_bool("LAYER2_USE_TRADINGVIEW_WEBHOOK", False):
        return [], []
    path = Path(os.getenv("LAYER2_TRADINGVIEW_LEVELS_PATH", "data/tradingview_levels.csv"))
    if not path.exists():
        return [], []

    support: list[PriceLevel] = []
    resistance: list[PriceLevel] = []
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            row_symbol = normalize_symbol(row.get("symbol", ""))
            if symbol and row_symbol != symbol:
                continue
            try:
                price = float(row.get("price", ""))
                tests = int(float(row.get("tests", "1")))
            except ValueError:
                continue
            kind = (row.get("kind") or "").strip().lower()
            level = PriceLevel(price=price, tests=max(1, tests), kind=kind, source="TradingView")
            if kind == "support":
                support.append(level)
            elif kind == "resistance":
                resistance.append(level)
    return support, resistance


def cluster_levels(raw_levels: list[float], current_price: float, kind: str, tolerance_percent: float) -> list[PriceLevel]:
    if not raw_levels or current_price <= 0:
        return []

    levels = sorted(raw_levels)
    clusters: list[list[float]] = []
    for level in levels:
        if not clusters:
            clusters.append([level])
            continue
        center = float(np.mean(clusters[-1]))
        distance_percent = abs(level - center) / max(current_price, 0.00001) * 100.0
        if distance_percent <= tolerance_percent:
            clusters[-1].append(level)
        else:
            clusters.append([level])

    result = [
        PriceLevel(price=float(np.mean(cluster)), tests=len(cluster), kind=kind)
        for cluster in clusters
    ]
    return sorted(result, key=lambda item: (-item.tests, abs(item.price - current_price)))


def nearest_level(levels: list[PriceLevel], current_price: float, want_below: bool) -> PriceLevel | None:
    if current_price <= 0:
        return None
    filtered = [
        level
        for level in levels
        if (level.price <= current_price if want_below else level.price >= current_price)
    ]
    if not filtered:
        filtered = levels
    if not filtered:
        return None
    return min(filtered, key=lambda level: abs(level.price - current_price))


def distance_percent(price: float | None, current_price: float) -> float | None:
    if price is None or current_price <= 0:
        return None
    return abs(current_price - price) / current_price * 100.0


def apply_layer2(candidate: Candidate) -> Candidate | None:
    if not env_bool("LAYER2_ENABLE_SUPPORT_RESISTANCE", True):
        candidate.layer2_reason = "Layer 2 disabled"
        return candidate

    frame = support_resistance_frame(candidate.symbol)
    min_bars = env_int("LAYER2_MIN_LOOKBACK_CANDLES", 50)
    if len(frame) < min_bars:
        candidate.layer2_reason = f"eliminated: only {len(frame)} candles for S/R"
        return None

    current = float(frame.sort_values("time").iloc[-1]["close"])
    support_levels, resistance_levels = swing_levels(frame, candidate.symbol)
    support = nearest_level(support_levels, current, want_below=True)
    resistance = nearest_level(resistance_levels, current, want_below=False)

    candidate.nearest_support = None if support is None else support.price
    candidate.nearest_resistance = None if resistance is None else resistance.price
    candidate.support_tests = 0 if support is None else support.tests
    candidate.resistance_tests = 0 if resistance is None else resistance.tests
    candidate.support_distance_percent = distance_percent(candidate.nearest_support, current)
    candidate.resistance_distance_percent = distance_percent(candidate.nearest_resistance, current)

    max_distance = float(os.getenv("LAYER2_MAX_DISTANCE_PERCENT", "0.50"))
    min_tests = env_int("LAYER2_MIN_TESTS", 2)
    breakout_mode = env_bool("LAYER3_ENABLE_BREAKOUT", True)

    if candidate.state == TRENDING_UP:
        if breakout_mode:
            if resistance is None:
                candidate.layer2_reason = "eliminated: no resistance level for upside breakout"
                return None
            if resistance.tests < min_tests:
                candidate.layer2_reason = f"eliminated: resistance tests {resistance.tests} below {min_tests}"
                return None
            if candidate.resistance_distance_percent is None or candidate.resistance_distance_percent > max_distance:
                candidate.layer2_reason = f"eliminated: resistance distance {candidate.resistance_distance_percent:.4f}% above {max_distance:.4f}%"
                return None
            candidate.layer2_reason = "passed: buy breakout candidate near tested resistance"
            return candidate
        if support is None:
            candidate.layer2_reason = "eliminated: no support level"
            return None
        if support.tests < min_tests:
            candidate.layer2_reason = f"eliminated: support tests {support.tests} below {min_tests}"
            return None
        if candidate.support_distance_percent is None or candidate.support_distance_percent > max_distance:
            candidate.layer2_reason = f"eliminated: support distance {candidate.support_distance_percent:.4f}% above {max_distance:.4f}%"
            return None
        candidate.layer2_reason = "passed: buy candidate near tested support"
        return candidate

    if candidate.state == TRENDING_DOWN:
        if breakout_mode:
            if support is None:
                candidate.layer2_reason = "eliminated: no support level for downside breakout"
                return None
            if support.tests < min_tests:
                candidate.layer2_reason = f"eliminated: support tests {support.tests} below {min_tests}"
                return None
            if candidate.support_distance_percent is None or candidate.support_distance_percent > max_distance:
                candidate.layer2_reason = f"eliminated: support distance {candidate.support_distance_percent:.4f}% above {max_distance:.4f}%"
                return None
            candidate.layer2_reason = "passed: sell breakout candidate near tested support"
            return candidate
        if resistance is None:
            candidate.layer2_reason = "eliminated: no resistance level"
            return None
        if resistance.tests < min_tests:
            candidate.layer2_reason = f"eliminated: resistance tests {resistance.tests} below {min_tests}"
            return None
        if candidate.resistance_distance_percent is None or candidate.resistance_distance_percent > max_distance:
            candidate.layer2_reason = f"eliminated: resistance distance {candidate.resistance_distance_percent:.4f}% above {max_distance:.4f}%"
            return None
        candidate.layer2_reason = "passed: sell candidate near tested resistance"
        return candidate

    candidate.layer2_reason = "eliminated: ranging after Layer 1"
    return None


def breakout_frame(symbol: str) -> pd.DataFrame:
    timeframe = mt5_timeframe(os.getenv("LAYER3_TIMEFRAME", "M5"))
    if timeframe is None:
        return pd.DataFrame()
    count = env_int("LAYER3_LOOKBACK_CANDLES", 80)
    return mt5_rates(symbol, timeframe, count)


def bookmap_confirms(symbol: str, side: str) -> tuple[bool, str]:
    if not env_bool("LAYER3_USE_BOOKMAP", False):
        return True, "Bookmap disabled"
    path = Path(os.getenv("BOOKMAP_ORDERFLOW_PATH", "data/bookmap_orderflow.json"))
    max_age = env_float("BOOKMAP_MAX_SIGNAL_AGE_SECONDS", 5.0)
    min_imbalance = env_float("BOOKMAP_MIN_IMBALANCE_RATIO", 1.20)
    min_delta = env_float("BOOKMAP_MIN_TRADE_DELTA", 0.0)
    if not path.exists():
        return False, f"Bookmap signal file not found: {path}"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"Bookmap signal read error: {exc}"

    normalized = normalize_symbol(symbol)
    signal = payload.get(normalized) or payload.get(symbol) or {}
    if not signal:
        return False, f"Bookmap has no signal for {symbol}"

    timestamp = float(signal.get("timestamp", 0.0) or 0.0)
    age = time.time() - timestamp
    if timestamp <= 0 or age > max_age:
        return False, f"Bookmap signal stale: {age:.2f}s old"

    bid_depth = float(signal.get("bid_depth", 0.0) or 0.0)
    ask_depth = float(signal.get("ask_depth", 0.0) or 0.0)
    trade_delta = float(signal.get("trade_delta", 0.0) or 0.0)
    bid_ask_ratio = bid_depth / max(ask_depth, 1e-9)
    ask_bid_ratio = ask_depth / max(bid_depth, 1e-9)

    if side == "BUY":
        confirmed = bid_ask_ratio >= min_imbalance and trade_delta >= min_delta
        reason = f"bid/ask={bid_ask_ratio:.2f}, delta={trade_delta:.2f}"
    else:
        confirmed = ask_bid_ratio >= min_imbalance and trade_delta <= -min_delta
        reason = f"ask/bid={ask_bid_ratio:.2f}, delta={trade_delta:.2f}"

    if not confirmed:
        return False, f"Bookmap rejected {side}: {reason}"
    return True, f"Bookmap confirmed {side}: {reason}"


def mt5_tick_orderflow_confirms(symbol: str, side: str) -> tuple[bool, str]:
    if not env_bool("LAYER3_USE_MT5_TICK_CONFIRMATION", True):
        return True, "MT5 tick confirmation disabled"
    if mt5 is None:
        return False, "MT5 unavailable for tick confirmation"

    lookback_seconds = env_int("LAYER3_TICK_LOOKBACK_SECONDS", 60)
    max_ticks = env_int("LAYER3_MAX_TICKS", 2000)
    min_ticks = env_int("LAYER3_MIN_TICKS", 20)
    min_imbalance = env_float("LAYER3_MIN_TICK_IMBALANCE", 1.10)
    max_spread_percent = env_float("LAYER3_MAX_SPREAD_PERCENT", 0.05)

    mt5.symbol_select(symbol, True)
    start = datetime.now(UTC) - timedelta(seconds=lookback_seconds)
    ticks = mt5.copy_ticks_from(symbol, start, max_ticks, mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) < min_ticks:
        return False, f"only {0 if ticks is None else len(ticks)} ticks; need {min_ticks}"

    frame = pd.DataFrame(ticks)
    if "bid" not in frame or "ask" not in frame:
        return False, "tick data missing bid/ask"

    frame = frame[(frame["bid"] > 0) & (frame["ask"] > 0)].copy()
    if len(frame) < min_ticks:
        return False, f"only {len(frame)} valid bid/ask ticks; need {min_ticks}"

    frame["mid"] = (frame["bid"] + frame["ask"]) / 2.0
    frame["spread_percent"] = (frame["ask"] - frame["bid"]) / frame["mid"] * 100.0
    latest_spread = float(frame.iloc[-1]["spread_percent"])
    if latest_spread > max_spread_percent:
        return False, f"spread {latest_spread:.4f}% above {max_spread_percent:.4f}%"

    diffs = frame["mid"].diff().dropna()
    up_ticks = int((diffs > 0).sum())
    down_ticks = int((diffs < 0).sum())
    first_mid = float(frame.iloc[0]["mid"])
    last_mid = float(frame.iloc[-1]["mid"])

    if side == "BUY":
        ratio = up_ticks / max(down_ticks, 1)
        confirmed = ratio >= min_imbalance and last_mid > first_mid
        reason = f"up/down={ratio:.2f}, mid {first_mid:.8f}->{last_mid:.8f}, spread={latest_spread:.4f}%"
    else:
        ratio = down_ticks / max(up_ticks, 1)
        confirmed = ratio >= min_imbalance and last_mid < first_mid
        reason = f"down/up={ratio:.2f}, mid {first_mid:.8f}->{last_mid:.8f}, spread={latest_spread:.4f}%"

    if not confirmed:
        return False, f"MT5 tick-flow rejected {side}: {reason}"
    return True, f"MT5 tick-flow confirmed {side}: {reason}"


def orderflow_confirms(symbol: str, side: str) -> tuple[bool, str]:
    if env_bool("LAYER3_USE_BOOKMAP", False):
        return bookmap_confirms(symbol, side)
    return mt5_tick_orderflow_confirms(symbol, side)


def apply_layer3(candidate: Candidate) -> Candidate | None:
    if not env_bool("LAYER3_ENABLE_BREAKOUT", True):
        candidate.layer3_state = "NOT_CHECKED"
        candidate.layer3_reason = "Layer 3 disabled"
        return candidate

    frame = breakout_frame(candidate.symbol)
    min_bars = max(env_int("LAYER3_AVG_CANDLE_PERIOD", 20) + 5, 30)
    if len(frame) < min_bars:
        candidate.layer3_state = NO_BREAKOUT
        candidate.layer3_reason = f"eliminated: only {len(frame)} candles for breakout detection"
        return None

    frame = frame.sort_values("time").reset_index(drop=True)
    closed = frame.iloc[:-1].copy()
    if len(closed) < min_bars:
        candidate.layer3_state = NO_BREAKOUT
        candidate.layer3_reason = "eliminated: not enough closed candles"
        return None

    avg_period = env_int("LAYER3_AVG_CANDLE_PERIOD", 20)
    last = closed.iloc[-1]
    previous = closed.iloc[-2]
    history = closed.iloc[-(avg_period + 1):-1]
    avg_volume = max(float(history["volume"].mean()), 1.0)
    avg_range = max(float((history["high"] - history["low"]).mean()), 1e-12)
    volume_ratio = float(last["volume"]) / avg_volume
    candle_ratio = float(last["high"] - last["low"]) / avg_range

    min_volume_ratio = env_float("LAYER3_MIN_VOLUME_RATIO", 1.20)
    min_candle_ratio = env_float("LAYER3_MIN_CANDLE_RANGE_RATIO", 1.20)
    max_breakout_distance = env_float("LAYER3_MAX_BREAKOUT_DISTANCE_PERCENT", 0.30)
    recent_candles = env_int("LAYER3_RECENT_BREAKOUT_CANDLES", 5)
    pullback_distance = env_float("LAYER3_PULLBACK_DISTANCE_PERCENT", 0.15)

    level = None
    side = ""
    if candidate.state == TRENDING_UP:
        level = candidate.nearest_resistance
        side = "BUY"
    elif candidate.state == TRENDING_DOWN:
        level = candidate.nearest_support
        side = "SELL"

    if level is None or level <= 0:
        candidate.layer3_state = NO_BREAKOUT
        candidate.layer3_reason = "eliminated: no breakout level from Layer 2"
        return None

    last_close = float(last["close"])
    previous_close = float(previous["close"])
    candidate.breakout_level = level
    candidate.breakout_side = side
    candidate.breakout_volume_ratio = volume_ratio
    candidate.breakout_candle_ratio = candle_ratio
    candidate.breakout_distance_percent = distance_percent(level, last_close)

    if side == "BUY":
        closed_beyond = last_close > level
        just_broke = previous_close <= level and closed_beyond
        recent_break = (closed.tail(recent_candles)["close"] > level).any()
        pulled_back = candidate.breakout_distance_percent is not None and candidate.breakout_distance_percent <= pullback_distance
    else:
        closed_beyond = last_close < level
        just_broke = previous_close >= level and closed_beyond
        recent_break = (closed.tail(recent_candles)["close"] < level).any()
        pulled_back = candidate.breakout_distance_percent is not None and candidate.breakout_distance_percent <= pullback_distance

    if not closed_beyond:
        candidate.layer3_state = NO_BREAKOUT
        candidate.layer3_reason = "eliminated: price did not close beyond breakout level"
        if env_bool("AI_ALLOW_LAYER3_FLEXIBLE_WAIT", True) and ai_enabled():
            near_distance = env_float("AI_LAYER3_NEAR_BREAKOUT_DISTANCE_PERCENT", 0.08)
            if candidate.breakout_distance_percent is not None and candidate.breakout_distance_percent <= near_distance:
                ai_result = evaluate_candle_pattern(
                    {
                        "task": "near_breakout_flexibility_check",
                        "symbol": candidate.symbol,
                        "side": side,
                        "layer1_state": candidate.state,
                        "breakout_level": level,
                        "last_close": last_close,
                        "previous_close": previous_close,
                        "breakout_distance_percent": candidate.breakout_distance_percent,
                        "volume_ratio": volume_ratio,
                        "candle_ratio": candle_ratio,
                        "recent_candles": candle_rows_for_ai(closed, 8),
                    }
                )
                candidate.ai_decision = ai_result.decision
                candidate.ai_score = ai_result.score
                candidate.ai_reason = ai_result.reason
                candidate.ai_pattern = ai_result.pattern
                if ai_result.decision == "CONFIRM" and ai_result.score >= env_float("AI_LAYER3_MIN_FLEX_SCORE", 75.0):
                    candidate.layer3_state = WAIT
                    candidate.layer3_reason = f"AI flexible WAIT near breakout: {ai_result.reason}"
                    return candidate
        return None

    if recent_break and not just_broke:
        candidate.layer3_state = WAIT
        if pulled_back:
            candidate.layer3_reason = "recent breakout pulled back near the level; waiting for scalper timing"
        else:
            candidate.layer3_reason = "recent breakout already happened; waiting for pullback/retest"
        return candidate

    blockers = []
    if not just_broke:
        blockers.append("not a fresh breakout")
    if volume_ratio < min_volume_ratio:
        blockers.append(f"volume ratio {volume_ratio:.2f} below {min_volume_ratio:.2f}")
    if candle_ratio < min_candle_ratio:
        blockers.append(f"candle range ratio {candle_ratio:.2f} below {min_candle_ratio:.2f}")
    if candidate.breakout_distance_percent is None or candidate.breakout_distance_percent > max_breakout_distance:
        blockers.append(f"breakout distance {candidate.breakout_distance_percent:.4f}% above {max_breakout_distance:.4f}%")

    orderflow_ok, orderflow_reason = orderflow_confirms(candidate.symbol, side)
    if not orderflow_ok:
        blockers.append(orderflow_reason)

    if blockers:
        candidate.layer3_state = WAIT if recent_break else NO_BREAKOUT
        candidate.layer3_reason = "blocked: " + "; ".join(blockers)
        return candidate if candidate.layer3_state == WAIT else None

    candidate.layer3_state = HIGH_PRIORITY
    candidate.layer3_reason = f"fresh {side} breakout confirmed; {orderflow_reason}"
    return candidate


def pullback_frame(symbol: str) -> pd.DataFrame:
    timeframe = mt5_timeframe(os.getenv("LAYER4_TIMEFRAME", "M5"))
    if timeframe is None:
        return pd.DataFrame()
    count = env_int("LAYER4_LOOKBACK_CANDLES", 120)
    return mt5_rates(symbol, timeframe, count)


def recent_swing_range(frame: pd.DataFrame, side: str) -> tuple[float, float] | None:
    swing_lookback = env_int("LAYER4_SWING_LOOKBACK_CANDLES", 60)
    data = frame.tail(swing_lookback).copy()
    if len(data) < 20:
        return None
    swing_high = float(data["high"].max())
    swing_low = float(data["low"].min())
    if swing_high <= swing_low:
        return None
    return swing_low, swing_high


def fibonacci_levels(frame: pd.DataFrame, side: str) -> list[float]:
    swing = recent_swing_range(frame, side)
    if swing is None:
        return []
    swing_low, swing_high = swing
    move = swing_high - swing_low
    if side == "BUY":
        return [swing_high - move * ratio for ratio in (0.382, 0.5, 0.618)]
    return [swing_low + move * ratio for ratio in (0.382, 0.5, 0.618)]


def candle_body(row: pd.Series) -> float:
    return abs(float(row["close"]) - float(row["open"]))


def candle_range(row: pd.Series) -> float:
    return max(float(row["high"]) - float(row["low"]), 1e-12)


def bullish_confirmation_pattern(previous: pd.Series, current: pd.Series) -> str:
    body = candle_body(current)
    total = candle_range(current)
    lower_wick = min(float(current["open"]), float(current["close"])) - float(current["low"])
    upper_wick = float(current["high"]) - max(float(current["open"]), float(current["close"]))
    prev_bearish = float(previous["close"]) < float(previous["open"])
    curr_bullish = float(current["close"]) > float(current["open"])
    engulfing = (
        prev_bearish
        and curr_bullish
        and float(current["open"]) <= float(previous["close"])
        and float(current["close"]) >= float(previous["open"])
    )
    hammer = curr_bullish and lower_wick >= body * 2.0 and upper_wick <= body * 0.75
    pin_bar = lower_wick / total >= 0.55 and float(current["close"]) > float(current["open"])
    if engulfing:
        return "bullish_engulfing"
    if hammer:
        return "hammer"
    if pin_bar:
        return "bullish_pin_bar"
    return ""


def bearish_confirmation_pattern(previous: pd.Series, current: pd.Series) -> str:
    body = candle_body(current)
    total = candle_range(current)
    upper_wick = float(current["high"]) - max(float(current["open"]), float(current["close"]))
    lower_wick = min(float(current["open"]), float(current["close"])) - float(current["low"])
    prev_bullish = float(previous["close"]) > float(previous["open"])
    curr_bearish = float(current["close"]) < float(current["open"])
    engulfing = (
        prev_bullish
        and curr_bearish
        and float(current["open"]) >= float(previous["close"])
        and float(current["close"]) <= float(previous["open"])
    )
    shooting_star = curr_bearish and upper_wick >= body * 2.0 and lower_wick <= body * 0.75
    pin_bar = upper_wick / total >= 0.55 and float(current["close"]) < float(current["open"])
    if engulfing:
        return "bearish_engulfing"
    if shooting_star:
        return "shooting_star"
    if pin_bar:
        return "bearish_pin_bar"
    return ""


def nearest_price_level(current_price: float, levels: list[float]) -> tuple[float | None, float | None]:
    if current_price <= 0 or not levels:
        return None, None
    level = min(levels, key=lambda item: abs(item - current_price))
    return level, abs(current_price - level) / current_price * 100.0


def candle_rows_for_ai(frame: pd.DataFrame, count: int = 12) -> list[dict[str, float | str]]:
    rows = []
    for _, row in frame.tail(count).iterrows():
        rows.append(
            {
                "time": str(row.get("time", "")),
                "open": round(float(row["open"]), 8),
                "high": round(float(row["high"]), 8),
                "low": round(float(row["low"]), 8),
                "close": round(float(row["close"]), 8),
                "volume": round(float(row.get("volume", 0.0)), 2),
            }
        )
    return rows


def pullback_volume_declining(frame: pd.DataFrame) -> bool:
    window = env_int("LAYER4_PULLBACK_VOLUME_WINDOW", 5)
    if len(frame) < window + 5:
        return False
    recent = frame.tail(window)
    previous = frame.iloc[-(window * 2):-window]
    if previous.empty:
        return False
    return float(recent["volume"].mean()) < float(previous["volume"].mean())


def apply_layer4(candidate: Candidate) -> Candidate | None:
    if not env_bool("LAYER4_ENABLE_PULLBACK", True):
        candidate.layer4_state = "NOT_CHECKED"
        candidate.layer4_reason = "Layer 4 disabled"
        return candidate

    if candidate.layer3_state not in {HIGH_PRIORITY, WAIT}:
        candidate.layer4_state = PULLBACK_WAIT
        candidate.layer4_reason = "waiting: Layer 3 did not produce a breakout candidate"
        return None

    side = candidate.breakout_side or ("BUY" if candidate.state == TRENDING_UP else "SELL")
    frame = pullback_frame(candidate.symbol)
    min_bars = env_int("LAYER4_MIN_LOOKBACK_CANDLES", 60)
    if len(frame) < min_bars:
        candidate.layer4_state = PULLBACK_WAIT
        candidate.layer4_reason = f"waiting: only {len(frame)} candles for pullback confirmation"
        return None

    frame = frame.sort_values("time").reset_index(drop=True)
    closed = frame.iloc[:-1].copy()
    if len(closed) < min_bars:
        candidate.layer4_state = PULLBACK_WAIT
        candidate.layer4_reason = "waiting: not enough closed candles for pullback confirmation"
        return None

    data = closed.copy()
    data["ema21"] = ema(data["close"], 21)
    data["ema50"] = ema(data["close"], 50)
    previous = data.iloc[-2]
    current = data.iloc[-1]
    current_close = float(current["close"])
    max_distance = env_float("LAYER4_MAX_PULLBACK_DISTANCE_PERCENT", 0.20)

    fibs = fibonacci_levels(data, side)
    fib_level, fib_distance = nearest_price_level(current_close, fibs)
    ema_levels = [float(current["ema21"]), float(current["ema50"])]
    ema_level, ema_distance = nearest_price_level(current_close, [level for level in ema_levels if not pd.isna(level)])

    breakout_level = candidate.breakout_level
    breakout_distance = distance_percent(breakout_level, current_close)

    valid_levels: list[tuple[str, float, float]] = []
    if ema_level is not None and ema_distance is not None and ema_distance <= max_distance:
        valid_levels.append(("EMA", ema_level, ema_distance))
    if breakout_level is not None and breakout_distance is not None and breakout_distance <= max_distance:
        valid_levels.append(("breakout_retest", breakout_level, breakout_distance))
    if fib_level is not None and fib_distance is not None and fib_distance <= max_distance:
        valid_levels.append(("fibonacci", fib_level, fib_distance))

    if side == "BUY":
        pattern = bullish_confirmation_pattern(previous, current)
        direction_ok = current_close > float(current["open"])
    else:
        pattern = bearish_confirmation_pattern(previous, current)
        direction_ok = current_close < float(current["open"])

    volume_ok = pullback_volume_declining(data)
    ai_min_score = env_float("GEMINI_MIN_CANDLE_SCORE", 70.0)
    ai_can_relax = env_bool("GEMINI_ALLOW_FLEXIBLE_FUNNEL", True)
    if ai_enabled():
        ai_context = {
            "symbol": candidate.symbol,
            "side": side,
            "layer1_state": candidate.state,
            "layer2_reason": candidate.layer2_reason,
            "layer3_state": candidate.layer3_state,
            "layer3_reason": candidate.layer3_reason,
            "breakout_level": candidate.breakout_level,
            "current_close": current_close,
            "near_levels": valid_levels,
            "nearest_fib": fib_level,
            "fib_distance_percent": fib_distance,
            "volume_declining": volume_ok,
            "rule_based_pattern": pattern,
            "recent_candles": candle_rows_for_ai(data),
        }
        ai_result = evaluate_candle_pattern(ai_context)
        candidate.ai_decision = ai_result.decision
        candidate.ai_score = ai_result.score
        candidate.ai_reason = ai_result.reason
        candidate.ai_pattern = ai_result.pattern
        if ai_result.decision == "CONFIRM" and ai_result.score >= ai_min_score:
            pattern = ai_result.pattern or pattern or "gemini_confirmation"
            if ai_can_relax and not direction_ok:
                candidate.ai_reason = f"{candidate.ai_reason}; AI support noted but candle direction still failed"
        elif ai_result.decision in {"REJECT", "ERROR"}:
            direction_ok = False

    blockers = []
    if not valid_levels:
        blockers.append(f"not near EMA, breakout retest, or Fibonacci within {max_distance:.2f}%")
    if not pattern or not direction_ok:
        blockers.append("no confirmation candle")
    if not volume_ok:
        blockers.append("pullback volume not declining")

    if blockers:
        candidate.layer4_state = PULLBACK_WAIT
        candidate.layer4_reason = "waiting: " + "; ".join(blockers)
        return candidate if env_bool("LAYER4_KEEP_WAIT_CANDIDATES", True) else None

    chosen_kind, chosen_level, chosen_distance = sorted(valid_levels, key=lambda item: item[2])[0]
    candidate.layer4_state = PULLBACK_CONFIRMED
    candidate.layer4_reason = f"confirmed {side} pullback at {chosen_kind} with {pattern} and declining volume"
    candidate.pullback_level = chosen_level
    candidate.pullback_distance_percent = chosen_distance
    candidate.fib_level = fib_level
    candidate.confirmation_pattern = pattern
    return candidate


def pip_size(symbol: str) -> float:
    normalized = normalize_symbol(symbol)
    if normalized.endswith("JPY"):
        return 0.01
    if len(normalized) == 6 and normalized.isalpha():
        return 0.0001
    return 0.01


def range_frame(symbol: str) -> pd.DataFrame:
    timeframe = mt5_timeframe(os.getenv("LAYER5_TIMEFRAME", "M15"))
    if timeframe is None:
        return pd.DataFrame()
    count = env_int("LAYER5_LOOKBACK_CANDLES", 120)
    return mt5_rates(symbol, timeframe, count)


def apply_layer5(candidate: Candidate) -> Candidate | None:
    if not env_bool("LAYER5_ENABLE_RANGE_FILTER", True):
        candidate.layer5_state = "NOT_CHECKED"
        candidate.layer5_reason = "Layer 5 disabled"
        return None

    if candidate.state != RANGING:
        return None

    frame = range_frame(candidate.symbol)
    min_bars = env_int("LAYER5_MIN_LOOKBACK_CANDLES", 80)
    if len(frame) < min_bars:
        candidate.layer5_reason = f"eliminated: only {len(frame)} candles for range detection"
        return None

    data = frame.sort_values("time").reset_index(drop=True).copy()
    closed = data.iloc[:-1].copy()
    if len(closed) < min_bars:
        candidate.layer5_reason = "eliminated: not enough closed candles for range detection"
        return None

    lookback = env_int("LAYER5_LOOKBACK_CANDLES", 120)
    recent = closed.tail(lookback).copy()
    current = float(recent.iloc[-1]["close"])
    support_levels, resistance_levels = swing_levels(recent, candidate.symbol)
    min_touches = env_int("LAYER5_MIN_TOUCHES_PER_SIDE", 2)
    support_candidates = [level for level in support_levels if level.price < current and level.tests >= min_touches]
    resistance_candidates = [level for level in resistance_levels if level.price > current and level.tests >= min_touches]
    if not support_candidates or not resistance_candidates:
        candidate.layer5_reason = "eliminated: no tested swing support/resistance range boundaries"
        return None
    support_level = max(support_candidates, key=lambda level: level.price)
    resistance_level = min(resistance_candidates, key=lambda level: level.price)
    support = float(support_level.price)
    resistance = float(resistance_level.price)
    width = resistance - support
    if current <= 0 or width <= 0:
        candidate.layer5_reason = "eliminated: invalid range width"
        return None

    max_spread_percent = env_float("LAYER5_MAX_SPREAD_PERCENT", 0.05)
    min_range_spread_ratio = env_float("LAYER5_MIN_RANGE_SPREAD_RATIO", 8.0)
    if mt5 is not None:
        tick = mt5.symbol_info_tick(candidate.symbol)
        if tick is not None and tick.bid > 0 and tick.ask > 0:
            spread = float(tick.ask - tick.bid)
            mid = float((tick.ask + tick.bid) / 2.0)
            spread_percent = spread / mid * 100.0
            if spread_percent > max_spread_percent:
                candidate.layer5_reason = f"eliminated: spread {spread_percent:.4f}% above {max_spread_percent:.4f}%"
                return None
            if width / max(spread, 1e-12) < min_range_spread_ratio:
                candidate.layer5_reason = f"eliminated: range/spread ratio below {min_range_spread_ratio:.1f}"
                return None

    width_pips = width / pip_size(candidate.symbol)
    min_width_pips = env_float("LAYER5_MIN_RANGE_WIDTH_PIPS", 30.0)
    if width_pips < min_width_pips:
        candidate.layer5_reason = f"eliminated: range width {width_pips:.1f} pips below {min_width_pips:.1f}"
        return None

    period = env_int("LAYER5_ATR_PERIOD", 14)
    recent["atr"] = atr(recent, period)
    current_atr = float(recent["atr"].iloc[-1]) if not pd.isna(recent["atr"].iloc[-1]) else 0.0
    width_atr = width / max(current_atr, 1e-12)
    min_width_atr = env_float("LAYER5_MIN_RANGE_WIDTH_ATR", 2.0)
    if width_atr < min_width_atr:
        candidate.layer5_reason = f"eliminated: range width {width_atr:.2f} ATR below {min_width_atr:.2f}"
        return None

    touch_tolerance = width * env_float("LAYER5_TOUCH_TOLERANCE_FRACTION", 0.12)
    support_touches = int((recent["low"] <= support + touch_tolerance).sum())
    resistance_touches = int((recent["high"] >= resistance - touch_tolerance).sum())
    if support_touches < min_touches or resistance_touches < min_touches:
        candidate.layer5_reason = (
            f"eliminated: touches support={support_touches}, resistance={resistance_touches}, need {min_touches}+ each"
        )
        return None

    near_fraction = env_float("LAYER5_ENTRY_ZONE_FRACTION", 0.20)
    support_zone_top = support + width * near_fraction
    resistance_zone_bottom = resistance - width * near_fraction

    candidate.range_support = support
    candidate.range_resistance = resistance
    candidate.range_width_pips = width_pips
    candidate.range_width_atr = width_atr
    candidate.range_support_touches = support_touches
    candidate.range_resistance_touches = resistance_touches

    if current <= support_zone_top:
        candidate.layer5_state = RANGE_BUY
        candidate.layer5_reason = "confirmed clean range: scalp buy near range support"
        return candidate

    if current >= resistance_zone_bottom:
        candidate.layer5_state = RANGE_SELL
        candidate.layer5_reason = "confirmed clean range: scalp sell near range resistance"
        return candidate

    candidate.layer5_reason = "eliminated: clean range but price is not near either boundary"
    return None


def price_action_frame(symbol: str) -> pd.DataFrame:
    timeframe = mt5_timeframe(os.getenv("LAYER6_TIMEFRAME", "M5"))
    if timeframe is None:
        return pd.DataFrame()
    count = env_int("LAYER6_LOOKBACK_CANDLES", 40)
    return mt5_rates(symbol, timeframe, count)


def morning_star(first: pd.Series, second: pd.Series, third: pd.Series) -> bool:
    first_body = candle_body(first)
    second_body = candle_body(second)
    first_bearish = float(first["close"]) < float(first["open"])
    third_bullish = float(third["close"]) > float(third["open"])
    midpoint = (float(first["open"]) + float(first["close"])) / 2.0
    return first_bearish and second_body < first_body * 0.45 and third_bullish and float(third["close"]) > midpoint


def evening_star(first: pd.Series, second: pd.Series, third: pd.Series) -> bool:
    first_body = candle_body(first)
    second_body = candle_body(second)
    first_bullish = float(first["close"]) > float(first["open"])
    third_bearish = float(third["close"]) < float(third["open"])
    midpoint = (float(first["open"]) + float(first["close"])) / 2.0
    return first_bullish and second_body < first_body * 0.45 and third_bearish and float(third["close"]) < midpoint


def layer6_expected_side(candidate: Candidate) -> str:
    if candidate.layer5_state == RANGE_BUY:
        return "BUY"
    if candidate.layer5_state == RANGE_SELL:
        return "SELL"
    if candidate.breakout_side:
        return candidate.breakout_side
    if candidate.state == TRENDING_UP:
        return "BUY"
    if candidate.state == TRENDING_DOWN:
        return "SELL"
    return ""


def key_level_distance_for_side(candidate: Candidate, side: str, price: float) -> float | None:
    levels: list[float] = []
    if side == "BUY":
        levels.extend(level for level in [candidate.nearest_support, candidate.breakout_level, candidate.pullback_level, candidate.range_support] if level)
    elif side == "SELL":
        levels.extend(level for level in [candidate.nearest_resistance, candidate.breakout_level, candidate.pullback_level, candidate.range_resistance] if level)
    _, distance = nearest_price_level(price, levels)
    return distance


def apply_layer6(candidate: Candidate) -> Candidate:
    if not env_bool("LAYER6_ENABLE_PRICE_ACTION", True):
        candidate.layer6_reason = "Layer 6 disabled"
        candidate.layer6_score = 50.0
        return candidate

    frame = price_action_frame(candidate.symbol)
    if len(frame) < 10:
        candidate.layer6_score = 0.0
        candidate.layer6_reason = f"only {len(frame)} candles for price action"
        return candidate

    closed = frame.sort_values("time").reset_index(drop=True).iloc[:-1].copy()
    if len(closed) < 4:
        candidate.layer6_score = 0.0
        candidate.layer6_reason = "not enough closed candles for price action"
        return candidate

    side = layer6_expected_side(candidate)
    c1, c2, c3 = closed.iloc[-3], closed.iloc[-2], closed.iloc[-1]
    current_close = float(c3["close"])
    bullish = bullish_confirmation_pattern(c2, c3)
    bearish = bearish_confirmation_pattern(c2, c3)
    if morning_star(c1, c2, c3):
        bullish = "morning_star"
    if evening_star(c1, c2, c3):
        bearish = "evening_star"

    pattern = bullish if side == "BUY" else bearish if side == "SELL" else ""
    opposite = bearish if side == "BUY" else bullish if side == "SELL" else ""
    score = 50.0
    reasons = []
    if pattern:
        score += 30.0
        reasons.append(f"{pattern} agrees with {side}")
    if opposite:
        score -= 35.0
        reasons.append(f"{opposite} contradicts {side}")

    level_distance = key_level_distance_for_side(candidate, side, current_close)
    max_level_distance = env_float("LAYER6_MAX_KEY_LEVEL_DISTANCE_PERCENT", 0.25)
    if level_distance is not None and level_distance <= max_level_distance:
        score += 15.0
        reasons.append(f"formed near key level ({level_distance:.4f}%)")
    else:
        score -= 10.0
        reasons.append("not close enough to key level")

    if (side == "BUY" and candidate.state == TRENDING_UP) or (side == "SELL" and candidate.state == TRENDING_DOWN):
        score += 10.0
        reasons.append("agrees with Layer 1 trend")
    elif candidate.state in {TRENDING_UP, TRENDING_DOWN}:
        score -= 20.0
        reasons.append("contradicts Layer 1 trend")

    if ai_enabled() and env_bool("AI_USE_FOR_LAYER6", True):
        ai_context = {
            "symbol": candidate.symbol,
            "side": side,
            "layer1_state": candidate.state,
            "layer2_reason": candidate.layer2_reason,
            "layer3_state": candidate.layer3_state,
            "layer4_state": candidate.layer4_state,
            "layer5_state": candidate.layer5_state,
            "rule_pattern": pattern,
            "opposite_pattern": opposite,
            "key_level_distance_percent": level_distance,
            "recent_candles": candle_rows_for_ai(closed, 8),
        }
        ai_result = evaluate_candle_pattern(ai_context)
        candidate.ai_decision = ai_result.decision
        candidate.ai_score = ai_result.score
        candidate.ai_reason = ai_result.reason
        candidate.ai_pattern = ai_result.pattern
        if ai_result.decision == "CONFIRM":
            if pattern:
                score = max(score, min(ai_result.score, score + env_float("AI_LAYER6_MAX_SCORE_BOOST", 10.0)))
            pattern = ai_result.pattern or pattern
            reasons.append(f"Gemini confirms: {ai_result.reason}")
        elif ai_result.decision == "REJECT":
            score = min(score, ai_result.score)
            reasons.append(f"Gemini rejects: {ai_result.reason}")

    candidate.layer6_score = max(0.0, min(100.0, score))
    candidate.layer6_pattern = pattern or "none"
    candidate.layer6_reason = "; ".join(reasons) if reasons else "neutral candles"
    return candidate


def apply_layer7(candidate: Candidate) -> Candidate:
    if not env_bool("LAYER7_ENABLE_FUNDAMENTALS", False):
        candidate.layer7_bias = "NEUTRAL"
        candidate.layer7_score = 50.0
        candidate.layer7_reason = "Fundamental layer disabled"
        return candidate
    side = candidate.breakout_side or layer6_expected_side(candidate)
    try:
        result = evaluate_fundamentals(candidate.symbol, side)
    except Exception as exc:
        candidate.layer7_bias = "NEUTRAL"
        candidate.layer7_score = 50.0
        candidate.layer7_reason = f"Fundamental layer error: {exc}"
        return candidate

    use_ai_for_layer7 = ai_enabled() and env_bool("AI_USE_FOR_LAYER7", True)
    force_ai_layer7 = False
    if use_ai_for_layer7:
        pair = split_forex_symbol(candidate.symbol)
        majors = {"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"}
        if env_bool("AI_LAYER7_FORCE_FOR_EXOTICS", True) and pair is not None and (pair[0] not in majors or pair[1] not in majors):
            force_ai_layer7 = True
        elif env_bool("AI_LAYER7_FORCE_FOR_WEAK_EVIDENCE", True) and pair is not None and result.score == 50.0:
            force_ai_layer7 = True
    if use_ai_for_layer7:
        min_ai_score = env_float("AI_LAYER7_MIN_PRE_AI_SCORE", 60.0)
        if result.score < min_ai_score and env_bool("AI_LAYER7_SKIP_WEAK_CANDIDATES", True) and not force_ai_layer7:
            use_ai_for_layer7 = False
    if use_ai_for_layer7:
        ai_result = evaluate_fundamental_bias(
            {
                "symbol": candidate.symbol,
                "side": side,
                "computed_bias": result.bias,
                "computed_score": result.score,
                "computed_reason": result.reason,
                "evidence": result.evidence,
            }
        )
        if ai_result.enabled and ai_result.decision not in {"ERROR", ""}:
            result.bias = ai_result.decision if ai_result.decision in {"BULLISH", "BEARISH", "NEUTRAL"} else result.bias
            result.score = ai_result.score
            result.reason = f"AI+API: {ai_result.reason}"
            candidate.ai_decision = ai_result.decision
            candidate.ai_score = ai_result.score
            candidate.ai_reason = ai_result.reason
            candidate.ai_pattern = ai_result.pattern

    candidate.layer7_bias = result.bias
    candidate.layer7_score = result.score
    candidate.layer7_reason = result.reason
    return candidate


def apply_layer8(candidate: Candidate) -> Candidate:
    if not env_bool("LAYER8_ENABLE_NEWS_SENTIMENT", False):
        candidate.layer8_risk = "NEUTRAL"
        candidate.layer8_score = 50.0
        candidate.layer8_reason = "News sentiment layer disabled"
        return candidate
    side = candidate.breakout_side or layer6_expected_side(candidate)
    try:
        result = evaluate_news(candidate.symbol, side)
    except Exception as exc:
        candidate.layer8_risk = "NEUTRAL"
        candidate.layer8_score = 50.0
        candidate.layer8_reason = f"News layer error: {exc}"
        return candidate

    candidate.layer8_risk = result.risk
    candidate.layer8_score = result.score
    candidate.layer8_reason = result.reason
    return candidate


def layer_scores(candidate: Candidate) -> dict[str, float]:
    layer1 = 75.0 if candidate.state in {TRENDING_UP, TRENDING_DOWN} else 50.0
    if candidate.agreement > 1:
        layer1 = min(100.0, layer1 + 10.0 * (candidate.agreement - 1))

    distances = [
        value
        for value in [candidate.support_distance_percent, candidate.resistance_distance_percent]
        if value is not None
    ]
    layer2 = 0.0
    if candidate.layer2_reason.startswith("passed"):
        nearest = min(distances) if distances else 0.5
        layer2 = max(50.0, 100.0 - nearest * 100.0)
    elif candidate.layer5_state in {RANGE_BUY, RANGE_SELL}:
        layer2 = 70.0

    layer3 = 0.0
    if candidate.layer3_state == HIGH_PRIORITY:
        layer3 = min(100.0, 55.0 + (candidate.breakout_volume_ratio or 1.0) * 15.0 + (candidate.breakout_candle_ratio or 1.0) * 15.0)
    elif candidate.layer3_state == WAIT:
        layer3 = 55.0
    elif candidate.layer5_state in {RANGE_BUY, RANGE_SELL}:
        layer3 = 0.0

    layer4 = 90.0 if candidate.layer4_state == PULLBACK_CONFIRMED else 55.0 if candidate.layer4_state == PULLBACK_WAIT else 0.0
    if candidate.layer5_state in {RANGE_BUY, RANGE_SELL}:
        layer4 = 0.0

    layer5 = 0.0
    if candidate.layer5_state in {RANGE_BUY, RANGE_SELL}:
        layer5 = min(100.0, 50.0 + min(candidate.range_width_atr or 0.0, 5.0) * 8.0 + min(candidate.range_support_touches + candidate.range_resistance_touches, 10) * 2.0)
    elif candidate.state in {TRENDING_UP, TRENDING_DOWN}:
        layer5 = 50.0

    return {
        "layer1": layer1,
        "layer2": layer2,
        "layer3": layer3,
        "layer4": layer4,
        "layer5": layer5,
        "layer6": candidate.layer6_score,
        "layer7": candidate.layer7_score,
        "layer8": candidate.layer8_score,
    }


def finalize_candidate(candidate: Candidate) -> Candidate | None:
    candidate = apply_layer6(candidate)
    candidate = apply_layer7(candidate)
    candidate = apply_layer8(candidate)
    if candidate.layer8_risk in {"BLOCKED", "AGAINST_TRADE"}:
        candidate.composite_reason = f"blocked by Layer 8 news risk: {candidate.layer8_risk}"
        return None
    scores = layer_scores(candidate)
    candidate.layer1_score = scores["layer1"]
    candidate.layer2_score = scores["layer2"]
    candidate.layer3_score = scores["layer3"]
    candidate.layer4_score = scores["layer4"]
    candidate.layer5_score = scores["layer5"]

    if candidate.layer5_state in {RANGE_BUY, RANGE_SELL}:
        weights = {
            "layer1": env_float("SCORER_RANGE_WEIGHT_LAYER1", 15.0),
            "layer2": env_float("SCORER_RANGE_WEIGHT_LAYER2", 15.0),
            "layer3": 0.0,
            "layer4": 0.0,
            "layer5": env_float("SCORER_RANGE_WEIGHT_LAYER5", 25.0),
            "layer6": env_float("SCORER_RANGE_WEIGHT_LAYER6", 20.0),
            "layer7": env_float("SCORER_RANGE_WEIGHT_LAYER7", 15.0),
            "layer8": env_float("SCORER_RANGE_WEIGHT_LAYER8", 10.0),
        }
    else:
        weights = {
            "layer1": env_float("SCORER_WEIGHT_LAYER1", 20.0),
            "layer2": env_float("SCORER_WEIGHT_LAYER2", 15.0),
            "layer3": env_float("SCORER_WEIGHT_LAYER3", 15.0),
            "layer4": env_float("SCORER_WEIGHT_LAYER4", 15.0),
            "layer5": env_float("SCORER_WEIGHT_LAYER5", 10.0),
            "layer6": env_float("SCORER_WEIGHT_LAYER6", 10.0),
            "layer7": env_float("SCORER_WEIGHT_LAYER7", 10.0),
            "layer8": env_float("SCORER_WEIGHT_LAYER8", 5.0),
        }
    weight_mode = "range" if candidate.layer5_state in {RANGE_BUY, RANGE_SELL} else "trend"
    weights = adaptive_weights_for_mode(weight_mode, weights)
    total_weight = max(sum(weights.values()), 1e-9)
    composite = sum(scores[key] * weights[key] for key in weights) / total_weight
    candidate.composite_score = max(0.0, min(100.0, composite))
    candidate.composite_reason = ", ".join(f"{key}={scores[key]:.1f}" for key in weights)

    threshold = env_float("SCORER_MIN_COMPOSITE_SCORE", 65.0)
    if candidate.composite_score < threshold:
        return None
    spread_percent = current_spread_percent(candidate.symbol)
    candidate.live_spread_percent = spread_percent
    spread_for_penalty = spread_percent if spread_percent is not None else env_float("SCORER_UNKNOWN_SPREAD_PERCENT", 0.03)
    spread_multiplier = env_float("SCORER_SPREAD_COST_MULTIPLIER", 500.0)
    candidate.execution_cost_penalty = max(0.0, spread_for_penalty * spread_multiplier)
    priority_bonus = env_float("SCORER_HIGH_PRIORITY_BONUS", 2.0) if candidate.layer3_state == HIGH_PRIORITY else 0.0
    range_bonus = 0.0
    if candidate.layer5_state in {RANGE_BUY, RANGE_SELL} and candidate.range_width_atr is not None:
        range_bonus = min(env_float("SCORER_RANGE_WIDTH_MAX_BONUS", 3.0), max(0.0, candidate.range_width_atr - 2.0))
    candidate.expected_net_value = candidate.composite_score + priority_bonus + range_bonus - candidate.execution_cost_penalty
    return candidate


def rank_candidates_by_expected_value(candidates: list[Candidate]) -> list[Candidate]:
    return sorted(
        candidates,
        key=lambda item: (
            item.expected_net_value,
            item.composite_score,
            -(item.live_spread_percent if item.live_spread_percent is not None else 999.0),
        ),
        reverse=True,
    )


def write_candidates(candidates: list[Candidate], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "symbol",
                "state",
                "agreement",
                "nearest_support",
                "support_distance_percent",
                "support_tests",
                "nearest_resistance",
                "resistance_distance_percent",
                "resistance_tests",
                "layer2_reason",
                "layer3_state",
                "breakout_side",
                "breakout_level",
                "breakout_distance_percent",
                "breakout_volume_ratio",
                "breakout_candle_ratio",
                "layer3_reason",
                "layer4_state",
                "pullback_level",
                "pullback_distance_percent",
                "fib_level",
                "confirmation_pattern",
                "layer4_reason",
                "layer5_state",
                "range_support",
                "range_resistance",
                "range_width_pips",
                "range_width_atr",
                "range_support_touches",
                "range_resistance_touches",
                "layer5_reason",
                "ai_decision",
                "ai_score",
                "ai_pattern",
                "ai_reason",
                "layer1_score",
                "layer2_score",
                "layer3_score",
                "layer4_score",
                "layer5_score",
                "layer6_score",
                "layer6_pattern",
                "layer6_reason",
                "layer7_bias",
                "layer7_score",
                "layer7_reason",
                "layer8_risk",
                "layer8_score",
                "layer8_reason",
                "composite_score",
                "live_spread_percent",
                "execution_cost_penalty",
                "expected_net_value",
                "composite_reason",
                "sources",
            ]
        )
        for candidate in candidates:
            sources = " | ".join(f"{t.source}:{t.state}:{t.reason}" for t in candidate.sources)
            writer.writerow(
                [
                    candidate.symbol,
                    candidate.state,
                    candidate.agreement,
                    "" if candidate.nearest_support is None else f"{candidate.nearest_support:.8f}",
                    "" if candidate.support_distance_percent is None else f"{candidate.support_distance_percent:.4f}",
                    candidate.support_tests,
                    "" if candidate.nearest_resistance is None else f"{candidate.nearest_resistance:.8f}",
                    "" if candidate.resistance_distance_percent is None else f"{candidate.resistance_distance_percent:.4f}",
                    candidate.resistance_tests,
                    candidate.layer2_reason,
                    candidate.layer3_state,
                    candidate.breakout_side,
                    "" if candidate.breakout_level is None else f"{candidate.breakout_level:.8f}",
                    "" if candidate.breakout_distance_percent is None else f"{candidate.breakout_distance_percent:.4f}",
                    "" if candidate.breakout_volume_ratio is None else f"{candidate.breakout_volume_ratio:.2f}",
                    "" if candidate.breakout_candle_ratio is None else f"{candidate.breakout_candle_ratio:.2f}",
                    candidate.layer3_reason,
                    candidate.layer4_state,
                    "" if candidate.pullback_level is None else f"{candidate.pullback_level:.8f}",
                    "" if candidate.pullback_distance_percent is None else f"{candidate.pullback_distance_percent:.4f}",
                    "" if candidate.fib_level is None else f"{candidate.fib_level:.8f}",
                    candidate.confirmation_pattern,
                    candidate.layer4_reason,
                    candidate.layer5_state,
                    "" if candidate.range_support is None else f"{candidate.range_support:.8f}",
                    "" if candidate.range_resistance is None else f"{candidate.range_resistance:.8f}",
                    "" if candidate.range_width_pips is None else f"{candidate.range_width_pips:.1f}",
                    "" if candidate.range_width_atr is None else f"{candidate.range_width_atr:.2f}",
                    candidate.range_support_touches,
                    candidate.range_resistance_touches,
                    candidate.layer5_reason,
                    candidate.ai_decision,
                    "" if candidate.ai_score is None else f"{candidate.ai_score:.2f}",
                    candidate.ai_pattern,
                    candidate.ai_reason,
                    f"{candidate.layer1_score:.2f}",
                    f"{candidate.layer2_score:.2f}",
                    f"{candidate.layer3_score:.2f}",
                    f"{candidate.layer4_score:.2f}",
                    f"{candidate.layer5_score:.2f}",
                    f"{candidate.layer6_score:.2f}",
                    candidate.layer6_pattern,
                    candidate.layer6_reason,
                    candidate.layer7_bias,
                    f"{candidate.layer7_score:.2f}",
                    candidate.layer7_reason,
                    candidate.layer8_risk,
                    f"{candidate.layer8_score:.2f}",
                    candidate.layer8_reason,
                    f"{candidate.composite_score:.2f}",
                    "" if candidate.live_spread_percent is None else f"{candidate.live_spread_percent:.5f}",
                    f"{candidate.execution_cost_penalty:.2f}",
                    f"{candidate.expected_net_value:.2f}",
                    candidate.composite_reason,
                    sources,
                ]
            )


def validate_env() -> int:
    load_dotenv()
    required = [
        "ALPHA_VANTAGE_API_KEY",
        "MASSIVE_API_KEY",
        "MASSIVE_API_BASE",
        "FUNNEL_USE_MT5",
        "FUNNEL_USE_ALPHA_VANTAGE",
        "FUNNEL_USE_MASSIVE",
        "FUNNEL_LOCAL_PREFILTER_ALL",
        "FUNNEL_MAX_SYMBOLS",
        "FUNNEL_MAX_EXTERNAL_SYMBOLS",
        "FUNNEL_MIN_SOURCE_AGREEMENT",
        "FUNNEL_OUTPUT_PATH",
        "FUNNEL_MT5_TIMEOUT_MS",
        "FUNNEL_TIME_BUDGET_SECONDS",
        "FUNNEL_HTTP_TIMEOUT_SECONDS",
        "FUNNEL_ALPHA_SLEEP_SECONDS",
        "FUNNEL_MIN_REMAINING_FOR_EXTERNAL_SECONDS",
        "LAYER2_USE_TRADINGVIEW_WEBHOOK",
        "LAYER2_TRADINGVIEW_LEVELS_PATH",
        "LAYER2_USE_DUKASCOPY",
        "DUKASCOPY_MAX_HOURS",
        "TRADINGVIEW_WEBHOOK_HOST",
        "TRADINGVIEW_WEBHOOK_PORT",
        "LAYER3_ENABLE_BREAKOUT",
        "LAYER3_TIMEFRAME",
        "LAYER3_LOOKBACK_CANDLES",
        "LAYER3_AVG_CANDLE_PERIOD",
        "LAYER3_MIN_VOLUME_RATIO",
        "LAYER3_MIN_CANDLE_RANGE_RATIO",
        "LAYER3_MAX_BREAKOUT_DISTANCE_PERCENT",
        "LAYER3_RECENT_BREAKOUT_CANDLES",
        "LAYER3_PULLBACK_DISTANCE_PERCENT",
        "LAYER3_USE_BOOKMAP",
        "BOOKMAP_ORDERFLOW_PATH",
        "BOOKMAP_MAX_SIGNAL_AGE_SECONDS",
        "BOOKMAP_MIN_IMBALANCE_RATIO",
        "BOOKMAP_MIN_TRADE_DELTA",
        "BOOKMAP_DEPTH_LEVELS",
        "LAYER3_USE_MT5_TICK_CONFIRMATION",
        "LAYER3_TICK_LOOKBACK_SECONDS",
        "LAYER3_MAX_TICKS",
        "LAYER3_MIN_TICKS",
        "LAYER3_MIN_TICK_IMBALANCE",
        "LAYER3_MAX_SPREAD_PERCENT",
        "LAYER4_ENABLE_PULLBACK",
        "LAYER4_TIMEFRAME",
        "LAYER4_LOOKBACK_CANDLES",
        "LAYER4_MIN_LOOKBACK_CANDLES",
        "LAYER4_SWING_LOOKBACK_CANDLES",
        "LAYER4_MAX_PULLBACK_DISTANCE_PERCENT",
        "LAYER4_PULLBACK_VOLUME_WINDOW",
        "LAYER4_KEEP_WAIT_CANDIDATES",
        "LAYER5_ENABLE_RANGE_FILTER",
        "LAYER5_TIMEFRAME",
        "LAYER5_LOOKBACK_CANDLES",
        "LAYER5_MIN_LOOKBACK_CANDLES",
        "LAYER5_MIN_RANGE_WIDTH_PIPS",
        "LAYER5_ATR_PERIOD",
        "LAYER5_MIN_RANGE_WIDTH_ATR",
        "LAYER5_TOUCH_TOLERANCE_FRACTION",
        "LAYER5_MIN_TOUCHES_PER_SIDE",
        "LAYER5_ENTRY_ZONE_FRACTION",
        "LAYER5_MAX_SPREAD_PERCENT",
        "LAYER5_MIN_RANGE_SPREAD_RATIO",
        "AI_ENABLED",
        "AI_PROVIDER",
        "AI_USE_FOR_LAYER6",
        "AI_USE_FOR_LAYER7",
        "AI_LAYER7_SKIP_WEAK_CANDIDATES",
        "AI_LAYER7_MIN_PRE_AI_SCORE",
        "AI_LAYER7_FORCE_FOR_EXOTICS",
        "AI_LAYER7_FORCE_FOR_WEAK_EVIDENCE",
        "AI_ALLOW_LAYER3_FLEXIBLE_WAIT",
        "AI_LAYER3_NEAR_BREAKOUT_DISTANCE_PERCENT",
        "AI_LAYER3_MIN_FLEX_SCORE",
        "AI_USE_FOR_LAYER8",
        "AI_MAX_CALLS_PER_RUN",
        "FUNNEL_EXPAND_FOREX_UNIVERSE",
        "FUNNEL_MAX_RANGE_CANDIDATES",
        "NVIDIA_BASE_URL",
        "NVIDIA_MODEL",
        "NVIDIA_TIMEOUT_SECONDS",
        "NVIDIA_TEMPERATURE",
        "GEMINI_MODEL",
        "GEMINI_TIMEOUT_SECONDS",
        "GEMINI_TEMPERATURE",
        "GEMINI_MIN_CANDLE_SCORE",
        "GEMINI_ALLOW_FLEXIBLE_FUNNEL",
        "LAYER6_ENABLE_PRICE_ACTION",
        "LAYER6_TIMEFRAME",
        "LAYER6_LOOKBACK_CANDLES",
        "LAYER6_MAX_KEY_LEVEL_DISTANCE_PERCENT",
        "AI_LAYER6_MAX_SCORE_BOOST",
        "LAYER7_ENABLE_FUNDAMENTALS",
        "LAYER7_USE_FRED",
        "LAYER7_USE_BIS",
        "LAYER7_USE_CFTC",
        "LAYER7_USE_WORLD_BANK",
        "LAYER7_BIAS_THRESHOLD",
        "LAYER7_SOURCE_PAUSE_SECONDS",
        "LAYER7_HTTP_TIMEOUT_SECONDS",
        "FUNDAMENTAL_USE_CACHE",
        "FUNDAMENTAL_CACHE_PATH",
        "FUNDAMENTAL_CACHE_MAX_AGE_SECONDS",
        "FUNDAMENTAL_GDP_CACHE_MAX_AGE_SECONDS",
        "FUNDAMENTAL_CFTC_CACHE_MAX_AGE_SECONDS",
        "FUNDAMENTAL_RISK_SENTIMENT",
        "BIS_POLICY_RATES_URL",
        "CFTC_COT_URL",
        "FRED_API_KEY",
        "LAYER8_ENABLE_NEWS_SENTIMENT",
        "LAYER8_USE_GDELT",
        "LAYER8_USE_ALPHA_VANTAGE_NEWS",
        "LAYER8_USE_NEWSAPI",
        "NEWS_USE_CACHE",
        "NEWS_CACHE_PATH",
        "NEWS_CACHE_MAX_AGE_SECONDS",
        "NEWS_HTTP_TIMEOUT_SECONDS",
        "NEWS_LOOKBACK_HOURS",
        "NEWS_LOOKAHEAD_HOURS",
        "NEWS_BLOCK_MINUTES_AROUND_EVENT",
        "NEWS_CALENDAR_PATH",
        "FOREX_FACTORY_CALENDAR_URL",
        "NEWSAPI_KEY",
        "SCORER_MIN_COMPOSITE_SCORE",
        "SCORER_WEIGHT_LAYER1",
        "SCORER_WEIGHT_LAYER2",
        "SCORER_WEIGHT_LAYER3",
        "SCORER_WEIGHT_LAYER4",
        "SCORER_WEIGHT_LAYER5",
        "SCORER_WEIGHT_LAYER6",
        "SCORER_WEIGHT_LAYER7",
        "SCORER_WEIGHT_LAYER8",
        "NVIDIA_API_KEY",
        "GEMINI_API_KEY",
        "SCORER_RANGE_WEIGHT_LAYER1",
        "SCORER_RANGE_WEIGHT_LAYER2",
        "SCORER_RANGE_WEIGHT_LAYER3",
        "SCORER_RANGE_WEIGHT_LAYER4",
        "SCORER_RANGE_WEIGHT_LAYER5",
        "SCORER_RANGE_WEIGHT_LAYER6",
        "SCORER_RANGE_WEIGHT_LAYER7",
        "SCORER_RANGE_WEIGHT_LAYER8",
        "SCORER_SPREAD_COST_MULTIPLIER",
        "SCORER_UNKNOWN_SPREAD_PERCENT",
        "SCORER_HIGH_PRIORITY_BONUS",
        "SCORER_RANGE_WIDTH_MAX_BONUS",
        "ADAPTIVE_WEIGHTS_ENABLED",
        "ADAPTIVE_WEIGHTS_PATH",
        "ADAPTIVE_TRADE_LOG_PATH",
        "ADAPTIVE_WEIGHTS_MIN_TRADES",
        "ADAPTIVE_WEIGHT_LEARNING_RATE",
        "ADAPTIVE_WEIGHT_MIN_FACTOR",
        "ADAPTIVE_WEIGHT_MAX_FACTOR",
    ]
    missing = []
    for key in required:
        value = os.getenv(key, "")
        if key == "GEMINI_API_KEY" and not (env_bool("AI_ENABLED", False) and os.getenv("AI_PROVIDER", "nvidia").lower() == "gemini"):
            continue
        if key == "NVIDIA_API_KEY" and not (env_bool("AI_ENABLED", False) and os.getenv("AI_PROVIDER", "nvidia").lower() == "nvidia"):
            continue
        if key == "NEWSAPI_KEY" and not (env_bool("LAYER8_ENABLE_NEWS_SENTIMENT", False) and env_bool("LAYER8_USE_NEWSAPI", False)):
            continue
        if key == "FOREX_FACTORY_CALENDAR_URL":
            continue
        if key == "FRED_API_KEY" and not (env_bool("LAYER7_ENABLE_FUNDAMENTALS", False) and env_bool("LAYER7_USE_FRED", True)):
            continue
        if key == "BIS_POLICY_RATES_URL" and not env_bool("LAYER7_USE_BIS", True):
            continue
        if not value:
            missing.append(key)
            print(f"MISSING {key}")
        elif "KEY" in key or "SECRET" in key or "PASSWORD" in key:
            print(f"OK {key}=***len:{len(value)}***")
        else:
            print(f"OK {key}={value}")
    if missing:
        print("Market funnel env validation failed.")
        return 1
    if env_bool("AI_ENABLED", False) and os.getenv("AI_PROVIDER", "nvidia").lower() == "nvidia" and not os.getenv("NVIDIA_API_KEY", "").strip():
        print("MISSING NVIDIA_API_KEY because AI_ENABLED=true and AI_PROVIDER=nvidia")
        print("Market funnel env validation failed.")
        return 1
    if env_bool("AI_ENABLED", False) and os.getenv("AI_PROVIDER", "nvidia").lower() == "gemini" and not os.getenv("GEMINI_API_KEY", "").strip():
        print("MISSING GEMINI_API_KEY because AI_ENABLED=true and AI_PROVIDER=gemini")
        print("Market funnel env validation failed.")
        return 1
    if env_bool("LAYER7_ENABLE_FUNDAMENTALS", False) and env_bool("LAYER7_USE_FRED", True) and not os.getenv("FRED_API_KEY", "").strip():
        print("MISSING FRED_API_KEY because LAYER7_ENABLE_FUNDAMENTALS=true and LAYER7_USE_FRED=true")
        print("Market funnel env validation failed.")
        return 1
    print("Market funnel env validation passed.")
    return 0


def ai_report() -> int:
    load_dotenv()
    output = Path(os.getenv("FUNNEL_OUTPUT_PATH", "data/layer1_candidates.csv"))
    rows: list[dict[str, str]] = []
    if output.exists():
        with output.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    context = {
        "candidate_file": str(output),
        "candidate_count": len(rows),
        "layer3_high_priority": sum(1 for row in rows if row.get("layer3_state") == HIGH_PRIORITY),
        "layer4_confirmed": sum(1 for row in rows if row.get("layer4_state") == PULLBACK_CONFIRMED),
        "layer5_range_buy": sum(1 for row in rows if row.get("layer5_state") == RANGE_BUY),
        "layer5_range_sell": sum(1 for row in rows if row.get("layer5_state") == RANGE_SELL),
        "sample_candidates": rows[:20],
    }
    result = summarize_funnel_status(context)
    if not result.enabled:
        print("AI is disabled. Set AI_ENABLED=true plus NVIDIA_API_KEY or GEMINI_API_KEY to use --ai-report.")
        return 1
    print(f"AI report: {result.decision} score={result.score:.1f} bottleneck={result.pattern}")
    print(result.reason)
    return 0 if result.decision != "ERROR" else 1


def health_check() -> int:
    load_dotenv()
    output = Path(os.getenv("FUNNEL_OUTPUT_PATH", "data/layer1_candidates.csv"))
    rows: list[dict[str, str]] = []
    if output.exists():
        with output.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    output_age = time.time() - output.stat().st_mtime if output.exists() else None

    mt5_ready = init_mt5() if env_bool("FUNNEL_USE_MT5", True) else False
    mt5_symbol_count = 0
    mt5_account = "unavailable"
    if mt5_ready and mt5 is not None:
        symbols = mt5.symbols_get()
        mt5_symbol_count = len(symbols or [])
        account = mt5.account_info()
        if account is not None:
            mt5_account = f"{account.login} {account.server} balance={account.balance:.2f} equity={account.equity:.2f}"

    try:
        from funnel_candidates import (
            FUNNEL_FALLBACK_TO_WATCHLIST,
            FUNNEL_TOP_N,
            USE_FUNNEL_CANDIDATES,
            load_funnel_candidates,
        )

        scalper_candidates = load_funnel_candidates()
        handoff = (
            f"enabled={USE_FUNNEL_CANDIDATES} top_n={FUNNEL_TOP_N} "
            f"fallback_to_watchlist={FUNNEL_FALLBACK_TO_WATCHLIST} loaded={len(scalper_candidates)}"
        )
    except Exception as exc:
        scalper_candidates = []
        handoff = f"ERROR {exc}"

    print("---- System health ----")
    print(f"MT5: {'OK' if mt5_ready else 'FAIL'} | symbols={mt5_symbol_count} | account={mt5_account}")
    print(f"AI: {'OK' if ai_enabled() else 'OFF'} | provider={os.getenv('AI_PROVIDER', 'nvidia')} | max_calls={env_int('AI_MAX_CALLS_PER_RUN', 25)}")
    print(
        "External APIs configured: "
        f"alpha={env_bool('FUNNEL_USE_ALPHA_VANTAGE', True)} "
        f"massive={env_bool('FUNNEL_USE_MASSIVE', True)} "
        f"require_confirmation={env_bool('FUNNEL_REQUIRE_EXTERNAL_CONFIRMATION', True)}"
    )
    print(
        "Runtime controls: "
        f"time_budget={env_float('FUNNEL_TIME_BUDGET_SECONDS', 240.0)}s "
        f"http_timeout={http_timeout()}s "
        f"alpha_sleep={env_float('FUNNEL_ALPHA_SLEEP_SECONDS', 12.5)}s "
        f"max_symbols={env_int('FUNNEL_MAX_SYMBOLS', 200)} "
        f"max_external={env_int('FUNNEL_MAX_EXTERNAL_SYMBOLS', 25)}"
    )
    age_text = "missing" if output_age is None else f"{output_age:.1f}s"
    print(
        f"Funnel output: {output} | rows={len(rows)} | exists={output.exists()} "
        f"| age={age_text} | max_age={env_float('FUNNEL_CANDIDATE_MAX_AGE_SECONDS', 300.0)}s"
    )
    print(f"Scalper handoff: {handoff}")
    if scalper_candidates:
        print("Top scalper candidates:")
        for item in scalper_candidates[:5]:
            print(f"  {item.symbol} {item.side} score={item.composite_score:.2f}")
    if mt5_ready and mt5 is not None:
        mt5.shutdown()

    critical_ok = mt5_ready and (not os.getenv("BROKER", "").lower().endswith("mt5") or mt5_symbol_count > 0)
    return 0 if critical_ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Multi-layer market selection funnel")
    parser.add_argument("--validate-env", action="store_true", help="Validate required .env keys without running a scan")
    parser.add_argument("--ai-report", action="store_true", help="Ask Gemini to summarize current funnel output and bottlenecks")
    parser.add_argument("--health", action="store_true", help="Check MT5, AI, API config, funnel output, and scalper handoff")
    args = parser.parse_args()
    if args.validate_env:
        return validate_env()
    if args.ai_report:
        return ai_report()
    if args.health:
        return health_check()

    load_dotenv()
    max_symbols = env_int("FUNNEL_MAX_SYMBOLS", 200)
    max_external = env_int("FUNNEL_MAX_EXTERNAL_SYMBOLS", 25)
    min_agreement = env_int("FUNNEL_MIN_SOURCE_AGREEMENT", 2)
    require_external = env_bool("FUNNEL_REQUIRE_EXTERNAL_CONFIRMATION", True)
    local_prefilter_all = env_bool("FUNNEL_LOCAL_PREFILTER_ALL", True)
    use_mt5 = env_bool("FUNNEL_USE_MT5", True)
    use_alpha = env_bool("FUNNEL_USE_ALPHA_VANTAGE", True)
    use_massive = env_bool("FUNNEL_USE_MASSIVE", True)
    effective_min_agreement = min_agreement if (require_external and (use_alpha or use_massive)) else 1
    budget = RuntimeBudget(env_float("FUNNEL_TIME_BUDGET_SECONDS", 240.0))

    mt5_ready = init_mt5() if use_mt5 else False
    massive_key = os.getenv("MASSIVE_API_KEY") or os.getenv("POLYGON_API_KEY") or ""
    massive_base = os.getenv("MASSIVE_API_BASE", "https://api.massive.com")
    include_massive_universe = use_massive and (require_external or not mt5_ready)

    universe = unique_symbols(
        [
            mt5_universe(max_symbols) if mt5_ready else [],
            massive_universe(massive_key, massive_base, max_symbols) if include_massive_universe else [],
            configured_universe(max_symbols),
        ],
        max_symbols,
    )

    print(f"Layer 1 universe: {len(universe)} symbols")
    candidates: list[Candidate] = []
    processed_symbols = universe if local_prefilter_all and mt5_ready else universe[:max_external]
    local_layer1_candidates: list[Candidate] = []
    local_range_candidates: list[Candidate] = []
    local_prefilter_processed = 0
    external_processed = 0
    layer1_passed = 0
    layer2_processed = 0
    layer2_confirmed = 0
    layer3_processed = 0
    layer3_high_priority = 0
    layer3_wait = 0
    layer4_processed = 0
    layer4_confirmed = 0
    layer4_wait = 0
    layer5_processed = 0
    layer5_confirmed = 0

    if local_prefilter_all and mt5_ready:
        print("Layer 1 local prefilter: scanning full MT5 universe before ranking/external confirmation")
        for symbol in processed_symbols:
            if budget.expired():
                print(f"TIME_BUDGET_STOP layer1 prefilter after {budget.seconds:.1f}s")
                break
            try:
                local_candidate = mt5_layer1_candidate(symbol)
            except Exception as exc:
                print(f"{symbol}: MT5_PREFILTER_ERROR {exc}")
                continue
            local_prefilter_processed += 1
            print(f"{symbol}: MT5_{local_candidate.state} agreement={local_candidate.agreement}")
            if local_candidate.state != RANGING:
                local_layer1_candidates.append(local_candidate)
            else:
                local_range_candidates.append(local_candidate)

        local_layer1_candidates = sorted(local_layer1_candidates, key=local_prefilter_rank, reverse=True)
        local_range_candidates = sorted(local_range_candidates, key=local_prefilter_rank, reverse=True)
        external_symbols = local_layer1_candidates[:max_external]
        range_limit = env_int("FUNNEL_MAX_RANGE_CANDIDATES", max_external)
        for local_candidate in local_range_candidates[:range_limit]:
            if budget.expired():
                print(f"TIME_BUDGET_STOP range layer after {budget.seconds:.1f}s")
                break
            layer5_processed += 1
            layer5_candidate = apply_layer5(local_candidate)
            if layer5_candidate is not None:
                final_candidate = finalize_candidate(layer5_candidate)
                if final_candidate is not None:
                    candidates.append(final_candidate)
                    layer5_confirmed += 1
                    print(f"{local_candidate.symbol}: Layer5 {final_candidate.layer5_state} score={final_candidate.composite_score:.2f} {final_candidate.layer5_reason}")
    else:
        external_symbols = []

    scan_items = external_symbols if local_prefilter_all and mt5_ready else processed_symbols
    for item in scan_items:
        if budget.expired():
            print(f"TIME_BUDGET_STOP external/layer scan after {budget.seconds:.1f}s")
            break
        if budget.remaining() < env_float("FUNNEL_MIN_REMAINING_FOR_EXTERNAL_SECONDS", 5.0):
            print("TIME_BUDGET_STOP not enough remaining time for another external/layer pass")
            break
        symbol = item.symbol if isinstance(item, Candidate) else item
        external_processed += 1
        try:
            if local_prefilter_all and mt5_ready and isinstance(item, Candidate):
                if require_external:
                    candidate = externally_confirm_candidate(item, use_alpha, use_massive)
                else:
                    candidate = item
            else:
                candidate = symbol_candidate(symbol, mt5_ready, use_alpha, use_massive)
        except Exception as exc:  # Keep one bad provider response from killing the whole scan.
            print(f"{symbol}: ERROR {exc}")
            continue
        print(f"{symbol}: {candidate.state} agreement={candidate.agreement}")
        if candidate.state == RANGING:
            layer5_processed += 1
            layer5_candidate = apply_layer5(candidate)
            if layer5_candidate is not None:
                final_candidate = finalize_candidate(layer5_candidate)
                if final_candidate is not None:
                    candidates.append(final_candidate)
                    layer5_confirmed += 1
                    print(f"{symbol}: Layer5 {final_candidate.layer5_state} score={final_candidate.composite_score:.2f} {final_candidate.layer5_reason}")
            else:
                print(f"{symbol}: Layer5 FAIL {candidate.layer5_reason}")
            continue

        external_ok = any(
            trend.source != "MT5" and trend.state == candidate.state
            for trend in candidate.sources
        )
        if candidate.state != RANGING and candidate.agreement >= effective_min_agreement and (external_ok or not require_external):
            layer1_passed += 1
            layer2_processed += 1
            layer2_candidate = apply_layer2(candidate)
            if layer2_candidate is not None:
                layer2_confirmed += 1
                print(f"{symbol}: Layer2 PASS {layer2_candidate.layer2_reason}")
                layer3_processed += 1
                layer3_candidate = apply_layer3(layer2_candidate)
                if layer3_candidate is not None:
                    if layer3_candidate.layer3_state == HIGH_PRIORITY:
                        layer3_high_priority += 1
                    elif layer3_candidate.layer3_state == WAIT:
                        layer3_wait += 1
                    print(f"{symbol}: Layer3 {layer3_candidate.layer3_state} {layer3_candidate.layer3_reason}")
                    layer4_processed += 1
                    layer4_candidate = apply_layer4(layer3_candidate)
                    if layer4_candidate is not None:
                        final_candidate = finalize_candidate(layer4_candidate)
                        if final_candidate is None:
                            print(f"{symbol}: Scorer FAIL composite below threshold")
                            continue
                        candidates.append(final_candidate)
                        if final_candidate.layer4_state == PULLBACK_CONFIRMED:
                            layer4_confirmed += 1
                        elif final_candidate.layer4_state == PULLBACK_WAIT:
                            layer4_wait += 1
                        print(f"{symbol}: Layer4 {final_candidate.layer4_state} score={final_candidate.composite_score:.2f} {final_candidate.layer4_reason}")
                    else:
                        print(f"{symbol}: Layer4 FAIL {layer3_candidate.layer4_reason}")
                else:
                    print(f"{symbol}: Layer3 FAIL {layer2_candidate.layer3_reason}")
            else:
                print(f"{symbol}: Layer2 FAIL {candidate.layer2_reason}")

    output = Path(os.getenv("FUNNEL_OUTPUT_PATH", "data/layer1_candidates.csv"))
    candidates = rank_candidates_by_expected_value(candidates)
    write_candidates(candidates, output)
    print("---- Funnel summary ----")
    print(f"Universe gathered: {len(universe)}")
    print(f"Layer 1 processed: {local_prefilter_processed if local_prefilter_all and mt5_ready else len(processed_symbols)}")
    if local_prefilter_all and mt5_ready:
        print(f"Layer 1 MT5 prefilter passed: {len(local_layer1_candidates)}")
        print(f"Layer 1 MT5 ranging pool: {len(local_range_candidates)}")
        print(f"External confirmation selected: {len(scan_items)}")
        print(f"External confirmation processed: {external_processed}")
    print(f"Layer 1 passed: {layer1_passed}")
    print(f"Layer 2 processed: {layer2_processed}")
    print(f"Layer 2 confirmed: {layer2_confirmed}")
    print(f"Layer 3 processed: {layer3_processed}")
    print(f"Layer 3 HIGH_PRIORITY: {layer3_high_priority}")
    print(f"Layer 3 WAIT: {layer3_wait}")
    print(f"Layer 4 processed: {layer4_processed}")
    print(f"Layer 4 PULLBACK_CONFIRMED: {layer4_confirmed}")
    print(f"Layer 4 PULLBACK_WAIT: {layer4_wait}")
    print(f"Layer 5 processed: {layer5_processed}")
    print(f"Layer 5 range confirmed: {layer5_confirmed}")
    print(f"Funnel candidates written: {len(candidates)} -> {output}")
    if candidates:
        print(
            "Top expected net value: "
            + ", ".join(
                f"{item.symbol}:{item.expected_net_value:.2f}/score={item.composite_score:.2f}/spread={item.live_spread_percent if item.live_spread_percent is not None else -1:.5f}%"
                for item in candidates[:5]
            )
        )

    if mt5_ready:
        mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
