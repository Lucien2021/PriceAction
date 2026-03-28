from __future__ import annotations

import json
import sqlite3
import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.domain.candle import ClosedTrade
from app.storage.models import get_connection


@dataclass
class ChallengeRunRecord:
    id: int
    target_amount: float
    initial_capital: float
    final_equity: float
    outcome: str
    bars_elapsed: int
    calendar_seconds: float
    avg_hold_bars: float
    trade_count: int
    win_rate: float
    profit_factor: float
    total_commission: float
    max_drawdown_pct: float
    symbols_used: List[str]
    started_at: Optional[str]
    finished_at: Optional[str]
    last_symbol: str
    notes: str


@dataclass
class TargetGroupSummary:
    target_amount: float
    run_count: int
    wins: int
    win_rate_runs: float
    bars_median: Optional[float]
    bars_mean: float
    bars_min_win: Optional[int]
    bars_max_win: Optional[int]


class ChallengeService:
    def __init__(self, conn: Optional[sqlite3.Connection] = None):
        self._conn = conn or get_connection()

    def close(self) -> None:
        self._conn.close()

    def save_run(
        self,
        *,
        target_amount: float,
        initial_capital: float,
        final_equity: float,
        outcome: str,
        bars_elapsed: int,
        calendar_seconds: float,
        avg_hold_bars: float,
        trade_count: int,
        win_rate: float,
        profit_factor: float,
        total_commission: float,
        max_drawdown_pct: float,
        symbols_used: List[str],
        started_at: Optional[datetime],
        finished_at: Optional[datetime],
        last_symbol: str,
        notes: str = "",
        trades_with_symbols: Optional[List[tuple[str, ClosedTrade]]] = None,
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO challenge_runs ("
            " target_amount, initial_capital, final_equity, outcome, bars_elapsed, calendar_seconds,"
            " avg_hold_bars, trade_count, win_rate, profit_factor, total_commission, max_drawdown_pct,"
            " symbols_used, started_at, finished_at, last_symbol, notes"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                target_amount,
                initial_capital,
                final_equity,
                outcome,
                bars_elapsed,
                calendar_seconds,
                avg_hold_bars,
                trade_count,
                win_rate,
                profit_factor,
                total_commission,
                max_drawdown_pct,
                json.dumps(symbols_used, ensure_ascii=False),
                started_at.isoformat() if started_at else None,
                finished_at.isoformat() if finished_at else None,
                last_symbol,
                notes,
            ),
        )
        self._conn.commit()
        run_id = int(cur.lastrowid)
        if trades_with_symbols:
            for sym, t in trades_with_symbols:
                self._insert_trade(run_id, t, sym)
            self._conn.commit()
        return run_id

    def _insert_trade(self, run_id: int, t: ClosedTrade, symbol: str) -> None:
        self._conn.execute(
            "INSERT INTO challenge_trades ("
            " run_id, direction, entry_price, exit_price, quantity,"
            " entry_time, exit_time, entry_bar_index, exit_bar_index,"
            " exit_reason, pnl, pnl_pct, hold_bars, commission, symbol"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                t.direction.value,
                t.entry_price,
                t.exit_price,
                t.quantity,
                t.entry_time.isoformat() if t.entry_time else None,
                t.exit_time.isoformat() if t.exit_time else None,
                t.entry_bar_index,
                t.exit_bar_index,
                t.exit_reason or "",
                t.pnl,
                t.pnl_pct,
                t.hold_bars,
                t.commission,
                symbol,
            ),
        )

    def list_all_runs(self) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM challenge_runs ORDER BY id DESC"
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def list_runs_grouped_by_target(self) -> Dict[float, List[Dict[str, Any]]]:
        runs = self.list_all_runs()
        grouped: Dict[float, List[Dict[str, Any]]] = {}
        for r in runs:
            t = float(r["target_amount"])
            grouped.setdefault(t, []).append(r)
        return dict(sorted(grouped.items(), key=lambda x: x[0]))

    def group_summaries(self) -> List[TargetGroupSummary]:
        grouped = self.list_runs_grouped_by_target()
        out: List[TargetGroupSummary] = []
        for target, items in grouped.items():
            wins = sum(1 for r in items if r["outcome"] == "win")
            n = len(items)
            bars_list = [int(r["bars_elapsed"]) for r in items]
            win_bars = [int(r["bars_elapsed"]) for r in items if r["outcome"] == "win"]
            out.append(
                TargetGroupSummary(
                    target_amount=target,
                    run_count=n,
                    wins=wins,
                    win_rate_runs=wins / n if n else 0.0,
                    bars_median=statistics.median(bars_list) if bars_list else None,
                    bars_mean=statistics.mean(bars_list) if bars_list else 0.0,
                    bars_min_win=min(win_bars) if win_bars else None,
                    bars_max_win=max(win_bars) if win_bars else None,
                )
            )
        return out

    def get_trades(self, run_id: int) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM challenge_trades WHERE run_id=? ORDER BY id",
            (run_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        try:
            d["symbols_used"] = json.loads(d.get("symbols_used") or "[]")
        except json.JSONDecodeError:
            d["symbols_used"] = []
        return d
