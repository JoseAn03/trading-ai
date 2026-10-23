"""Calibración y métricas de estrategia: matemática explícita y testeable."""

from __future__ import annotations

import math

import pytest

from backtests.metrics import (
    brier_score,
    buy_and_hold,
    calibration_table,
    equity_curve,
    expected_calibration_error,
    log_loss,
    monthly_consistency,
    sharpe_ratio,
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


def test_equity_curve_compounds_only_above_threshold() -> None:
    curve = equity_curve([0.6, 0.5, 0.7], [0.01, -0.02, 0.03], threshold=0.55, cost_rate=0.002)
    assert curve[0] == pytest.approx(1.0)
    assert len(curve) == 3
    assert curve[-1] == pytest.approx(1.008 * 1.028, rel=1e-9)


def test_equity_curve_without_trades_is_flat() -> None:
    assert equity_curve([0.1, 0.2], [0.5, -0.5], threshold=0.55) == [1.0]


def test_sharpe_is_zero_without_dispersion_or_sample() -> None:
    assert sharpe_ratio([0.01, 0.01, 0.01]) == 0.0
    assert sharpe_ratio([0.01]) == 0.0


def test_sharpe_positive_for_positive_mean() -> None:
    assert sharpe_ratio([0.01, 0.02, 0.015, 0.005], periods_per_year=252) > 0


def test_strategy_stats_reports_sharpe() -> None:
    stats = strategy_stats([0.9, 0.9, 0.9], [0.02, 0.01, 0.03], threshold=0.55)
    assert stats.sharpe > 0


def test_monthly_consistency_compounds_by_month() -> None:
    from datetime import UTC, datetime

    values = [
        (datetime(2026, 1, 5, tzinfo=UTC), 0.10),
        (datetime(2026, 1, 20, tzinfo=UTC), -0.10),
        (datetime(2026, 2, 3, tzinfo=UTC), 0.05),
    ]
    result = monthly_consistency(values)
    assert result["months"] == 2
    assert result["positive_months"] == 1
    assert result["positive_ratio"] == pytest.approx(0.5)
    assert result["worst_month"] == pytest.approx(-0.01)
