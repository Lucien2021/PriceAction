from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from app.domain.candle import (
    ClosedTrade, Position, TradeDirection, Candle,
)
from app.domain.market_rules import MarketRules, AShareRules
from app.replay.session import ReplaySession


INITIAL_CAPITAL = 100_000.0
BANKRUPTCY_THRESHOLD = 2_000.0


@dataclass
class EquitySnapshot:
    equity_before: float
    equity_after: float
    is_reset: bool = False
    bankruptcy_count: int = 0
    timestamp: Optional[datetime] = None
    trade_id: Optional[str] = None


@dataclass
class TradeStats:
    total_trades: int = 0
    winners: int = 0
    losers: int = 0
    breakeven: int = 0
    win_rate: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    avg_r: Optional[float] = None
    expectancy: float = 0.0
    profit_factor: float = 0.0
    total_pnl: float = 0.0
    total_commission: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    max_drawdown_pct: float = 0.0
    avg_hold_bars: float = 0.0


class TradeMode:
    """Simulated-trading training logic with capital management."""

    def __init__(
        self,
        session: ReplaySession,
        initial_capital: float = INITIAL_CAPITAL,
        bankruptcy_count: int = 0,
        rules: Optional[MarketRules] = None,
        slippage_pct: float = 0.0005,
        use_commission: bool = True,
    ):
        self._session = session
        self._initial_capital = initial_capital
        self._capital = initial_capital
        self._peak_equity = initial_capital
        self._bankruptcy_count = bankruptcy_count
        self._equity_snapshots: List[EquitySnapshot] = []
        self._rules = rules or AShareRules()
        self._slippage_pct = slippage_pct
        self._use_commission = use_commission
        self._entry_commission = 0.0
        self._current_position_id = ""

    @property
    def capital(self) -> float:
        return self._capital

    @property
    def bankruptcy_count(self) -> int:
        return self._bankruptcy_count

    @property
    def peak_equity(self) -> float:
        return self._peak_equity

    @property
    def equity_snapshots(self) -> List[EquitySnapshot]:
        return self._equity_snapshots

    @property
    def position(self) -> Optional[Position]:
        return self._session.position

    @property
    def closed_trades(self) -> List[ClosedTrade]:
        return self._session.closed_trades

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------

    def calculate_risk_position(
        self, risk_pct: float, entry_price: float, stop_loss: float,
    ) -> int:
        if stop_loss == 0 or entry_price == 0 or stop_loss == entry_price:
            return 100
        risk_amount = self._capital * (risk_pct / 100.0)
        per_share_risk = abs(entry_price - stop_loss)
        qty = int(risk_amount / per_share_risk)
        lot_size = 100
        qty = max(lot_size, (qty // lot_size) * lot_size)
        return qty

    def _apply_slippage(self, price: float, is_buy: bool) -> float:
        slip = price * self._slippage_pct
        return round(price + slip, 2) if is_buy else round(price - slip, 2)

    def _calc_commission(self, price: float, quantity: int, is_sell: bool) -> float:
        if not self._use_commission:
            return 0.0
        return self._rules.calculate_commission(price, quantity, is_sell)

    # ------------------------------------------------------------------
    # Open
    # ------------------------------------------------------------------

    def open_long(
        self,
        quantity: int,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> Optional[Position]:
        self._current_position_id = uuid.uuid4().hex[:12]
        pos = self._session.open_position(
            TradeDirection.LONG, quantity, stop_loss, take_profit,
        )
        if pos:
            pos.position_id = self._current_position_id
            if self._slippage_pct > 0:
                pos.entry_price = self._apply_slippage(pos.entry_price, True)
            if self._use_commission:
                comm = self._calc_commission(pos.entry_price, pos.quantity, False)
                self._capital -= comm
                self._entry_commission = comm
        return pos

    def open_short(
        self,
        quantity: int,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> Optional[Position]:
        self._current_position_id = uuid.uuid4().hex[:12]
        pos = self._session.open_position(
            TradeDirection.SHORT, quantity, stop_loss, take_profit,
        )
        if pos:
            pos.position_id = self._current_position_id
            if self._slippage_pct > 0:
                pos.entry_price = self._apply_slippage(pos.entry_price, False)
            if self._use_commission:
                comm = self._calc_commission(pos.entry_price, pos.quantity, False)
                self._capital -= comm
                self._entry_commission = comm
        return pos

    # ------------------------------------------------------------------
    # Close (full)
    # ------------------------------------------------------------------

    def close(
        self,
        reason: str = "manual",
        trigger_candle: Optional[Candle] = None,
        trigger_bar_index: Optional[int] = None,
        exit_price_base: Optional[float] = None,
    ) -> Optional[ClosedTrade]:
        equity_before = self._capital
        entry_comm = self._entry_commission
        trade = self._session.close_position(
            reason,
            trigger_candle=trigger_candle,
            trigger_bar_index=trigger_bar_index,
            exit_price_base=exit_price_base,
        )
        if trade:
            if self._slippage_pct > 0:
                is_buy_to_close = trade.direction == TradeDirection.SHORT
                trade.exit_price = self._apply_slippage(trade.exit_price, is_buy_to_close)
            exit_comm = self._calc_commission(trade.exit_price, trade.quantity, True)
            trade.commission = round(entry_comm + exit_comm, 2)
            self._capital += trade.pnl - exit_comm
            trade.position_id = self._current_position_id
            trade.equity_before = round(equity_before, 2)
            trade.equity_after = round(self._capital, 2)
            self._entry_commission = 0.0
            self._record_equity(trade)
            self._check_bankruptcy(trade)
        return trade

    # ------------------------------------------------------------------
    # Partial close
    # ------------------------------------------------------------------

    def close_partial(self, ratio: float, reason: str = "partial") -> Optional[ClosedTrade]:
        pos = self._session.position
        if pos is None:
            return None
        close_qty = self._round_lot(int(pos.quantity * ratio))
        if close_qty <= 0:
            return None
        if close_qty >= pos.quantity:
            return self.close(reason)
        return self._do_partial_close(close_qty, reason)

    def close_quantity(self, qty: int, reason: str = "partial") -> Optional[ClosedTrade]:
        pos = self._session.position
        if pos is None:
            return None
        close_qty = min(qty, pos.quantity)
        close_qty = self._round_lot(close_qty)
        if close_qty <= 0:
            return None
        if close_qty >= pos.quantity:
            return self.close(reason)
        return self._do_partial_close(close_qty, reason)

    def _do_partial_close(self, close_qty: int, reason: str) -> Optional[ClosedTrade]:
        pos = self._session.position
        if pos is None:
            return None
        equity_before = self._capital
        candle = self._session.current_candle
        if candle is None:
            return None

        exit_price = candle.close
        if self._slippage_pct > 0:
            is_buy_to_close = pos.direction == TradeDirection.SHORT
            exit_price = self._apply_slippage(exit_price, is_buy_to_close)

        entry_comm_share = self._entry_commission * (close_qty / pos.quantity)
        exit_comm = self._calc_commission(exit_price, close_qty, True)

        trade = ClosedTrade(
            direction=pos.direction,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            quantity=close_qty,
            entry_time=pos.entry_time or candle.timestamp,
            exit_time=candle.timestamp,
            entry_bar_index=pos.entry_bar_index,
            exit_bar_index=self._session.absolute_bar_index,
            stop_loss=pos.stop_loss,
            take_profit=pos.take_profit,
            exit_reason=reason,
            max_favorable=pos.max_favorable,
            max_adverse=pos.max_adverse,
            commission=round(entry_comm_share + exit_comm, 2),
            position_id=self._current_position_id,
        )
        self._session.closed_trades.append(trade)

        self._capital += trade.pnl - exit_comm
        self._entry_commission -= entry_comm_share
        pos.quantity -= close_qty

        trade.equity_before = round(equity_before, 2)
        trade.equity_after = round(self._capital, 2)
        self._record_equity(trade)
        self._check_bankruptcy(trade)
        return trade

    @staticmethod
    def _round_lot(qty: int, lot_size: int = 100) -> int:
        return max(0, (qty // lot_size) * lot_size)

    # ------------------------------------------------------------------
    # Capital management
    # ------------------------------------------------------------------

    def _record_equity(self, trade: ClosedTrade) -> None:
        if self._capital > self._peak_equity:
            self._peak_equity = self._capital
        self._equity_snapshots.append(EquitySnapshot(
            equity_before=trade.equity_before,
            equity_after=trade.equity_after,
            bankruptcy_count=self._bankruptcy_count,
            timestamp=datetime.now(),
            trade_id=trade.position_id,
        ))

    def _check_bankruptcy(self, trade: ClosedTrade) -> None:
        if self._capital < BANKRUPTCY_THRESHOLD:
            self._bankruptcy_count += 1
            old_capital = self._capital
            self._capital = INITIAL_CAPITAL
            self._peak_equity = INITIAL_CAPITAL
            self._entry_commission = 0.0
            self._equity_snapshots.append(EquitySnapshot(
                equity_before=old_capital,
                equity_after=INITIAL_CAPITAL,
                is_reset=True,
                bankruptcy_count=self._bankruptcy_count,
                timestamp=datetime.now(),
            ))

    # ------------------------------------------------------------------
    # Auto SL/TP check
    # ------------------------------------------------------------------

    def advance_and_check(self, steps: int = 1) -> Optional[ClosedTrade]:
        revealed = self._session.advance(steps)
        n = len(revealed)
        for i, candle in enumerate(revealed):
            pos = self._session.position
            if pos is None:
                continue
            if pos.should_stop_loss(candle):
                fill = pos.stop_loss_fill_price(candle)
                trigger_bar = self._session.absolute_bar_index - n + i
                return self.close(
                    "stop_loss",
                    trigger_candle=candle,
                    trigger_bar_index=trigger_bar,
                    exit_price_base=fill,
                )
            if pos.should_take_profit(candle):
                trigger_bar = self._session.absolute_bar_index - n + i
                return self.close(
                    "take_profit",
                    trigger_candle=candle,
                    trigger_bar_index=trigger_bar,
                    exit_price_base=pos.take_profit,
                )
        return None

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def compute_stats(self, trades: Optional[List[ClosedTrade]] = None) -> TradeStats:
        trades = trades if trades is not None else self.closed_trades
        stats = TradeStats(total_trades=len(trades))
        if not trades:
            return stats

        wins, losses = [], []
        r_values = []
        for t in trades:
            if t.pnl > 0:
                stats.winners += 1
                wins.append(t.pnl_pct)
            elif t.pnl < 0:
                stats.losers += 1
                losses.append(t.pnl_pct)
            else:
                stats.breakeven += 1
            if t.r_multiple is not None:
                r_values.append(t.r_multiple)

        closed = stats.winners + stats.losers
        stats.win_rate = stats.winners / closed if closed else 0.0
        stats.avg_win_pct = sum(wins) / len(wins) if wins else 0.0
        stats.avg_loss_pct = sum(losses) / len(losses) if losses else 0.0
        stats.avg_r = sum(r_values) / len(r_values) if r_values else None

        gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))
        stats.profit_factor = gross_profit / gross_loss if gross_loss else float("inf")
        stats.total_pnl = sum(t.pnl for t in trades)
        stats.total_commission = sum(t.commission for t in trades)

        loss_rate = 1 - stats.win_rate
        stats.expectancy = (stats.win_rate * stats.avg_win_pct) + (loss_rate * stats.avg_loss_pct)

        stats.max_consecutive_wins = self._max_streak(trades, winning=True)
        stats.max_consecutive_losses = self._max_streak(trades, winning=False)
        stats.max_drawdown_pct = self._max_drawdown(trades)
        stats.avg_hold_bars = sum(t.hold_bars for t in trades) / len(trades)

        return stats

    @staticmethod
    def _max_streak(trades: List[ClosedTrade], winning: bool) -> int:
        best = current = 0
        for t in trades:
            if (t.pnl > 0) == winning:
                current += 1
                best = max(best, current)
            else:
                current = 0
        return best

    @staticmethod
    def _max_drawdown(trades: List[ClosedTrade]) -> float:
        if not trades:
            return 0.0
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in trades:
            cumulative += t.pnl_pct
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd
        return max_dd

    def summary_text(self) -> str:
        s = self.compute_stats()
        r_str = f"{s.avg_r:.2f}R" if s.avg_r is not None else "N/A"
        lines = [
            f"交易: {s.total_trades}  |  胜率: {s.win_rate:.1%}  |  盈亏比: {r_str}",
            f"期望值: {s.expectancy:.2f}%  |  净盈亏: {s.total_pnl:+.2f}",
            f"手续费: {s.total_commission:.2f}  |  资金: {self._capital:,.0f}",
            f"最大回撤: {s.max_drawdown_pct:.2f}%  |  破产: {self._bankruptcy_count}次",
        ]
        return "\n".join(lines)
