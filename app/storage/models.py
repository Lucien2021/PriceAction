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
    score         INTEGER DEFAULT 0,
    training_goal TEXT DEFAULT ''
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

CREATE TABLE IF NOT EXISTS equity_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(id),
    trade_id        INTEGER,
    created_at      TEXT NOT NULL,
    equity_before   REAL NOT NULL DEFAULT 100000,
    equity_after    REAL NOT NULL DEFAULT 100000,
    is_reset        INTEGER DEFAULT 0,
    bankruptcy_count INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_trades_session ON trades(session_id);
CREATE INDEX IF NOT EXISTS idx_predictions_session ON predictions(session_id);
CREATE INDEX IF NOT EXISTS idx_notes_session ON session_notes(session_id);
CREATE INDEX IF NOT EXISTS idx_pa_ann_session ON pa_annotations(session_id);
CREATE INDEX IF NOT EXISTS idx_mistake_session ON mistake_book(session_id);
CREATE INDEX IF NOT EXISTS idx_equity_session ON equity_snapshots(session_id);

CREATE TABLE IF NOT EXISTS challenge_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    target_amount       REAL NOT NULL,
    initial_capital     REAL NOT NULL DEFAULT 200000,
    final_equity        REAL NOT NULL DEFAULT 0,
    outcome             TEXT NOT NULL,
    bars_elapsed        INTEGER NOT NULL DEFAULT 0,
    calendar_seconds    REAL DEFAULT 0,
    avg_hold_bars       REAL DEFAULT 0,
    trade_count         INTEGER NOT NULL DEFAULT 0,
    win_rate            REAL DEFAULT 0,
    profit_factor       REAL DEFAULT 0,
    total_commission    REAL DEFAULT 0,
    max_drawdown_pct    REAL DEFAULT 0,
    symbols_used        TEXT NOT NULL DEFAULT '[]',
    started_at          TEXT,
    finished_at         TEXT,
    last_symbol         TEXT DEFAULT '',
    notes               TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS challenge_trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES challenge_runs(id),
    direction       TEXT NOT NULL,
    entry_price     REAL NOT NULL,
    exit_price      REAL NOT NULL,
    quantity        INTEGER NOT NULL,
    entry_time      TEXT,
    exit_time       TEXT,
    entry_bar_index INTEGER,
    exit_bar_index  INTEGER,
    exit_reason     TEXT,
    pnl             REAL,
    pnl_pct         REAL,
    hold_bars       INTEGER,
    commission      REAL DEFAULT 0,
    symbol          TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_challenge_trades_run ON challenge_trades(run_id);
CREATE INDEX IF NOT EXISTS idx_challenge_runs_target ON challenge_runs(target_amount);

CREATE TABLE IF NOT EXISTS discipline_violations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(id),
    created_at      TEXT NOT NULL,
    violation_type  TEXT NOT NULL,
    severity        TEXT NOT NULL DEFAULT 'warning',
    details         TEXT DEFAULT '',
    bar_index       INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_violations_session ON discipline_violations(session_id);
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
    "ALTER TABLE trades ADD COLUMN snapshot_path TEXT DEFAULT ''",
    "ALTER TABLE trades ADD COLUMN position_id TEXT DEFAULT ''",
    "ALTER TABLE trades ADD COLUMN equity_before REAL DEFAULT 0",
    "ALTER TABLE trades ADD COLUMN equity_after REAL DEFAULT 0",
    "ALTER TABLE sessions ADD COLUMN training_goal TEXT DEFAULT ''",
]


def _drop_stale_challenge_tables(conn: sqlite3.Connection) -> None:
    """若 challenge_trades 已存在但缺少 run_id（旧/半建表），先删掉再让 _SCHEMA 重建。"""
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='challenge_trades'"
        ).fetchone()
        if not row:
            return
        cols = conn.execute("PRAGMA table_info(challenge_trades)").fetchall()
        names = {c[1] for c in cols}
        if "run_id" not in names:
            conn.execute("DROP TABLE IF EXISTS challenge_trades")
            conn.execute("DROP TABLE IF EXISTS challenge_runs")
            conn.commit()
    except sqlite3.OperationalError:
        pass


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else _DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    _drop_stale_challenge_tables(conn)
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
