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
    future_bars  INTEGER DEFAULT 0,
    -- plan & scenario fields
    setup_type    TEXT DEFAULT '',
    scenario_tag  TEXT DEFAULT '',
    plan_notes    TEXT DEFAULT '',
    plan_direction TEXT DEFAULT '',
    plan_invalidation TEXT DEFAULT '',
    difficulty    INTEGER DEFAULT 0,
    score         INTEGER DEFAULT 0
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
    notes           TEXT DEFAULT '',
    -- execution quality fields
    mistake_tags      TEXT DEFAULT '[]',
    execution_score   INTEGER DEFAULT 0,
    planned_risk_pct  REAL DEFAULT 0,
    entry_reason      TEXT DEFAULT '',
    exit_review       TEXT DEFAULT '',
    commission        REAL DEFAULT 0
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

CREATE TABLE IF NOT EXISTS pa_annotations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    created_at  TEXT NOT NULL,
    ann_type    TEXT NOT NULL,
    data_json   TEXT NOT NULL DEFAULT '{}',
    notes       TEXT DEFAULT '',
    is_correct  INTEGER
);

CREATE TABLE IF NOT EXISTS mistake_book (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    created_at  TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    timeframe   TEXT NOT NULL,
    category    TEXT NOT NULL DEFAULT 'trade',
    setup_type  TEXT DEFAULT '',
    mistake_tags TEXT DEFAULT '[]',
    description TEXT DEFAULT '',
    slice_start INTEGER DEFAULT 0,
    slice_end   INTEGER DEFAULT 0,
    retrained   INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_trades_session ON trades(session_id);
CREATE INDEX IF NOT EXISTS idx_predictions_session ON predictions(session_id);
CREATE INDEX IF NOT EXISTS idx_notes_session ON session_notes(session_id);
CREATE INDEX IF NOT EXISTS idx_pa_ann_session ON pa_annotations(session_id);
CREATE INDEX IF NOT EXISTS idx_mistake_session ON mistake_book(session_id);
"""

_MIGRATIONS = [
    "ALTER TABLE sessions ADD COLUMN setup_type TEXT DEFAULT ''",
    "ALTER TABLE sessions ADD COLUMN scenario_tag TEXT DEFAULT ''",
    "ALTER TABLE sessions ADD COLUMN plan_notes TEXT DEFAULT ''",
    "ALTER TABLE sessions ADD COLUMN plan_direction TEXT DEFAULT ''",
    "ALTER TABLE sessions ADD COLUMN plan_invalidation TEXT DEFAULT ''",
    "ALTER TABLE sessions ADD COLUMN difficulty INTEGER DEFAULT 0",
    "ALTER TABLE sessions ADD COLUMN score INTEGER DEFAULT 0",
    "ALTER TABLE trades ADD COLUMN mistake_tags TEXT DEFAULT '[]'",
    "ALTER TABLE trades ADD COLUMN execution_score INTEGER DEFAULT 0",
    "ALTER TABLE trades ADD COLUMN planned_risk_pct REAL DEFAULT 0",
    "ALTER TABLE trades ADD COLUMN entry_reason TEXT DEFAULT ''",
    "ALTER TABLE trades ADD COLUMN exit_review TEXT DEFAULT ''",
    "ALTER TABLE trades ADD COLUMN commission REAL DEFAULT 0",
]


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else _DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _apply_migrations(conn)
    return conn


def _apply_migrations(conn: sqlite3.Connection) -> None:
    for stmt in _MIGRATIONS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass
    conn.commit()
