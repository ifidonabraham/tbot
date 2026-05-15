from __future__ import annotations

import csv
import argparse
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover - lets the scanner run without MT5 installed.
    mt5 = None


TRENDING_UP = "TRENDING_UP"
TRENDING_DOWN = "TRENDING_DOWN"
RANGING = "RANGING"


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
        response = requests.get(url, params={"apiKey": api_key}, timeout=30)
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
    response = requests.get("https://www.alphavantage.co/query", params=params, timeout=30)
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
        response = requests.get(url, params=params, timeout=30)
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
    recent = data.iloc[-5:-1]

    min_adx = float(os.getenv("LAYER1_MIN_ADX", "25"))
    if pd.isna(row["adx"]) or row["adx"] < min_adx:
        return SourceTrend(label, RANGING, f"ADX {row['adx']:.2f} below {min_adx:.2f}")

    higher = all(
        recent.iloc[i]["high"] > recent.iloc[i - 1]["high"]
        and recent.iloc[i]["low"] > recent.iloc[i - 1]["low"]
        for i in range(1, len(recent))
    )
    lower = all(
        recent.iloc[i]["high"] < recent.iloc[i - 1]["high"]
        and recent.iloc[i]["low"] < recent.iloc[i - 1]["low"]
        for i in range(1, len(recent))
    )

    if higher and row["close"] > row["ema200"] and row["ema9"] > row["ema21"] > row["ema50"]:
        return SourceTrend(label, TRENDING_UP, "HH/HL + EMA200 + EMA stack + ADX")
    if lower and row["close"] < row["ema200"] and row["ema9"] < row["ema21"] < row["ema50"]:
        return SourceTrend(label, TRENDING_DOWN, "LH/LL + EMA200 + EMA stack + ADX")
    return SourceTrend(label, RANGING, "structure/EMA alignment failed")


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
        time.sleep(12.5)  # Friendly to Alpha Vantage free-tier pacing.
    if use_massive:
        trends.append(source_trend("MASSIVE", massive_rates(symbol)))

    up = sum(1 for trend in trends if trend.state == TRENDING_UP)
    down = sum(1 for trend in trends if trend.state == TRENDING_DOWN)
    if up > down:
        return Candidate(symbol, TRENDING_UP, up, trends)
    if down > up:
        return Candidate(symbol, TRENDING_DOWN, down, trends)
    return Candidate(symbol, RANGING, max(up, down), trends)


def write_candidates(candidates: list[Candidate], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["symbol", "state", "agreement", "sources"])
        for candidate in candidates:
            sources = " | ".join(f"{t.source}:{t.state}:{t.reason}" for t in candidate.sources)
            writer.writerow([candidate.symbol, candidate.state, candidate.agreement, sources])


def validate_env() -> int:
    load_dotenv()
    required = [
        "ALPHA_VANTAGE_API_KEY",
        "MASSIVE_API_KEY",
        "MASSIVE_API_BASE",
        "FUNNEL_USE_MT5",
        "FUNNEL_USE_ALPHA_VANTAGE",
        "FUNNEL_USE_MASSIVE",
        "FUNNEL_MAX_SYMBOLS",
        "FUNNEL_MAX_EXTERNAL_SYMBOLS",
        "FUNNEL_MIN_SOURCE_AGREEMENT",
        "FUNNEL_OUTPUT_PATH",
        "FUNNEL_MT5_TIMEOUT_MS",
    ]
    missing = []
    for key in required:
        value = os.getenv(key, "")
        if not value:
            missing.append(key)
            print(f"MISSING {key}")
        elif "KEY" in key or "SECRET" in key or "PASSWORD" in key:
            print(f"OK {key}=***len:{len(value)}***")
        else:
            print(f"OK {key}={value}")
    if missing:
        print("Layer 1 env validation failed.")
        return 1
    print("Layer 1 env validation passed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Layer 1 market selection funnel")
    parser.add_argument("--validate-env", action="store_true", help="Validate required .env keys without running a scan")
    args = parser.parse_args()
    if args.validate_env:
        return validate_env()

    load_dotenv()
    max_symbols = env_int("FUNNEL_MAX_SYMBOLS", 200)
    max_external = env_int("FUNNEL_MAX_EXTERNAL_SYMBOLS", 25)
    min_agreement = env_int("FUNNEL_MIN_SOURCE_AGREEMENT", 2)
    require_external = env_bool("FUNNEL_REQUIRE_EXTERNAL_CONFIRMATION", True)
    use_mt5 = env_bool("FUNNEL_USE_MT5", True)
    use_alpha = env_bool("FUNNEL_USE_ALPHA_VANTAGE", True)
    use_massive = env_bool("FUNNEL_USE_MASSIVE", True)

    mt5_ready = init_mt5() if use_mt5 else False
    massive_key = os.getenv("MASSIVE_API_KEY") or os.getenv("POLYGON_API_KEY") or ""
    massive_base = os.getenv("MASSIVE_API_BASE", "https://api.massive.com")

    universe = unique_symbols(
        [
            mt5_universe(max_symbols) if mt5_ready else [],
            massive_universe(massive_key, massive_base, max_symbols) if use_massive else [],
            configured_universe(max_symbols),
        ],
        max_symbols,
    )

    print(f"Layer 1 universe: {len(universe)} symbols")
    candidates: list[Candidate] = []
    for symbol in universe[:max_external]:
        try:
            candidate = symbol_candidate(symbol, mt5_ready, use_alpha, use_massive)
        except Exception as exc:  # Keep one bad provider response from killing the whole scan.
            print(f"{symbol}: ERROR {exc}")
            continue
        print(f"{symbol}: {candidate.state} agreement={candidate.agreement}")
        external_ok = any(
            trend.source != "MT5" and trend.state == candidate.state
            for trend in candidate.sources
        )
        if candidate.state != RANGING and candidate.agreement >= min_agreement and (external_ok or not require_external):
            candidates.append(candidate)

    output = Path(os.getenv("FUNNEL_OUTPUT_PATH", "data/layer1_candidates.csv"))
    write_candidates(candidates, output)
    print(f"Layer 1 candidates written: {len(candidates)} -> {output}")

    if mt5_ready:
        mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
