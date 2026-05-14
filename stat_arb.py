from config import STAT_ARB_DIVERGENCE_PERCENT, STAT_ARB_ENABLED, STAT_ARB_LOOKBACK


CORRELATED_PAIRS = [
    ("EURUSD", "GBPUSD"),
    ("EURJPY", "GBPJPY"),
    ("AUDUSD", "NZDUSD"),
    ("AUDJPY", "NZDJPY"),
    ("EURAUD", "GBPAUD"),
    ("CADJPY", "CHFJPY"),
]


def _closed_return_percent(df, lookback):
    if df is None or len(df) < lookback + 2:
        return None
    closed = df.iloc[:-1]
    start = float(closed.iloc[-lookback]["close"])
    end = float(closed.iloc[-1]["close"])
    if start <= 0:
        return None
    return ((end - start) / start) * 100


def find_stat_arb_setups(contexts):
    if not STAT_ARB_ENABLED:
        return []

    by_symbol = {context["symbol"]: context for context in contexts}
    setups = []

    for first_symbol, second_symbol in CORRELATED_PAIRS:
        first = by_symbol.get(first_symbol)
        second = by_symbol.get(second_symbol)
        if first is None or second is None:
            continue

        first_return = _closed_return_percent(first["df"], STAT_ARB_LOOKBACK)
        second_return = _closed_return_percent(second["df"], STAT_ARB_LOOKBACK)
        if first_return is None or second_return is None:
            continue

        divergence = first_return - second_return
        if abs(divergence) < STAT_ARB_DIVERGENCE_PERCENT:
            continue

        leader = first if divergence > 0 else second
        lagger = second if divergence > 0 else first
        lagger_return = second_return if divergence > 0 else first_return

        if lagger_return < -STAT_ARB_DIVERGENCE_PERCENT:
            continue

        blockers = [
            blocker for blocker in lagger.get("blockers", [])
            if blocker != "confirmation candle not valid"
        ]

        setups.append({
            **lagger,
            "blockers": blockers,
            "score": min(100.0, 72.0 + abs(divergence) * 10),
            "signal": "BUY",
            "strategy_type": "STAT_ARB",
            "details": {
                **lagger.get("details", {}),
                "confirmed": True,
                "stat_arb_pair": f"{first_symbol}/{second_symbol}",
                "leader": leader["symbol"],
                "lagger": lagger["symbol"],
                "divergence_percent": round(abs(divergence), 4),
                "lookback": STAT_ARB_LOOKBACK,
            },
        })

    return setups


def stat_arb_exit_reason(position, lagger_df, leader_df):
    entry_divergence = float(position.get("entry_divergence_percent", 0.0))
    if entry_divergence <= 0:
        return None

    lagger_return = _closed_return_percent(lagger_df, STAT_ARB_LOOKBACK)
    leader_return = _closed_return_percent(leader_df, STAT_ARB_LOOKBACK)
    if lagger_return is None or leader_return is None:
        return None

    current_divergence = abs(leader_return - lagger_return)
    if current_divergence <= entry_divergence * 0.35:
        return "STAT_ARB_SPREAD_NORMALIZED"
    if current_divergence >= entry_divergence * 2.0:
        return "STAT_ARB_SPREAD_FAILED"
    return None
