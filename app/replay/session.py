from __future__ import annotations

from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
import uuid

from app.domain.candle import (
    Candle, ClosedTrade, Position, Prediction,
    Symbol, Timeframe, TradeDirection, MarketType,
)


class SessionState(Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    FINISHED = "finished"


class TrainingMode(Enum):
    PREDICT = "predict"
    TRADE = "trade"
    CHALLENGE = "challenge"


@dataclass
class ReplaySession:
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    symbol: Optional[Symbol] = None
    timeframe: Optional[Timeframe] = None
    mode: TrainingMode = TrainingMode.TRADE
    state: SessionState = SessionState.IDLE

    visible_candles: List[Candle] = field(default_factory=list)
    future_candles: List[Candle] = field(default_factory=list)
    current_index: int = 0

    position: Optional[Position] = None
    closed_trades: List[ClosedTrade] = field(default_factory=list)
    predictions: List[Prediction] = field(default_factory=list)

    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    setup_type: str = ""
    scenario_tag: str = ""
    plan_notes: str = ""
    plan_direction: str = ""
    plan_invalidation: str = ""
    difficulty: int = 0
    score: int = 0
    training_goal: str = ""
    violations: List[dict] = field(default_factory=list)

    # ------------------------------------------------------------------

    def setup(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        mode: TrainingMode,
        visible: List[Candle],
        future: List[Candle],
    ) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.mode = mode
        self.visible_candles = list(visible)
        self.future_candles = list(future)
        self.current_index = 0
        self.state = SessionState.IDLE
        self.position = None
        self.closed_trades.clear()
        self.predictions.clear()

    def start(self) -> None:
        self.state = SessionState.RUNNING
        self.started_at = datetime.now()

    def pause(self) -> None:
        if self.state == SessionState.RUNNING:
            self.state = SessionState.PAUSED

    def resume(self) -> None:
        if self.state == SessionState.PAUSED:
            self.state = SessionState.RUNNING

    def finish(self) -> None:
        self.state = SessionState.FINISHED
        self.finished_at = datetime.now()

    # ------------------------------------------------------------------

    @property
    def total_future_bars(self) -> int:
        return len(self.future_candles)

    @property
    def bars_remaining(self) -> int:
        return self.total_future_bars - self.current_index

    @property
    def current_candle(self) -> Optional[Candle]:
        if 0 <= self.current_index - 1 < len(self.future_candles):
            return self.future_candles[self.current_index - 1]
        if self.visible_candles:
            return self.visible_candles[-1]
        return None

    @property
    def displayed_candles(self) -> List[Candle]:
        return self.visible_candles + self.future_candles[: self.current_index]

    @property
    def absolute_bar_index(self) -> int:
        return len(self.visible_candles) + self.current_index

    def advance(self, steps: int = 1) -> List[Candle]:
        """Reveal next N bars. Returns newly revealed candles."""
        if self.state != SessionState.RUNNING:
            return []
        revealed: List[Candle] = []
        for _ in range(steps):
            if self.current_index >= self.total_future_bars:
                self.finish()
                break
            candle = self.future_candles[self.current_index]
            self.current_index += 1
            revealed.append(candle)

            if self.position:
                self.position.update_excursions(candle.close)
        return revealed

    # ------------------------------------------------------------------
    # Trade actions
    # ------------------------------------------------------------------

    def open_position(
        self,
        direction: TradeDirection,
        quantity: int,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> Optional[Position]:
        if self.position is not None:
            return None
        candle = self.current_candle
        if candle is None:
            return None
        self.position = Position(
            direction=direction,
            entry_price=candle.close,
            quantity=quantity,
            stop_loss=stop_loss,
            take_profit=take_profit,
            entry_bar_index=self.absolute_bar_index,
            entry_time=candle.timestamp,
        )
        return self.position

    def close_position(
        self,
        reason: str = "manual",
        trigger_candle: Optional[Candle] = None,
        trigger_bar_index: Optional[int] = None,
        exit_price_base: Optional[float] = None,
    ) -> Optional[ClosedTrade]:
        if self.position is None:
            return None
        candle = trigger_candle if trigger_candle is not None else self.current_candle
        if candle is None:
            return None
        bar_index = trigger_bar_index if trigger_bar_index is not None else self.absolute_bar_index
        exit_px = candle.close if exit_price_base is None else exit_price_base
        trade = ClosedTrade(
            direction=self.position.direction,
            entry_price=self.position.entry_price,
            exit_price=exit_px,
            quantity=self.position.quantity,
            entry_time=self.position.entry_time or candle.timestamp,
            exit_time=candle.timestamp,
            entry_bar_index=self.position.entry_bar_index,
            exit_bar_index=bar_index,
            stop_loss=self.position.stop_loss,
            take_profit=self.position.take_profit,
            exit_reason=reason,
            max_favorable=self.position.max_favorable,
            max_adverse=self.position.max_adverse,
        )
        self.closed_trades.append(trade)
        self.position = None
        return trade

    def check_stops(self) -> Optional[ClosedTrade]:
        """Auto-close position if stop-loss or take-profit is hit."""
        if self.position is None:
            return None
        candle = self.current_candle
        if candle is None:
            return None
        allows_t0 = bool(self.symbol and self.symbol.market_type == MarketType.FUTURE)
        if self.position.should_stop_loss(candle):
            if not self.position.can_sell_asof(candle, allows_t0):
                return None
            fill = self.position.stop_loss_fill_price(candle)
            return self.close_position(reason="stop_loss", exit_price_base=fill)
        if self.position.should_take_profit(candle):
            if not self.position.can_sell_asof(candle, allows_t0):
                return None
            tp_price = self.position.take_profit
            if tp_price is None:
                return None
            return self.close_position(
                reason="take_profit",
                exit_price_base=tp_price,
            )
        return None

    # ------------------------------------------------------------------
    # Prediction actions
    # ------------------------------------------------------------------

    def add_prediction(self, direction, lookahead: int = 1) -> Prediction:
        candle = self.current_candle
        pred = Prediction(
            direction=direction,
            bar_index=self.absolute_bar_index,
            timestamp=candle.timestamp if candle else datetime.now(),
            lookahead_bars=lookahead,
        )
        self.predictions.append(pred)
        return pred

    def evaluate_predictions(self) -> None:
        from app.domain.candle import PredictionDirection

        displayed = self.displayed_candles
        for pred in self.predictions:
            target_idx = pred.bar_index + pred.lookahead_bars
            base_idx = pred.bar_index
            if target_idx >= len(displayed) or base_idx >= len(displayed):
                continue
            base_close = displayed[base_idx - 1].close if base_idx > 0 else displayed[0].open
            target_close = displayed[target_idx - 1].close

            if target_close > base_close:
                actual = PredictionDirection.UP
            elif target_close < base_close:
                actual = PredictionDirection.DOWN
            else:
                actual = PredictionDirection.SIDEWAYS

            pred.actual_direction = actual
            pred.is_correct = pred.direction == actual
