"""Backtesting: sin fuga temporal, con costos y predicciones auditables."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backtests.run_backtest import evaluate
from backtests.walk_forward import (
    Prediction,
    build_samples,
    evaluate_with_costs,
    walk_forward,
    walk_forward_predictions,
)
from tests.conftest import make_candles


def test_samples_are_built_without_future_leak() -> None:
    candles = make_candles(200, daily_return=0.002, volatility=0.01)
    samples = build_samples(candles)
    assert len(samples) > 100
    # La etiqueta de la muestra i solo depende de i+1: la última vela no genera muestra.
    assert samples[-1].ts < candles[-1].ts


def test_sample_label_matches_next_return() -> None:
    candles = make_candles(120, daily_return=0.002, volatility=0.02)
    for sample in build_samples(candles):
        assert (sample.next_return > 0) == bool(sample.label)


def test_walk_forward_runs_and_reports() -> None:
    candles = make_candles(240, daily_return=0.002, volatility=0.01)
    result = walk_forward(candles, train=40, test=20, step=20)
    assert result.windows
    summary = result.summary()
    assert summary["windows"] >= 3
    assert 0 <= summary["mean_brier"] <= 1
    assert isinstance(summary["beats_baseline"], bool)


def test_predictions_are_out_of_sample_and_non_overlapping() -> None:
    candles = make_candles(300, daily_return=0.002, volatility=0.01)
    preds = walk_forward_predictions(candles, train=60, test=20, step=20)
    windows = {p.window for p in preds}
    assert len(preds) == len(windows) * 20
    for w in windows:
        group = [p for p in preds if p.window == w]
        assert group[-1].ts < candles[-1].ts
    # Los timestamps de test no se repiten entre ventanas (step == test).
    stamps = [p.ts for p in preds]
    assert len(stamps) == len(set(stamps))


def test_walk_forward_requires_enough_samples() -> None:
    candles = make_candles(40)
    with pytest.raises(ValueError):
        walk_forward(candles, train=60, test=20)


def test_predictions_are_probabilities() -> None:
    candles = make_candles(260, daily_return=0.0, volatility=0.02)
    preds = walk_forward_predictions(candles, train=60, test=20, step=20)
    assert preds
    assert all(0.0 <= p.prob <= 1.0 for p in preds)
    assert all(p.asset == "BTC" for p in preds)


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


def test_horizon_uses_forward_return_without_leak() -> None:
    candles = make_candles(120, daily_return=0.01, volatility=0.0)
    one_day = build_samples(candles, horizon=1)
    three_days = build_samples(candles, horizon=3)
    assert len(three_days) == len(one_day) - 2
    expected = candles[31 + 3].close / candles[31].close - 1
    assert three_days[0].next_return == pytest.approx(expected)


def test_horizon_rejects_zero() -> None:
    with pytest.raises(ValueError):
        build_samples(make_candles(60), horizon=0)


def test_feature_set_v02_uses_long_history_and_columns() -> None:
    assert build_samples(make_candles(150), feature_set="v0.2") == []
    samples = build_samples(make_candles(260, daily_return=0.002, volatility=0.01), feature_set="v0.2")
    assert samples
    assert len(samples[0].features) == 9
    assert set(samples[0].features) == {
        "trend",
        "momentum_10d",
        "distance_to_sma",
        "volume_relative",
        "vol_20d",
        "trend_long",
        "distance_to_sma200",
        "vol_ratio",
        "drawdown_60d",
    }


def test_unknown_feature_set_rejected() -> None:
    with pytest.raises(ValueError):
        build_samples(make_candles(260), feature_set="v9.9")


def test_evaluate_avoids_overlapping_bets() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    preds = [
        Prediction(
            window=0,
            asset="BTC",
            ts=base + timedelta(days=i),
            prob=0.9,
            label=1,
            next_return=0.01,
            horizon=3,
        )
        for i in range(6)
    ]
    daily = evaluate(preds, threshold=0.55, cost_rate=0.0, horizon=1)
    tri = evaluate(preds, threshold=0.55, cost_rate=0.0, horizon=3)
    assert daily["trades"] == 6
    assert tri["trades"] == 2
    # Las métricas de predicción usan todas las muestras en ambos casos.
    assert daily["log_loss"] == pytest.approx(tri["log_loss"])
