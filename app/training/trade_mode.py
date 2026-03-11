from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from app.domain.candle import (
    ClosedTrade, Position, TradeDirection, Candle,
)
from app.replay.session import ReplaySession


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
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    max_drawdown_pct: float = 0.0
    avg_hold_bars: float = 0.0


class TradeMode:
    """Simulated-trading training logic."""

    def __init__(self, session: ReplaySession, initial_capital: float = 100000.0):
        self._session = session
        self._initial_capital = initial_capital
        self._capital = initial_capital

    @property
    def capital(self) -> float:
        return self._capital

    @property
    def position(self) -> Optional[Position]:
        return self._session.position

    @property
    def closed_trades(self) -> List[ClosedTrade]:
        return self._session.closed_trades

    # ------------------------------------------------------------------

    def open_long(
        self,
        quantity: int,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> Optional[Position]:
        return self._session.open_position(
            TradeDirection.LONG, quantity, stop_loss, take_profit,
        )

    def open_short(
        self,
        quantity: int,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> Optional[Position]:
        return self._session.open_position(
            TradeDirection.SHORT, quantity, stop_loss, take_profit,
        )

    def close(self, reason: str = "manual") -> Optional[ClosedTrade]:
        trade = self._session.close_position(reason)
        if trade:
            self._capital += trade.pnl
        return trade

    def advance_and_check(self, steps: int = 1) -> Optional[ClosedTrade]:
        """Advance bars and auto-close on stop/TP hit."""
        revealed = self._session.advance(steps)
        for candle in revealed:
            pos = self._session.position
            if pos is None:
                continue
            if pos.should_stop_loss(candle):
                sl_price = pos.stop_loss
                trade = self.close("stop_loss")
                if trade and sl_price is not None:
                    old_pnl = trade.pnl
                    trade.exit_price = sl_price
                    self._capital += trade.pnl - old_pnl
                return trade
            if pos.should_take_profit(candle):
                tp_price = pos.take_profit
                trade = self.close("take_profit")
                if trade and tp_price is not None:
                    old_pnl = trade.pnl
                    trade.exit_price = tp_price
                    self._capital += trade.pnl - old_pnl
                return trade
        return None

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

        loss_rate = 1 - stats.win_rate
        stats.expectancy = (stats.win_rate * stats.avg_win_pct) + (loss_rate * stats.avg_loss_pct)

        # consecutive wins/losses
        stats.max_consecutive_wins = self._max_streak(trades, winning=True)
        stats.max_consecutive_losses = self._max_streak(trades, winning=False)

        # max drawdown
        stats.max_drawdown_pct = self._max_drawdown(trades)

        # average hold
        stats.avg_hold_bars = sum(t.hold_bars for t in trades) / len(trades)

        return stats

    # ------------------------------------------------------------------

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
        return (
            f"交易: {s.total_trades}  |  胜率: {s.win_rate:.1%}  |  "
            f"盈亏比: {r_str}  |  期望值: {s.expectancy:.2f}%  |  "
            f"净盈亏: {s.total_pnl:+.2f}  |  最大回撤: {s.max_drawdown_pct:.2f}%"
        )
