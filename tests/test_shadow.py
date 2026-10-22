"""Paper trading persistente: entrada congelada, resolución al vencimiento y costos."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from src.data.store import Store
from src.paper_trading.shadow import (
    SignalToOpen,
    close_at,
    equity_points,
    horizon_to_days,
    open_from_signals,
    paper_stats,
    resolve_due,
)
from src.schemas.market import Candle

START = datetime(2026, 1, 1, tzinfo=UTC)


def _history(closes: list[float], start: datetime = START, asset: str = "BTC") -> list[Candle]:
    return [
        Candle(
            asset=asset,
            ts=start + timedelta(days=i),
            open=price,
            high=price,
            low=price,
            close=price,
            volume=1.0,
        )
        for i, price in enumerate(closes)
    ]


def _candidate(**overrides) -> SignalToOpen:
    base = {
        "signal_id": 1,
        "asset": "BTC",
        "horizon": "24h",
        "data_timestamp": START,
        "entry_price": 100.0,
        "size_pct": 10.0,
        "cost_rate": 0.002,
        "model_version": "test-v1",
    }
    return SignalToOpen(**{**base, **overrides})


@pytest.fixture
def store(tmp_path) -> Store:
    instance = Store(tmp_path / "paper.db")
    instance.init_schema()
    return instance


@pytest.mark.parametrize(
    ("horizon", "expected"),
    [("24h", 1), ("72h", 3), ("168h", 7), ("1d", 1), ("3d", 3), ("12h", 1), ("basura", 1)],
)
def test_horizon_to_days(horizon: str, expected: int) -> None:
    assert horizon_to_days(horizon) == expected


def test_close_at_uses_last_candle_not_after_timestamp() -> None:
    candles = _history([100.0, 110.0, 121.0])
    assert close_at(candles, START + timedelta(days=1)) == pytest.approx(110.0)
    assert close_at(candles, START - timedelta(days=1)) is None


def test_open_and_resolve_freezes_entry_and_applies_cost(store: Store) -> None:
    candles = _history([100.0, 110.0, 121.0])

    opened = open_from_signals(store, [_candidate()])
    assert len(opened) == 1
    row = store.open_paper_trades()[0]
    assert row["status"] == "open"
    assert datetime.fromisoformat(row["due_at"]) == START + timedelta(days=1)
    assert row["cost_rate"] == pytest.approx(0.002)

    closed = resolve_due(
        store, {"BTC": candles}, now=START + timedelta(days=1, hours=1)
    )
    assert len(closed) == 1
    assert closed[0]["exit_price"] == pytest.approx(110.0)
    assert closed[0]["gross_return"] == pytest.approx(0.10)
    assert closed[0]["net_return"] == pytest.approx(0.098)

    stats = paper_stats(store)
    assert stats.trades == 1
    assert stats.open_trades == 0
    assert stats.win_rate == 1.0
    assert stats.equity == pytest.approx(1 + 0.098 * 0.10)


def test_no_resolution_before_due_or_without_exit_candle(store: Store) -> None:
    open_from_signals(store, [_candidate()])
    assert resolve_due(store, {"BTC": _history([100.0])}, now=START + timedelta(hours=12)) == []
    # Vencida pero sin vela posterior al vencimiento: no se inventa un precio.
    assert resolve_due(store, {"BTC": _history([100.0])}, now=START + timedelta(days=2)) == []
    assert len(store.open_paper_trades()) == 1


def test_open_is_idempotent_and_one_position_per_asset(store: Store) -> None:
    assert len(open_from_signals(store, [_candidate()])) == 1
    # Misma señal: no duplica.
    assert open_from_signals(store, [_candidate()]) == []
    # Otra señal para el mismo activo con posición abierta: tampoco.
    assert open_from_signals(store, [_candidate(signal_id=2)]) == []
    # Señal inválida: se ignora.
    assert open_from_signals(store, [_candidate(signal_id=0)]) == []


def test_equity_points_start_at_one_and_grow_with_wins(store: Store) -> None:
    open_from_signals(store, [_candidate()])
    resolve_due(store, {"BTC": _history([100.0, 110.0])}, now=START + timedelta(days=1, hours=1))
    points = equity_points(store)
    assert points[0]["equity"] == pytest.approx(1.0)
    assert points[-1]["equity"] == pytest.approx(1 + 0.098 * 0.10)


def test_init_schema_migrates_old_paper_trades(tmp_path) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE paper_trades (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               asset TEXT NOT NULL,
               signal_id INTEGER,
               opened_at TEXT NOT NULL,
               closed_at TEXT,
               entry_price REAL NOT NULL,
               exit_price REAL,
               size_pct REAL NOT NULL,
               gross_return REAL,
               net_return REAL,
               fees REAL,
               status TEXT NOT NULL DEFAULT 'open'
           )"""
    )
    conn.commit()
    conn.close()

    Store(path).init_schema()

    conn = sqlite3.connect(path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(paper_trades)")}
    conn.close()
    assert {"horizon_days", "due_at", "cost_rate", "model_version"} <= columns
