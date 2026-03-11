from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict, Any

from app.domain.candle import (
    ClosedTrade, Prediction, Symbol, Timeframe, TradeDirection,
    PredictionDirection,
)
from app.replay.session import ReplaySession, TrainingMode
from app.storage.models import get_connection


@dataclass
class AggregateStats:
    total_sessions: int = 0
    total_trades: int = 0
    winners: int = 0
    losers: int = 0
    win_rate: float = 0.0
    avg_pnl_pct: float = 0.0
    avg_r: Optional[float] = None
    expectancy: float = 0.0
    profit_factor: float = 0.0
    total_pnl: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    max_drawdown_pct: float = 0.0
    prediction_accuracy: float = 0.0
    total_predictions: int = 0
    correct_predictions: int = 0


class StatsService:
    def __init__(self, conn: Optional[sqlite3.Connection] = None):
        self._conn = conn or get_connection()

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_session(self, session: ReplaySession) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO sessions "
            "(id, symbol, timeframe, mode, started_at, finished_at, visible_bars, future_bars) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session.session_id,
                session.symbol.code if session.symbol else "",
                session.timeframe.label if session.timeframe else "",
                session.mode.value,
                session.started_at.isoformat() if session.started_at else None,
                session.finished_at.isoformat() if session.finished_at else None,
                len(session.visible_candles),
                len(session.future_candles),
            ),
        )

        for t in session.closed_trades:
            self._save_trade(session.session_id, t)

        for p in session.predictions:
            self._save_prediction(session.session_id, p)

        self._conn.commit()

    def _save_trade(self, session_id: str, t: ClosedTrade) -> None:
        self._conn.execute(
            "INSERT INTO trades "
            "(session_id, direction, entry_price, exit_price, quantity, "
            " entry_time, exit_time, entry_bar_index, exit_bar_index, "
            " stop_loss, take_profit, exit_reason, pnl, pnl_pct, r_multiple, "
            " hold_bars, max_favorable, max_adverse, tags, notes) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                session_id,
                t.direction.value,
                t.entry_price,
                t.exit_price,
                t.quantity,
                t.entry_time.isoformat() if t.entry_time else None,
                t.exit_time.isoformat() if t.exit_time else None,
                t.entry_bar_index,
                t.exit_bar_index,
                t.stop_loss,
                t.take_profit,
                t.exit_reason,
                t.pnl,
                t.pnl_pct,
                t.r_multiple,
                t.hold_bars,
                t.max_favorable,
                t.max_adverse,
                json.dumps(t.tags),
                t.notes,
            ),
        )

    def _save_prediction(self, session_id: str, p: Prediction) -> None:
        self._conn.execute(
            "INSERT INTO predictions "
            "(session_id, direction, bar_index, timestamp, lookahead_bars, "
            " actual_direction, is_correct) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                session_id,
                p.direction.value,
                p.bar_index,
                p.timestamp.isoformat() if p.timestamp else None,
                p.lookahead_bars,
                p.actual_direction.value if p.actual_direction else None,
                int(p.is_correct) if p.is_correct is not None else None,
            ),
        )

    def save_note(self, session_id: str, content: str, tags: List[str] | None = None) -> None:
        self._conn.execute(
            "INSERT INTO session_notes (session_id, created_at, content, tags) VALUES (?,?,?,?)",
            (session_id, datetime.now().isoformat(), content, json.dumps(tags or [])),
        )
        self._conn.commit()

    def update_trade_tags(self, trade_id: int, tags: List[str], notes: str = "") -> None:
        self._conn.execute(
            "UPDATE trades SET tags=?, notes=? WHERE id=?",
            (json.dumps(tags), notes, trade_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_all_sessions(self) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM sessions ORDER BY started_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_session_trades(self, session_id: str) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM trades WHERE session_id=? ORDER BY entry_bar_index",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_session_predictions(self, session_id: str) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM predictions WHERE session_id=? ORDER BY bar_index",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_session_notes(self, session_id: str) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM session_notes WHERE session_id=? ORDER BY created_at DESC",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Aggregate statistics
    # ------------------------------------------------------------------

    def get_overall_stats(
        self,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
    ) -> AggregateStats:
        where_parts, params = self._build_filter(symbol, timeframe)

        stats = AggregateStats()

        # sessions count
        q = f"SELECT COUNT(*) FROM sessions {where_parts}"
        stats.total_sessions = self._conn.execute(q, params).fetchone()[0]

        # trades
        trade_q = (
            f"SELECT t.* FROM trades t JOIN sessions s ON t.session_id=s.id {where_parts} "
            f"ORDER BY t.entry_time"
        )
        rows = self._conn.execute(trade_q, params).fetchall()
        trades = [dict(r) for r in rows]
        stats.total_trades = len(trades)

        if trades:
            wins = [t for t in trades if (t["pnl"] or 0) > 0]
            losses = [t for t in trades if (t["pnl"] or 0) < 0]
            stats.winners = len(wins)
            stats.losers = len(losses)
            closed = stats.winners + stats.losers
            stats.win_rate = stats.winners / closed if closed else 0.0

            stats.avg_pnl_pct = sum(t["pnl_pct"] or 0 for t in trades) / len(trades)

            r_vals = [t["r_multiple"] for t in trades if t["r_multiple"] is not None]
            stats.avg_r = sum(r_vals) / len(r_vals) if r_vals else None

            gross_profit = sum(t["pnl"] for t in trades if (t["pnl"] or 0) > 0)
            gross_loss = abs(sum(t["pnl"] for t in trades if (t["pnl"] or 0) < 0))
            stats.profit_factor = gross_profit / gross_loss if gross_loss else float("inf")
            stats.total_pnl = sum(t["pnl"] or 0 for t in trades)

            loss_rate = 1 - stats.win_rate
            avg_win = sum(t["pnl_pct"] or 0 for t in wins) / len(wins) if wins else 0
            avg_loss = sum(t["pnl_pct"] or 0 for t in losses) / len(losses) if losses else 0
            stats.expectancy = (stats.win_rate * avg_win) + (loss_rate * avg_loss)

            stats.max_consecutive_wins = self._streak(trades, True)
            stats.max_consecutive_losses = self._streak(trades, False)
            stats.max_drawdown_pct = self._drawdown(trades)

        # predictions
        pred_q = (
            f"SELECT p.* FROM predictions p JOIN sessions s ON p.session_id=s.id {where_parts}"
        )
        pred_rows = self._conn.execute(pred_q, params).fetchall()
        stats.total_predictions = len(pred_rows)
        stats.correct_predictions = sum(1 for r in pred_rows if r["is_correct"] == 1)
        stats.prediction_accuracy = (
            stats.correct_predictions / stats.total_predictions
            if stats.total_predictions else 0.0
        )

        return stats

    def get_equity_curve(
        self,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        where_parts, params = self._build_filter(symbol, timeframe)
        q = (
            f"SELECT t.exit_time, t.pnl, t.pnl_pct, t.r_multiple "
            f"FROM trades t JOIN sessions s ON t.session_id=s.id {where_parts} "
            f"ORDER BY t.exit_time"
        )
        rows = self._conn.execute(q, params).fetchall()
        curve = []
        cumulative = 0.0
        for r in rows:
            cumulative += r["pnl"] or 0
            curve.append({
                "time": r["exit_time"],
                "pnl": r["pnl"],
                "cumulative_pnl": cumulative,
                "pnl_pct": r["pnl_pct"],
                "r_multiple": r["r_multiple"],
            })
        return curve

    def get_rolling_win_rate(self, window: int = 20) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT exit_time, pnl FROM trades ORDER BY exit_time"
        ).fetchall()
        results = []
        buffer = []
        for r in rows:
            buffer.append(1 if (r["pnl"] or 0) > 0 else 0)
            if len(buffer) > window:
                buffer.pop(0)
            results.append({
                "time": r["exit_time"],
                "win_rate": sum(buffer) / len(buffer),
                "sample_size": len(buffer),
            })
        return results

    def get_r_distribution(self) -> Dict[str, int]:
        rows = self._conn.execute(
            "SELECT r_multiple FROM trades WHERE r_multiple IS NOT NULL"
        ).fetchall()
        buckets: Dict[str, int] = {}
        for r in rows:
            val = r["r_multiple"]
            if val <= -3:
                key = "<-3R"
            elif val <= -2:
                key = "-3R~-2R"
            elif val <= -1:
                key = "-2R~-1R"
            elif val <= 0:
                key = "-1R~0R"
            elif val <= 1:
                key = "0R~1R"
            elif val <= 2:
                key = "1R~2R"
            elif val <= 3:
                key = "2R~3R"
            else:
                key = ">3R"
            buckets[key] = buckets.get(key, 0) + 1
        return buckets

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_filter(symbol: Optional[str], timeframe: Optional[str]):
        parts = []
        params: list = []
        if symbol:
            parts.append("s.symbol=?")
            params.append(symbol)
        if timeframe:
            parts.append("s.timeframe=?")
            params.append(timeframe)
        where = ("WHERE " + " AND ".join(parts)) if parts else ""
        return where, params

    @staticmethod
    def _streak(trades: List[dict], winning: bool) -> int:
        best = cur = 0
        for t in trades:
            if ((t["pnl"] or 0) > 0) == winning:
                cur += 1
                best = max(best, cur)
            else:
                cur = 0
        return best

    @staticmethod
    def _drawdown(trades: List[dict]) -> float:
        cum = peak = max_dd = 0.0
        for t in trades:
            cum += t["pnl_pct"] or 0
            if cum > peak:
                peak = cum
            dd = peak - cum
            if dd > max_dd:
                max_dd = dd
        return max_dd
