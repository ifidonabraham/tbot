from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)


@dataclass(frozen=True)
class FunnelCandidate:
    symbol: str
    side: str
    composite_score: float
    reason: str


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


USE_FUNNEL_CANDIDATES = _bool("USE_FUNNEL_CANDIDATES", True)
FUNNEL_OUTPUT_PATH = Path(os.getenv("FUNNEL_OUTPUT_PATH", "data/layer1_candidates.csv"))
FUNNEL_TOP_N = _int("FUNNEL_TOP_N", 5)
FUNNEL_FALLBACK_TO_WATCHLIST = _bool("FUNNEL_FALLBACK_TO_WATCHLIST", False)
FUNNEL_MAX_SCALPER_SIDE_DISAGREEMENT = _float("FUNNEL_MAX_SCALPER_SIDE_DISAGREEMENT", 15.0)


def _resolve_side(row: dict[str, str]) -> str | None:
    layer5_state = row.get("layer5_state", "").strip().upper()
    breakout_side = row.get("breakout_side", "").strip().upper()
    trend_state = row.get("state", "").strip().upper()

    if layer5_state == "RANGE_BUY":
        return "BUY"
    if layer5_state == "RANGE_SELL":
        return "SELL"
    if breakout_side in {"BUY", "SELL"}:
        return breakout_side
    if trend_state == "TRENDING_UP":
        return "BUY"
    if trend_state == "TRENDING_DOWN":
        return "SELL"
    return None


def load_funnel_candidates(
    path: Path | None = None,
    minimum_score: float | None = None,
    limit: int | None = None,
) -> list[FunnelCandidate]:
    csv_path = path or FUNNEL_OUTPUT_PATH
    threshold = minimum_score if minimum_score is not None else _float("SCORER_MIN_COMPOSITE_SCORE", 65.0)
    max_items = limit if limit is not None else FUNNEL_TOP_N

    if not csv_path.exists():
        return []

    candidates: list[FunnelCandidate] = []
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            symbol = row.get("symbol", "").strip()
            if not symbol:
                continue
            if row.get("layer8_risk", "").strip().upper() == "BLOCKED":
                continue
            try:
                composite_score = float(row.get("composite_score", "0") or 0)
            except ValueError:
                continue
            if composite_score < threshold:
                continue
            side = _resolve_side(row)
            if side is None:
                continue
            reason = row.get("composite_reason", "").strip()
            if row.get("layer8_reason", "").strip():
                reason = f"{reason}; {row['layer8_reason'].strip()}" if reason else row["layer8_reason"].strip()
            candidates.append(FunnelCandidate(symbol, side, composite_score, reason))

    candidates.sort(key=lambda item: item.composite_score, reverse=True)
    if max_items > 0:
        return candidates[:max_items]
    return candidates
