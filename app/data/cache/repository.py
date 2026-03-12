from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from app.domain.candle import Candle, Symbol, Timeframe


_DB_DIR = Path.home() / ".priceaction"
_DB_PATH = _DB_DIR / "cache.db"

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS candle_cache (
    symbol   TEXT    NOT NULL,
    tf       TEXT    NOT NULL,
    ts       TEXT    NOT NULL,
    open     REAL    NOT NULL,
    high     REAL    NOT NULL,
    low      REAL    NOT NULL,
    close    REAL    NOT NULL,
    volume   REAL    NOT NULL,
    turnover REAL    NOT NULL DEFAULT 0,
    PRIMARY KEY (symbol, tf, ts)
);
CREATE INDEX IF NOT EXISTS idx_candle_symbol_tf ON candle_cache(symbol, tf);
"""


class CacheRepository:
    def __init__(self, db_path: Path | str | None = None):
        self._db_path = Path(db_path) if db_path else _DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.executescript(_CREATE_SQL)

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------

    def save_candles(self, symbol: Symbol, timeframe: Timeframe, candles: List[Candle]) -> None:
        rows = [
            (symbol.code, timeframe.label, c.timestamp.isoformat(),
             c.open, c.high, c.low, c.close, c.volume, c.turnover)
            for c in candles
        ]
        self._conn.executemany(
            "INSERT OR REPLACE INTO candle_cache "
            "(symbol, tf, ts, open, high, low, close, volume, turnover) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()

    def load_candles(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> List[Candle]:
        query = "SELECT ts, open, high, low, close, volume, turnover FROM candle_cache WHERE symbol=? AND tf=?"
        params: list = [symbol.code, timeframe.label]
        if start:
            query += " AND ts >= ?"
            params.append(start)
        if end:
            query += " AND ts <= ?"
            params.append(end)
        query += " ORDER BY ts ASC"

        rows = self._conn.execute(query, params).fetchall()
        return [
            Candle(
                timestamp=datetime.fromisoformat(r[0]),
                open=r[1], high=r[2], low=r[3], close=r[4],
                volume=r[5], turnover=r[6],
            )
            for r in rows
        ]

    def has_data(self, symbol: Symbol, timeframe: Timeframe) -> bool:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM candle_cache WHERE symbol=? AND tf=?",
            (symbol.code, timeframe.label),
        ).fetchone()
        return (row[0] or 0) > 0

    def get_date_range(self, symbol: Symbol, timeframe: Timeframe) -> Optional[tuple]:
        row = self._conn.execute(
            "SELECT MIN(ts), MAX(ts) FROM candle_cache WHERE symbol=? AND tf=?",
            (symbol.code, timeframe.label),
        ).fetchone()
        if row and row[0]:
            return (row[0], row[1])
        return None

    def get_common_date_range(self, symbol: Symbol, timeframes: List[Timeframe]) -> Optional[tuple]:
        """Return (max_of_mins, min_of_maxes) across all given TFs — the overlap."""
        from datetime import datetime as _dt
        latest_start = None
        earliest_end = None
        for tf in timeframes:
            rng = self.get_date_range(symbol, tf)
            if rng is None:
                continue
            s = _dt.fromisoformat(rng[0]) if isinstance(rng[0], str) else rng[0]
            e = _dt.fromisoformat(rng[1]) if isinstance(rng[1], str) else rng[1]
            if latest_start is None or s > latest_start:
                latest_start = s
            if earliest_end is None or e < earliest_end:
                earliest_end = e
        if latest_start and earliest_end and latest_start < earliest_end:
            return (latest_start, earliest_end)
        return None

    def list_cached_symbols(self) -> List[str]:
        rows = self._conn.execute("SELECT DISTINCT symbol FROM candle_cache").fetchall()
        return [r[0] for r in rows]

    def get_cache_summary(self) -> List[dict]:
        rows = self._conn.execute(
            "SELECT symbol, tf, COUNT(*) as cnt, MIN(ts), MAX(ts) "
            "FROM candle_cache GROUP BY symbol, tf ORDER BY symbol, tf"
        ).fetchall()
        return [
            {"symbol": r[0], "tf": r[1], "bars": r[2], "start": r[3][:10], "end": r[4][:10]}
            for r in rows
        ]

    def delete_symbol_data(self, symbol_code: str, timeframe_label: str | None = None) -> int:
        if timeframe_label:
            cur = self._conn.execute(
                "DELETE FROM candle_cache WHERE symbol=? AND tf=?",
                (symbol_code, timeframe_label),
            )
        else:
            cur = self._conn.execute(
                "DELETE FROM candle_cache WHERE symbol=?", (symbol_code,),
            )
        self._conn.commit()
        return cur.rowcount
