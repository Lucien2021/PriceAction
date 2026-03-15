from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import time
from typing import List, Tuple


@dataclass
class TradingSession:
    start: time
    end: time
    name: str = ""


@dataclass
class FeeRule:
    commission_rate: float = 0.0003
    min_commission: float = 5.0
    stamp_tax_rate: float = 0.001


@dataclass
class MarginRule:
    initial_margin_rate: float = 1.0
    maintenance_margin_rate: float = 1.0


class MarketRules(ABC):
    @abstractmethod
    def trading_sessions(self) -> List[TradingSession]:
        ...

    @abstractmethod
    def tick_size(self, price: float) -> float:
        ...

    @abstractmethod
    def price_limit(self, prev_close: float) -> Tuple[float, float]:
        ...

    @abstractmethod
    def fee_rule(self) -> FeeRule:
        ...

    @abstractmethod
    def margin_rule(self) -> MarginRule:
        ...

    @abstractmethod
    def contract_multiplier(self) -> float:
        ...

    @abstractmethod
    def allows_short(self) -> bool:
        ...

    @abstractmethod
    def allows_t0(self) -> bool:
        ...

    def calculate_commission(self, price: float, quantity: int, is_sell: bool) -> float:
        rule = self.fee_rule()
        turnover = abs(price) * quantity * self.contract_multiplier()
        commission = max(turnover * rule.commission_rate, rule.min_commission)
        if is_sell:
            commission += turnover * rule.stamp_tax_rate
        return round(commission, 2)


class AShareRules(MarketRules):
    def trading_sessions(self) -> List[TradingSession]:
        return [
            TradingSession(time(9, 30), time(11, 30), "morning"),
            TradingSession(time(13, 0), time(15, 0), "afternoon"),
        ]

    def tick_size(self, price: float) -> float:
        return 0.01

    def price_limit(self, prev_close: float) -> Tuple[float, float]:
        limit = round(prev_close * 0.1, 2)
        return (prev_close - limit, prev_close + limit)

    def fee_rule(self) -> FeeRule:
        return FeeRule(commission_rate=0.0003, min_commission=5.0, stamp_tax_rate=0.001)

    def margin_rule(self) -> MarginRule:
        return MarginRule(initial_margin_rate=1.0, maintenance_margin_rate=1.0)

    def contract_multiplier(self) -> float:
        return 1.0

    def allows_short(self) -> bool:
        return False

    def allows_t0(self) -> bool:
        return False


class FutureRules(MarketRules):
    """Placeholder – configure per contract when futures support is added."""

    def __init__(
        self,
        sessions: List[TradingSession] | None = None,
        tick: float = 1.0,
        multiplier: float = 10.0,
        margin_rate: float = 0.1,
        commission: float = 0.000025,
    ):
        self._sessions = sessions or [
            TradingSession(time(9, 0), time(11, 30), "morning"),
            TradingSession(time(13, 30), time(15, 0), "afternoon"),
            TradingSession(time(21, 0), time(23, 0), "night"),
        ]
        self._tick = tick
        self._multiplier = multiplier
        self._margin_rate = margin_rate
        self._commission = commission

    def trading_sessions(self) -> List[TradingSession]:
        return self._sessions

    def tick_size(self, price: float) -> float:
        return self._tick

    def price_limit(self, prev_close: float) -> Tuple[float, float]:
        limit = round(prev_close * 0.05, 2)
        return (prev_close - limit, prev_close + limit)

    def fee_rule(self) -> FeeRule:
        return FeeRule(commission_rate=self._commission, min_commission=0.0, stamp_tax_rate=0.0)

    def margin_rule(self) -> MarginRule:
        return MarginRule(
            initial_margin_rate=self._margin_rate,
            maintenance_margin_rate=self._margin_rate * 0.8,
        )

    def contract_multiplier(self) -> float:
        return self._multiplier

    def allows_short(self) -> bool:
        return True

    def allows_t0(self) -> bool:
        return True
