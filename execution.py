import csv
from datetime import datetime, timezone
from pathlib import Path

from config import BASE_ASSET, CONTRACT_SIZE, PAPER_TRADING, QUOTE_ASSET, SYMBOL
from risk import (
    estimate_buy_total,
    estimate_sell_proceeds,
    live_trading_unlocked,
    net_profit_percent,
)

TRADE_LOG = Path("trades.csv")


def _append_trade(row):
    file_exists = TRADE_LOG.exists()
    with TRADE_LOG.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=row.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def _trade_row(symbol, mode, side, price, amount, fee_slippage, pnl_usdt=0.0, pnl_percent=0.0, reason="", ticket=""):
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "symbol": symbol,
        "side": side,
        "price": f"{price:.8f}",
        f"amount_{BASE_ASSET.lower()}": f"{amount:.8f}",
        f"fee_slippage_{QUOTE_ASSET.lower()}": f"{fee_slippage:.8f}",
        f"pnl_{QUOTE_ASSET.lower()}": f"{pnl_usdt:.8f}",
        "pnl_percent": f"{pnl_percent:.6f}",
        "ticket": ticket,
        "reason": reason,
    }


def _ticket_from_order(order):
    if isinstance(order, dict):
        return order.get("order") or order.get("ticket") or order.get("id")
    return None


def buy(
    exchange,
    state,
    price,
    amount,
    reason,
    entry_score=0.0,
    symbol=SYMBOL,
    contract_size=CONTRACT_SIZE,
    strategy_type="MOMENTUM",
    metadata=None,
):
    total_cost = estimate_buy_total(price, amount, contract_size)
    gross = price * amount * contract_size
    fee_slippage = total_cost - gross

    if PAPER_TRADING:
        state.paper_usdt -= total_cost
        state.paper_btc += amount
        position = state.open_position(
            symbol,
            price,
            amount,
            total_cost,
            entry_score,
            contract_size,
            strategy_type=strategy_type,
            metadata=metadata,
        )
        state.save()
        _append_trade(_trade_row(symbol, "paper", "BUY", price, amount, fee_slippage, reason=reason, ticket=position["id"]))
        return {"mode": "paper", "side": "BUY", "total_cost": total_cost, "position_id": position["id"]}

    if not live_trading_unlocked():
        raise RuntimeError("Live trading is locked. Enable PAPER_TRADING=false and set LIVE_TRADING_CONFIRMATION.")

    order = exchange.create_market_buy_order(symbol, amount)
    broker_ticket = _ticket_from_order(order)
    if hasattr(exchange, "latest_buy_position_ticket"):
        broker_ticket = exchange.latest_buy_position_ticket(symbol) or broker_ticket
    position = state.open_position(
        symbol,
        price,
        amount,
        total_cost,
        entry_score,
        contract_size,
        broker_ticket,
        strategy_type=strategy_type,
        metadata=metadata,
    )
    state.save()
    _append_trade(_trade_row(symbol, "live", "BUY", price, amount, fee_slippage, reason=reason, ticket=broker_ticket or position["id"]))
    return order


def sell_position(exchange, state, position, price, reason):
    amount = position["amount"]
    contract_size = position["entry_contract_size"]
    proceeds = estimate_sell_proceeds(price, amount, contract_size)
    gross = price * amount * contract_size
    fee_slippage = gross - proceeds
    pnl_usdt = proceeds - position["entry_total_cost"]
    pnl_percent = net_profit_percent(position["entry_total_cost"], proceeds)
    symbol = position["symbol"]

    if PAPER_TRADING:
        state.paper_btc -= amount
        state.paper_usdt += proceeds
        state.close_position(position["id"], proceeds)
        state.save()
        _append_trade(_trade_row(symbol, "paper", "SELL", price, amount, fee_slippage, pnl_usdt, pnl_percent, reason, position["id"]))
        return {"mode": "paper", "side": "SELL", "proceeds": proceeds, "pnl_usdt": pnl_usdt}

    if not live_trading_unlocked():
        raise RuntimeError("Live trading is locked. Enable PAPER_TRADING=false and set LIVE_TRADING_CONFIRMATION.")

    ticket = position.get("broker_ticket")
    if hasattr(exchange, "close_position") and ticket:
        order = exchange.close_position(symbol, amount, ticket)
    else:
        order = exchange.create_market_sell_order(symbol, amount)
    state.close_position(position["id"], proceeds)
    state.save()
    _append_trade(_trade_row(symbol, "live", "SELL", price, amount, fee_slippage, pnl_usdt, pnl_percent, reason, ticket or ""))
    return order


def sell(exchange, state, price, reason, symbol=SYMBOL):
    for position in list(state.positions):
        if position["symbol"] == symbol:
            return sell_position(exchange, state, position, price, reason)
    raise RuntimeError(f"No tracked position found for {symbol}")
