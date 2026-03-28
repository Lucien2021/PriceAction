from __future__ import annotations

import random
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.domain.candle import Candle, Symbol, Timeframe
from app.data.cache.repository import CacheRepository
from app.data.providers.akshare_provider import AKShareProvider


TRAINING_TFS = [Timeframe.MONTHLY, Timeframe.DAILY, Timeframe.M5, Timeframe.M1]

_USED_DB = Path.home() / ".priceaction" / "used_ranges.db"


def _used_db():
    _USED_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_USED_DB))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS used_ranges ("
        "  symbol TEXT NOT NULL, tf TEXT NOT NULL, "
        "  start_idx INT NOT NULL, end_idx INT NOT NULL)"
    )
    return conn


class ReplayEngine:
    def __init__(self, cache: CacheRepository, provider: AKShareProvider):
        self._cache = cache
        self._provider = provider

    def ensure_data(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        force: bool = False,
    ) -> int:
        if force or not self._cache.has_data(symbol, timeframe):
            candles = self._provider.fetch_candles(symbol, timeframe, start_date, end_date)
            if candles:
                self._cache.save_candles(symbol, timeframe, candles)
        return len(self._cache.load_candles(symbol, timeframe))

    def ensure_all_timeframes(
        self,
        symbol: Symbol,
        force: bool = False,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[Timeframe, int]:
        sd = start_date or "20100101"
        ed = end_date or datetime.now().strftime("%Y%m%d")
        counts = {}
        for tf in TRAINING_TFS:
            try:
                counts[tf] = self.ensure_data(symbol, tf, start_date=sd, end_date=ed, force=force)
            except Exception:
                counts[tf] = 0
        return counts

    def load_all(self, symbol: Symbol, timeframe: Timeframe) -> List[Candle]:
        return self._trim_negative_qfq(self._cache.load_candles(symbol, timeframe))

    def symbols_with_timeframe(self, timeframe: Timeframe, min_bars: int = 61) -> List[str]:
        return self._cache.list_symbols_with_timeframe(timeframe, min_bars)

    def challenge_slice(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        visible_bars: int,
    ) -> Tuple[List[Candle], List[Candle]]:
        """随机起点；future 为剩余全部 K 线（无 future 根数上限）。至少保留 2 根未来 K。"""
        all_candles = self.load_all(symbol, timeframe)
        if len(all_candles) < visible_bars + 2:
            raise ValueError(
                f"挑战切片数据不足: 需要至少 {visible_bars + 2} 根，当前 {len(all_candles)}"
            )
        max_start = len(all_candles) - visible_bars - 2
        start = random.randint(0, max_start)
        visible = all_candles[start : start + visible_bars]
        future = all_candles[start + visible_bars :]
        return visible, future

    @staticmethod
    def _trim_negative_qfq(candles: List[Candle]) -> List[Candle]:
        """Drop leading candles whose qfq-adjusted prices went negative.

        For stocks with persistent dividends, forward-adjusted (qfq) prices
        can become negative for very early history.  Find the first bar where
        all OHLC values are positive and keep everything from there onward.
        """
        for i, c in enumerate(candles):
            if c.open > 0 and c.high > 0 and c.low > 0 and c.close > 0:
                return candles[i:]
        return []

    def random_slice(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        visible_bars: int = 60,
        future_bars: int = 120,
        date_start: Optional[datetime] = None,
        date_end: Optional[datetime] = None,
        scenario_tag: str = "",
    ) -> Tuple[List[Candle], List[Candle]]:
        all_candles = self._cache.load_candles(symbol, timeframe)
        all_candles = self._trim_negative_qfq(all_candles)
        if date_start or date_end:
            all_candles = [
                c for c in all_candles
                if (date_start is None or c.timestamp >= date_start)
                and (date_end is None or c.timestamp <= date_end)
            ]
        total_needed = visible_bars + future_bars
        if len(all_candles) < total_needed:
            raise ValueError(
                f"Not enough data: need {total_needed} bars, have {len(all_candles)}"
            )

        conn = _used_db()
        rows = conn.execute(
            "SELECT start_idx, end_idx FROM used_ranges WHERE symbol=? AND tf=?",
            (symbol.code, timeframe.label),
        ).fetchall()
        used = set()
        for s, e in rows:
            for i in range(s, e):
                used.add(i)

        max_start = len(all_candles) - total_needed
        candidates = [i for i in range(0, max_start + 1) if i not in used]
        candidates = self._filter_candidates_by_scenario(
            all_candles,
            candidates,
            visible_bars=visible_bars,
            future_bars=future_bars,
            scenario_tag=scenario_tag,
        ) or candidates

        if not candidates:
            conn.execute(
                "DELETE FROM used_ranges WHERE symbol=? AND tf=?",
                (symbol.code, timeframe.label),
            )
            conn.commit()
            candidates = list(range(0, max_start + 1))

        start = random.choice(candidates)
        conn.execute(
            "INSERT INTO used_ranges (symbol, tf, start_idx, end_idx) VALUES (?,?,?,?)",
            (symbol.code, timeframe.label, start, start + total_needed),
        )
        conn.commit()
        conn.close()

        visible = all_candles[start: start + visible_bars]
        future = all_candles[start + visible_bars: start + total_needed]
        return visible, future

    def _filter_candidates_by_scenario(
        self,
        candles: List[Candle],
        candidates: List[int],
        visible_bars: int,
        future_bars: int,
        scenario_tag: str,
    ) -> List[int]:
        tag = (scenario_tag or "").strip().lower()
        if not tag:
            return candidates

        matched: List[int] = []
        total_needed = visible_bars + future_bars
        for start in candidates:
            window = candles[start: start + total_needed]
            if len(window) < total_needed:
                continue
            visible = window[:visible_bars]
            future = window[visible_bars:]
            if self._match_scenario(visible, future, tag):
                matched.append(start)
        return matched

    def _match_scenario(self, visible: List[Candle], future: List[Candle], tag: str) -> bool:
        if not visible or not future:
            return False
        vis_start = visible[0].close
        vis_end = visible[-1].close
        fut_end = future[-1].close
        vis_high = max(c.high for c in visible)
        vis_low = min(c.low for c in visible)
        vis_range = max(vis_high - vis_low, 1e-6)
        future_high = max(c.high for c in future)
        future_low = min(c.low for c in future)
        total_move = (fut_end - vis_start) / vis_start if vis_start else 0.0
        visible_move = (vis_end - vis_start) / vis_start if vis_start else 0.0
        future_move = (fut_end - vis_end) / vis_end if vis_end else 0.0

        if tag in {"趋势回踩", "trend_pullback", "trend", "趋势"}:
            return visible_move > 0.03 and future_low <= vis_end * 0.985 and fut_end >= vis_end

        if tag in {"区间突破", "range_breakout", "breakout"}:
            return (
                vis_range / vis_end < 0.08
                and (future_high > vis_high * 1.01 or future_low < vis_low * 0.99)
            )

        if tag in {"假突破", "false_breakout"}:
            broke_up = future_high > vis_high * 1.01 and fut_end < vis_high
            broke_down = future_low < vis_low * 0.99 and fut_end > vis_low
            return broke_up or broke_down

        if tag in {"反转确认", "reversal_confirm", "reversal"}:
            return visible_move * future_move < 0 and abs(future_move) > 0.02

        if tag in {"高波动", "high_volatility"}:
            return (future_high - future_low) / max(vis_end, 1e-6) > 0.08

        if tag in {"震荡", "range"}:
            return abs(total_move) < 0.03 and (future_high - future_low) / max(vis_end, 1e-6) < 0.1

        return True

    def get_candles_for_date_range(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        start_dt: datetime,
        end_dt: datetime,
    ) -> List[Candle]:
        all_candles = self._cache.load_candles(symbol, timeframe)
        return [c for c in all_candles if start_dt <= c.timestamp <= end_dt]

    def load_multi_timeframe(
        self,
        symbol: Symbol,
        primary_tf: Timeframe,
        higher_tf: Timeframe,
    ) -> Tuple[List[Candle], List[Candle]]:
        primary = self._cache.load_candles(symbol, primary_tf)
        higher = self._cache.load_candles(symbol, higher_tf)
        return primary, higher
