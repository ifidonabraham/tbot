from datetime import datetime, timezone

from config import (
    MT5_DEVIATION,
    MT5_LOGIN,
    MT5_MAGIC,
    MT5_PASSWORD,
    MT5_PATH,
    MT5_SERVER,
    MT5_TIMEOUT_MS,
    QUOTE_ASSET,
    SYMBOL,
)

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None


TIMEFRAMES = {
    "1m": "TIMEFRAME_M1",
    "5m": "TIMEFRAME_M5",
    "15m": "TIMEFRAME_M15",
    "30m": "TIMEFRAME_M30",
    "1h": "TIMEFRAME_H1",
    "4h": "TIMEFRAME_H4",
    "1d": "TIMEFRAME_D1",
}


class MT5Broker:
    """Small adapter that gives MT5 the methods this bot expects."""

    def __init__(self):
        if mt5 is None:
            raise RuntimeError("MetaTrader5 is not installed. Run: pip install MetaTrader5")

        init_kwargs = {}
        if MT5_PATH:
            init_kwargs["path"] = MT5_PATH.replace("\\", "/")
        init_kwargs["timeout"] = MT5_TIMEOUT_MS
        if MT5_LOGIN and MT5_PASSWORD and MT5_SERVER:
            init_kwargs["login"] = MT5_LOGIN
            init_kwargs["password"] = MT5_PASSWORD
            init_kwargs["server"] = MT5_SERVER

        if not mt5.initialize(**init_kwargs):
            raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

        if not mt5.symbol_select(SYMBOL, True):
            raise RuntimeError(f"Could not select MT5 symbol {SYMBOL}: {mt5.last_error()}")

    def fetch_ohlcv(self, symbol, timeframe, limit=100):
        self.ensure_symbol(symbol)
        mt5_timeframe_name = TIMEFRAMES.get(timeframe)
        if mt5_timeframe_name is None:
            raise ValueError(f"Unsupported MT5 timeframe: {timeframe}")

        mt5_timeframe = getattr(mt5, mt5_timeframe_name)
        rates = mt5.copy_rates_from_pos(symbol, mt5_timeframe, 0, limit)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"No MT5 candle data for {symbol}: {mt5.last_error()}")

        rows = []
        for rate in rates:
            timestamp_ms = int(rate["time"]) * 1000
            rows.append([
                timestamp_ms,
                float(rate["open"]),
                float(rate["high"]),
                float(rate["low"]),
                float(rate["close"]),
                float(rate["tick_volume"]),
            ])
        return rows

    def fetch_ticker(self, symbol):
        self.ensure_symbol(symbol)
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(f"No MT5 tick data for {symbol}: {mt5.last_error()}")

        bid = float(tick.bid)
        ask = float(tick.ask)
        last = float(tick.last) if tick.last else (bid + ask) / 2
        return {
            "bid": bid,
            "ask": ask,
            "last": last,
            "datetime": datetime.now(timezone.utc).isoformat(),
        }

    def fetch_balance(self):
        account = mt5.account_info()
        if account is None:
            raise RuntimeError(f"No MT5 account info: {mt5.last_error()}")

        return {
            "free": {
                QUOTE_ASSET: float(account.margin_free),
                "BALANCE": float(account.balance),
                "EQUITY": float(account.equity),
                "MARGIN": float(account.margin),
            }
        }

    def create_market_buy_order(self, symbol, amount):
        self.ensure_symbol(symbol)
        return self._send_market_order(symbol, amount, mt5.ORDER_TYPE_BUY)

    def create_market_sell_order(self, symbol, amount):
        self.ensure_symbol(symbol)
        closed = self._close_long_positions(symbol, amount)
        if closed:
            return {"closed_positions": closed}
        return self._send_market_order(symbol, amount, mt5.ORDER_TYPE_SELL)

    def close_position(self, symbol, amount, position_ticket):
        self.ensure_symbol(symbol)
        return self._send_market_order(
            symbol,
            amount,
            mt5.ORDER_TYPE_SELL,
            position_ticket=position_ticket,
        )

    def move_stop_loss(self, symbol, position_ticket, stop_loss, take_profit=0.0):
        self.ensure_symbol(symbol)
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": symbol,
            "position": int(position_ticket),
            "sl": float(stop_loss),
            "tp": float(take_profit),
            "magic": MT5_MAGIC,
            "comment": "TradingBot breakeven",
        }
        result = mt5.order_send(request)
        if result is None:
            raise RuntimeError(f"MT5 stop-loss update returned None: {mt5.last_error()}")
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise RuntimeError(f"MT5 stop-loss update failed: retcode={result.retcode}, comment={result.comment}")
        return result._asdict()

    def _send_market_order(self, symbol, amount, order_type, position_ticket=None):
        self.ensure_symbol(symbol)
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(f"No MT5 tick data for {symbol}: {mt5.last_error()}")

        price = float(tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(amount),
            "type": order_type,
            "price": price,
            "deviation": MT5_DEVIATION,
            "magic": MT5_MAGIC,
            "comment": "TradingBot Python",
            "type_time": mt5.ORDER_TIME_GTC,
        }
        if position_ticket is not None:
            request["position"] = int(position_ticket)

        errors = []
        for filling_mode in self._filling_modes(symbol):
            request["type_filling"] = filling_mode
            result = mt5.order_send(request)
            if result is None:
                errors.append(f"None result: {mt5.last_error()}")
                continue
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                return result._asdict()
            errors.append(f"filling={filling_mode}, retcode={result.retcode}, comment={result.comment}")

        raise RuntimeError(f"MT5 order failed after filling retries: {' | '.join(errors)}")

    def _close_long_positions(self, symbol, amount):
        positions = mt5.positions_get(symbol=symbol)
        if not positions:
            return []

        remaining = float(amount)
        results = []
        for position in positions:
            if remaining <= 0:
                break
            if position.type != mt5.POSITION_TYPE_BUY:
                continue

            close_volume = min(float(position.volume), remaining)
            result = self._send_market_order(
                symbol,
                close_volume,
                mt5.ORDER_TYPE_SELL,
                position_ticket=position.ticket,
            )
            results.append(result)
            remaining -= close_volume

        return results

    def _filling_mode(self, symbol):
        return self._filling_modes(symbol)[0]

    def _filling_modes(self, symbol):
        info = mt5.symbol_info(symbol)
        if info is None:
            return [mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN]

        filling_mode = int(info.filling_mode)
        modes = []
        if filling_mode & mt5.ORDER_FILLING_FOK:
            modes.append(mt5.ORDER_FILLING_FOK)
        if filling_mode & mt5.ORDER_FILLING_IOC:
            modes.append(mt5.ORDER_FILLING_IOC)
        modes.append(mt5.ORDER_FILLING_RETURN)

        fallback_modes = [mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN]
        for mode in fallback_modes:
            if mode not in modes:
                modes.append(mode)
        return modes

    def ensure_symbol(self, symbol):
        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"Could not select MT5 symbol {symbol}: {mt5.last_error()}")

    def open_position_volume(self, symbol):
        positions = mt5.positions_get(symbol=symbol)
        if positions is None:
            return 0.0

        volume = 0.0
        for position in positions:
            if position.type == mt5.POSITION_TYPE_BUY:
                volume += float(position.volume)
            elif position.type == mt5.POSITION_TYPE_SELL:
                volume -= float(position.volume)
        return volume

    def latest_buy_position_ticket(self, symbol):
        positions = mt5.positions_get(symbol=symbol)
        if not positions:
            return None

        buys = [position for position in positions if position.type == mt5.POSITION_TYPE_BUY]
        if not buys:
            return None
        latest = max(buys, key=lambda position: position.time_msc)
        return int(latest.ticket)

    def contract_size(self, symbol):
        self.ensure_symbol(symbol)
        info = mt5.symbol_info(symbol)
        if info is None:
            raise RuntimeError(f"No MT5 symbol info for {symbol}: {mt5.last_error()}")
        return float(info.trade_contract_size)
