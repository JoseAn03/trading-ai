"""Reconstrucción del PortfolioState desde el paper trading persistente."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.data.store import Store
from src.paper_trading.state import portfolio_state_from_store

NOW = datetime(2026, 1, 7, 12, 0, tzinfo=UTC)  # miércoles


@pytest.fixture
def store(tmp_path) -> Store:
    instance = Store(tmp_path / "state.db")
    instance.init_schema()
    return instance


def _open(
    store: Store,
    *,
    signal_id: int,
    asset: str = "BTC",
    opened_at: datetime = NOW - timedelta(hours=2),
    entry_price: float = 100.0,
    size_pct: float = 2.5,
) -> int:
    trade_id = store.save_paper_trade(
        asset=asset,
        signal_id=signal_id,
        opened_at=opened_at,
        entry_price=entry_price,
        size_pct=size_pct,
        horizon_days=1,
        due_at=opened_at + timedelta(days=1),
        cost_rate=0.002,
        model_version="test-v1",
        signal_price=entry_price,
    )
    assert trade_id is not None
    return trade_id


def _closed(
    store: Store,
    *,
    signal_id: int,
    opened_at: datetime,
    closed_at: datetime,
    net_return: float,
    size_pct: float = 10.0,
    asset: str = "BTC",
) -> None:
    trade_id = _open(
        store,
        signal_id=signal_id,
        asset=asset,
        opened_at=opened_at,
        size_pct=size_pct,
    )
    store.close_paper_trade(
        trade_id,
        exit_price=100.0 * (1 + net_return),
        closed_at=closed_at,
        gross_return=net_return,
        net_return=net_return,
        fees=0.002,
    )


def test_empty_store_is_clean(store: Store) -> None:
    state = portfolio_state_from_store(store, now=NOW)
    assert state.equity == pytest.approx(10_000)
    assert state.peak_equity == pytest.approx(10_000)
    assert state.daily_pnl_pct == pytest.approx(0.0)
    assert state.weekly_pnl_pct == pytest.approx(0.0)
    assert state.trades_today == 0
    assert state.consecutive_losses == 0
    assert state.exposure_by_asset_pct == {}
    assert state.manual_kill_switch is False


def test_reconstructs_daily_loss_drawdown_and_exposure(store: Store) -> None:
    # Ganancia de la semana pasada: equity 1.005.
    _closed(
        store,
        signal_id=1,
        opened_at=NOW - timedelta(days=8),
        closed_at=NOW - timedelta(days=7),
        net_return=0.05,
        size_pct=10.0,
    )
    # Pérdida de hoy: -4% sobre el 100% del capital.
    _closed(
        store,
        signal_id=2,
        opened_at=NOW - timedelta(hours=2),
        closed_at=NOW - timedelta(hours=1),
        net_return=-0.04,
        size_pct=100.0,
    )
    _open(store, signal_id=3, asset="ETH", size_pct=1.5)

    state = portfolio_state_from_store(store, now=NOW)

    assert state.equity == pytest.approx(10_000 * 1.005 * 0.96)
    assert state.peak_equity == pytest.approx(10_000 * 1.005)
    assert state.daily_pnl_pct == pytest.approx(-4.0)
    assert state.weekly_pnl_pct == pytest.approx(-4.0)
    assert state.drawdown_pct == pytest.approx((1 - 0.96) * 100, rel=1e-6)
    assert state.trades_today == 2  # pérdida cerrada + ETH abierta, ambas hoy
    assert state.consecutive_losses == 1
    assert state.exposure_by_asset_pct == {"ETH": 1.5}


def test_negative_day_without_today_closes_has_zero_daily_pnl(store: Store) -> None:
    _closed(
        store,
        signal_id=1,
        opened_at=NOW - timedelta(days=2),
        closed_at=NOW - timedelta(days=1),
        net_return=-0.04,
        size_pct=100.0,
    )

    state = portfolio_state_from_store(store, now=NOW)

    assert state.daily_pnl_pct == pytest.approx(0.0)
    assert state.weekly_pnl_pct == pytest.approx(-4.0)
    assert state.consecutive_losses == 1


def test_control_flag_kill_switch(store: Store) -> None:
    assert store.control_flag("kill_switch") is False
    store.set_control_flag("kill_switch", True)
    assert store.control_flag("kill_switch") is True
    state = portfolio_state_from_store(store, now=NOW, kill_switch=True)
    assert state.manual_kill_switch is True
    store.set_control_flag("kill_switch", False)
    assert store.control_flag("kill_switch") is False
