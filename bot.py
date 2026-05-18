import logging
import time

from config import (
    BASE_ASSET,
    BROKER,
    LOOP_SECONDS,
    MAX_NEW_POSITIONS_PER_LOOP,
    MAX_TRADE_AMOUNT,
    PAPER_TRADING,
    ENTRY_SCAN_SECONDS,
    POSITION_CHECK_SECONDS,
    QUOTE_ASSET,
    TRADE_AMOUNT,
    TREND_TIMEFRAME,
    USE_TESTNET,
    WATCHLIST,
)
from exchange import get_balance, get_current_price, get_exchange
from execution import open_trade, sell_position
from funnel_candidates import (
    FUNNEL_FALLBACK_TO_WATCHLIST,
    FUNNEL_MAX_SCALPER_SIDE_DISAGREEMENT,
    FUNNEL_TOP_N,
    USE_FUNNEL_CANDIDATES,
    load_funnel_candidates,
)
from indicators import compute_indicators, fetch_candles
from risk import (
    active_entry_threshold_for_state,
    calculate_trade_amount,
    entry_blockers,
    estimate_entry_required_quote,
    position_pnl,
    sell_reason_for_position,
)
from stat_arb import find_stat_arb_setups, stat_arb_exit_reason
from strategy import confirmed_entry_score, directional_exit_momentum_score, generate_signal, get_trend_status
from trading_state import TradingState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler(),
    ],
)


def _broker_profit(exchange, position):
    ticket = position.get("broker_ticket")
    if not ticket or not hasattr(exchange, "position_profit"):
        return None
    try:
        snapshot = exchange.position_profit(ticket)
    except Exception as exc:
        logging.warning("Broker PnL unavailable for %s ticket %s: %s", position["symbol"], ticket, exc)
        return None
    if not snapshot:
        return None
    return snapshot.get("profit")


def _broker_position_exists(exchange, position):
    ticket = position.get("broker_ticket")
    if not ticket or not hasattr(exchange, "position_profit"):
        return True
    try:
        return exchange.position_profit(ticket) is not None
    except Exception as exc:
        logging.warning("Broker position existence check failed for %s ticket %s: %s", position["symbol"], ticket, exc)
        return True


def _sync_broker_positions(exchange, state):
    if PAPER_TRADING or not hasattr(exchange, "open_positions"):
        return

    broker_positions = exchange.open_positions()
    tracked_tickets = {
        int(position.get("broker_ticket"))
        for position in state.positions
        if position.get("broker_ticket")
    }
    changed = False
    for broker_position in broker_positions:
        ticket = int(broker_position["ticket"])
        if ticket in tracked_tickets:
            continue
        symbol = broker_position["symbol"]
        side = broker_position["side"]
        amount = float(broker_position["volume"])
        entry_price = float(broker_position["price_open"])
        contract_size = exchange.contract_size(symbol) if hasattr(exchange, "contract_size") else 1.0
        entry_total_cost = entry_price * amount * contract_size
        if entry_total_cost <= 0:
            entry_total_cost = estimate_entry_required_quote(entry_price, amount, contract_size, side)
        state.open_position(
            symbol,
            entry_price,
            amount,
            entry_total_cost,
            entry_score=0.0,
            contract_size=contract_size,
            broker_ticket=ticket,
            strategy_type="SYNCED_MT5",
            metadata={"synced_from_mt5": True},
            side=side,
        )
        logging.warning(
            "Synced untracked MT5 position | %s %s %.8f @ %.5f ticket %s",
            side,
            symbol,
            amount,
            entry_price,
            ticket,
        )
        changed = True
    if changed:
        state.save()


def _position_snapshot(position, price, broker_profit=None):
    pnl, pnl_percent, _ = position_pnl(position, price, broker_profit)
    side = position.get("side", "BUY")
    return (
        f"Position: {side} {position['symbol']} {position['amount']:.8f} {BASE_ASSET} @ "
        f"${position['entry_price']:,.5f} | Unrealized PnL: {pnl:.4f} {QUOTE_ASSET} ({pnl_percent:.3f}%) | "
        f"Peak: {position.get('peak_pnl_money', 0.0):.4f} {QUOTE_ASSET}"
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
    buy_score, buy_details = confirmed_entry_score(df, trend_df, "BUY")
    sell_score, sell_details = confirmed_entry_score(df, trend_df, "SELL")
    score = max(buy_score, sell_score)
    details = buy_details if buy_score >= sell_score else sell_details
    signal = generate_signal(df, trend_df)
    return {
        "symbol": symbol,
        "df": df,
        "trend_df": trend_df,
        "price": price,
        "trend_status": trend_status,
        "score": score,
        "buy_score": buy_score,
        "sell_score": sell_score,
        "buy_details": buy_details,
        "sell_details": sell_details,
        "details": details,
        "signal": signal,
    }


def _manage_positions(exchange, state):
    for position in list(state.positions):
        symbol = position["symbol"]
        if not _broker_position_exists(exchange, position):
            logging.warning(
                "Forgetting stale local position %s ticket %s because MT5 has no matching broker position.",
                symbol,
                position.get("broker_ticket"),
            )
            state.forget_position(position["id"])
            state.save()
            continue
        context = _market_context(exchange, symbol)
        price = context["price"]
        broker_profit = _broker_profit(exchange, position)
        reason = None
        if position.get("strategy_type") == "STAT_ARB" and position.get("leader_symbol"):
            try:
                leader_context = _market_context(exchange, position["leader_symbol"])
                reason = stat_arb_exit_reason(position, context["df"], leader_context["df"])
            except Exception as exc:
                logging.warning("Stat-arb exit check failed for %s: %s", symbol, exc)
        reason = reason or sell_reason_for_position(position, price, context["df"], context["trend_df"], broker_profit)
        if (
            position.get("breakeven_armed")
            and not position.get("breakeven_sl_set")
            and hasattr(exchange, "move_stop_loss")
            and position.get("broker_ticket")
        ):
            try:
                exchange.move_stop_loss(symbol, position["broker_ticket"], position["entry_price"])
                position["breakeven_sl_set"] = True
                logging.info("Breakeven SL moved for %s ticket %s", symbol, position["broker_ticket"])
            except Exception as exc:
                logging.warning("Could not move breakeven SL for %s: %s", symbol, exc)
        state.update_position(position)
        exit_score, _ = directional_exit_momentum_score(
            context["df"],
            context["trend_df"],
            position.get("side", "BUY"),
        )

        logging.info(
            "Manage %s | Price: $%.5f | Exit momentum: %.2f | Peak PnL: %.3f%% | %s",
            symbol,
            price,
            exit_score,
            position.get("peak_pnl_percent", 0.0),
            _position_snapshot(position, price, broker_profit),
        )

        if reason:
            result = sell_position(exchange, state, position, price, reason)
            logging.info("Close executed for %s: %s", symbol, result)


def _scan_markets(exchange, state, quote_balance):
    candidates = []
    active_threshold = active_entry_threshold_for_state(state, quote_balance)
    funnel_by_symbol = {}

    if active_threshold is None:
        logging.warning("Daily loss limit reached. New entries are stopped for today.")
        return []

    symbols = WATCHLIST
    if USE_FUNNEL_CANDIDATES:
        funnel_candidates = load_funnel_candidates(limit=FUNNEL_TOP_N)
        funnel_by_symbol = {candidate.symbol: candidate for candidate in funnel_candidates}
        if funnel_candidates:
            symbols = list(dict.fromkeys(
                [candidate.symbol for candidate in funnel_candidates]
                + (WATCHLIST if FUNNEL_FALLBACK_TO_WATCHLIST else [])
            ))
            logging.info(
                "Funnel handoff active | Candidates: %s",
                ", ".join(f"{item.symbol}:{item.side}:{item.composite_score:.2f}" for item in funnel_candidates),
            )
        elif FUNNEL_FALLBACK_TO_WATCHLIST:
            logging.warning("Funnel handoff active but no candidates found. Falling back to WATCHLIST.")
        else:
            logging.warning("Funnel handoff active but no candidates found. New entries skipped.")
            return []

    for symbol in symbols:
        try:
            context = _market_context(exchange, symbol)
            funnel_candidate = funnel_by_symbol.get(symbol)
            if funnel_candidate:
                context["funnel_side"] = funnel_candidate.side
                context["funnel_score"] = funnel_candidate.composite_score
                context["funnel_reason"] = funnel_candidate.reason
                context["funnel_layer_scores"] = funnel_candidate.layer_scores
                context["funnel_layer5_state"] = funnel_candidate.layer5_state
                context["funnel_layer8_risk"] = funnel_candidate.layer8_risk
                context["funnel_expected_net_value"] = funnel_candidate.expected_net_value
            contract_size = exchange.contract_size(symbol) if hasattr(exchange, "contract_size") else 1.0
            amount = calculate_trade_amount(context["price"], quote_balance, contract_size)
            blocker_df = context["df"].iloc[:-1].copy() if len(context["df"]) > 1 else context["df"]
            context["amount"] = amount
            context["blocker_df"] = blocker_df
            context["blockers"] = []
            context["contract_size"] = contract_size
            candidates.append(context)

            logging.info(
                "Scan %s | Price: $%.5f | Signal: %s | Buy score: %.2f | Sell score: %.2f | Active threshold: %.2f | 5m Trend: %s | Amount: %.8f | Contract: %s | Blockers: %s",
                symbol,
                context["price"],
                context["signal"],
                context["buy_score"],
                context["sell_score"],
                active_threshold,
                context["trend_status"],
                amount,
                contract_size,
                "checked after BUY/SELL side selection",
            )
            logging.info("Score details %s: %s", symbol, context["details"])
            if funnel_candidate:
                logging.info(
                    "Funnel approval %s | Side: %s | Composite: %.2f | Reason: %s",
                    symbol,
                    funnel_candidate.side,
                    funnel_candidate.composite_score,
                    funnel_candidate.reason or "none",
                )
        except Exception as exc:
            logging.exception("Scan failed for %s: %s", symbol, exc)

    candidates.extend(find_stat_arb_setups(candidates))
    directional_candidates = []
    for item in candidates:
        if item.get("strategy_type") == "STAT_ARB":
            item["side"] = "BUY"
            directional_candidates.append(item)
            continue
        allowed_sides = ("BUY", "SELL")
        for side in allowed_sides:
            details = item["buy_details"] if side == "BUY" else item["sell_details"]
            score = item["buy_score"] if side == "BUY" else item["sell_score"]
            opposite_score = item["sell_score"] if side == "BUY" else item["buy_score"]
            side_item = {
                **item,
                "side": side,
                "score": score,
                "details": details,
                "blockers": entry_blockers(
                    state,
                    item["blocker_df"],
                    quote_balance,
                    item["amount"],
                    item["contract_size"],
                    item["symbol"],
                    side,
                ),
            }
            if item.get("funnel_score") is not None:
                side_item["score"] = score
                side_item["strategy_type"] = "FUNNEL_SCALP"
                side_item["details"] = {
                    **details,
                    "funnel_score": item["funnel_score"],
                    "funnel_reason": item.get("funnel_reason", ""),
                    "funnel_layer_scores": item.get("funnel_layer_scores", {}),
                    "funnel_layer5_state": item.get("funnel_layer5_state", ""),
                    "funnel_layer8_risk": item.get("funnel_layer8_risk", ""),
                    "funnel_expected_net_value": item.get("funnel_expected_net_value", 0.0),
                }
                if opposite_score - score > FUNNEL_MAX_SCALPER_SIDE_DISAGREEMENT:
                    side_item["blockers"] = [
                        *side_item["blockers"],
                        (
                            f"funnel side {side} conflicts with scalper score "
                            f"by {opposite_score - score:.2f}"
                        ),
                    ]
            if not side_item["details"].get("confirmed", False):
                side_item["blockers"] = [*side_item["blockers"], f"{side.lower()} confirmation candle not valid"]
            directional_candidates.append(side_item)

    tradable = [
        item
        for item in directional_candidates
        if item["score"] >= active_threshold
        and item["details"].get("confirmed", True)
        and not item["blockers"]
        and item["amount"] > 0
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

    last_entry_scan = 0.0
    while True:
        try:
            state.reset_daily_if_needed()
            _sync_broker_positions(exchange, state)

            if state.positions:
                _manage_positions(exchange, state)

            now = time.monotonic()
            if now - last_entry_scan >= ENTRY_SCAN_SECONDS:
                exchange_quote = get_balance(exchange, QUOTE_ASSET)
                quote_balance = state.paper_usdt if PAPER_TRADING else exchange_quote
                best_setups = _scan_markets(exchange, state, quote_balance)
                opened = 0
                for best in best_setups:
                    if opened >= MAX_NEW_POSITIONS_PER_LOOP:
                        break
                    result = open_trade(
                        exchange,
                        state,
                        best["price"],
                        best["amount"],
                        f"{best.get('strategy_type', 'MOMENTUM')}_{best['side']}_{best['symbol']}_SCORE_{best['score']:.2f}",
                        best["score"],
                        symbol=best["symbol"],
                        contract_size=best["contract_size"],
                        strategy_type=best.get("strategy_type", "MOMENTUM"),
                        metadata={
                            **({
                                "leader_symbol": best["details"].get("leader"),
                                "stat_arb_pair": best["details"].get("stat_arb_pair"),
                                "entry_divergence_percent": best["details"].get("divergence_percent", 0.0),
                            } if best.get("strategy_type") == "STAT_ARB" else {}),
                            "funnel_score": best["details"].get("funnel_score", 0.0),
                            "funnel_reason": best["details"].get("funnel_reason", ""),
                            "funnel_layer_scores": best["details"].get("funnel_layer_scores", {}),
                            "funnel_layer5_state": best["details"].get("funnel_layer5_state", ""),
                            "funnel_layer8_risk": best["details"].get("funnel_layer8_risk", ""),
                            "funnel_expected_net_value": best["details"].get("funnel_expected_net_value", 0.0),
                        },
                        side=best["side"],
                    )
                    logging.info("%s executed for %s: %s", best["side"], best["symbol"], result)
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
                last_entry_scan = now

            state.save()
            time.sleep(POSITION_CHECK_SECONDS)

        except KeyboardInterrupt:
            logging.info("Bot stopped by user.")
            break
        except Exception as exc:
            logging.exception("Error: %s", exc)
            time.sleep(30)


if __name__ == "__main__":
    run_bot()
