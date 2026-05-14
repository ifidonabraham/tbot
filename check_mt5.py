from config import SYMBOL
from mt5_connector import MT5Broker


def main():
    broker = MT5Broker()
    ticker = broker.fetch_ticker(SYMBOL)
    balance = broker.fetch_balance()["free"]

    print(f"MT5 connected: {SYMBOL}")
    print(f"Bid: {ticker['bid']}")
    print(f"Ask: {ticker['ask']}")
    print(f"Last: {ticker['last']}")
    print("Account:")
    for key, value in balance.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
