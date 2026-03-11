from __future__ import annotations

import random
from typing import List, Optional, Tuple

from app.domain.candle import Candle, Symbol, Timeframe
from app.data.cache.repository import CacheRepository
from app.data.providers.akshare_provider import AKShareProvider


class ReplayEngine:
    """Provides bar-by-bar replay of historical candle data."""

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
        """Download data if not cached (or force); return total bar count."""
        if force or not self._cache.has_data(symbol, timeframe):
            candles = self._provider.fetch_candles(symbol, timeframe, start_date, end_date)
            if candles:
                self._cache.save_candles(symbol, timeframe, candles)
        return len(self._cache.load_candles(symbol, timeframe))

    def load_all(self, symbol: Symbol, timeframe: Timeframe) -> List[Candle]:
        return self._cache.load_candles(symbol, timeframe)

    def random_slice(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        visible_bars: int = 60,
        future_bars: int = 120,
    ) -> Tuple[List[Candle], List[Candle]]:
        """Return (visible_history, hidden_future) for a random segment."""
        all_candles = self._cache.load_candles(symbol, timeframe)
        total_needed = visible_bars + future_bars
        if len(all_candles) < total_needed:
            raise ValueError(
                f"Not enough data: need {total_needed} bars, have {len(all_candles)}"
            )
        max_start = len(all_candles) - total_needed
        start = random.randint(0, max_start)
        visible = all_candles[start: start + visible_bars]
        future = all_candles[start + visible_bars: start + total_needed]
        return visible, future

    def load_multi_timeframe(
        self,
        symbol: Symbol,
        primary_tf: Timeframe,
        higher_tf: Timeframe,
    ) -> Tuple[List[Candle], List[Candle]]:
        primary = self._cache.load_candles(symbol, primary_tf)
        higher = self._cache.load_candles(symbol, higher_tf)
        return primary, higher
