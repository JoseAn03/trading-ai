"""Backtesting: sin fuga temporal y con costos aplicados."""

from __future__ import annotations

import pytest

from backtests.walk_forward import build_samples, evaluate_with_costs, walk_forward
from tests.conftest import make_candles


def test_samples_are_built_without_future_leak() -> None:
    candles = make_candles(200, daily_return=0.002, volatility=0.01)
    samples = build_samples(candles)
    assert len(samples) > 100
    # La etiqueta de la muestra i solo depende de i+1: la última vela no genera muestra.
    assert samples[-1].ts < candles[-1].ts


def test_walk_forward_runs_and_reports() -> None:
    candles = make_candles(240, daily_return=0.002, volatility=0.01)
    result = walk_forward(candles, train=40, test=20, step=20)
    assert result.windows
    summary = result.summary()
    assert summary["windows"] >= 3
    assert 0 <= summary["mean_brier"] <= 1
    assert isinstance(summary["beats_baseline"], bool)


def test_walk_forward_requires_enough_samples() -> None:
    candles = make_candles(40)
    with pytest.raises(ValueError):
        walk_forward(candles, train=60, test=20)


def test_evaluate_with_costs() -> None:
    probs = [0.6, 0.5, 0.7]
    returns = [0.01, -0.02, 0.03]
    stats = evaluate_with_costs(probs, returns, cost_rate=0.002, threshold=0.55)
    assert stats["trades"] == 2
    assert stats["gross_return"] == pytest.approx(0.04)
    assert stats["fees"] == pytest.approx(0.004)
    assert stats["net_return"] == pytest.approx(0.036)


def test_no_trades_below_threshold() -> None:
    stats = evaluate_with_costs([0.5, 0.4], [0.01, -0.01], threshold=0.55)
    assert stats["trades"] == 0
    assert stats["net_return"] == 0.0
