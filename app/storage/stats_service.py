from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.domain.candle import ClosedTrade, Prediction
from app.replay.session import ReplaySession
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
    total_commission: float = 0.0
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
        self._conn.execute("DELETE FROM trades WHERE session_id=?", (session.session_id,))
        self._conn.execute("DELETE FROM predictions WHERE session_id=?", (session.session_id,))
        self._conn.execute(
            "INSERT OR REPLACE INTO sessions "
            "(id, symbol, timeframe, mode, started_at, finished_at, visible_bars, future_bars, "
            " setup_type, scenario_tag, plan_notes, plan_direction, plan_invalidation, difficulty, score, training_goal) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session.session_id,
                session.symbol.code if session.symbol else "",
                session.timeframe.label if session.timeframe else "",
                session.mode.value,
                session.started_at.isoformat() if session.started_at else None,
                session.finished_at.isoformat() if session.finished_at else None,
                len(session.visible_candles),
                len(session.future_candles),
                session.setup_type,
                session.scenario_tag,
                session.plan_notes,
                session.plan_direction,
                session.plan_invalidation,
                session.difficulty,
                session.score,
                session.training_goal,
            ),
        )

        for trade in session.closed_trades:
            self._save_trade(session.session_id, trade)
        for prediction in session.predictions:
            self._save_prediction(session.session_id, prediction)

        self._conn.commit()

    def _save_trade(self, session_id: str, trade: ClosedTrade) -> None:
        self._conn.execute(
            "INSERT INTO trades "
            "(session_id, direction, entry_price, exit_price, quantity, "
            " entry_time, exit_time, entry_bar_index, exit_bar_index, "
            " stop_loss, take_profit, exit_reason, pnl, pnl_pct, r_multiple, "
            " hold_bars, max_favorable, max_adverse, tags, notes, "
            " mistake_tags, execution_score, planned_risk_pct, entry_reason, exit_review, commission,"
            " snapshot_path, position_id, equity_before, equity_after) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                session_id,
                trade.direction.value,
                trade.entry_price,
                trade.exit_price,
                trade.quantity,
                trade.entry_time.isoformat() if trade.entry_time else None,
                trade.exit_time.isoformat() if trade.exit_time else None,
                trade.entry_bar_index,
                trade.exit_bar_index,
                trade.stop_loss,
                trade.take_profit,
                trade.exit_reason,
                trade.pnl,
                trade.pnl_pct,
                trade.r_multiple,
                trade.hold_bars,
                trade.max_favorable,
                trade.max_adverse,
                json.dumps(trade.tags, ensure_ascii=False),
                trade.notes,
                json.dumps(trade.mistake_tags, ensure_ascii=False),
                trade.execution_score,
                trade.planned_risk_pct,
                trade.entry_reason,
                trade.exit_review,
                trade.commission,
                trade.snapshot_path,
                trade.position_id,
                trade.equity_before,
                trade.equity_after,
            ),
        )

    def _save_prediction(self, session_id: str, prediction: Prediction) -> None:
        self._conn.execute(
            "INSERT INTO predictions "
            "(session_id, direction, bar_index, timestamp, lookahead_bars, actual_direction, is_correct) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                session_id,
                prediction.direction.value,
                prediction.bar_index,
                prediction.timestamp.isoformat() if prediction.timestamp else None,
                prediction.lookahead_bars,
                prediction.actual_direction.value if prediction.actual_direction else None,
                int(prediction.is_correct) if prediction.is_correct is not None else None,
            ),
        )

    def save_note(self, session_id: str, content: str, tags: Optional[List[str]] = None) -> None:
        self._conn.execute(
            "INSERT INTO session_notes (session_id, created_at, content, tags) VALUES (?,?,?,?)",
            (session_id, datetime.now().isoformat(), content, json.dumps(tags or [], ensure_ascii=False)),
        )
        self._conn.commit()

    def update_trade_tags(self, trade_id: int, tags: List[str], notes: str = "") -> None:
        self._conn.execute(
            "UPDATE trades SET tags=?, notes=? WHERE id=?",
            (json.dumps(tags, ensure_ascii=False), notes, trade_id),
        )
        self._conn.commit()

    def update_trade_execution(
        self,
        trade_id: int,
        mistake_tags: List[str],
        execution_score: int,
        entry_reason: str = "",
        exit_review: str = "",
    ) -> None:
        self._conn.execute(
            "UPDATE trades SET mistake_tags=?, execution_score=?, entry_reason=?, exit_review=? WHERE id=?",
            (
                json.dumps(mistake_tags, ensure_ascii=False),
                execution_score,
                entry_reason,
                exit_review,
                trade_id,
            ),
        )
        self._conn.commit()

    def update_session_score(self, session_id: str, score: int, difficulty: int = 0) -> None:
        self._conn.execute(
            "UPDATE sessions SET score=?, difficulty=? WHERE id=?",
            (score, difficulty, session_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # PA annotations / mistake book
    # ------------------------------------------------------------------

    def save_pa_annotation(
        self,
        session_id: str,
        ann_type: str,
        data_json: str,
        notes: str = "",
        is_correct: Optional[int] = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO pa_annotations (session_id, created_at, ann_type, data_json, notes, is_correct) "
            "VALUES (?,?,?,?,?,?)",
            (session_id, datetime.now().isoformat(), ann_type, data_json, notes, is_correct),
        )
        self._conn.commit()

    def get_pa_annotations(self, session_id: str) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM pa_annotations WHERE session_id=? ORDER BY created_at",
            (session_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def save_mistake(
        self,
        session_id: str,
        symbol: str,
        timeframe: str,
        category: str = "trade",
        setup_type: str = "",
        mistake_tags: Optional[List[str]] = None,
        description: str = "",
        slice_start: int = 0,
        slice_end: int = 0,
    ) -> None:
        self._conn.execute(
            "INSERT INTO mistake_book "
            "(session_id, created_at, symbol, timeframe, category, setup_type, mistake_tags, description, slice_start, slice_end) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                session_id,
                datetime.now().isoformat(),
                symbol,
                timeframe,
                category,
                setup_type,
                json.dumps(mistake_tags or [], ensure_ascii=False),
                description,
                slice_start,
                slice_end,
            ),
        )
        self._conn.commit()

    def get_mistakes(
        self,
        symbol: Optional[str] = None,
        category: Optional[str] = None,
        retrained: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        parts: List[str] = []
        params: List[Any] = []
        if symbol:
            parts.append("symbol=?")
            params.append(symbol)
        if category:
            parts.append("category=?")
            params.append(category)
        if retrained is not None:
            parts.append("retrained=?")
            params.append(int(retrained))
        where = f"WHERE {' AND '.join(parts)}" if parts else ""
        rows = self._conn.execute(
            f"SELECT * FROM mistake_book {where} ORDER BY created_at DESC",
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def save_equity_snapshots(self, session_id: str, snapshots: list) -> None:
        for snap in snapshots:
            self._conn.execute(
                "INSERT INTO equity_snapshots "
                "(session_id, trade_id, created_at, equity_before, equity_after, is_reset, bankruptcy_count) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    session_id,
                    snap.trade_id or None,
                    snap.timestamp.isoformat() if snap.timestamp else datetime.now().isoformat(),
                    snap.equity_before,
                    snap.equity_after,
                    int(snap.is_reset),
                    snap.bankruptcy_count,
                ),
            )
        self._conn.commit()

    def get_equity_snapshots(self, session_id: Optional[str] = None) -> list:
        if session_id:
            rows = self._conn.execute(
                "SELECT * FROM equity_snapshots WHERE session_id=? ORDER BY id",
                (session_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM equity_snapshots ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_bankruptcy_count(self) -> int:
        row = self._conn.execute(
            "SELECT MAX(bankruptcy_count) AS cnt FROM equity_snapshots"
        ).fetchone()
        return row["cnt"] or 0 if row else 0

    def get_last_equity(self) -> float:
        """Return the most recent equity_after across all sessions."""
        row = self._conn.execute(
            "SELECT equity_after FROM equity_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["equity_after"] if row else 100000.0

    def get_full_equity_curve(self) -> List[Dict[str, Any]]:
        """Get all equity points joined with trade details, ordered by actual training sequence."""
        rows = self._conn.execute(
            "SELECT e.id, e.session_id, e.created_at, e.equity_before, e.equity_after, "
            "       e.is_reset, e.bankruptcy_count, "
            "       t.direction, t.entry_price, t.exit_price, t.quantity, "
            "       t.pnl, t.pnl_pct, t.r_multiple, t.exit_reason, "
            "       t.entry_time, t.exit_time, t.snapshot_path, "
            "       t.entry_reason, t.exit_review, t.stop_loss, t.take_profit, "
            "       s.symbol, s.timeframe, s.setup_type "
            "FROM equity_snapshots e "
            "LEFT JOIN trades t ON e.session_id = t.session_id "
            "    AND (t.position_id = e.trade_id "
            "         OR (e.trade_id IS NULL AND t.exit_time = e.created_at)) "
            "LEFT JOIN sessions s ON e.session_id = s.id "
            "ORDER BY e.id"
        ).fetchall()
        return [dict(row) for row in rows]

    def update_trade_snapshot(self, trade_id: int, snapshot_path: str) -> None:
        self._conn.execute(
            "UPDATE trades SET snapshot_path=? WHERE id=?",
            (snapshot_path, trade_id),
        )
        self._conn.commit()

    def mark_mistake_retrained(self, mistake_id: int) -> None:
        self._conn.execute("UPDATE mistake_book SET retrained=1 WHERE id=?", (mistake_id,))
        self._conn.commit()

    def save_violations(self, session_id: str, violations: List[Dict[str, Any]]) -> None:
        for v in violations:
            self._conn.execute(
                "INSERT INTO discipline_violations "
                "(session_id, created_at, violation_type, severity, details, bar_index) "
                "VALUES (?,?,?,?,?,?)",
                (
                    session_id,
                    datetime.now().isoformat(),
                    v.get("type", ""),
                    v.get("severity", "warning"),
                    v.get("details", ""),
                    v.get("bar_index", 0),
                ),
            )
        self._conn.commit()

    def get_violations(
        self,
        session_id: Optional[str] = None,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if session_id:
            rows = self._conn.execute(
                "SELECT * FROM discipline_violations WHERE session_id=? ORDER BY id",
                (session_id,),
            ).fetchall()
        else:
            parts: List[str] = []
            params: list = []
            if symbol:
                parts.append("s.symbol=?")
                params.append(symbol)
            if timeframe:
                parts.append("s.timeframe=?")
                params.append(timeframe)
            where = ("WHERE " + " AND ".join(parts)) if parts else ""
            rows = self._conn.execute(
                f"SELECT v.* FROM discipline_violations v "
                f"JOIN sessions s ON v.session_id=s.id {where} ORDER BY v.id DESC",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def get_violation_stats(
        self,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
    ) -> Dict[str, Any]:
        where_clause, params = self._build_session_filter(symbol=symbol, timeframe=timeframe)
        rows = self._conn.execute(
            f"SELECT v.violation_type, COUNT(*) AS cnt "
            f"FROM discipline_violations v JOIN sessions s ON v.session_id=s.id "
            f"{where_clause} GROUP BY v.violation_type ORDER BY cnt DESC",
            params,
        ).fetchall()
        total = sum(r["cnt"] for r in rows)
        return {
            "total": total,
            "by_type": [dict(r) for r in rows],
        }

    def get_mistake_for_retrain(self, mistake_id: int) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM mistake_book WHERE id=?", (mistake_id,)
        ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Capability diagnostics
    # ------------------------------------------------------------------

    def get_capability_stats(
        self,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
        recent_n: Optional[int] = None,
    ) -> Dict[str, Any]:
        where_clause, params = self._build_session_filter(symbol=symbol, timeframe=timeframe)

        limit_sql = f"LIMIT {recent_n}" if recent_n else ""
        trade_rows = self._conn.execute(
            f"SELECT t.*, s.setup_type, s.scenario_tag, s.training_goal "
            f"FROM trades t JOIN sessions s ON t.session_id=s.id "
            f"{where_clause} ORDER BY t.id DESC {limit_sql}",
            params,
        ).fetchall()
        trades = [dict(r) for r in trade_rows]

        setup_map: Dict[str, list] = {}
        scenario_map: Dict[str, list] = {}
        for t in trades:
            setup = t.get("setup_type") or "未分类"
            scenario = t.get("scenario_tag") or "随机"
            setup_map.setdefault(setup, []).append(t)
            scenario_map.setdefault(scenario, []).append(t)

        def _group_stats(group: list) -> dict:
            total = len(group)
            wins = sum(1 for t in group if (t["pnl"] or 0) > 0)
            losses = sum(1 for t in group if (t["pnl"] or 0) < 0)
            wr = wins / max(wins + losses, 1)
            rs = [t["r_multiple"] for t in group if t.get("r_multiple") is not None]
            avg_r = sum(rs) / len(rs) if rs else None
            es = [t["execution_score"] for t in group if t.get("execution_score")]
            avg_exec = sum(es) / len(es) if es else None
            net = sum(t["pnl"] or 0 for t in group)
            return {
                "total": total, "wins": wins, "losses": losses,
                "win_rate": wr, "avg_r": avg_r,
                "avg_execution_score": avg_exec, "net_pnl": net,
            }

        setup_stats = {k: _group_stats(v) for k, v in setup_map.items()}
        scenario_stats = {k: _group_stats(v) for k, v in scenario_map.items()}

        mistake_impact: Dict[str, float] = {}
        for t in trades:
            mtags = json.loads(t.get("mistake_tags") or "[]")
            pnl = t.get("pnl") or 0
            for tag in mtags:
                mistake_impact[tag] = mistake_impact.get(tag, 0.0) + pnl

        violation_stats = self.get_violation_stats(symbol=symbol, timeframe=timeframe)

        with_sl = sum(1 for t in trades if t.get("stop_loss") is not None and t["stop_loss"] > 0)
        risk_compliance = with_sl / len(trades) if trades else 1.0

        exec_scores = [t["execution_score"] for t in trades if t.get("execution_score")]
        avg_consistency = sum(exec_scores) / len(exec_scores) if exec_scores else 0

        return {
            "sample_size": len(trades),
            "setup_stats": setup_stats,
            "scenario_stats": scenario_stats,
            "mistake_pnl_impact": dict(sorted(mistake_impact.items(), key=lambda x: x[1])),
            "violation_stats": violation_stats,
            "risk_compliance_rate": risk_compliance,
            "avg_execution_score": avg_consistency,
        }

    def get_progress_curves(
        self,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
        window: int = 20,
    ) -> List[Dict[str, Any]]:
        where_clause, params = self._build_session_filter(symbol=symbol, timeframe=timeframe)
        rows = self._conn.execute(
            f"SELECT t.id, t.exit_time, t.pnl, t.execution_score, t.stop_loss, "
            f"       t.mistake_tags "
            f"FROM trades t JOIN sessions s ON t.session_id=s.id "
            f"{where_clause} ORDER BY t.id",
            params,
        ).fetchall()

        result: List[Dict[str, Any]] = []
        win_buf: List[int] = []
        exec_buf: List[float] = []
        sl_buf: List[int] = []
        mistake_buf: List[int] = []

        for row in rows:
            pnl = row["pnl"] or 0
            win_buf.append(1 if pnl > 0 else 0)
            if len(win_buf) > window:
                win_buf.pop(0)

            es = row["execution_score"] or 0
            if es > 0:
                exec_buf.append(es)
                if len(exec_buf) > window:
                    exec_buf.pop(0)

            sl_buf.append(1 if row["stop_loss"] else 0)
            if len(sl_buf) > window:
                sl_buf.pop(0)

            has_mistake = 1 if json.loads(row["mistake_tags"] or "[]") else 0
            mistake_buf.append(has_mistake)
            if len(mistake_buf) > window:
                mistake_buf.pop(0)

            result.append({
                "time": row["exit_time"],
                "rolling_win_rate": sum(win_buf) / len(win_buf),
                "rolling_exec_score": sum(exec_buf) / len(exec_buf) if exec_buf else 0,
                "rolling_risk_compliance": sum(sl_buf) / len(sl_buf) if sl_buf else 0,
                "rolling_mistake_rate": sum(mistake_buf) / len(mistake_buf) if mistake_buf else 0,
            })
        return result

    def get_stage_report(
        self,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate a stage report with recent vs overall comparisons."""
        overall = self.get_capability_stats(symbol=symbol, timeframe=timeframe)
        recent_20 = self.get_capability_stats(symbol=symbol, timeframe=timeframe, recent_n=20)
        recent_50 = self.get_capability_stats(symbol=symbol, timeframe=timeframe, recent_n=50)

        best_setup = None
        worst_setup = None
        if overall["setup_stats"]:
            by_wr = sorted(overall["setup_stats"].items(), key=lambda x: -x[1]["win_rate"])
            has_samples = [(k, v) for k, v in by_wr if v["total"] >= 3]
            if has_samples:
                best_setup = has_samples[0]
                worst_setup = has_samples[-1]

        top_mistake = None
        if overall["mistake_pnl_impact"]:
            items = list(overall["mistake_pnl_impact"].items())
            if items:
                top_mistake = items[0]

        return {
            "overall": overall,
            "recent_20": recent_20,
            "recent_50": recent_50,
            "best_setup": best_setup,
            "worst_setup": worst_setup,
            "top_mistake_drag": top_mistake,
        }

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_all_sessions(self) -> List[Dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM sessions ORDER BY started_at DESC").fetchall()
        return [dict(row) for row in rows]

    def get_session_trades(self, session_id: str) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM trades WHERE session_id=? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_all_trades_with_session(
        self,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
        setup_type: Optional[str] = None,
        scenario_tag: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return all trades joined with session info, ordered by t.id DESC (newest first)."""
        conditions: List[str] = []
        params: list = []
        if symbol:
            conditions.append("s.symbol = ?")
            params.append(symbol)
        if timeframe:
            conditions.append("s.timeframe = ?")
            params.append(timeframe)
        if setup_type:
            conditions.append("s.setup_type = ?")
            params.append(setup_type)
        if scenario_tag:
            conditions.append("s.scenario_tag = ?")
            params.append(scenario_tag)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = self._conn.execute(
            f"SELECT t.*, s.symbol, s.timeframe, s.setup_type, s.scenario_tag "
            f"FROM trades t JOIN sessions s ON t.session_id = s.id "
            f"{where} ORDER BY t.id DESC",
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def get_session_predictions(self, session_id: str) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM predictions WHERE session_id=? ORDER BY bar_index",
            (session_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_session_notes(self, session_id: str) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM session_notes WHERE session_id=? ORDER BY created_at DESC",
            (session_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_distinct_session_values(self, field_name: str) -> List[str]:
        allowed = {"symbol", "timeframe", "setup_type", "scenario_tag", "mode"}
        if field_name not in allowed:
            return []
        rows = self._conn.execute(
            f"SELECT DISTINCT {field_name} FROM sessions WHERE {field_name} IS NOT NULL AND {field_name}!='' ORDER BY {field_name}"
        ).fetchall()
        return [row[0] for row in rows if row[0]]

    def get_distinct_trade_values(self, field_name: str) -> List[str]:
        allowed = {"direction", "exit_reason"}
        if field_name not in allowed:
            return []
        rows = self._conn.execute(
            f"SELECT DISTINCT {field_name} FROM trades WHERE {field_name} IS NOT NULL AND {field_name}!='' ORDER BY {field_name}"
        ).fetchall()
        return [row[0] for row in rows if row[0]]

    # ------------------------------------------------------------------
    # Aggregate statistics
    # ------------------------------------------------------------------

    def get_overall_stats(
        self,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
    ) -> AggregateStats:
        where_clause, params = self._build_session_filter(symbol=symbol, timeframe=timeframe)
        stats = AggregateStats()

        stats.total_sessions = self._conn.execute(
            f"SELECT COUNT(*) FROM sessions s {where_clause}",
            params,
        ).fetchone()[0]

        trade_rows = self._conn.execute(
            f"SELECT t.* FROM trades t JOIN sessions s ON t.session_id=s.id {where_clause} ORDER BY t.id",
            params,
        ).fetchall()
        trades = [dict(row) for row in trade_rows]
        stats.total_trades = len(trades)

        if trades:
            wins = [trade for trade in trades if (trade["pnl"] or 0) > 0]
            losses = [trade for trade in trades if (trade["pnl"] or 0) < 0]
            stats.winners = len(wins)
            stats.losers = len(losses)
            closed = stats.winners + stats.losers
            stats.win_rate = stats.winners / closed if closed else 0.0
            stats.avg_pnl_pct = sum(trade["pnl_pct"] or 0 for trade in trades) / len(trades)
            r_values = [trade["r_multiple"] for trade in trades if trade["r_multiple"] is not None]
            stats.avg_r = sum(r_values) / len(r_values) if r_values else None
            gross_profit = sum(trade["pnl"] or 0 for trade in trades if (trade["pnl"] or 0) > 0)
            gross_loss = abs(sum(trade["pnl"] or 0 for trade in trades if (trade["pnl"] or 0) < 0))
            stats.profit_factor = gross_profit / gross_loss if gross_loss else float("inf")
            stats.total_pnl = sum(trade["pnl"] or 0 for trade in trades)
            stats.total_commission = sum(trade["commission"] or 0 for trade in trades)
            avg_win = sum(trade["pnl_pct"] or 0 for trade in wins) / len(wins) if wins else 0.0
            avg_loss = sum(trade["pnl_pct"] or 0 for trade in losses) / len(losses) if losses else 0.0
            loss_rate = 1 - stats.win_rate
            stats.expectancy = (stats.win_rate * avg_win) + (loss_rate * avg_loss)
            stats.max_consecutive_wins = self._streak(trades, winning=True)
            stats.max_consecutive_losses = self._streak(trades, winning=False)
            stats.max_drawdown_pct = self._drawdown(trades)

        prediction_rows = self._conn.execute(
            f"SELECT p.* FROM predictions p JOIN sessions s ON p.session_id=s.id {where_clause}",
            params,
        ).fetchall()
        stats.total_predictions = len(prediction_rows)
        stats.correct_predictions = sum(1 for row in prediction_rows if row["is_correct"] == 1)
        stats.prediction_accuracy = (
            stats.correct_predictions / stats.total_predictions if stats.total_predictions else 0.0
        )
        return stats

    def get_equity_curve(
        self,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        where_clause, params = self._build_session_filter(symbol=symbol, timeframe=timeframe)
        rows = self._conn.execute(
            f"SELECT t.exit_time, t.pnl, t.pnl_pct, t.r_multiple FROM trades t "
            f"JOIN sessions s ON t.session_id=s.id {where_clause} ORDER BY t.id",
            params,
        ).fetchall()
        cumulative = 0.0
        result: List[Dict[str, Any]] = []
        for row in rows:
            cumulative += row["pnl"] or 0
            result.append(
                {
                    "time": row["exit_time"],
                    "pnl": row["pnl"],
                    "cumulative_pnl": cumulative,
                    "pnl_pct": row["pnl_pct"],
                    "r_multiple": row["r_multiple"],
                }
            )
        return result

    def get_rolling_win_rate(self, window: int = 20) -> List[Dict[str, Any]]:
        rows = self._conn.execute("SELECT exit_time, pnl FROM trades ORDER BY id").fetchall()
        bucket: List[int] = []
        result: List[Dict[str, Any]] = []
        for row in rows:
            bucket.append(1 if (row["pnl"] or 0) > 0 else 0)
            if len(bucket) > window:
                bucket.pop(0)
            result.append(
                {
                    "time": row["exit_time"],
                    "win_rate": sum(bucket) / len(bucket),
                    "sample_size": len(bucket),
                }
            )
        return result

    def get_r_distribution(self) -> Dict[str, int]:
        rows = self._conn.execute(
            "SELECT r_multiple FROM trades WHERE r_multiple IS NOT NULL"
        ).fetchall()
        buckets: Dict[str, int] = {}
        for row in rows:
            value = row["r_multiple"]
            if value <= -3:
                key = "<-3R"
            elif value <= -2:
                key = "-3R~-2R"
            elif value <= -1:
                key = "-2R~-1R"
            elif value <= 0:
                key = "-1R~0R"
            elif value <= 1:
                key = "0R~1R"
            elif value <= 2:
                key = "1R~2R"
            elif value <= 3:
                key = "2R~3R"
            else:
                key = ">3R"
            buckets[key] = buckets.get(key, 0) + 1
        return buckets

    # ------------------------------------------------------------------
    # Drill-down aggregations
    # ------------------------------------------------------------------

    def get_stats_by_setup(self, symbol: Optional[str] = None, timeframe: Optional[str] = None) -> List[Dict[str, Any]]:
        where_clause, params = self._build_session_filter(symbol=symbol, timeframe=timeframe)
        where_clause = self._extend_where(where_clause, "s.setup_type!=''")
        rows = self._conn.execute(
            f"SELECT s.setup_type, COUNT(t.id) AS cnt, "
            f"SUM(CASE WHEN t.pnl>0 THEN 1 ELSE 0 END) AS wins, "
            f"SUM(CASE WHEN t.pnl<0 THEN 1 ELSE 0 END) AS losses, "
            f"SUM(t.pnl) AS total_pnl, AVG(t.pnl_pct) AS avg_pnl_pct "
            f"FROM trades t JOIN sessions s ON t.session_id=s.id "
            f"{where_clause} GROUP BY s.setup_type ORDER BY cnt DESC",
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def get_stats_by_mistake(self, symbol: Optional[str] = None, timeframe: Optional[str] = None) -> List[Dict[str, Any]]:
        where_clause, params = self._build_session_filter(symbol=symbol, timeframe=timeframe)
        rows = self._conn.execute(
            f"SELECT t.mistake_tags FROM trades t JOIN sessions s ON t.session_id=s.id {where_clause}",
            params,
        ).fetchall()
        counter: Dict[str, int] = {}
        for row in rows:
            for tag in json.loads(row["mistake_tags"] or "[]"):
                counter[tag] = counter.get(tag, 0) + 1
        return [{"tag": tag, "count": count} for tag, count in sorted(counter.items(), key=lambda item: -item[1])]

    def get_stats_by_scenario(self, symbol: Optional[str] = None, timeframe: Optional[str] = None) -> List[Dict[str, Any]]:
        where_clause, params = self._build_session_filter(symbol=symbol, timeframe=timeframe)
        where_clause = self._extend_where(where_clause, "s.scenario_tag!=''")
        rows = self._conn.execute(
            f"SELECT s.scenario_tag, COUNT(t.id) AS cnt, "
            f"SUM(CASE WHEN t.pnl>0 THEN 1 ELSE 0 END) AS wins, "
            f"SUM(t.pnl) AS total_pnl "
            f"FROM trades t JOIN sessions s ON t.session_id=s.id "
            f"{where_clause} GROUP BY s.scenario_tag ORDER BY cnt DESC",
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def get_stats_by_exit_reason(self, symbol: Optional[str] = None, timeframe: Optional[str] = None) -> List[Dict[str, Any]]:
        where_clause, params = self._build_session_filter(symbol=symbol, timeframe=timeframe)
        where_clause = self._extend_where(where_clause, "t.exit_reason!=''")
        rows = self._conn.execute(
            f"SELECT t.exit_reason, COUNT(*) AS cnt, "
            f"SUM(CASE WHEN t.pnl>0 THEN 1 ELSE 0 END) AS wins, "
            f"AVG(t.pnl_pct) AS avg_pnl_pct "
            f"FROM trades t JOIN sessions s ON t.session_id=s.id "
            f"{where_clause} GROUP BY t.exit_reason ORDER BY cnt DESC",
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def get_execution_score_avg(self, symbol: Optional[str] = None, timeframe: Optional[str] = None) -> Optional[float]:
        where_clause, params = self._build_session_filter(symbol=symbol, timeframe=timeframe)
        where_clause = self._extend_where(where_clause, "t.execution_score>0")
        row = self._conn.execute(
            f"SELECT AVG(t.execution_score) AS avg_score FROM trades t JOIN sessions s ON t.session_id=s.id {where_clause}",
            params,
        ).fetchone()
        return row["avg_score"] if row and row["avg_score"] is not None else None

    def get_pa_stats(self, symbol: Optional[str] = None, timeframe: Optional[str] = None) -> Dict[str, Any]:
        where_clause, params = self._build_session_filter(symbol=symbol, timeframe=timeframe)
        ann_rows = self._conn.execute(
            f"SELECT a.is_correct FROM pa_annotations a JOIN sessions s ON a.session_id=s.id {where_clause}",
            params,
        ).fetchall()
        mistake_rows = self._conn.execute(
            "SELECT retrained, category FROM mistake_book WHERE category='pa'"
        ).fetchall()
        total = len(ann_rows)
        evaluated = sum(1 for row in ann_rows if row["is_correct"] is not None)
        correct = sum(1 for row in ann_rows if row["is_correct"] == 1)
        retrained = sum(1 for row in mistake_rows if row["retrained"] == 1)
        return {
            "total_annotations": total,
            "evaluated_annotations": evaluated,
            "correct_annotations": correct,
            "annotation_accuracy": (correct / evaluated) if evaluated else 0.0,
            "pa_mistakes": len(mistake_rows),
            "retrained_pa_mistakes": retrained,
            "retrain_rate": (retrained / len(mistake_rows)) if mistake_rows else 0.0,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_session_filter(symbol: Optional[str] = None, timeframe: Optional[str] = None) -> tuple[str, List[Any]]:
        parts: List[str] = []
        params: List[Any] = []
        if symbol:
            parts.append("s.symbol=?")
            params.append(symbol)
        if timeframe:
            parts.append("s.timeframe=?")
            params.append(timeframe)
        return (f"WHERE {' AND '.join(parts)}" if parts else ""), params

    @staticmethod
    def _extend_where(where_clause: str, condition: str) -> str:
        if not where_clause:
            return f"WHERE {condition}"
        return f"{where_clause} AND {condition}"

    @staticmethod
    def _streak(trades: List[dict], winning: bool) -> int:
        best = 0
        current = 0
        for trade in trades:
            if ((trade["pnl"] or 0) > 0) == winning:
                current += 1
                best = max(best, current)
            else:
                current = 0
        return best

    @staticmethod
    def _drawdown(trades: List[dict]) -> float:
        cumulative = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for trade in trades:
            cumulative += trade["pnl_pct"] or 0
            if cumulative > peak:
                peak = cumulative
            max_drawdown = max(max_drawdown, peak - cumulative)
        return max_drawdown
