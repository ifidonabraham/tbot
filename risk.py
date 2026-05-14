from config import (
    LIVE_TRADING_CONFIRMATION,
    LIVE_TRADING_UNLOCK_PHRASE,
    CONTRACT_SIZE,
    MAX_CANDLE_RANGE_PERCENT,
    MAX_DAILY_LOSS_USDT,
    MAX_TRADES_PER_DAY,
    MAX_TRADE_AMOUNT,
    MIN_PROFIT_PERCENT,
    MIN_USDT_RESERVE,
    PAPER_TRADING,
    SLIPPAGE_RATE,
    STOP_LOSS_PERCENT,
    TAKER_FEE_RATE,
    TAKE_PROFIT_PERCENT,
    TRADE_AMOUNT,
    QUOTE_ASSET,
    MT5_ACCOUNT_MODE,
)


def live_trading_unlocked():
    if not PAPER_TRADING and MT5_ACCOUNT_MODE == "demo":
        return True

    return (
        not PAPER_TRADING
        and LIVE_TRADING_CONFIRMATION == LIVE_TRADING_UNLOCK_PHRASE
    )


def estimate_buy_total(price, amount):
    gross = price * amount * CONTRACT_SIZE
    fee = gross * TAKER_FEE_RATE
    slippage = gross * SLIPPAGE_RATE
    return gross + fee + slippage


def estimate_sell_proceeds(price, amount):
    gross = price * amount * CONTRACT_SIZE
    fee = gross * TAKER_FEE_RATE
    slippage = gross * SLIPPAGE_RATE
    return gross - fee - slippage


def net_profit_percent(entry_total_cost, exit_proceeds):
    if entry_total_cost <= 0:
        return 0.0
    return ((exit_proceeds - entry_total_cost) / entry_total_cost) * 100


def candle_range_percent(df):
    latest = df.iloc[-1]
    close = float(latest["close"])
    if close <= 0:
        return 999.0
    high = float(latest["high"])
    low = float(latest["low"])
    return ((high - low) / close) * 100


def buy_blockers(state, df, quote_balance):
    amount = min(TRADE_AMOUNT, MAX_TRADE_AMOUNT)
    price = float(df.iloc[-1]["close"])
    required_quote = estimate_buy_total(price, amount) + MIN_USDT_RESERVE
    blockers = []

    if amount <= 0:
        blockers.append("trade amount must be greater than zero")
    if TRADE_AMOUNT > MAX_TRADE_AMOUNT:
        blockers.append("trade amount is above configured max trade amount")
    if quote_balance < required_quote:
        blockers.append(f"insufficient {QUOTE_ASSET} after fee/slippage/reserve")
    if state.daily_pnl_usdt <= -MAX_DAILY_LOSS_USDT:
        blockers.append("daily loss limit reached")
    if state.daily_trade_count >= MAX_TRADES_PER_DAY:
        blockers.append("max daily trade count reached")
    if candle_range_percent(df) > MAX_CANDLE_RANGE_PERCENT:
        blockers.append("latest candle range is too volatile")

    return blockers


def sell_reason(state, current_price):
    if not state.in_position or state.entry_total_cost <= 0:
        return None

    proceeds = estimate_sell_proceeds(current_price, state.entry_amount)
    pnl_percent = net_profit_percent(state.entry_total_cost, proceeds)

    if pnl_percent <= -STOP_LOSS_PERCENT:
        return "STOP_LOSS"
    if pnl_percent >= TAKE_PROFIT_PERCENT:
        return "TAKE_PROFIT"
    return None


def sell_blockers(state, current_price, allow_strategy_sell=False):
    if not state.in_position:
        return ["no tracked open position"]

    proceeds = estimate_sell_proceeds(current_price, state.entry_amount)
    pnl_percent = net_profit_percent(state.entry_total_cost, proceeds)

    if allow_strategy_sell or pnl_percent >= MIN_PROFIT_PERCENT:
        return []

    return [f"net profit {pnl_percent:.3f}% below minimum {MIN_PROFIT_PERCENT:.3f}%"]
