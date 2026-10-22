"""Persistencia SQLite (MVP). Migrar a PostgreSQL cuando pase a 24/7.

Principio del plan: todo dato y toda señal quedan registrados con su timestamp,
versión de modelo y versión de código, para poder auditar después.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from src.schemas.market import Candle, DataQualityReport
from src.schemas.signal import SignalContract

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    symbol TEXT PRIMARY KEY,
    name   TEXT,
    kind   TEXT NOT NULL DEFAULT 'crypto',
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS market_candles (
    asset  TEXT NOT NULL,
    ts     TEXT NOT NULL,
    open   REAL NOT NULL,
    high   REAL NOT NULL,
    low    REAL NOT NULL,
    close  REAL NOT NULL,
    volume REAL NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (asset, ts)
);

CREATE TABLE IF NOT EXISTS raw_provider_responses (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    provider   TEXT NOT NULL,
    endpoint   TEXT NOT NULL,
    asset      TEXT,
    fetched_at TEXT NOT NULL,
    payload    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS data_quality_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    asset      TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    quality    TEXT NOT NULL,
    report     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    asset            TEXT NOT NULL,
    horizon          TEXT NOT NULL,
    timestamp        TEXT NOT NULL,
    data_timestamp   TEXT NOT NULL,
    signal           TEXT NOT NULL,
    probability_net  REAL NOT NULL,
    expected_ret_net REAL NOT NULL,
    confidence       TEXT NOT NULL,
    rank_score       REAL NOT NULL,
    rank_position    INTEGER NOT NULL,
    is_actionable    INTEGER NOT NULL,
    data_quality     TEXT NOT NULL,
    model_version    TEXT NOT NULL,
    code_version     TEXT NOT NULL,
    payload          TEXT NOT NULL,
    UNIQUE (asset, horizon, timestamp, model_version)
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    asset         TEXT NOT NULL,
    signal_id     INTEGER,
    opened_at     TEXT NOT NULL,
    closed_at     TEXT,
    entry_price   REAL NOT NULL,
    exit_price    REAL,
    size_pct      REAL NOT NULL,
    gross_return  REAL,
    net_return    REAL,
    fees          REAL,
    status        TEXT NOT NULL DEFAULT 'open'
);

CREATE TABLE IF NOT EXISTS performance_snapshots (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    taken_at   TEXT NOT NULL,
    equity     REAL NOT NULL,
    drawdown   REAL NOT NULL,
    trades     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    actor      TEXT NOT NULL,
    action     TEXT NOT NULL,
    detail     TEXT NOT NULL
);
"""


class Store:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    # ----------------------------- escritura -----------------------------
    def save_candles(self, candles: list[Candle], source: str) -> int:
        with self.connect() as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO market_candles
                   (asset, ts, open, high, low, close, volume, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (c.asset, c.ts.isoformat(), c.open, c.high, c.low, c.close, c.volume, source)
                    for c in candles
                ],
            )
        return len(candles)

    def save_quality(self, report: DataQualityReport) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO data_quality_events (asset, checked_at, quality, report)
                   VALUES (?, ?, ?, ?)""",
                (
                    report.asset,
                    datetime.now(UTC).isoformat(),
                    report.quality,
                    report.model_dump_json(),
                ),
            )

    def save_signal(self, signal: SignalContract) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO signals
                   (asset, horizon, timestamp, data_timestamp, signal, probability_net,
                    expected_ret_net, confidence, rank_score, rank_position, is_actionable,
                    data_quality, model_version, code_version, payload)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    signal.asset,
                    signal.horizon,
                    signal.timestamp.isoformat(),
                    signal.data_timestamp.isoformat(),
                    signal.signal.value,
                    signal.probability_net_positive,
                    signal.expected_return_net,
                    signal.confidence.value,
                    signal.rank_score,
                    signal.rank_position,
                    int(signal.is_actionable),
                    signal.data_quality.value,
                    signal.model_version,
                    signal.code_version,
                    signal.model_dump_json(),
                ),
            )
            return int(cur.lastrowid or 0)

    def audit(self, actor: str, action: str, detail: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO audit_events (at, actor, action, detail) VALUES (?, ?, ?, ?)",
                (datetime.now(UTC).isoformat(), actor, action, detail),
            )

    # ------------------------------ lectura ------------------------------
    def load_candles(self, asset: str, limit: int = 200) -> list[Candle]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT asset, ts, open, high, low, close, volume
                   FROM market_candles WHERE asset = ?
                   ORDER BY ts DESC LIMIT ?""",
                (asset.upper(), limit),
            ).fetchall()
        return [
            Candle(
                asset=r["asset"],
                ts=datetime.fromisoformat(r["ts"]),
                open=r["open"],
                high=r["high"],
                low=r["low"],
                close=r["close"],
                volume=r["volume"],
            )
            for r in reversed(rows)
        ]

    def latest_signals(self, limit: int = 20) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT asset, horizon, timestamp, signal, probability_net,
                          expected_ret_net, confidence, rank_score, rank_position,
                          is_actionable, data_quality
                   FROM signals ORDER BY timestamp DESC, rank_position ASC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
