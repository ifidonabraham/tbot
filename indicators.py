import numpy as np
import pandas as pd
from config import SYMBOL, TIMEFRAME


def fetch_candles(exchange, limit=100):
    """Fetch historical OHLCV candles."""
    ohlcv = exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=limit)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df


def compute_indicators(df):
    """Apply technical indicators with pandas/numpy."""
    close = df['close'].astype(float)

    # RSI - measures overbought/oversold (0-100)
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    df['rsi'] = np.select(
        [(avg_loss == 0) & (avg_gain == 0), avg_loss == 0, avg_gain == 0],
        [50, 100, 0],
        default=rsi,
    )

    # MACD - momentum direction
    ema_12 = close.ewm(span=12, adjust=False).mean()
    ema_26 = close.ewm(span=26, adjust=False).mean()
    df['macd'] = ema_12 - ema_26
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['macd_signal']

    # Bollinger Bands - volatility measure
    df['bb_middle'] = close.rolling(window=20).mean()
    rolling_std = close.rolling(window=20).std()
    df['bb_upper'] = df['bb_middle'] + (2 * rolling_std)
    df['bb_lower'] = df['bb_middle'] - (2 * rolling_std)

    # EMA - trend direction
    df['ema_9'] = close.ewm(span=9, adjust=False).mean()
    df['ema_21'] = close.ewm(span=21, adjust=False).mean()

    return df
