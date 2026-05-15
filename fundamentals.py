from __future__ import annotations

import csv
import io
import json
import os
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests


@dataclass
class FundamentalResult:
    bias: str = "NEUTRAL"
    score: float = 50.0
    reason: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


CURRENCY_COUNTRY = {
    "USD": ("US", "united states"),
    "EUR": ("EMU", "euro area"),
    "GBP": ("GB", "united kingdom"),
    "JPY": ("JP", "japan"),
    "CHF": ("CH", "switzerland"),
    "CAD": ("CA", "canada"),
    "AUD": ("AU", "australia"),
    "NZD": ("NZ", "new zealand"),
    "CNH": ("CN", "china"),
    "CNY": ("CN", "china"),
    "MXN": ("MX", "mexico"),
    "ZAR": ("ZA", "south africa"),
    "SEK": ("SE", "sweden"),
    "NOK": ("NO", "norway"),
    "DKK": ("DK", "denmark"),
    "PLN": ("PL", "poland"),
    "HUF": ("HU", "hungary"),
    "TRY": ("TR", "turkey"),
    "SGD": ("SG", "singapore"),
    "HKD": ("HK", "hong kong"),
    "THB": ("TH", "thailand"),
}

COUNTRY_CURRENCY = {
    "US": "USD",
    "XM": "EUR",
    "EA": "EUR",
    "GB": "GBP",
    "JP": "JPY",
    "CH": "CHF",
    "CA": "CAD",
    "AU": "AUD",
    "NZ": "NZD",
    "CN": "CNY",
    "MX": "MXN",
    "ZA": "ZAR",
    "SE": "SEK",
    "NO": "NOK",
    "DK": "DKK",
    "PL": "PLN",
    "HU": "HUF",
    "TR": "TRY",
    "SG": "SGD",
    "HK": "HKD",
    "TH": "THB",
}

RISK_ON = {"AUD", "NZD", "CAD", "ZAR", "MXN", "NOK"}
RISK_OFF = {"USD", "JPY", "CHF"}

FRED_SERIES = {
    "USD_RATE": "FEDFUNDS",
    "USD_GDP_GROWTH": "A191RL1Q225SBEA",
}

CFTC_MARKETS = {
    "USD": ["U.S. DOLLAR INDEX", "USD INDEX"],
    "EUR": ["EURO FX"],
    "GBP": ["BRITISH POUND"],
    "JPY": ["JAPANESE YEN"],
    "CHF": ["SWISS FRANC"],
    "CAD": ["CANADIAN DOLLAR"],
    "AUD": ["AUSTRALIAN DOLLAR"],
    "NZD": ["NEW ZEALAND DOLLAR"],
    "MXN": ["MEXICAN PESO"],
}

CACHE_PATH = Path(os.getenv("FUNDAMENTAL_CACHE_PATH", "data/fundamental_cache.json"))


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


def load_cache() -> dict[str, Any]:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cache(cache: dict[str, Any]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")


def cached_value(key: str, max_age_seconds: float) -> float | None:
    cache = load_cache()
    item = cache.get(key, {})
    try:
        age = time.time() - float(item.get("timestamp", 0.0))
        if age <= max_age_seconds:
            return float(item["value"])
    except (TypeError, ValueError, KeyError):
        return None
    return None


def set_cached_value(key: str, value: float) -> None:
    cache = load_cache()
    cache[key] = {"timestamp": time.time(), "value": value}
    save_cache(cache)


def split_pair(symbol: str) -> tuple[str, str] | None:
    cleaned = "".join(ch for ch in symbol.upper().replace("/", "") if ch.isalpha())
    if len(cleaned) < 6:
        return None
    base, quote = cleaned[:3], cleaned[3:6]
    if base not in CURRENCY_COUNTRY or quote not in CURRENCY_COUNTRY:
        return None
    return base, quote


def latest_numeric(values: list[dict[str, Any]], key: str = "value") -> float | None:
    for row in reversed(values):
        raw = row.get(key)
        try:
            if raw not in {None, "."}:
                return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def fred_series(series_id: str) -> float | None:
    cache_key = f"fred:{series_id}"
    max_age = env_float("FUNDAMENTAL_CACHE_MAX_AGE_SECONDS", 86400.0)
    if env_bool("FUNDAMENTAL_USE_CACHE", True):
        cached = cached_value(cache_key, max_age)
        if cached is not None:
            return cached
    key = os.getenv("FRED_API_KEY", "").strip()
    if not key:
        return None
    response = requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={
            "series_id": series_id,
            "api_key": key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 4,
        },
        timeout=env_float("LAYER7_HTTP_TIMEOUT_SECONDS", 10.0),
    )
    response.raise_for_status()
    value = latest_numeric(list(reversed(response.json().get("observations", []))))
    if value is not None and env_bool("FUNDAMENTAL_USE_CACHE", True):
        set_cached_value(cache_key, value)
    return value


def world_bank_gdp_growth(currency: str) -> float | None:
    cache_key = f"world_bank_gdp:{currency}"
    max_age = env_float("FUNDAMENTAL_GDP_CACHE_MAX_AGE_SECONDS", 2592000.0)
    if env_bool("FUNDAMENTAL_USE_CACHE", True):
        cached = cached_value(cache_key, max_age)
        if cached is not None:
            return cached
    country = CURRENCY_COUNTRY.get(currency)
    if country is None:
        return None
    iso2, _ = country
    if iso2 == "EMU":
        return None
    response = requests.get(
        f"https://api.worldbank.org/v2/country/{iso2}/indicator/NY.GDP.MKTP.KD.ZG",
        params={"format": "json", "per_page": 5},
        timeout=env_float("LAYER7_HTTP_TIMEOUT_SECONDS", 10.0),
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list) or len(payload) < 2:
        return None
    for row in payload[1]:
        value = row.get("value")
        if value is not None:
            numeric = float(value)
            if env_bool("FUNDAMENTAL_USE_CACHE", True):
                set_cached_value(cache_key, numeric)
            return numeric
    return None


def bis_policy_rates() -> dict[str, float]:
    max_age = env_float("FUNDAMENTAL_CACHE_MAX_AGE_SECONDS", 86400.0)
    if env_bool("FUNDAMENTAL_USE_CACHE", True):
        cache = load_cache()
        item = cache.get("bis_policy_rates", {})
        try:
            if time.time() - float(item.get("timestamp", 0.0)) <= max_age:
                return {str(k): float(v) for k, v in item.get("value", {}).items()}
        except (TypeError, ValueError):
            pass
    url = os.getenv(
        "BIS_POLICY_RATES_URL",
        "https://data.bis.org/static/bulk/WS_CBPOL_csv_flat.zip",
    ).strip()
    response = requests.get(url, timeout=env_float("LAYER7_HTTP_TIMEOUT_SECONDS", 10.0))
    response.raise_for_status()
    if url.lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            csv_name = next((name for name in archive.namelist() if name.lower().endswith(".csv")), "")
            if not csv_name:
                return {}
            text = archive.read(csv_name).decode("utf-8-sig", errors="replace")
    else:
        text = response.text

    rows = csv.DictReader(io.StringIO(text))
    result: dict[str, float] = {}
    latest_time: dict[str, str] = {}
    for row in rows:
        currency = (
            row.get("currency")
            or row.get("Currency")
            or row.get("CUR")
            or row.get("CURRENCY")
            or ""
        ).upper()
        ref_area = (row.get("REF_AREA") or row.get("ref_area") or row.get("Reference area") or "").upper()
        if not currency and ref_area:
            currency = COUNTRY_CURRENCY.get(ref_area, "")
        value = (
            row.get("OBS_VALUE")
            or row.get("obs_value")
            or row.get("rate")
            or row.get("Rate")
            or row.get("value")
            or row.get("Value")
        )
        period = str(row.get("TIME_PERIOD") or row.get("time_period") or row.get("Time period") or "")
        if currency and value:
            try:
                numeric = float(value)
            except ValueError:
                continue
            if currency not in latest_time or period >= latest_time[currency]:
                result[currency] = numeric
                latest_time[currency] = period
    if result and env_bool("FUNDAMENTAL_USE_CACHE", True):
        cache = load_cache()
        cache["bis_policy_rates"] = {"timestamp": time.time(), "value": result}
        save_cache(cache)
    return result


def cftc_positioning() -> dict[str, float]:
    max_age = env_float("FUNDAMENTAL_CFTC_CACHE_MAX_AGE_SECONDS", 604800.0)
    if env_bool("FUNDAMENTAL_USE_CACHE", True):
        cache = load_cache()
        item = cache.get("cftc_positioning", {})
        try:
            if time.time() - float(item.get("timestamp", 0.0)) <= max_age:
                return {str(k): float(v) for k, v in item.get("value", {}).items()}
        except (TypeError, ValueError):
            pass
    url = os.getenv("CFTC_COT_URL", "https://www.cftc.gov/dea/newcot/FinFutWk.txt").strip()
    response = requests.get(url, timeout=env_float("LAYER7_HTTP_TIMEOUT_SECONDS", 10.0))
    response.raise_for_status()
    text = response.text
    result: dict[str, float] = {}

    try:
        rows = list(csv.DictReader(io.StringIO(text)))
    except csv.Error:
        rows = []

    if rows:
        for row in rows:
            market = " ".join(str(row.get(key, "")).upper() for key in row)
            for currency, names in CFTC_MARKETS.items():
                if not any(name in market for name in names):
                    continue
                long_value = row.get("Noncommercial Positions-Long (All)") or row.get("NonComm_Positions_Long_All")
                short_value = row.get("Noncommercial Positions-Short (All)") or row.get("NonComm_Positions_Short_All")
                try:
                    result[currency] = float(long_value) - float(short_value)
                except (TypeError, ValueError):
                    pass
        if result and env_bool("FUNDAMENTAL_USE_CACHE", True):
            cache = load_cache()
            cache["cftc_positioning"] = {"timestamp": time.time(), "value": result}
            save_cache(cache)
        return result

    upper = text.upper().splitlines()
    for idx, line in enumerate(upper):
        for currency, names in CFTC_MARKETS.items():
            if any(name in line for name in names):
                numbers = []
                for part in line.replace(",", " ").split():
                    try:
                        numbers.append(float(part))
                    except ValueError:
                        continue
                if len(numbers) >= 2:
                    result[currency] = numbers[-2] - numbers[-1]
                elif idx + 1 < len(upper):
                    numbers = []
                    for part in upper[idx + 1].replace(",", " ").split():
                        try:
                            numbers.append(float(part))
                        except ValueError:
                            continue
                    if len(numbers) >= 2:
                        result[currency] = numbers[-2] - numbers[-1]
    if result and env_bool("FUNDAMENTAL_USE_CACHE", True):
        cache = load_cache()
        cache["cftc_positioning"] = {"timestamp": time.time(), "value": result}
        save_cache(cache)
    return result


def currency_score(currency: str, bis_rates: dict[str, float], cot: dict[str, float]) -> tuple[float, dict[str, Any]]:
    evidence: dict[str, Any] = {}
    score = 50.0

    rate = None
    if currency == "USD":
        try:
            rate = fred_series(FRED_SERIES["USD_RATE"])
            if rate is not None:
                evidence["fred_rate"] = rate
        except Exception as exc:
            evidence["fred_rate_error"] = str(exc)
    if rate is None:
        rate = bis_rates.get(currency)
        if rate is not None:
            evidence["bis_rate"] = rate

    if rate is not None:
        score += max(-20.0, min(20.0, rate * 2.0))

    gdp = None
    if currency == "USD":
        try:
            gdp = fred_series(FRED_SERIES["USD_GDP_GROWTH"])
            if gdp is not None:
                evidence["fred_gdp_growth"] = gdp
        except Exception as exc:
            evidence["fred_gdp_error"] = str(exc)
    if gdp is None:
        try:
            gdp = world_bank_gdp_growth(currency)
            if gdp is not None:
                evidence["world_bank_gdp_growth"] = gdp
        except Exception as exc:
            evidence["world_bank_gdp_error"] = str(exc)

    if gdp is not None:
        score += max(-15.0, min(15.0, gdp * 2.0))

    cot_net = cot.get(currency)
    if cot_net is not None:
        evidence["cftc_net_position"] = cot_net
        score += 10.0 if cot_net > 0 else -10.0 if cot_net < 0 else 0.0

    risk_sentiment = os.getenv("FUNDAMENTAL_RISK_SENTIMENT", "neutral").strip().lower()
    evidence["risk_sentiment"] = risk_sentiment
    if not evidence or set(evidence) <= {"risk_sentiment"}:
        if currency in RISK_ON:
            evidence["currency_group"] = "risk_on_currency"
        elif currency in RISK_OFF:
            evidence["currency_group"] = "risk_off_currency"
        else:
            evidence["currency_group"] = "limited_macro_coverage"
    if risk_sentiment == "risk_on":
        if currency in RISK_ON:
            score += 8.0
        if currency in RISK_OFF:
            score -= 8.0
    elif risk_sentiment == "risk_off":
        if currency in RISK_OFF:
            score += 8.0
        if currency in RISK_ON:
            score -= 8.0

    return max(0.0, min(100.0, score)), evidence


def evaluate_fundamentals(symbol: str, side: str = "") -> FundamentalResult:
    pair = split_pair(symbol)
    if pair is None:
        return FundamentalResult(reason="Unsupported symbol for fundamental currency-pair analysis")

    base, quote = pair
    evidence: dict[str, Any] = {"base": base, "quote": quote}
    bis_rates: dict[str, float] = {}
    cot: dict[str, float] = {}

    if env_bool("LAYER7_USE_BIS", True):
        try:
            bis_rates = bis_policy_rates()
            evidence["bis_available"] = bool(bis_rates)
        except Exception as exc:
            evidence["bis_error"] = str(exc)

    if env_bool("LAYER7_USE_CFTC", True):
        try:
            cot = cftc_positioning()
            evidence["cftc_available"] = bool(cot)
        except Exception as exc:
            evidence["cftc_error"] = str(exc)

    base_score, base_evidence = currency_score(base, bis_rates, cot)
    time.sleep(env_float("LAYER7_SOURCE_PAUSE_SECONDS", 0.0))
    quote_score, quote_evidence = currency_score(quote, bis_rates, cot)
    evidence["base_evidence"] = base_evidence
    evidence["quote_evidence"] = quote_evidence
    evidence["base_score"] = base_score
    evidence["quote_score"] = quote_score

    differential = base_score - quote_score
    bullish_threshold = env_float("LAYER7_BIAS_THRESHOLD", 8.0)
    if differential >= bullish_threshold:
        bias = "BULLISH"
        score = 50.0 + min(50.0, differential)
    elif differential <= -bullish_threshold:
        bias = "BEARISH"
        score = 50.0 + max(-50.0, differential)
    else:
        bias = "NEUTRAL"
        score = 50.0 + max(-10.0, min(10.0, differential))

    side = side.upper()
    if side == "BUY" and bias == "BEARISH":
        score = min(score, 35.0)
        reason = f"blocked: {symbol} BUY conflicts with bearish fundamentals"
    elif side == "SELL" and bias == "BULLISH":
        score = min(100.0 - score, 35.0)
        reason = f"blocked: {symbol} SELL conflicts with bullish fundamentals"
    else:
        reason = f"{bias} fundamental bias; base={base_score:.1f}, quote={quote_score:.1f}"

    return FundamentalResult(bias=bias, score=max(0.0, min(100.0, score)), reason=reason, evidence=evidence)
