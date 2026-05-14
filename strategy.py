from config import ENTRY_SCORE_THRESHOLD


def _clamp(value, low=0.0, high=100.0):
    return max(low, min(high, value))


def get_trend_status(trend_df):
    if trend_df is None or len(trend_df) < 2:
        return "UNKNOWN"

    latest = trend_df.iloc[-1]
    prev = trend_df.iloc[-2]

    required = ["ema_9", "ema_21", "close"]
    if latest[required].isna().any() or prev[required].isna().any():
        return "UNKNOWN"

    if (
        latest["ema_9"] > latest["ema_21"]
        and latest["close"] > latest["ema_21"]
        and latest["ema_9"] >= prev["ema_9"]
    ):
        return "BULLISH"

    return "BEARISH"


def _rsi_entry_score(df):
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    older = df.iloc[-3]

    rsi = float(latest["rsi"])
    prev_rsi = float(prev["rsi"])
    older_rsi = float(older["rsi"])

    oversold_depth = _clamp((45 - rsi) * 2.2)
    turning_up = 25.0 if rsi > prev_rsi else 0.0
    rebound_speed = _clamp((rsi - older_rsi) * 3.0, 0.0, 25.0)
    return _clamp(oversold_depth + turning_up + rebound_speed)


def _macd_entry_score(df):
    latest = df.iloc[-1]
    prev = df.iloc[-2]

    macd = float(latest["macd"])
    signal = float(latest["macd_signal"])
    hist = float(latest["macd_hist"])
    prev_hist = float(prev["macd_hist"])
    price = max(float(latest["close"]), 1.0)

    crossed_up = prev_hist <= 0 < hist
    histogram_turn = hist > prev_hist
    strength = _clamp(abs(macd - signal) / price * 100000)
    return _clamp((35 if crossed_up else 0) + (25 if histogram_turn else 0) + strength)


def _bollinger_entry_score(df):
    latest = df.iloc[-1]

    price = float(latest["close"])
    lower = float(latest["bb_lower"])
    middle = float(latest["bb_middle"])
    if middle <= lower:
        return 0.0

    band_width = middle - lower
    breach_depth = (lower - price) / band_width
    near_band = (lower * 1.01 - price) / band_width

    if breach_depth > 0:
        return _clamp(70 + breach_depth * 60)
    return _clamp(near_band * 70)


def _volume_entry_score(df):
    if len(df) < 22:
        return 50.0

    latest_volume = float(df.iloc[-1]["volume"])
    avg_volume = float(df["volume"].iloc[-21:-1].mean())
    if avg_volume <= 0:
        return 50.0

    ratio = latest_volume / avg_volume
    return _clamp(35 + (ratio - 1.0) * 45)


def volume_ratio(df):
    if len(df) < 22:
        return 0.0

    latest_volume = float(df.iloc[-1]["volume"])
    avg_volume = float(df["volume"].iloc[-21:-1].mean())
    if avg_volume <= 0:
        return 0.0
    return latest_volume / avg_volume


def _trend_entry_score(trend_status):
    if trend_status == "BULLISH":
        return 100.0
    if trend_status == "UNKNOWN":
        return 65.0
    return 0.0


def _short_trend_entry_score(trend_status):
    if trend_status == "BEARISH":
        return 100.0
    if trend_status == "UNKNOWN":
        return 65.0
    return 0.0


def entry_score(df, trend_df=None):
    if len(df) < 30:
        return 0.0, {"reason": "not enough candles"}

    latest = df.iloc[-1]
    required = ["rsi", "macd", "macd_signal", "macd_hist", "bb_lower", "bb_middle", "ema_9", "ema_21", "close"]
    if latest[required].isna().any():
        return 0.0, {"reason": "indicator warmup"}

    trend_status = get_trend_status(trend_df)
    components = {
        "rsi": _rsi_entry_score(df),
        "macd": _macd_entry_score(df),
        "bollinger": _bollinger_entry_score(df),
        "volume": _volume_entry_score(df),
        "trend": _trend_entry_score(trend_status),
    }
    score = (
        components["rsi"] * 0.25
        + components["macd"] * 0.25
        + components["bollinger"] * 0.20
        + components["volume"] * 0.15
        + components["trend"] * 0.15
    )
    components["trend_status"] = trend_status
    components["volume_ratio"] = round(volume_ratio(df), 4)
    return round(score, 2), components


def _rsi_short_score(df):
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    older = df.iloc[-3]

    rsi = float(latest["rsi"])
    prev_rsi = float(prev["rsi"])
    older_rsi = float(older["rsi"])

    overbought_depth = _clamp((rsi - 55) * 2.2)
    turning_down = 25.0 if rsi < prev_rsi else 0.0
    rejection_speed = _clamp((older_rsi - rsi) * 3.0, 0.0, 25.0)
    return _clamp(overbought_depth + turning_down + rejection_speed)


def _macd_short_score(df):
    latest = df.iloc[-1]
    prev = df.iloc[-2]

    macd = float(latest["macd"])
    signal = float(latest["macd_signal"])
    hist = float(latest["macd_hist"])
    prev_hist = float(prev["macd_hist"])
    price = max(float(latest["close"]), 1.0)

    crossed_down = prev_hist >= 0 > hist
    histogram_turn = hist < prev_hist
    strength = _clamp(abs(macd - signal) / price * 100000)
    return _clamp((35 if crossed_down else 0) + (25 if histogram_turn else 0) + strength)


def _bollinger_short_score(df):
    latest = df.iloc[-1]

    price = float(latest["close"])
    upper = float(latest["bb_upper"])
    middle = float(latest["bb_middle"])
    if upper <= middle:
        return 0.0

    band_width = upper - middle
    breach_depth = (price - upper) / band_width
    near_band = (price - upper * 0.99) / band_width

    if breach_depth > 0:
        return _clamp(70 + breach_depth * 60)
    return _clamp(near_band * 70)


def short_entry_score(df, trend_df=None):
    if len(df) < 30:
        return 0.0, {"reason": "not enough candles"}

    latest = df.iloc[-1]
    required = ["rsi", "macd", "macd_signal", "macd_hist", "bb_upper", "bb_middle", "ema_9", "ema_21", "close"]
    if latest[required].isna().any():
        return 0.0, {"reason": "indicator warmup"}

    trend_status = get_trend_status(trend_df)
    components = {
        "rsi": _rsi_short_score(df),
        "macd": _macd_short_score(df),
        "bollinger": _bollinger_short_score(df),
        "volume": _volume_entry_score(df),
        "trend": _short_trend_entry_score(trend_status),
    }
    score = (
        components["rsi"] * 0.25
        + components["macd"] * 0.25
        + components["bollinger"] * 0.20
        + components["volume"] * 0.15
        + components["trend"] * 0.15
    )
    components["trend_status"] = trend_status
    components["volume_ratio"] = round(volume_ratio(df), 4)
    return round(score, 2), components


def confirmed_entry_score(df, trend_df=None, side="BUY"):
    if len(df) < 31:
        return 0.0, {"reason": "not enough candles for confirmation", "confirmed": False}

    signal_df = df.iloc[:-1].copy()
    if side == "SELL":
        score, details = short_entry_score(signal_df, trend_df)
    else:
        score, details = entry_score(signal_df, trend_df)
    signal_level = float(signal_df.iloc[-1]["close"])
    confirmation_open = float(df.iloc[-1]["open"])
    confirmed = confirmation_open <= signal_level if side == "SELL" else confirmation_open >= signal_level

    details = {
        **details,
        "side": side,
        "confirmed": confirmed,
        "signal_level": round(signal_level, 5),
        "confirmation_open": round(confirmation_open, 5),
    }
    if not confirmed:
        direction = "above" if side == "SELL" else "below"
        details["reason"] = f"confirmation candle opened {direction} signal level"
    return score, details


def exit_momentum_score(df, trend_df=None):
    if len(df) < 22:
        return 50.0, {"reason": "not enough candles"}

    latest = df.iloc[-1]
    prev = df.iloc[-2]
    trend_status = get_trend_status(trend_df)

    rsi = float(latest["rsi"])
    prev_rsi = float(prev["rsi"])
    hist = float(latest["macd_hist"])
    prev_hist = float(prev["macd_hist"])
    price = float(latest["close"])
    ema_9 = float(latest["ema_9"])
    ema_21 = float(latest["ema_21"])

    components = {
        "rsi": _clamp(50 + (rsi - 50) * 1.3 + (rsi - prev_rsi) * 4),
        "macd": _clamp(50 + (hist - prev_hist) * 800 + hist * 800),
        "ema": 75.0 if ema_9 > ema_21 and price > ema_9 else 35.0,
        "trend": _trend_entry_score(trend_status),
        "volume": _volume_entry_score(df),
    }
    score = (
        components["rsi"] * 0.20
        + components["macd"] * 0.30
        + components["ema"] * 0.20
        + components["trend"] * 0.15
        + components["volume"] * 0.15
    )
    components["trend_status"] = trend_status
    return round(score, 2), components


def short_exit_momentum_score(df, trend_df=None):
    if len(df) < 22:
        return 50.0, {"reason": "not enough candles"}

    latest = df.iloc[-1]
    prev = df.iloc[-2]
    trend_status = get_trend_status(trend_df)

    rsi = float(latest["rsi"])
    prev_rsi = float(prev["rsi"])
    hist = float(latest["macd_hist"])
    prev_hist = float(prev["macd_hist"])
    price = float(latest["close"])
    ema_9 = float(latest["ema_9"])
    ema_21 = float(latest["ema_21"])

    components = {
        "rsi": _clamp(50 + (50 - rsi) * 1.3 + (prev_rsi - rsi) * 4),
        "macd": _clamp(50 + (prev_hist - hist) * 800 - hist * 800),
        "ema": 75.0 if ema_9 < ema_21 and price < ema_9 else 35.0,
        "trend": _short_trend_entry_score(trend_status),
        "volume": _volume_entry_score(df),
    }
    score = (
        components["rsi"] * 0.20
        + components["macd"] * 0.30
        + components["ema"] * 0.20
        + components["trend"] * 0.15
        + components["volume"] * 0.15
    )
    components["trend_status"] = trend_status
    return round(score, 2), components


def directional_exit_momentum_score(df, trend_df=None, side="BUY"):
    if side == "SELL":
        return short_exit_momentum_score(df, trend_df)
    return exit_momentum_score(df, trend_df)


def generate_signal(df, trend_df=None):
    buy_score, _ = entry_score(df, trend_df)
    sell_score, _ = short_entry_score(df, trend_df)
    if buy_score >= ENTRY_SCORE_THRESHOLD and buy_score >= sell_score:
        return "BUY"
    if sell_score >= ENTRY_SCORE_THRESHOLD:
        return "SELL"

    return "HOLD"
