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
    status        TEXT NOT NULL DEFAULT 'open',
    horizon_days  INTEGER NOT NULL DEFAULT 1,
    due_at        TEXT,
    cost_rate     REAL NOT NULL DEFAULT 0.002,
    model_version TEXT NOT NULL DEFAULT 'unknown'
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_paper_signal
    ON paper_trades(signal_id) WHERE signal_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_paper_open_asset
    ON paper_trades(asset) WHERE status = 'open';
CREATE UNIQUE INDEX IF NOT EXISTS ux_paper_asset_entry
    ON paper_trades(asset, opened_at);

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
            # Migracion de bases creadas antes de la fase 5 (paper trading persistente).
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(paper_trades)")}
            additions = {
                "horizon_days": "INTEGER NOT NULL DEFAULT 1",
                "due_at": "TEXT",
                "cost_rate": "REAL NOT NULL DEFAULT 0.002",
                "model_version": "TEXT NOT NULL DEFAULT 'unknown'",
            }
            for name, ddl in additions.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE paper_trades ADD COLUMN {name} {ddl}")

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
            if cur.lastrowid:
                return int(cur.lastrowid)
            # INSERT OR IGNORE: la señal ya existía; devolver su id para no duplicar paper trades.
            row = conn.execute(
                """SELECT id FROM signals
                   WHERE asset = ? AND horizon = ? AND timestamp = ? AND model_version = ?""",
                (
                    signal.asset,
                    signal.horizon,
                    signal.timestamp.isoformat(),
                    signal.model_version,
                ),
            ).fetchone()
            return int(row["id"]) if row else 0

    def save_paper_trade(
        self,
        *,
        asset: str,
        signal_id: int,
        opened_at: datetime,
        entry_price: float,
        size_pct: float,
        horizon_days: int,
        due_at: datetime,
        cost_rate: float,
        model_version: str,
    ) -> int | None:
        """Abre una posición simulada. Devuelve None si ya existía (idempotente)."""
        try:
            with self.connect() as conn:
                cur = conn.execute(
                    """INSERT INTO paper_trades
                       (asset, signal_id, opened_at, entry_price, size_pct, status,
                        horizon_days, due_at, cost_rate, model_version)
                       VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)""",
                    (
                        asset,
                        signal_id,
                        opened_at.isoformat(),
                        entry_price,
                        size_pct,
                        horizon_days,
                        due_at.isoformat(),
                        cost_rate,
                        model_version,
                    ),
                )
                return int(cur.lastrowid or 0) or None
        except sqlite3.IntegrityError:
            return None

    def open_paper_trades(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM paper_trades WHERE status = 'open' ORDER BY opened_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def close_paper_trade(
        self,
        trade_id: int,
        *,
        exit_price: float,
        closed_at: datetime,
        gross_return: float,
        net_return: float,
        fees: float,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """UPDATE paper_trades
                   SET status = 'closed', exit_price = ?, closed_at = ?,
                       gross_return = ?, net_return = ?, fees = ?
                   WHERE id = ? AND status = 'open'""",
                (exit_price, closed_at.isoformat(), gross_return, net_return, fees, trade_id),
            )

    def paper_trades(self, status: str | None = None, limit: int = 500) -> list[dict]:
        query = "SELECT * FROM paper_trades"
        params: list[object] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY COALESCE(closed_at, opened_at) DESC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def save_performance_snapshot(self, *, equity: float, drawdown: float, trades: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO performance_snapshots (taken_at, equity, drawdown, trades)
                   VALUES (?, ?, ?, ?)""",
                (datetime.now(UTC).isoformat(), equity, drawdown, trades),
            )

    def latest_performance_snapshot(self) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM performance_snapshots ORDER BY taken_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def audit(self, actor: str, action: str, detail: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO audit_events (at, actor, action, detail) VALUES (?, ?, ?, ?)",
                (datetime.now(UTC).isoformat(), actor, action, detail),
            )

    # ------------------------------ lectura ------------------------------
    def load_candles(self, asset: str, limit: int = 200, source: str | None = None) -> list[Candle]:
        query = """SELECT asset, ts, open, high, low, close, volume
                   FROM market_candles WHERE asset = ?"""
        params: list[object] = [asset.upper()]
        if source:
            query += " AND source = ?"
            params.append(source)
        query += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)

        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
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
