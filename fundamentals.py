from __future__ import annotations

import csv
import io
import os
import time
from dataclasses import dataclass, field
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
        timeout=20,
    )
    response.raise_for_status()
    return latest_numeric(list(reversed(response.json().get("observations", []))))


def world_bank_gdp_growth(currency: str) -> float | None:
    country = CURRENCY_COUNTRY.get(currency)
    if country is None:
        return None
    iso2, _ = country
    if iso2 == "EMU":
        return None
    response = requests.get(
        f"https://api.worldbank.org/v2/country/{iso2}/indicator/NY.GDP.MKTP.KD.ZG",
        params={"format": "json", "per_page": 5},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list) or len(payload) < 2:
        return None
    for row in payload[1]:
        value = row.get("value")
        if value is not None:
            return float(value)
    return None


def trading_economics_indicator(currency: str, indicator: str) -> float | None:
    key = os.getenv("TRADING_ECONOMICS_KEY", "").strip()
    if not key:
        return None
    country = CURRENCY_COUNTRY.get(currency)
    if country is None:
        return None
    _, country_name = country
    response = requests.get(
        f"https://api.tradingeconomics.com/historical/country/{country_name}/indicator/{indicator}",
        params={"c": key},
        timeout=20,
    )
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list):
        return None
    return latest_numeric(rows, "Value")


def bis_policy_rates() -> dict[str, float]:
    url = os.getenv("BIS_POLICY_RATES_URL", "").strip()
    if not url:
        return {}
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    rows = csv.DictReader(io.StringIO(response.text))
    result: dict[str, float] = {}
    for row in rows:
        currency = (row.get("currency") or row.get("Currency") or row.get("CUR") or "").upper()
        value = row.get("rate") or row.get("Rate") or row.get("value") or row.get("Value")
        if currency and value:
            try:
                result[currency] = float(value)
            except ValueError:
                continue
    return result


def cftc_positioning() -> dict[str, float]:
    url = os.getenv("CFTC_COT_URL", "https://www.cftc.gov/dea/newcot/FinFutWk.txt").strip()
    response = requests.get(url, timeout=30)
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
    return result


def currency_score(currency: str, bis_rates: dict[str, float], cot: dict[str, float]) -> tuple[float, dict[str, Any]]:
    evidence: dict[str, Any] = {}
    score = 50.0

    rate = None
    if currency == "USD":
        rate = fred_series(FRED_SERIES["USD_RATE"])
        if rate is not None:
            evidence["fred_rate"] = rate
    if rate is None:
        rate = bis_rates.get(currency)
        if rate is not None:
            evidence["bis_rate"] = rate
    if rate is None:
        rate = trading_economics_indicator(currency, "Interest Rate")
        if rate is not None:
            evidence["trading_economics_rate"] = rate

    if rate is not None:
        score += max(-20.0, min(20.0, rate * 2.0))

    gdp = None
    if currency == "USD":
        gdp = fred_series(FRED_SERIES["USD_GDP_GROWTH"])
        if gdp is not None:
            evidence["fred_gdp_growth"] = gdp
    if gdp is None:
        gdp = world_bank_gdp_growth(currency)
        if gdp is not None:
            evidence["world_bank_gdp_growth"] = gdp
    if gdp is None:
        gdp = trading_economics_indicator(currency, "GDP Growth Rate")
        if gdp is not None:
            evidence["trading_economics_gdp_growth"] = gdp

    if gdp is not None:
        score += max(-15.0, min(15.0, gdp * 2.0))

    cot_net = cot.get(currency)
    if cot_net is not None:
        evidence["cftc_net_position"] = cot_net
        score += 10.0 if cot_net > 0 else -10.0 if cot_net < 0 else 0.0

    risk_sentiment = os.getenv("FUNDAMENTAL_RISK_SENTIMENT", "neutral").strip().lower()
    evidence["risk_sentiment"] = risk_sentiment
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
