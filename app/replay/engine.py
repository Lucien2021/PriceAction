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

    def ensure_all_timeframes(self, symbol: Symbol, force: bool = False) -> Dict[Timeframe, int]:
        counts = {}
        for tf in TRAINING_TFS:
            try:
                counts[tf] = self.ensure_data(symbol, tf, force=force)
            except Exception:
                counts[tf] = 0
        return counts

    def load_all(self, symbol: Symbol, timeframe: Timeframe) -> List[Candle]:
        return self._cache.load_candles(symbol, timeframe)

    def random_slice(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        visible_bars: int = 60,
        future_bars: int = 120,
    ) -> Tuple[List[Candle], List[Candle]]:
        all_candles = self._cache.load_candles(symbol, timeframe)
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
