import logging
import time

from config import (
    BASE_ASSET,
    ACTIVE_ENTRY_SCORE_THRESHOLD,
    BROKER,
    LOOP_SECONDS,
    MAX_NEW_POSITIONS_PER_LOOP,
    MAX_TRADE_AMOUNT,
    PAPER_TRADING,
    QUOTE_ASSET,
    TRADE_AMOUNT,
    TREND_TIMEFRAME,
    USE_TESTNET,
    WATCHLIST,
)
from exchange import get_balance, get_current_price, get_exchange
from execution import buy, sell_position
from indicators import compute_indicators, fetch_candles
from risk import (
    buy_blockers,
    calculate_trade_amount,
    estimate_sell_proceeds,
    net_profit_percent,
    sell_reason_for_position,
)
from strategy import entry_score, exit_momentum_score, generate_signal, get_trend_status
from trading_state import TradingState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler(),
    ],
)


def _position_snapshot(position, price):
    proceeds = estimate_sell_proceeds(
        price,
        position["amount"],
        position["entry_contract_size"],
    )
    pnl = proceeds - position["entry_total_cost"]
    pnl_percent = net_profit_percent(position["entry_total_cost"], proceeds)
    return (
        f"Position: {position['symbol']} {position['amount']:.8f} {BASE_ASSET} @ "
        f"${position['entry_price']:,.5f} | Unrealized PnL: {pnl:.4f} {QUOTE_ASSET} ({pnl_percent:.3f}%)"
    )


def _market_context(exchange, symbol):
    df = compute_indicators(fetch_candles(exchange, symbol=symbol))
    try:
        trend_df = compute_indicators(fetch_candles(exchange, timeframe=TREND_TIMEFRAME, symbol=symbol))
    except Exception as exc:
        logging.warning("%s trend filter unavailable, falling back to primary timeframe only: %s", symbol, exc)
        trend_df = None

    price = float(get_current_price(exchange, symbol))
    trend_status = get_trend_status(trend_df)
    score, details = entry_score(df, trend_df)
    signal = generate_signal(df, trend_df)
    return {
        "symbol": symbol,
        "df": df,
        "trend_df": trend_df,
        "price": price,
        "trend_status": trend_status,
        "score": score,
        "details": details,
        "signal": signal,
    }


def _manage_positions(exchange, state):
    for position in list(state.positions):
        symbol = position["symbol"]
        context = _market_context(exchange, symbol)
        price = context["price"]
        reason = sell_reason_for_position(position, price, context["df"], context["trend_df"])
        state.update_position(position)
        exit_score, _ = exit_momentum_score(context["df"], context["trend_df"])

        logging.info(
            "Manage %s | Price: $%.5f | Exit momentum: %.2f | Peak PnL: %.3f%% | %s",
            symbol,
            price,
            exit_score,
            position.get("peak_pnl_percent", 0.0),
            _position_snapshot(position, price),
        )

        if reason:
            result = sell_position(exchange, state, position, price, reason)
            logging.info("SELL executed for %s: %s", symbol, result)


def _scan_markets(exchange, state, quote_balance):
    candidates = []

    for symbol in WATCHLIST:
        try:
            context = _market_context(exchange, symbol)
            contract_size = exchange.contract_size(symbol) if hasattr(exchange, "contract_size") else 1.0
            amount = calculate_trade_amount(context["price"], quote_balance, contract_size)
            blockers = buy_blockers(state, context["df"], quote_balance, amount, contract_size, symbol)
            context["amount"] = amount
            context["blockers"] = blockers
            context["contract_size"] = contract_size
            candidates.append(context)

            logging.info(
                "Scan %s | Price: $%.5f | Signal: %s | Score: %.2f | 5m Trend: %s | Amount: %.8f | Contract: %s | Blockers: %s",
                symbol,
                context["price"],
                context["signal"],
                context["score"],
                context["trend_status"],
                amount,
                contract_size,
                "; ".join(blockers) if blockers else "none",
            )
            logging.info("Score details %s: %s", symbol, context["details"])
        except Exception as exc:
            logging.exception("Scan failed for %s: %s", symbol, exc)

    tradable = [
        item
        for item in candidates
        if item["score"] >= ACTIVE_ENTRY_SCORE_THRESHOLD and not item["blockers"] and item["amount"] > 0
    ]
    return sorted(tradable, key=lambda item: item["score"], reverse=True)


def run_bot():
    exchange = get_exchange()
    state = TradingState.load()
    mode = "PAPER" if PAPER_TRADING else "LIVE"

    logging.info(
        "Bot started | Broker: %s | Mode: %s | Testnet: %s | Watchlist: %s | Trade amount cap: %.8f | Max trade: %.8f",
        BROKER,
        mode,
        USE_TESTNET,
        ", ".join(WATCHLIST),
        TRADE_AMOUNT,
        MAX_TRADE_AMOUNT,
    )

    while True:
        try:
            state.reset_daily_if_needed()

            exchange_quote = get_balance(exchange, QUOTE_ASSET)
            quote_balance = state.paper_usdt if PAPER_TRADING else exchange_quote

            if state.positions:
                _manage_positions(exchange, state)

            best_setups = _scan_markets(exchange, state, quote_balance)
            opened = 0
            for best in best_setups:
                if opened >= MAX_NEW_POSITIONS_PER_LOOP:
                    break
                result = buy(
                    exchange,
                    state,
                    best["price"],
                    best["amount"],
                    f"STRATEGY_BUY_{best['symbol']}_SCORE_{best['score']:.2f}",
                    best["score"],
                    symbol=best["symbol"],
                    contract_size=best["contract_size"],
                )
                logging.info("BUY executed for %s: %s", best["symbol"], result)
                opened += 1

            logging.info(
                "Balances | Exchange %s: %.2f | Paper %s: %.2f %s: %.8f | Tracked positions: %s | Daily PnL: %.4f %s | Trades today: %s",
                QUOTE_ASSET,
                exchange_quote,
                QUOTE_ASSET,
                state.paper_usdt,
                BASE_ASSET,
                state.paper_btc,
                len(state.positions),
                state.daily_pnl_usdt,
                QUOTE_ASSET,
                state.daily_trade_count,
            )

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
