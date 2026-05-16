from __future__ import annotations

import csv
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

DEFAULT_TIER1_SYMBOLS = {
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD",
    "EURJPY", "GBPJPY", "EURGBP", "AUDJPY", "CADJPY", "CHFJPY", "NZDJPY",
    "EURCHF", "EURAUD", "EURNZD", "EURCAD", "GBPAUD", "GBPCAD", "GBPCHF",
    "GBPNZD", "AUDCAD", "AUDCHF", "AUDNZD", "NZDCAD", "NZDCHF", "CADCHF",
    "XAUUSD", "XAGUSD",
}


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return float(value)


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


def normalize_symbol(symbol: str) -> str:
    cleaned = (symbol or "").upper().replace("C:", "").replace("/", "").replace("_", "")
    return "".join(ch for ch in cleaned if ch.isalnum())


def tier1_symbols() -> set[str]:
    configured = os.getenv("SCALPER_TIER1_SYMBOLS", "")
    if not configured.strip():
        return set(DEFAULT_TIER1_SYMBOLS)
    return {normalize_symbol(item) for item in configured.split(",") if item.strip()}


def _pnl_column(fieldnames: list[str]) -> str | None:
    for name in fieldnames:
        if name.startswith("pnl_") and name != "pnl_percent":
            return name
    return None


@lru_cache(maxsize=1)
def validated_profitable_symbols() -> set[str]:
    trade_path = Path(os.getenv("SYMBOL_QUALITY_TRADE_LOG_PATH", os.getenv("ADAPTIVE_TRADE_LOG_PATH", "trades.csv")))
    min_trades = _int("SYMBOL_QUALITY_MIN_TRADES", 100)
    min_win_rate = _float("SYMBOL_QUALITY_MIN_WIN_RATE", 0.55)
    min_net_pnl = _float("SYMBOL_QUALITY_MIN_NET_PNL", 0.0)
    if not trade_path.exists():
        return set()

    stats: dict[str, dict[str, float]] = {}
    with trade_path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        pnl_name = _pnl_column(list(reader.fieldnames or []))
        if not pnl_name:
            return set()
        for row in reader:
            symbol = normalize_symbol(row.get("symbol", ""))
            if not symbol:
                continue
            try:
                pnl = float(row.get(pnl_name, "0") or 0.0)
            except ValueError:
                continue
            if pnl == 0.0:
                continue
            item = stats.setdefault(symbol, {"trades": 0.0, "wins": 0.0, "pnl": 0.0})
            item["trades"] += 1.0
            item["wins"] += 1.0 if pnl > 0 else 0.0
            item["pnl"] += pnl

    validated = set()
    for symbol, item in stats.items():
        win_rate = item["wins"] / item["trades"] if item["trades"] else 0.0
        if item["trades"] >= min_trades and win_rate >= min_win_rate and item["pnl"] > min_net_pnl:
            validated.add(symbol)
    return validated


def is_tier1_or_validated(symbol: str) -> bool:
    if not _bool("SCALPER_TIER1_GATE_ENABLED", True):
        return True
    normalized = normalize_symbol(symbol)
    return normalized in tier1_symbols() or normalized in validated_profitable_symbols()
