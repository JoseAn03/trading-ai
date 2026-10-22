"""Calibración y métricas de estrategia: matemática explícita y testeable."""

from __future__ import annotations

import math

import pytest

from backtests.metrics import (
    brier_score,
    buy_and_hold,
    calibration_table,
    expected_calibration_error,
    log_loss,
    strategy_stats,
)


def test_calibration_perfect_when_prediction_matches_frequency() -> None:
    probs = [0.5] * 100
    labels = [1] * 50 + [0] * 50
    assert expected_calibration_error(probs, labels, bins=10) == pytest.approx(0.0)


def test_calibration_detects_overconfidence() -> None:
    probs = [0.9] * 100
    labels = [0] * 100
    ece = expected_calibration_error(probs, labels, bins=10)
    assert ece == pytest.approx(0.9, abs=0.01)


def test_calibration_table_bins() -> None:
    table = calibration_table([0.1, 0.3, 0.7, 0.9], [0, 0, 1, 1], bins=5)
    assert len(table) == 5
    assert sum(b.count for b in table) == 4
    assert all(0 <= b.lower < b.upper <= 1 for b in table)


def test_brier_and_log_loss_reference_values() -> None:
    probs = [0.9, 0.1]
    labels = [1, 0]
    assert brier_score(probs, labels) == pytest.approx(0.01)
    assert log_loss(probs, labels) < 0.12


def test_strategy_compounds_only_when_above_threshold() -> None:
    probs = [0.6, 0.5, 0.7]
    returns = [0.01, -0.02, 0.03]
    stats = strategy_stats(probs, returns, threshold=0.55, cost_rate=0.002)
    assert stats.trades == 2
    assert stats.total_net_return == pytest.approx((1.008 * 1.028) - 1, rel=1e-9)
    assert stats.win_rate == pytest.approx(1.0)
    assert stats.profit_factor == math.inf
    assert stats.max_drawdown == pytest.approx(0.0)


def test_strategy_counts_losses_and_drawdown() -> None:
    probs = [0.9, 0.9]
    returns = [0.05, -0.10]
    stats = strategy_stats(probs, returns, threshold=0.55, cost_rate=0.002)
    assert stats.trades == 2
    assert stats.win_rate == pytest.approx(0.5)
    assert stats.max_drawdown > 0.09


def test_strategy_never_trades_below_threshold() -> None:
    stats = strategy_stats([0.5, 0.54], [0.05, 0.05], threshold=0.55)
    assert stats.trades == 0
    assert stats.total_net_return == 0.0
    assert stats.avg_net_per_trade == 0.0


def test_buy_and_hold_compounds() -> None:
    assert buy_and_hold([0.1, -0.1]) == pytest.approx(0.99 - 1)
