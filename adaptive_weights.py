from __future__ import annotations

import csv
import json
import os
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

LAYERS = [f"layer{index}" for index in range(1, 9)]
RANGE_STATES = {"RANGE_BUY", "RANGE_SELL"}


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


def _path(name: str, default: str) -> Path:
    return Path(os.getenv(name, default))


def _base_weights(mode: str) -> dict[str, float]:
    if mode == "range":
        return {
            "layer1": _float("SCORER_RANGE_WEIGHT_LAYER1", 15.0),
            "layer2": _float("SCORER_RANGE_WEIGHT_LAYER2", 15.0),
            "layer3": 0.0,
            "layer4": 0.0,
            "layer5": _float("SCORER_RANGE_WEIGHT_LAYER5", 25.0),
            "layer6": _float("SCORER_RANGE_WEIGHT_LAYER6", 20.0),
            "layer7": _float("SCORER_RANGE_WEIGHT_LAYER7", 15.0),
            "layer8": _float("SCORER_RANGE_WEIGHT_LAYER8", 10.0),
        }
    return {
        "layer1": _float("SCORER_WEIGHT_LAYER1", 20.0),
        "layer2": _float("SCORER_WEIGHT_LAYER2", 15.0),
        "layer3": _float("SCORER_WEIGHT_LAYER3", 15.0),
        "layer4": _float("SCORER_WEIGHT_LAYER4", 15.0),
        "layer5": _float("SCORER_WEIGHT_LAYER5", 10.0),
        "layer6": _float("SCORER_WEIGHT_LAYER6", 10.0),
        "layer7": _float("SCORER_WEIGHT_LAYER7", 10.0),
        "layer8": _float("SCORER_WEIGHT_LAYER8", 5.0),
    }


def _pnl_column(fieldnames: list[str]) -> str | None:
    for name in fieldnames:
        if name.startswith("pnl_") and name != "pnl_percent":
            return name
    return None


def _trade_mode(row: dict[str, str]) -> str:
    return "range" if row.get("funnel_layer5_state", "").strip().upper() in RANGE_STATES else "trend"


def _safe_float(value: str | None, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def _normalize(weights: dict[str, float], target_total: float) -> dict[str, float]:
    current_total = sum(max(value, 0.0) for value in weights.values())
    if current_total <= 0:
        return weights
    return {key: max(value, 0.0) * target_total / current_total for key, value in weights.items()}


def _learn_mode_weights(rows: list[dict[str, str]], mode: str) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    base = _base_weights(mode)
    learning_rate = _float("ADAPTIVE_WEIGHT_LEARNING_RATE", 0.20)
    min_factor = _float("ADAPTIVE_WEIGHT_MIN_FACTOR", 0.50)
    max_factor = _float("ADAPTIVE_WEIGHT_MAX_FACTOR", 1.50)
    totals: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)

    for row in rows:
        pnl = _safe_float(row.get("_pnl"))
        outcome = 1.0 if pnl > 0 else -1.0
        for layer in LAYERS:
            score = _safe_float(row.get(f"{layer}_score"))
            if score <= 0:
                continue
            totals[layer] += outcome * (score / 100.0)
            counts[layer] += 1

    learned = {}
    stats = {}
    for layer in LAYERS:
        if base[layer] <= 0:
            learned[layer] = 0.0
            stats[layer] = {"avg_signal_outcome": 0.0, "count": float(counts[layer])}
            continue
        average = totals[layer] / counts[layer] if counts[layer] else 0.0
        factor = max(min_factor, min(max_factor, 1.0 + learning_rate * average))
        learned[layer] = base[layer] * factor
        stats[layer] = {"avg_signal_outcome": average, "count": float(counts[layer])}

    return _normalize(learned, sum(base.values())), stats


def learn_adaptive_weights(
    trade_log: Path | str | None = None,
    output_path: Path | str | None = None,
) -> dict:
    trade_path = Path(trade_log) if trade_log is not None else _path("ADAPTIVE_TRADE_LOG_PATH", "trades.csv")
    weights_path = Path(output_path) if output_path is not None else _path("ADAPTIVE_WEIGHTS_PATH", "data/adaptive_weights.json")
    min_trades = _int("ADAPTIVE_WEIGHTS_MIN_TRADES", 30)

    rows: list[dict[str, str]] = []
    if trade_path.exists():
        with trade_path.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            pnl_name = _pnl_column(list(reader.fieldnames or []))
            if pnl_name:
                for row in reader:
                    pnl = _safe_float(row.get(pnl_name))
                    layer_values = [_safe_float(row.get(f"{layer}_score")) for layer in LAYERS]
                    if pnl == 0.0 or not any(layer_values):
                        continue
                    row["_pnl"] = str(pnl)
                    rows.append(row)

    trend_rows = [row for row in rows if _trade_mode(row) == "trend"]
    range_rows = [row for row in rows if _trade_mode(row) == "range"]
    trend_weights, trend_stats = _learn_mode_weights(trend_rows, "trend")
    range_weights, range_stats = _learn_mode_weights(range_rows, "range")
    payload = {
        "updated_at": datetime.now(UTC).isoformat(),
        "trade_count": len(rows),
        "min_trades": min_trades,
        "enabled": _bool("ADAPTIVE_WEIGHTS_ENABLED", True),
        "active": len(rows) >= min_trades,
        "trend_trade_count": len(trend_rows),
        "range_trade_count": len(range_rows),
        "trend": trend_weights,
        "range": range_weights,
        "stats": {
            "trend": trend_stats,
            "range": range_stats,
        },
    }

    weights_path.parent.mkdir(parents=True, exist_ok=True)
    with weights_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    return payload


def adaptive_weights_for_mode(mode: str, fallback: dict[str, float]) -> dict[str, float]:
    if not _bool("ADAPTIVE_WEIGHTS_ENABLED", True):
        return fallback
    weights_path = _path("ADAPTIVE_WEIGHTS_PATH", "data/adaptive_weights.json")
    min_trades = _int("ADAPTIVE_WEIGHTS_MIN_TRADES", 30)
    if not weights_path.exists():
        return fallback
    try:
        with weights_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return fallback
    if int(payload.get("trade_count", 0) or 0) < min_trades or not payload.get("active", False):
        return fallback
    learned = payload.get(mode, {})
    if not isinstance(learned, dict):
        return fallback
    return {layer: _safe_float(learned.get(layer), fallback[layer]) for layer in fallback}


if __name__ == "__main__":
    print(json.dumps(learn_adaptive_weights(), indent=2, sort_keys=True))
