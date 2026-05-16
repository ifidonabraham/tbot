from config import (
    LIVE_TRADING_CONFIRMATION,
    LIVE_TRADING_UNLOCK_PHRASE,
    BROKER,
    CONTRACT_SIZE,
    ACTIVE_ENTRY_SCORE_THRESHOLD,
    BREAKEVEN_TRIGGER_PERCENT,
    BREAKEVEN_TRIGGER_MONEY,
    DAILY_LOSS_HALF_THRESHOLD,
    DAILY_LOSS_THREE_QUARTER_THRESHOLD,
    EXIT_MOMENTUM_FADE_SCORE,
    EXTENDED_TP_HOLD_SCORE,
    EXTENDED_TAKE_PROFIT_PERCENT,
    EXTENDED_TAKE_PROFIT_MONEY,
    MICRO_PROFIT_EXIT_ENABLED,
    MICRO_PROFIT_FADE_SCORE,
    MICRO_PROFIT_LOCK_GIVEBACK_PERCENT,
    MICRO_PROFIT_GIVEBACK_MONEY,
    MICRO_PROFIT_MIN_MONEY,
    MICRO_PROFIT_MIN_PERCENT,
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
    LIVE_SPREAD_CHECK_ENABLED,
    MAX_LIVE_SPREAD_PERCENT,
    PAPER_TRADING,
    POSITION_RISK_FRACTION,
    RESERVE_PERCENT,
    SLIPPAGE_RATE,
    STOP_LOSS_PERCENT,
    STOP_LOSS_MONEY,
    TAKER_FEE_RATE,
    TAKE_PROFIT_PERCENT,
    TAKE_PROFIT_MONEY,
    TRAILING_PROFIT_GIVEBACK_PERCENT,
    TRAILING_ACTIVATION_PERCENT,
    TRAILING_ACTIVATION_MONEY,
    TRAILING_GIVEBACK_MONEY,
    TRADE_VOLUME_STEP,
    QUOTE_ASSET,
    MT5_ACCOUNT_MODE,
    VOLUME_MIN_RATIO,
)
from strategy import directional_exit_momentum_score, exit_momentum_score, volume_ratio

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover - non-MT5 brokers/tests can still use risk helpers.
    mt5 = None


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


def estimate_entry_cost(price, amount, contract_size=CONTRACT_SIZE):
    return estimate_buy_total(price, amount, contract_size)


def estimate_entry_required_quote(price, amount, contract_size=CONTRACT_SIZE, side="BUY"):
    gross = price * amount * contract_size
    side = (side or "BUY").upper()
    if side == "SELL":
        return gross * (TAKER_FEE_RATE + SLIPPAGE_RATE)
    return estimate_buy_total(price, amount, contract_size)


def estimate_close_value(position, current_price):
    side = position.get("side", "BUY")
    amount = position["amount"]
    contract_size = position["entry_contract_size"]
    if side == "SELL":
        gross_entry = position["entry_price"] * amount * contract_size
        gross_close = current_price * amount * contract_size
        close_fee_slippage = estimate_buy_total(current_price, amount, contract_size) - gross_close
        return gross_entry + (position["entry_price"] - current_price) * amount * contract_size - close_fee_slippage
    return estimate_sell_proceeds(current_price, amount, contract_size)


def position_pnl(position, current_price, broker_profit=None):
    if broker_profit is not None:
        try:
            pnl = float(broker_profit)
            close_value = position["entry_total_cost"] + pnl
            pnl_percent = net_profit_percent(position["entry_total_cost"], close_value)
            return pnl, pnl_percent, close_value
        except (TypeError, ValueError):
            pass
    close_value = estimate_close_value(position, current_price)
    pnl = close_value - position["entry_total_cost"]
    pnl_percent = net_profit_percent(position["entry_total_cost"], close_value)
    return pnl, pnl_percent, close_value


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


def money_threshold(position, explicit_money, percent):
    if explicit_money is not None and explicit_money > 0:
        return explicit_money
    return max(0.0, position.get("entry_total_cost", 0.0) * (percent / 100.0))


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


def live_spread_percent(symbol):
    if not LIVE_SPREAD_CHECK_ENABLED or symbol is None:
        return None
    if BROKER != "exness_mt5":
        return None
    if mt5 is None:
        return None
    mt5.symbol_select(symbol, True)
    tick = mt5.symbol_info_tick(symbol)
    if tick is None or tick.bid <= 0 or tick.ask <= 0:
        return None
    mid = (float(tick.bid) + float(tick.ask)) / 2.0
    if mid <= 0:
        return None
    return (float(tick.ask) - float(tick.bid)) / mid * 100.0


def entry_blockers(state, df, quote_balance, amount, contract_size=CONTRACT_SIZE, symbol=None, side="BUY"):
    price = float(df.iloc[-1]["close"])
    side = (side or "BUY").upper()
    dynamic_reserve = reserve_amount(quote_balance)
    required_quote = estimate_entry_required_quote(price, amount, contract_size, side) + dynamic_reserve
    real_position_value = position_value(price, amount, contract_size)
    loss_limit = daily_loss_limit(quote_balance)
    blockers = []

    if amount <= 0:
        blockers.append("trade amount must be greater than zero")
    if real_position_value < MIN_POSITION_VALUE:
        blockers.append(f"position value below minimum {MIN_POSITION_VALUE:.2f} {QUOTE_ASSET}")
    if quote_balance < required_quote:
        blockers.append(f"insufficient {QUOTE_ASSET} for {side.lower()} entry after fee/slippage/reserve")
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
    spread_percent = live_spread_percent(symbol)
    if spread_percent is not None and spread_percent > MAX_LIVE_SPREAD_PERCENT:
        blockers.append(f"live spread {spread_percent:.4f}% above max {MAX_LIVE_SPREAD_PERCENT:.4f}%")
    if TAKE_PROFIT_PERCENT <= round_trip_cost_percent() + MIN_PROFIT_PERCENT:
        blockers.append("take-profit target does not clear fees/slippage plus minimum profit")

    return blockers


def buy_blockers(state, df, quote_balance, amount, contract_size=CONTRACT_SIZE, symbol=None):
    return entry_blockers(state, df, quote_balance, amount, contract_size, symbol, "BUY")


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


def sell_reason_for_position(position, current_price, df=None, trend_df=None, broker_profit=None):
    pnl_money, pnl_percent, _ = position_pnl(position, current_price, broker_profit)
    position["peak_pnl_money"] = max(position.get("peak_pnl_money", 0.0), pnl_money)
    position["peak_pnl_percent"] = max(position.get("peak_pnl_percent", 0.0), pnl_percent)

    stop_loss_money = money_threshold(position, STOP_LOSS_MONEY, STOP_LOSS_PERCENT)
    breakeven_trigger_money = money_threshold(position, BREAKEVEN_TRIGGER_MONEY, BREAKEVEN_TRIGGER_PERCENT)
    take_profit_money = money_threshold(position, TAKE_PROFIT_MONEY, TAKE_PROFIT_PERCENT)
    extended_take_profit_money = money_threshold(position, EXTENDED_TAKE_PROFIT_MONEY, EXTENDED_TAKE_PROFIT_PERCENT)
    trailing_activation_money = money_threshold(position, TRAILING_ACTIVATION_MONEY, TRAILING_ACTIVATION_PERCENT)
    trailing_giveback_money = money_threshold(position, TRAILING_GIVEBACK_MONEY, TRAILING_PROFIT_GIVEBACK_PERCENT)
    micro_profit_min_money = money_threshold(position, MICRO_PROFIT_MIN_MONEY, MICRO_PROFIT_MIN_PERCENT)
    micro_giveback_money = money_threshold(position, MICRO_PROFIT_GIVEBACK_MONEY, MICRO_PROFIT_LOCK_GIVEBACK_PERCENT)

    if pnl_money <= -stop_loss_money:
        return "STOP_LOSS"

    momentum_score = 50.0
    if df is not None:
        momentum_score, _ = directional_exit_momentum_score(df, trend_df, position.get("side", "BUY"))

    if pnl_money >= breakeven_trigger_money:
        position["breakeven_armed"] = True

    if position.get("breakeven_armed") and pnl_money <= 0:
        return "BREAKEVEN_STOP"

    if pnl_money <= -stop_loss_money:
        return "STOP_LOSS"

    if position.get("strategy_type") == "STAT_ARB":
        return None

    if MICRO_PROFIT_EXIT_ENABLED and pnl_money >= micro_profit_min_money:
        if momentum_score <= MICRO_PROFIT_FADE_SCORE:
            return "MICRO_PROFIT_MOMENTUM_FADE"
        if (
            position.get("peak_pnl_money", 0.0) >= micro_profit_min_money
            and pnl_money <= position["peak_pnl_money"] - micro_giveback_money
        ):
            return "MICRO_PROFIT_GIVEBACK"

    if pnl_money > 0 and momentum_score <= EXIT_MOMENTUM_FADE_SCORE:
        position["momentum_fade_count"] = position.get("momentum_fade_count", 0) + 1
        if position["momentum_fade_count"] >= 2:
            return "MOMENTUM_FADE"
    else:
        position["momentum_fade_count"] = 0

    if (
        position["peak_pnl_money"] >= trailing_activation_money
        and pnl_money <= position["peak_pnl_money"] - trailing_giveback_money
    ):
        return "TRAILING_GIVEBACK"

    if pnl_money >= extended_take_profit_money:
        if momentum_score < EXTENDED_TP_HOLD_SCORE:
            return "EXTENDED_TAKE_PROFIT"
        return None

    if pnl_money >= take_profit_money and momentum_score < 65:
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
