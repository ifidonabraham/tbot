import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from config import PAPER_STARTING_BTC, PAPER_STARTING_USDT

STATE_FILE = Path("trading_state.json")


@dataclass
class TradingState:
    paper_usdt: float = PAPER_STARTING_USDT
    paper_btc: float = PAPER_STARTING_BTC
    in_position: bool = False
    entry_price: float | None = None
    entry_amount: float = 0.0
    entry_total_cost: float = 0.0
    realized_pnl_usdt: float = 0.0
    daily_pnl_usdt: float = 0.0
    daily_trade_count: int = 0
    daily_date: str = ""

    @classmethod
    def load(cls):
        if not STATE_FILE.exists():
            state = cls()
            state.reset_daily_if_needed()
            state.save()
            return state

        with STATE_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)

        state = cls(**data)
        state.reset_daily_if_needed()
        return state

    def save(self):
        with STATE_FILE.open("w", encoding="utf-8") as file:
            json.dump(asdict(self), file, indent=2)

    def reset_daily_if_needed(self):
        today = datetime.now(timezone.utc).date().isoformat()
        if self.daily_date != today:
            self.daily_date = today
            self.daily_pnl_usdt = 0.0
            self.daily_trade_count = 0

    def open_position(self, price, amount, total_cost):
        self.in_position = True
        self.entry_price = price
        self.entry_amount = amount
        self.entry_total_cost = total_cost

    def close_position(self, proceeds):
        pnl = proceeds - self.entry_total_cost
        self.in_position = False
        self.entry_price = None
        self.entry_amount = 0.0
        self.entry_total_cost = 0.0
        self.realized_pnl_usdt += pnl
        self.daily_pnl_usdt += pnl
        self.daily_trade_count += 1
        return pnl
