from __future__ import annotations

from typing import Optional

from app.domain.candle import ClosedTrade
from app.domain.market_rules import MarketRules
from app.replay.session import ReplaySession
from app.training.trade_mode import TradeMode


CHALLENGE_INITIAL_CAPITAL = 200_000.0


class ChallengeTradeMode(TradeMode):
    """模拟挑战：固定初始资金与目标金额，无破产重置；归零失败，达目标成功。"""

    def __init__(
        self,
        session: ReplaySession,
        target_amount: float,
        initial_capital: float = CHALLENGE_INITIAL_CAPITAL,
        rules: Optional[MarketRules] = None,
        slippage_pct: float = 0.0005,
        use_commission: bool = True,
    ):
        super().__init__(
            session,
            initial_capital=initial_capital,
            bankruptcy_count=0,
            rules=rules,
            slippage_pct=slippage_pct,
            use_commission=use_commission,
        )
        self._target_amount = float(target_amount)
        self._challenge_outcome: Optional[str] = None

    @property
    def target_amount(self) -> float:
        return self._target_amount

    @property
    def challenge_outcome(self) -> Optional[str]:
        return self._challenge_outcome

    def clear_outcome(self) -> None:
        self._challenge_outcome = None

    def _check_bankruptcy(self, trade: ClosedTrade) -> None:
        if self._challenge_outcome:
            return
        if self._capital >= self._target_amount:
            self._challenge_outcome = "win"
        elif self._capital <= 0:
            self._challenge_outcome = "lose"

    def check_outcome_after_advance(self) -> None:
        """无成交推进后也可检查是否已达标或归零（与父类资金口径一致）。"""
        if self._challenge_outcome:
            return
        if self._capital >= self._target_amount:
            self._challenge_outcome = "win"
        elif self._capital <= 0:
            self._challenge_outcome = "lose"

    def summary_text(self) -> str:
        s = self.compute_stats()
        r_str = f"{s.avg_r:.2f}R" if s.avg_r is not None else "N/A"
        lines = [
            f"交易: {s.total_trades}  |  胜率: {s.win_rate:.1%}  |  盈亏比: {r_str}",
            f"期望值: {s.expectancy:.2f}%  |  净盈亏: {s.total_pnl:+.2f}",
            f"手续费: {s.total_commission:.2f}  |  资金: {self.capital:,.0f}",
            f"最大回撤: {s.max_drawdown_pct:.2f}%  |  挑战目标: {self._target_amount:,.0f}",
        ]
        return "\n".join(lines)
