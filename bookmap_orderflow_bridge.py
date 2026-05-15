from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

try:
    import bookmap as bm
except ImportError as exc:  # pragma: no cover - this runs from the Bookmap Python API environment.
    raise SystemExit(
        "Bookmap Python API is not installed. Run this file from the Bookmap Python API environment."
    ) from exc


load_dotenv()

OUTPUT_PATH = Path(os.getenv("BOOKMAP_ORDERFLOW_PATH", "data/bookmap_orderflow.json"))
DEPTH_LEVELS = int(os.getenv("BOOKMAP_DEPTH_LEVELS", "10"))
SYMBOL_FILTER = {
    "".join(ch for ch in item.upper().replace("/", "") if ch.isalnum())
    for item in os.getenv("WATCHLIST", "").split(",")
    if item.strip()
}

state: dict[str, dict[str, Any]] = {}
alias_to_symbol: dict[str, str] = {}


def normalize_symbol(symbol: str) -> str:
    return "".join(ch for ch in symbol.upper().replace("C:", "").replace("/", "") if ch.isalnum())


def write_state() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        symbol: {
            "timestamp": values.get("timestamp", time.time()),
            "bid_depth": round(float(values.get("bid_depth", 0.0)), 6),
            "ask_depth": round(float(values.get("ask_depth", 0.0)), 6),
            "trade_delta": round(float(values.get("trade_delta", 0.0)), 6),
            "last_price": values.get("last_price"),
        }
        for symbol, values in state.items()
    }
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def ensure_symbol(alias: str) -> dict[str, Any] | None:
    symbol = alias_to_symbol.get(alias, normalize_symbol(alias))
    if SYMBOL_FILTER and symbol not in SYMBOL_FILTER:
        return None
    return state.setdefault(
        symbol,
        {
            "timestamp": time.time(),
            "bid_depth": 0.0,
            "ask_depth": 0.0,
            "trade_delta": 0.0,
            "last_price": None,
            "bids": {},
            "asks": {},
        },
    )


def depth_sum(levels: dict[float, float], reverse: bool) -> float:
    ordered = sorted(levels.items(), key=lambda item: item[0], reverse=reverse)
    return sum(size for _, size in ordered[:DEPTH_LEVELS])


def handle_depth(_addon: Any, alias: str, is_bid: bool, price: int, size: int) -> None:
    values = ensure_symbol(alias)
    if values is None:
        return
    level_price = float(price)
    level_size = float(size)
    book = values["bids"] if is_bid else values["asks"]
    if level_size <= 0:
        book.pop(level_price, None)
    else:
        book[level_price] = level_size
    values["bid_depth"] = depth_sum(values["bids"], reverse=True)
    values["ask_depth"] = depth_sum(values["asks"], reverse=False)
    values["timestamp"] = time.time()
    write_state()


def handle_trade(_addon: Any, alias: str, _price: int, size: int, trade_info: Any) -> None:
    values = ensure_symbol(alias)
    if values is None:
        return
    aggressor_order_id = str(getattr(trade_info, "aggressor_order_id", ""))
    is_buy = aggressor_order_id.endswith("1")
    signed_size = float(size) if is_buy else -float(size)
    values["trade_delta"] = float(values.get("trade_delta", 0.0)) * 0.90 + signed_size
    values["last_price"] = float(_price)
    values["timestamp"] = time.time()
    write_state()


def subscribe(addon: Any, alias: str, _instrument_info: Any) -> None:
    symbol = normalize_symbol(alias)
    alias_to_symbol[alias] = symbol
    if SYMBOL_FILTER and symbol not in SYMBOL_FILTER:
        return
    bm.subscribe_to_depth(addon, alias, 1)
    bm.subscribe_to_trades(addon, alias, 2)


def unsubscribe(_addon: Any, alias: str) -> None:
    alias_to_symbol.pop(alias, None)


if __name__ == "__main__":
    addon = bm.create_addon()
    bm.add_depth_handler(addon, handle_depth)
    bm.add_trades_handler(addon, handle_trade)
    bm.start_addon(addon, subscribe, unsubscribe)
