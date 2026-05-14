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


def _trade_row(mode, side, price, amount, fee_slippage, pnl_usdt=0.0, pnl_percent=0.0, reason=""):
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "symbol": SYMBOL,
        "side": side,
        "price": f"{price:.8f}",
        f"amount_{BASE_ASSET.lower()}": f"{amount:.8f}",
        f"fee_slippage_{QUOTE_ASSET.lower()}": f"{fee_slippage:.8f}",
        f"pnl_{QUOTE_ASSET.lower()}": f"{pnl_usdt:.8f}",
        "pnl_percent": f"{pnl_percent:.6f}",
        "reason": reason,
    }


def buy(exchange, state, price, amount, reason):
    total_cost = estimate_buy_total(price, amount)
    gross = price * amount * CONTRACT_SIZE
    fee_slippage = total_cost - gross

    if PAPER_TRADING:
        state.paper_usdt -= total_cost
        state.paper_btc += amount
        state.open_position(price, amount, total_cost)
        state.save()
        _append_trade(_trade_row("paper", "BUY", price, amount, fee_slippage, reason=reason))
        return {"mode": "paper", "side": "BUY", "total_cost": total_cost}

    if not live_trading_unlocked():
        raise RuntimeError("Live trading is locked. Enable PAPER_TRADING=false and set LIVE_TRADING_CONFIRMATION.")

    order = exchange.create_market_buy_order(SYMBOL, amount)
    state.open_position(price, amount, total_cost)
    state.save()
    _append_trade(_trade_row("live", "BUY", price, amount, fee_slippage, reason=reason))
    return order


def sell(exchange, state, price, reason):
    amount = state.entry_amount
    proceeds = estimate_sell_proceeds(price, amount)
    gross = price * amount * CONTRACT_SIZE
    fee_slippage = gross - proceeds
    pnl_usdt = proceeds - state.entry_total_cost
    pnl_percent = net_profit_percent(state.entry_total_cost, proceeds)

    if PAPER_TRADING:
        state.paper_btc -= amount
        state.paper_usdt += proceeds
        state.close_position(proceeds)
        state.save()
        _append_trade(_trade_row("paper", "SELL", price, amount, fee_slippage, pnl_usdt, pnl_percent, reason))
        return {"mode": "paper", "side": "SELL", "proceeds": proceeds, "pnl_usdt": pnl_usdt}

    if not live_trading_unlocked():
        raise RuntimeError("Live trading is locked. Enable PAPER_TRADING=false and set LIVE_TRADING_CONFIRMATION.")

    order = exchange.create_market_sell_order(SYMBOL, amount)
    state.close_position(proceeds)
    state.save()
    _append_trade(_trade_row("live", "SELL", price, amount, fee_slippage, pnl_usdt, pnl_percent, reason))
    return order
