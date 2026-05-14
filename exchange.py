import ccxt
from config import BINANCE_API_KEY, BINANCE_SECRET, BROKER, SYMBOL, USE_TESTNET
from mt5_connector import MT5Broker

def get_exchange():
    if BROKER == "exness_mt5":
        return MT5Broker()

    exchange = ccxt.binance({
        'apiKey': BINANCE_API_KEY,
        'secret': BINANCE_SECRET,
        'enableRateLimit': True,
        'options': {
            'defaultType': 'spot',
        }
    })
    
    if USE_TESTNET:
        exchange.set_sandbox_mode(True)  # Switches to testnet automatically
    
    return exchange

def get_balance(exchange, currency="USDT"):
    balance = exchange.fetch_balance()
    return balance['free'].get(currency, 0)

def get_current_price(exchange, symbol=SYMBOL):
    ticker = exchange.fetch_ticker(symbol)
    return ticker['last']
