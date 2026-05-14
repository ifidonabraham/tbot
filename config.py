import os
from dotenv import load_dotenv

load_dotenv()

def _bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _float(name, default):
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return float(value)


def _int(name, default):
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET = os.getenv("BINANCE_SECRET")
COINAPI_KEY = os.getenv("COINAPI_KEY")

BROKER = os.getenv("BROKER", "binance").strip().lower()

MT5_ACCOUNT_MODE = os.getenv("MT5_ACCOUNT_MODE", "real").strip().lower()
if MT5_ACCOUNT_MODE not in {"real", "demo"}:
    raise ValueError("MT5_ACCOUNT_MODE must be either 'real' or 'demo'")

MT5_REAL_LOGIN = _int("MT5_REAL_LOGIN", _int("MT5_LOGIN", 0))
MT5_REAL_PASSWORD = os.getenv("MT5_REAL_PASSWORD", os.getenv("MT5_PASSWORD", ""))
MT5_REAL_SERVER = os.getenv("MT5_REAL_SERVER", os.getenv("MT5_SERVER", ""))

MT5_DEMO_LOGIN = _int("MT5_DEMO_LOGIN", 0)
MT5_DEMO_PASSWORD = os.getenv("MT5_DEMO_PASSWORD", "")
MT5_DEMO_SERVER = os.getenv("MT5_DEMO_SERVER", "")

if MT5_ACCOUNT_MODE == "demo":
    MT5_LOGIN = MT5_DEMO_LOGIN
    MT5_PASSWORD = MT5_DEMO_PASSWORD
    MT5_SERVER = MT5_DEMO_SERVER
else:
    MT5_LOGIN = MT5_REAL_LOGIN
    MT5_PASSWORD = MT5_REAL_PASSWORD
    MT5_SERVER = MT5_REAL_SERVER

MT5_PATH = os.getenv("MT5_PATH", "")
MT5_SYMBOL = os.getenv("MT5_SYMBOL", "BTCUSD")
WATCHLIST = [
    item.strip()
    for item in os.getenv("WATCHLIST", MT5_SYMBOL).split(",")
    if item.strip()
]
MT5_VOLUME = _float("MT5_VOLUME", 0.01)
MT5_MAX_VOLUME = _float("MT5_MAX_VOLUME", MT5_VOLUME)
MT5_DEVIATION = _int("MT5_DEVIATION", 20)
MT5_MAGIC = _int("MT5_MAGIC", 260514)
MT5_TIMEOUT_MS = _int("MT5_TIMEOUT_MS", 30000)
CONTRACT_SIZE = _float("CONTRACT_SIZE", 100.0 if BROKER == "exness_mt5" else 1.0)

SYMBOL = os.getenv("SYMBOL", "BTC/USDT")
if BROKER == "exness_mt5":
    SYMBOL = MT5_SYMBOL
if not WATCHLIST:
    WATCHLIST = [SYMBOL]

TIMEFRAME = os.getenv("TIMEFRAME", "1m")
TREND_TIMEFRAME = os.getenv("TREND_TIMEFRAME", "5m")
TRADE_AMOUNT = _float("TRADE_AMOUNT", MT5_VOLUME if BROKER == "exness_mt5" else 0.001)
MAX_TRADE_AMOUNT = _float("MAX_TRADE_AMOUNT", MT5_MAX_VOLUME if BROKER == "exness_mt5" else 0.001)
MIN_TRADE_AMOUNT = _float("MIN_TRADE_AMOUNT", MT5_VOLUME if BROKER == "exness_mt5" else 0.0)
TRADE_VOLUME_STEP = _float("TRADE_VOLUME_STEP", 0.01 if BROKER == "exness_mt5" else 0.000001)
USE_TESTNET = _bool("USE_TESTNET", True)
BASE_ASSET = os.getenv("BASE_ASSET", "BTC")
QUOTE_ASSET = os.getenv("QUOTE_ASSET", "USD" if BROKER == "exness_mt5" else "USDT")

PAPER_TRADING = _bool("PAPER_TRADING", True)
LIVE_TRADING_CONFIRMATION = os.getenv("LIVE_TRADING_CONFIRMATION", "")
LIVE_TRADING_UNLOCK_PHRASE = "I_UNDERSTAND_REAL_MONEY_RISK"

PAPER_STARTING_USDT = _float("PAPER_STARTING_USDT", 10000.0)
PAPER_STARTING_BTC = _float("PAPER_STARTING_BTC", 0.0)

TAKER_FEE_RATE = _float("TAKER_FEE_RATE", 0.001)
SLIPPAGE_RATE = _float("SLIPPAGE_RATE", 0.0005)
MIN_PROFIT_PERCENT = _float("MIN_PROFIT_PERCENT", 0.30)
ENTRY_SCORE_THRESHOLD = _float("ENTRY_SCORE_THRESHOLD", 72.0)
ACTIVE_ENTRY_SCORE_THRESHOLD = _float("ACTIVE_ENTRY_SCORE_THRESHOLD", 40.0)
MAX_NEW_POSITIONS_PER_LOOP = _int("MAX_NEW_POSITIONS_PER_LOOP", 1)
EXIT_MOMENTUM_FADE_SCORE = _float("EXIT_MOMENTUM_FADE_SCORE", 42.0)
TRAILING_PROFIT_GIVEBACK_PERCENT = _float("TRAILING_PROFIT_GIVEBACK_PERCENT", 0.35)
EXTENDED_TAKE_PROFIT_PERCENT = _float("EXTENDED_TAKE_PROFIT_PERCENT", 1.80)
STOP_LOSS_PERCENT = _float("STOP_LOSS_PERCENT", 0.75)
TAKE_PROFIT_PERCENT = _float("TAKE_PROFIT_PERCENT", 1.00)
MAX_DAILY_LOSS_USDT = _float("MAX_DAILY_LOSS_USDT", 25.0)
MAX_DAILY_LOSS_PERCENT = _float("MAX_DAILY_LOSS_PERCENT", 20.0)
MAX_TRADES_PER_DAY = _int("MAX_TRADES_PER_DAY", 6)
MAX_OPEN_POSITIONS = _int("MAX_OPEN_POSITIONS", 3)
MAX_OPEN_POSITIONS_PER_SYMBOL = _int("MAX_OPEN_POSITIONS_PER_SYMBOL", 1)
MIN_USDT_RESERVE = _float("MIN_USDT_RESERVE", 10.0)
POSITION_RISK_FRACTION = _float("POSITION_RISK_FRACTION", 0.80)
MIN_POSITION_VALUE = _float("MIN_POSITION_VALUE", 1.0)
MAX_CANDLE_RANGE_PERCENT = _float("MAX_CANDLE_RANGE_PERCENT", 1.5)
LOOP_SECONDS = _int("LOOP_SECONDS", 60)
