from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from app.domain.candle import Prediction, PredictionDirection
from app.replay.session import ReplaySession


@dataclass
class PredictResult:
    total: int = 0
    correct: int = 0
    wrong: int = 0
    pending: int = 0

    @property
    def accuracy(self) -> float:
        evaluated = self.correct + self.wrong
        return self.correct / evaluated if evaluated else 0.0


class PredictMode:
    """Direction-prediction training logic."""

    def __init__(self, session: ReplaySession):
        self._session = session

    def predict(self, direction: PredictionDirection, lookahead: int = 1) -> Prediction:
        return self._session.add_prediction(direction, lookahead)

    def reveal_and_evaluate(self, steps: int = 1) -> List[Prediction]:
        """Advance bars and evaluate any matured predictions."""
        self._session.advance(steps)
        self._session.evaluate_predictions()
        return [p for p in self._session.predictions if p.is_correct is not None]

    def get_result(self) -> PredictResult:
        self._session.evaluate_predictions()
        result = PredictResult(total=len(self._session.predictions))
        for p in self._session.predictions:
            if p.is_correct is None:
                result.pending += 1
            elif p.is_correct:
                result.correct += 1
            else:
                result.wrong += 1
        return result

    def summary_text(self) -> str:
        r = self.get_result()
        return (
            f"预测总数: {r.total}  |  正确: {r.correct}  |  错误: {r.wrong}  |  "
            f"待验证: {r.pending}  |  准确率: {r.accuracy:.1%}"
        )
