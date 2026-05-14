import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from config import PAPER_STARTING_BTC, PAPER_STARTING_USDT

STATE_FILE = Path("trading_state.json")


@dataclass
class TradingState:
    paper_usdt: float = PAPER_STARTING_USDT
    paper_btc: float = PAPER_STARTING_BTC
    in_position: bool = False
    position_symbol: str = ""
    entry_price: float | None = None
    entry_amount: float = 0.0
    entry_total_cost: float = 0.0
    entry_contract_size: float = 1.0
    entry_score: float = 0.0
    peak_pnl_percent: float = 0.0
    realized_pnl_usdt: float = 0.0
    daily_pnl_usdt: float = 0.0
    daily_trade_count: int = 0
    daily_date: str = ""
    positions: list = field(default_factory=list)

    @classmethod
    def load(cls):
        if not STATE_FILE.exists():
            state = cls()
            state.reset_daily_if_needed()
            state.save()
            return state

        with STATE_FILE.open("r", encoding="utf-8-sig") as file:
            data = json.load(file)

        state = cls(**{**asdict(cls()), **data})
        if state.positions is None:
            state.positions = []
        if state.in_position and not state.positions:
            state.positions.append({
                "id": str(uuid4()),
                "symbol": state.position_symbol,
                "entry_price": state.entry_price,
                "amount": state.entry_amount,
                "entry_total_cost": state.entry_total_cost,
                "entry_contract_size": state.entry_contract_size,
                "entry_score": state.entry_score,
                "peak_pnl_percent": state.peak_pnl_percent,
                "broker_ticket": None,
            })
        state._normalize_positions()
        state.reset_daily_if_needed()
        state._sync_legacy_position()
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

    def open_position(
        self,
        symbol,
        price,
        amount,
        total_cost,
        entry_score=0.0,
        contract_size=1.0,
        broker_ticket=None,
        strategy_type="MOMENTUM",
        metadata=None,
        side="BUY",
    ):
        metadata = metadata or {}
        position = {
            "id": str(uuid4()),
            "symbol": symbol,
            "entry_price": price,
            "amount": amount,
            "entry_total_cost": total_cost,
            "entry_contract_size": contract_size,
            "entry_score": entry_score,
            "side": side,
            "peak_pnl_percent": 0.0,
            "broker_ticket": broker_ticket,
            "breakeven_armed": False,
            "breakeven_sl_set": False,
            "momentum_fade_count": 0,
            "strategy_type": strategy_type,
            **metadata,
        }
        self.positions.append(position)
        self._sync_legacy_position()
        return position

    def close_position(self, position_id, proceeds):
        position = self.get_position(position_id)
        if position is None:
            return 0.0

        pnl = proceeds - position["entry_total_cost"]
        self.positions = [item for item in self.positions if item["id"] != position_id]
        self.realized_pnl_usdt += pnl
        self.daily_pnl_usdt += pnl
        self.daily_trade_count += 1
        self._sync_legacy_position()
        return pnl

    def get_position(self, position_id):
        for position in self.positions:
            if position["id"] == position_id:
                return position
        return None

    def update_position(self, position):
        for index, existing in enumerate(self.positions):
            if existing["id"] == position["id"]:
                self.positions[index] = position
                self._sync_legacy_position()
                return

    def _normalize_positions(self):
        for position in self.positions:
            position.setdefault("breakeven_armed", False)
            position.setdefault("breakeven_sl_set", False)
            position.setdefault("momentum_fade_count", 0)
            position.setdefault("strategy_type", "MOMENTUM")
            position.setdefault("side", "BUY")

    def _sync_legacy_position(self):
        if not self.positions:
            self.in_position = False
            self.position_symbol = ""
            self.entry_price = None
            self.entry_amount = 0.0
            self.entry_total_cost = 0.0
            self.entry_contract_size = 1.0
            self.entry_score = 0.0
            self.peak_pnl_percent = 0.0
            return

        latest = self.positions[-1]
        self.in_position = True
        self.position_symbol = latest["symbol"]
        self.entry_price = latest["entry_price"]
        self.entry_amount = latest["amount"]
        self.entry_total_cost = latest["entry_total_cost"]
        self.entry_contract_size = latest["entry_contract_size"]
        self.entry_score = latest["entry_score"]
        self.peak_pnl_percent = latest["peak_pnl_percent"]
