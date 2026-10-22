"""Paper trading: contabilidad explícita, sin dinero real."""

from __future__ import annotations

import pytest

from src.paper_trading.engine import PaperTradingEngine


def test_open_and_close_with_costs() -> None:
    engine = PaperTradingEngine(capital=10_000, cost_rate=0.002)
    engine.open(asset="BTC", price=100.0, size_pct=1.0)
    trade = engine.close("BTC", price=110.0)

    assert trade.gross_return == pytest.approx(0.10)
    assert trade.fees == pytest.approx(0.2)
    assert trade.net_return == pytest.approx(0.098)
    assert engine.capital == pytest.approx(10_009.8)


def test_loss_updates_streak_and_drawdown() -> None:
    engine = PaperTradingEngine(capital=10_000, cost_rate=0.002)
    engine.open(asset="BTC", price=100.0, size_pct=10.0)
    engine.close("BTC", price=90.0)
    assert engine.consecutive_losses == 1
    assert engine.capital < 10_000
    assert engine.drawdown_pct > 0


def test_win_resets_loss_streak() -> None:
    engine = PaperTradingEngine(capital=10_000, cost_rate=0.002)
    engine.open(asset="BTC", price=100.0, size_pct=5.0)
    engine.close("BTC", price=90.0)
    engine.open(asset="BTC", price=100.0, size_pct=5.0)
    engine.close("BTC", price=120.0)
    assert engine.consecutive_losses == 0


def test_cannot_open_twice_same_asset() -> None:
    engine = PaperTradingEngine()
    engine.open(asset="BTC", price=100.0, size_pct=1.0)
    with pytest.raises(ValueError):
        engine.open(asset="BTC", price=101.0, size_pct=1.0)


def test_invalid_inputs_rejected() -> None:
    engine = PaperTradingEngine()
    with pytest.raises(ValueError):
        engine.open(asset="BTC", price=100.0, size_pct=0)
    with pytest.raises(ValueError):
        engine.open(asset="BTC", price=0, size_pct=1.0)
    with pytest.raises(KeyError):
        engine.close("ETH", price=100.0)


def test_metrics() -> None:
    engine = PaperTradingEngine(capital=10_000, cost_rate=0.0)
    engine.open(asset="BTC", price=100.0, size_pct=1.0)
    engine.close("BTC", price=110.0)
    engine.open(asset="BTC", price=100.0, size_pct=1.0)
    engine.close("BTC", price=95.0)
    metrics = engine.metrics()
    assert metrics["trades"] == 2
    assert metrics["win_rate"] == pytest.approx(0.5)


def test_portfolio_state_exposure() -> None:
    engine = PaperTradingEngine()
    engine.open(asset="BTC", price=100.0, size_pct=2.5)
    state = engine.portfolio_state()
    assert state.exposure_by_asset_pct == {"BTC": 2.5}
