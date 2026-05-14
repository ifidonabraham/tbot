from config import (
    LIVE_TRADING_CONFIRMATION,
    LIVE_TRADING_UNLOCK_PHRASE,
    CONTRACT_SIZE,
    ACTIVE_ENTRY_SCORE_THRESHOLD,
    BREAKEVEN_TRIGGER_PERCENT,
    DAILY_LOSS_HALF_THRESHOLD,
    DAILY_LOSS_THREE_QUARTER_THRESHOLD,
    EXIT_MOMENTUM_FADE_SCORE,
    EXTENDED_TP_HOLD_SCORE,
    EXTENDED_TAKE_PROFIT_PERCENT,
    MIN_RESERVE_AMOUNT,
    MAX_CANDLE_RANGE_PERCENT,
    MAX_DAILY_LOSS_PERCENT,
    MAX_DAILY_LOSS_USDT,
    MAX_OPEN_POSITIONS,
    MAX_OPEN_POSITIONS_PER_SYMBOL,
    MAX_TRADES_PER_DAY,
    MAX_TRADE_AMOUNT,
    MIN_POSITION_VALUE,
    MIN_TRADE_AMOUNT,
    MIN_PROFIT_PERCENT,
    PAPER_TRADING,
    POSITION_RISK_FRACTION,
    RESERVE_PERCENT,
    SLIPPAGE_RATE,
    STOP_LOSS_PERCENT,
    TAKER_FEE_RATE,
    TAKE_PROFIT_PERCENT,
    TRAILING_PROFIT_GIVEBACK_PERCENT,
    TRAILING_ACTIVATION_PERCENT,
    TRADE_VOLUME_STEP,
    QUOTE_ASSET,
    MT5_ACCOUNT_MODE,
    VOLUME_MIN_RATIO,
)
from strategy import exit_momentum_score, volume_ratio


def live_trading_unlocked():
    if not PAPER_TRADING and MT5_ACCOUNT_MODE == "demo":
        return True

    return (
        not PAPER_TRADING
        and LIVE_TRADING_CONFIRMATION == LIVE_TRADING_UNLOCK_PHRASE
    )


def estimate_buy_total(price, amount, contract_size=CONTRACT_SIZE):
    gross = price * amount * contract_size
    fee = gross * TAKER_FEE_RATE
    slippage = gross * SLIPPAGE_RATE
    return gross + fee + slippage


def estimate_sell_proceeds(price, amount, contract_size=CONTRACT_SIZE):
    gross = price * amount * contract_size
    fee = gross * TAKER_FEE_RATE
    slippage = gross * SLIPPAGE_RATE
    return gross - fee - slippage


def position_value(price, amount, contract_size=CONTRACT_SIZE):
    return price * amount * contract_size


def _round_down_to_step(amount):
    if TRADE_VOLUME_STEP <= 0:
        return amount
    steps = int(amount / TRADE_VOLUME_STEP)
    return steps * TRADE_VOLUME_STEP


def reserve_amount(quote_balance):
    return max(MIN_RESERVE_AMOUNT, quote_balance * (RESERVE_PERCENT / 100))


def calculate_trade_amount(price, quote_balance, contract_size=CONTRACT_SIZE):
    deployable_quote = max(0.0, quote_balance - reserve_amount(quote_balance))
    risk_quote = deployable_quote * POSITION_RISK_FRACTION
    cost_per_unit = price * contract_size * (1 + TAKER_FEE_RATE + SLIPPAGE_RATE)
    if cost_per_unit <= 0:
        return 0.0

    amount = risk_quote / cost_per_unit
    amount = min(amount, MAX_TRADE_AMOUNT)
    amount = _round_down_to_step(amount)
    if amount < MIN_TRADE_AMOUNT:
        return 0.0
    return amount


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


def daily_loss_limit(quote_balance):
    percent_limit = quote_balance * (MAX_DAILY_LOSS_PERCENT / 100)
    if MAX_DAILY_LOSS_USDT > 0:
        return min(percent_limit, MAX_DAILY_LOSS_USDT)
    return percent_limit


def active_entry_threshold_for_state(state, quote_balance):
    loss_limit = daily_loss_limit(quote_balance)
    if loss_limit <= 0:
        return ACTIVE_ENTRY_SCORE_THRESHOLD

    daily_loss = max(0.0, -state.daily_pnl_usdt)
    if daily_loss >= loss_limit:
        return None
    if daily_loss >= loss_limit * 0.75:
        return DAILY_LOSS_THREE_QUARTER_THRESHOLD
    if daily_loss >= loss_limit * 0.50:
        return DAILY_LOSS_HALF_THRESHOLD
    return ACTIVE_ENTRY_SCORE_THRESHOLD


def round_trip_cost_percent():
    buy_cost = TAKER_FEE_RATE + SLIPPAGE_RATE
    sell_cost = TAKER_FEE_RATE + SLIPPAGE_RATE
    return (buy_cost + sell_cost) * 100


def buy_blockers(state, df, quote_balance, amount, contract_size=CONTRACT_SIZE, symbol=None):
    price = float(df.iloc[-1]["close"])
    dynamic_reserve = reserve_amount(quote_balance)
    required_quote = estimate_buy_total(price, amount, contract_size) + dynamic_reserve
    real_position_value = position_value(price, amount, contract_size)
    loss_limit = daily_loss_limit(quote_balance)
    blockers = []

    if amount <= 0:
        blockers.append("trade amount must be greater than zero")
    if real_position_value < MIN_POSITION_VALUE:
        blockers.append(f"position value below minimum {MIN_POSITION_VALUE:.2f} {QUOTE_ASSET}")
    if quote_balance < required_quote:
        blockers.append(f"insufficient {QUOTE_ASSET} after fee/slippage/reserve")
    if state.daily_pnl_usdt <= -loss_limit:
        blockers.append(f"daily loss limit reached ({loss_limit:.2f} {QUOTE_ASSET})")
    if MAX_TRADES_PER_DAY > 0 and state.daily_trade_count >= MAX_TRADES_PER_DAY:
        blockers.append("max daily trade count reached")
    if MAX_OPEN_POSITIONS > 0 and len(state.positions) >= MAX_OPEN_POSITIONS:
        blockers.append("max open positions reached")
    if symbol is not None:
        same_symbol = sum(1 for position in state.positions if position["symbol"] == symbol)
        if MAX_OPEN_POSITIONS_PER_SYMBOL > 0 and same_symbol >= MAX_OPEN_POSITIONS_PER_SYMBOL:
            blockers.append("max open positions for symbol reached")
    if candle_range_percent(df) > MAX_CANDLE_RANGE_PERCENT:
        blockers.append("latest candle range is too volatile")
    if volume_ratio(df) < VOLUME_MIN_RATIO:
        blockers.append(f"volume below {VOLUME_MIN_RATIO:.2f}x 20-candle average")
    if TAKE_PROFIT_PERCENT <= round_trip_cost_percent() + MIN_PROFIT_PERCENT:
        blockers.append("take-profit target does not clear fees/slippage plus minimum profit")

    return blockers


def sell_reason(state, current_price, df=None, trend_df=None):
    if not state.in_position or state.entry_total_cost <= 0:
        return None

    proceeds = estimate_sell_proceeds(current_price, state.entry_amount, state.entry_contract_size)
    pnl_percent = net_profit_percent(state.entry_total_cost, proceeds)
    state.peak_pnl_percent = max(state.peak_pnl_percent, pnl_percent)

    if pnl_percent <= -STOP_LOSS_PERCENT:
        return "STOP_LOSS"

    momentum_score = 50.0
    if df is not None:
        momentum_score, _ = exit_momentum_score(df, trend_df)

    if pnl_percent > 0 and momentum_score <= EXIT_MOMENTUM_FADE_SCORE:
        return "MOMENTUM_FADE"

    if (
        state.peak_pnl_percent >= TAKE_PROFIT_PERCENT
        and pnl_percent <= state.peak_pnl_percent - TRAILING_PROFIT_GIVEBACK_PERCENT
    ):
        return "TRAILING_GIVEBACK"

    if pnl_percent >= EXTENDED_TAKE_PROFIT_PERCENT:
        return "EXTENDED_TAKE_PROFIT"

    if pnl_percent >= TAKE_PROFIT_PERCENT and momentum_score < 65:
        return "TAKE_PROFIT"

    return None


def sell_reason_for_position(position, current_price, df=None, trend_df=None):
    proceeds = estimate_sell_proceeds(
        current_price,
        position["amount"],
        position["entry_contract_size"],
    )
    pnl_percent = net_profit_percent(position["entry_total_cost"], proceeds)
    position["peak_pnl_percent"] = max(position.get("peak_pnl_percent", 0.0), pnl_percent)

    if pnl_percent <= -STOP_LOSS_PERCENT:
        return "STOP_LOSS"

    momentum_score = 50.0
    if df is not None:
        momentum_score, _ = exit_momentum_score(df, trend_df)

    if pnl_percent >= BREAKEVEN_TRIGGER_PERCENT:
        position["breakeven_armed"] = True

    if position.get("breakeven_armed") and pnl_percent <= 0:
        return "BREAKEVEN_STOP"

    if pnl_percent <= -STOP_LOSS_PERCENT:
        return "STOP_LOSS"

    if position.get("strategy_type") == "STAT_ARB":
        return None

    if pnl_percent > 0 and momentum_score <= EXIT_MOMENTUM_FADE_SCORE:
        position["momentum_fade_count"] = position.get("momentum_fade_count", 0) + 1
        if position["momentum_fade_count"] >= 2:
            return "MOMENTUM_FADE"
    else:
        position["momentum_fade_count"] = 0

    if (
        position["peak_pnl_percent"] >= TRAILING_ACTIVATION_PERCENT
        and pnl_percent <= position["peak_pnl_percent"] - TRAILING_PROFIT_GIVEBACK_PERCENT
    ):
        return "TRAILING_GIVEBACK"

    if pnl_percent >= EXTENDED_TAKE_PROFIT_PERCENT:
        if momentum_score < EXTENDED_TP_HOLD_SCORE:
            return "EXTENDED_TAKE_PROFIT"
        return None

    if pnl_percent >= TAKE_PROFIT_PERCENT and momentum_score < 65:
        return "TAKE_PROFIT"

    return None


def sell_blockers(state, current_price, allow_strategy_sell=False):
    if not state.in_position:
        return ["no tracked open position"]

    proceeds = estimate_sell_proceeds(current_price, state.entry_amount, state.entry_contract_size)
    pnl_percent = net_profit_percent(state.entry_total_cost, proceeds)

    if allow_strategy_sell or pnl_percent >= MIN_PROFIT_PERCENT:
        return []

    return [f"net profit {pnl_percent:.3f}% below minimum {MIN_PROFIT_PERCENT:.3f}%"]
