from __future__ import annotations

import sqlite3
from pathlib import Path

_DB_DIR = Path.home() / ".priceaction"
_DB_PATH = _DB_DIR / "training.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    symbol      TEXT NOT NULL,
    timeframe   TEXT NOT NULL,
    mode        TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT,
    visible_bars INTEGER DEFAULT 0,
    future_bars  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(id),
    direction       TEXT NOT NULL,
    entry_price     REAL NOT NULL,
    exit_price      REAL NOT NULL,
    quantity        INTEGER NOT NULL,
    entry_time      TEXT,
    exit_time       TEXT,
    entry_bar_index INTEGER,
    exit_bar_index  INTEGER,
    stop_loss       REAL,
    take_profit     REAL,
    exit_reason     TEXT,
    pnl             REAL,
    pnl_pct         REAL,
    r_multiple      REAL,
    hold_bars       INTEGER,
    max_favorable   REAL DEFAULT 0,
    max_adverse     REAL DEFAULT 0,
    tags            TEXT DEFAULT '[]',
    notes           TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS predictions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(id),
    direction       TEXT NOT NULL,
    bar_index       INTEGER,
    timestamp       TEXT,
    lookahead_bars  INTEGER DEFAULT 1,
    actual_direction TEXT,
    is_correct      INTEGER
);

CREATE TABLE IF NOT EXISTS session_notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    created_at  TEXT NOT NULL,
    content     TEXT NOT NULL,
    tags        TEXT DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_trades_session ON trades(session_id);
CREATE INDEX IF NOT EXISTS idx_predictions_session ON predictions(session_id);
CREATE INDEX IF NOT EXISTS idx_notes_session ON session_notes(session_id);
"""


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else _DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn
