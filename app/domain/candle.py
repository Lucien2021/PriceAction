from __future__ import annotations

from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List


class MarketType(Enum):
    A_SHARE = "a_share"
    FUTURE = "future"


class Timeframe(Enum):
    M1 = ("1m", 1)
    M5 = ("5m", 5)
    M15 = ("15m", 15)
    M30 = ("30m", 30)
    H1 = ("60m", 60)
    DAILY = ("daily", 1440)
    WEEKLY = ("weekly", 10080)
    MONTHLY = ("monthly", 43200)

    def __init__(self, label: str, minutes: int):
        self.label = label
        self.minutes = minutes

    @classmethod
    def from_label(cls, label: str) -> "Timeframe":
        for tf in cls:
            if tf.label == label:
                return tf
        raise ValueError(f"Unknown timeframe label: {label}")


@dataclass(frozen=True)
class Symbol:
    code: str
    name: str
    market_type: MarketType = MarketType.A_SHARE

    def __str__(self) -> str:
        return f"{self.code} {self.name}"


@dataclass
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover: float = 0.0

    @property
    def is_bullish(self) -> bool:
        return self.close >= self.open

    @property
    def body_size(self) -> float:
        return abs(self.close - self.open)

    @property
    def range_size(self) -> float:
        return self.high - self.low

    def to_chart_dict(self, timeframe: Timeframe) -> dict:
        if timeframe.minutes >= Timeframe.DAILY.minutes:
            time_val = self.timestamp.strftime("%Y-%m-%d")
        else:
            time_val = int(self.timestamp.timestamp())
        return {
            "time": time_val,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
        }

    def to_volume_dict(self, timeframe: Timeframe) -> dict:
        if timeframe.minutes >= Timeframe.DAILY.minutes:
            time_val = self.timestamp.strftime("%Y-%m-%d")
        else:
            time_val = int(self.timestamp.timestamp())
        color = "#ef535080" if self.is_bullish else "#26a69a80"
        return {"time": time_val, "value": self.volume, "color": color}


class TradeDirection(Enum):
    LONG = "long"
    SHORT = "short"


@dataclass
class Position:
    direction: TradeDirection
    entry_price: float
    quantity: int
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    entry_bar_index: int = 0
    entry_time: Optional[datetime] = None
    max_favorable: float = 0.0
    max_adverse: float = 0.0
    position_id: str = ""

    @property
    def is_long(self) -> bool:
        return self.direction == TradeDirection.LONG

    def update_stop_loss(self, price: Optional[float]) -> None:
        self.stop_loss = round(price, 2) if price is not None else None

    def update_take_profit(self, price: Optional[float]) -> None:
        self.take_profit = round(price, 2) if price is not None else None

    def reward_risk_ratio(self, current_price: float) -> Optional[float]:
        if self.stop_loss is None or self.take_profit is None:
            return None
        risk = abs(self.entry_price - self.stop_loss)
        if risk == 0:
            return None
        reward = abs(self.take_profit - self.entry_price)
        return round(reward / risk, 2)

    def unrealized_pnl(self, current_price: float) -> float:
        diff = current_price - self.entry_price
        if not self.is_long:
            diff = -diff
        return diff * self.quantity

    def unrealized_pnl_pct(self, current_price: float) -> float:
        diff = current_price - self.entry_price
        if not self.is_long:
            diff = -diff
        return diff / self.entry_price * 100

    def unrealized_r(self, current_price: float) -> Optional[float]:
        if self.stop_loss is None or self.stop_loss == self.entry_price:
            return None
        risk = abs(self.entry_price - self.stop_loss)
        diff = current_price - self.entry_price
        if not self.is_long:
            diff = -diff
        return diff / risk

    def update_excursions(self, current_price: float) -> None:
        diff = current_price - self.entry_price
        if not self.is_long:
            diff = -diff
        if diff > self.max_favorable:
            self.max_favorable = diff
        if diff < self.max_adverse:
            self.max_adverse = diff

    def should_stop_loss(self, candle: Candle) -> bool:
        if self.stop_loss is None:
            return False
        if self.is_long:
            return candle.low <= self.stop_loss
        return candle.high >= self.stop_loss

    def should_take_profit(self, candle: Candle) -> bool:
        if self.take_profit is None:
            return False
        if self.is_long:
            return candle.high >= self.take_profit
        return candle.low <= self.take_profit


@dataclass
class ClosedTrade:
    direction: TradeDirection
    entry_price: float
    exit_price: float
    quantity: int
    entry_time: datetime
    exit_time: datetime
    entry_bar_index: int
    exit_bar_index: int
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    exit_reason: str = ""
    max_favorable: float = 0.0
    max_adverse: float = 0.0
    tags: List[str] = field(default_factory=list)
    notes: str = ""
    mistake_tags: List[str] = field(default_factory=list)
    execution_score: int = 0
    planned_risk_pct: float = 0.0
    entry_reason: str = ""
    exit_review: str = ""
    commission: float = 0.0
    snapshot_path: str = ""
    position_id: str = ""
    equity_before: float = 0.0
    equity_after: float = 0.0

    @property
    def pnl(self) -> float:
        diff = self.exit_price - self.entry_price
        if self.direction == TradeDirection.SHORT:
            diff = -diff
        return diff * self.quantity

    @property
    def pnl_pct(self) -> float:
        diff = self.exit_price - self.entry_price
        if self.direction == TradeDirection.SHORT:
            diff = -diff
        return diff / self.entry_price * 100

    @property
    def is_winner(self) -> bool:
        return self.pnl > 0

    @property
    def r_multiple(self) -> Optional[float]:
        if self.stop_loss is None or self.stop_loss == self.entry_price:
            return None
        risk = abs(self.entry_price - self.stop_loss)
        diff = self.exit_price - self.entry_price
        if self.direction == TradeDirection.SHORT:
            diff = -diff
        return diff / risk

    @property
    def hold_bars(self) -> int:
        return self.exit_bar_index - self.entry_bar_index


class PredictionDirection(Enum):
    UP = "up"
    DOWN = "down"
    SIDEWAYS = "sideways"


@dataclass
class Prediction:
    direction: PredictionDirection
    bar_index: int
    timestamp: datetime
    lookahead_bars: int = 1
    actual_direction: Optional[PredictionDirection] = None
    is_correct: Optional[bool] = None
