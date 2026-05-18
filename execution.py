import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from config import BASE_ASSET, CONTRACT_SIZE, PAPER_TRADING, QUOTE_ASSET, SYMBOL
from risk import (
    estimate_close_value,
    estimate_buy_total,
    live_trading_unlocked,
    net_profit_percent,
)

TRADE_LOG = Path("trades.csv")
LAYER_KEYS = [f"layer{index}" for index in range(1, 9)]


def _append_trade(row):
    existing_rows = []
    existing_fields = []
    if TRADE_LOG.exists():
        with TRADE_LOG.open("r", newline="", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)
            existing_fields = [field for field in list(reader.fieldnames or []) if field is not None]
            existing_rows = [
                {key: value for key, value in existing_row.items() if key is not None}
                for existing_row in reader
            ]

    fieldnames = existing_fields + [key for key in row if key not in existing_fields]
    with TRADE_LOG.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(existing_rows)
        writer.writerow(row)


def _metadata_fields(metadata=None, strategy_type=""):
    metadata = metadata or {}
    layer_scores = metadata.get("funnel_layer_scores", {}) or {}
    fields = {
        "strategy_type": strategy_type or metadata.get("strategy_type", ""),
        "funnel_score": f"{float(metadata.get('funnel_score', 0.0) or 0.0):.6f}",
        "funnel_expected_net_value": f"{float(metadata.get('funnel_expected_net_value', 0.0) or 0.0):.6f}",
        "funnel_layer5_state": metadata.get("funnel_layer5_state", ""),
        "funnel_layer8_risk": metadata.get("funnel_layer8_risk", ""),
        "funnel_reason": metadata.get("funnel_reason", ""),
        "metadata_json": json.dumps(metadata, sort_keys=True, default=str),
    }
    for key in LAYER_KEYS:
        fields[f"{key}_score"] = f"{float(layer_scores.get(key, 0.0) or 0.0):.6f}"
    return fields


def _trade_row(
    symbol,
    mode,
    side,
    price,
    amount,
    fee_slippage,
    pnl_usdt=0.0,
    pnl_percent=0.0,
    reason="",
    ticket="",
    strategy_type="",
    metadata=None,
):
    row = {
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
    row.update(_metadata_fields(metadata, strategy_type))
    return row


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
    return open_trade(
        exchange,
        state,
        price,
        amount,
        reason,
        entry_score,
        symbol,
        contract_size,
        strategy_type,
        metadata,
        side="BUY",
    )


def open_trade(
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
    side="BUY",
):
    total_cost = estimate_buy_total(price, amount, contract_size)
    gross = price * amount * contract_size
    fee_slippage = total_cost - gross
    side = side.upper()

    if PAPER_TRADING:
        state.paper_usdt -= total_cost
        position = state.open_position(
            symbol,
            price,
            amount,
            total_cost,
            entry_score,
            contract_size,
            strategy_type=strategy_type,
            metadata=metadata,
            side=side,
        )
        state.save()
        _append_trade(_trade_row(symbol, "paper", side, price, amount, fee_slippage, reason=reason, ticket=position["id"], strategy_type=strategy_type, metadata=metadata))
        return {"mode": "paper", "side": side, "total_cost": total_cost, "position_id": position["id"]}

    if not live_trading_unlocked():
        raise RuntimeError("Live trading is locked. Enable PAPER_TRADING=false and set LIVE_TRADING_CONFIRMATION.")

    if side == "SELL":
        if hasattr(exchange, "open_market_sell_order"):
            order = exchange.open_market_sell_order(symbol, amount)
        else:
            order = exchange.create_market_sell_order(symbol, amount)
    else:
        order = exchange.create_market_buy_order(symbol, amount)
    broker_ticket = _ticket_from_order(order)
    if hasattr(exchange, "latest_position_ticket"):
        broker_ticket = exchange.latest_position_ticket(symbol, side) or broker_ticket
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
        side=side,
    )
    state.save()
    _append_trade(_trade_row(symbol, "live", side, price, amount, fee_slippage, reason=reason, ticket=broker_ticket or position["id"], strategy_type=strategy_type, metadata=metadata))
    return order


def sell_position(exchange, state, position, price, reason):
    amount = position["amount"]
    contract_size = position["entry_contract_size"]
    symbol = position["symbol"]
    entry_side = position.get("side", "BUY")
    ticket = position.get("broker_ticket")
    broker_profit = None
    if not PAPER_TRADING and ticket and hasattr(exchange, "position_profit"):
        broker_snapshot = exchange.position_profit(ticket)
        if broker_snapshot is not None:
            broker_profit = float(broker_snapshot.get("profit", 0.0))

    proceeds = (
        position["entry_total_cost"] + broker_profit
        if broker_profit is not None
        else estimate_close_value(position, price)
    )
    gross = price * amount * contract_size
    pnl_usdt = proceeds - position["entry_total_cost"]
    pnl_percent = net_profit_percent(position["entry_total_cost"], proceeds)
    close_side = "BUY" if entry_side == "SELL" else "SELL"
    if entry_side == "SELL":
        fee_slippage = estimate_buy_total(price, amount, contract_size) - gross
    else:
        fee_slippage = gross - proceeds

    if PAPER_TRADING:
        state.paper_usdt += proceeds
        state.close_position(position["id"], proceeds)
        state.save()
        _append_trade(_trade_row(symbol, "paper", close_side, price, amount, fee_slippage, pnl_usdt, pnl_percent, reason, position["id"], position.get("strategy_type", ""), position))
        return {"mode": "paper", "side": close_side, "proceeds": proceeds, "pnl_usdt": pnl_usdt}

    if not live_trading_unlocked():
        raise RuntimeError("Live trading is locked. Enable PAPER_TRADING=false and set LIVE_TRADING_CONFIRMATION.")

    if hasattr(exchange, "close_position") and ticket:
        try:
            order = exchange.close_position(symbol, amount, ticket, entry_side)
        except Exception:
            if hasattr(exchange, "position_profit") and exchange.position_profit(ticket) is None:
                order = {"already_closed": True, "ticket": ticket}
            else:
                raise
    else:
        order = exchange.create_market_buy_order(symbol, amount) if entry_side == "SELL" else exchange.create_market_sell_order(symbol, amount)
    state.close_position(position["id"], proceeds)
    state.save()
    _append_trade(_trade_row(symbol, "live", close_side, price, amount, fee_slippage, pnl_usdt, pnl_percent, reason, ticket or "", position.get("strategy_type", ""), position))
    return order


def sell(exchange, state, price, reason, symbol=SYMBOL):
    for position in list(state.positions):
        if position["symbol"] == symbol:
            return sell_position(exchange, state, position, price, reason)
    raise RuntimeError(f"No tracked position found for {symbol}")
