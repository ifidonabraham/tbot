import logging
import time

from config import (
    BASE_ASSET,
    BROKER,
    LOOP_SECONDS,
    MAX_TRADE_AMOUNT,
    PAPER_TRADING,
    QUOTE_ASSET,
    SYMBOL,
    TRADE_AMOUNT,
    USE_TESTNET,
)
from exchange import get_balance, get_current_price, get_exchange
from execution import buy, sell
from indicators import compute_indicators, fetch_candles
from risk import (
    buy_blockers,
    estimate_sell_proceeds,
    net_profit_percent,
    sell_blockers,
    sell_reason,
)
from strategy import generate_signal
from trading_state import TradingState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler(),
    ],
)


def _position_snapshot(state, price):
    if not state.in_position:
        return "Position: none"

    proceeds = estimate_sell_proceeds(price, state.entry_amount)
    pnl = proceeds - state.entry_total_cost
    pnl_percent = net_profit_percent(state.entry_total_cost, proceeds)
    return (
        f"Position: {state.entry_amount:.8f} {BASE_ASSET} @ ${state.entry_price:,.2f} | "
        f"Unrealized PnL: {pnl:.4f} {QUOTE_ASSET} ({pnl_percent:.3f}%)"
    )


def run_bot():
    exchange = get_exchange()
    state = TradingState.load()
    mode = "PAPER" if PAPER_TRADING else "LIVE"

    logging.info(
        "Bot started | Broker: %s | Mode: %s | Testnet: %s | Symbol: %s | Trade amount: %.8f | Max trade: %.8f",
        BROKER,
        mode,
        USE_TESTNET,
        SYMBOL,
        TRADE_AMOUNT,
        MAX_TRADE_AMOUNT,
    )

    while True:
        try:
            state.reset_daily_if_needed()

            df = fetch_candles(exchange)
            df = compute_indicators(df)
            signal = generate_signal(df)
            price = float(get_current_price(exchange, SYMBOL))

            exchange_quote = get_balance(exchange, QUOTE_ASSET)
            if hasattr(exchange, "open_position_volume"):
                exchange_base = exchange.open_position_volume(SYMBOL)
            else:
                exchange_base = get_balance(exchange, BASE_ASSET)
            quote_balance = state.paper_usdt if PAPER_TRADING else exchange_quote

            forced_sell_reason = sell_reason(state, price)
            if forced_sell_reason:
                signal = "SELL"

            logging.info(
                "Price: $%.2f | Signal: %s | %s | Daily PnL: %.4f %s | Trades today: %s",
                price,
                signal,
                _position_snapshot(state, price),
                state.daily_pnl_usdt,
                QUOTE_ASSET,
                state.daily_trade_count,
            )
            logging.info(
                "Balances | Exchange %s: %.2f %s: %.8f | Paper %s: %.2f %s: %.8f",
                QUOTE_ASSET,
                exchange_quote,
                BASE_ASSET,
                exchange_base,
                QUOTE_ASSET,
                state.paper_usdt,
                BASE_ASSET,
                state.paper_btc,
            )

            if signal == "BUY" and not state.in_position:
                blockers = buy_blockers(state, df, quote_balance)
                if blockers:
                    logging.info("BUY blocked: %s", "; ".join(blockers))
                else:
                    amount = min(TRADE_AMOUNT, MAX_TRADE_AMOUNT)
                    result = buy(exchange, state, price, amount, "STRATEGY_BUY")
                    logging.info("BUY executed: %s", result)

            elif signal == "SELL" and state.in_position:
                reason = forced_sell_reason or "STRATEGY_SELL"
                blockers = sell_blockers(state, price, allow_strategy_sell=bool(forced_sell_reason))
                if blockers:
                    logging.info("SELL blocked: %s", "; ".join(blockers))
                else:
                    result = sell(exchange, state, price, reason)
                    logging.info("SELL executed: %s", result)

            state.save()
            time.sleep(LOOP_SECONDS)

        except KeyboardInterrupt:
            logging.info("Bot stopped by user.")
            break
        except Exception as exc:
            logging.exception("Error: %s", exc)
            time.sleep(30)


if __name__ == "__main__":
    run_bot()
