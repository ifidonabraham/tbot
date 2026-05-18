from __future__ import annotations

import csv
import os
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from symbol_quality import is_tier1_or_validated

load_dotenv(override=True)


@dataclass(frozen=True)
class FunnelCandidate:
    symbol: str
    side: str
    composite_score: float
    reason: str
    layer_scores: dict[str, float]
    layer5_state: str = ""
    layer8_risk: str = ""
    expected_net_value: float = 0.0


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


def _float_from_row(row: dict[str, str], key: str, default: float) -> float:
    try:
        return float(row.get(key, "") or default)
    except ValueError:
        return default


USE_FUNNEL_CANDIDATES = _bool("USE_FUNNEL_CANDIDATES", True)
FUNNEL_OUTPUT_PATH = Path(os.getenv("FUNNEL_OUTPUT_PATH", "data/layer1_candidates.csv"))
FUNNEL_TOP_N = _int("FUNNEL_TOP_N", 50)
FUNNEL_FALLBACK_TO_WATCHLIST = _bool("FUNNEL_FALLBACK_TO_WATCHLIST", False)
FUNNEL_MAX_SCALPER_SIDE_DISAGREEMENT = _float("FUNNEL_MAX_SCALPER_SIDE_DISAGREEMENT", 15.0)
FUNNEL_CANDIDATE_MAX_AGE_SECONDS = _float("FUNNEL_CANDIDATE_MAX_AGE_SECONDS", 300.0)


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
    threshold = minimum_score if minimum_score is not None else _float("FUNNEL_HANDOFF_MIN_COMPOSITE_SCORE", 0.0)
    max_items = limit if limit is not None else FUNNEL_TOP_N

    if not csv_path.exists():
        return []
    if FUNNEL_CANDIDATE_MAX_AGE_SECONDS > 0:
        age_seconds = time.time() - csv_path.stat().st_mtime
        if age_seconds > FUNNEL_CANDIDATE_MAX_AGE_SECONDS:
            return []

    candidates: list[FunnelCandidate] = []
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            symbol = row.get("symbol", "").strip()
            if not symbol:
                continue
            if not is_tier1_or_validated(symbol):
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
            layer_scores = {}
            for layer in range(1, 9):
                key = f"layer{layer}_score"
                try:
                    layer_scores[f"layer{layer}"] = float(row.get(key, "0") or 0)
                except ValueError:
                    layer_scores[f"layer{layer}"] = 0.0
            candidates.append(
                FunnelCandidate(
                    symbol,
                    side,
                    composite_score,
                    reason,
                    layer_scores,
                    row.get("layer5_state", "").strip(),
                    row.get("layer8_risk", "").strip(),
                    _float_from_row(row, "expected_net_value", composite_score),
                )
            )

    candidates.sort(key=lambda item: (item.expected_net_value, item.composite_score), reverse=True)
    if max_items > 0:
        return candidates[:max_items]
    return candidates
