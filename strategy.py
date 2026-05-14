def generate_signal(df):
    """
    Strategy: Momentum Scalping
    BUY when:  RSI < 40 AND MACD crosses above signal AND price near lower Bollinger Band
    SELL when: RSI > 65 AND MACD crosses below signal AND price near upper Bollinger Band
    """
    latest = df.iloc[-1]
    prev   = df.iloc[-2]
    
    rsi         = latest['rsi']
    macd        = latest['macd']
    macd_signal = latest['macd_signal']
    prev_macd   = prev['macd']
    prev_signal = prev['macd_signal']
    price       = latest['close']
    bb_lower    = latest['bb_lower']
    bb_upper    = latest['bb_upper']
    ema_9       = latest['ema_9']
    ema_21      = latest['ema_21']
    
    # MACD crossover detection
    macd_crossed_up   = (prev_macd < prev_signal) and (macd > macd_signal)
    macd_crossed_down = (prev_macd > prev_signal) and (macd < macd_signal)
    
    # BUY signal
    if rsi < 40 and macd_crossed_up and price <= bb_lower * 1.01 and ema_9 > ema_21:
        return "BUY"
    
    # SELL signal
    if rsi > 65 and macd_crossed_down and price >= bb_upper * 0.99:
        return "SELL"
    
    return "HOLD"