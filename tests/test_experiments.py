"""Fase 2: split temporal, reglas sin lookahead y gate explícito."""

from __future__ import annotations

import pytest

from backtests.experiments import (
    CANDIDATES,
    _gate,
    _holdout_predictions,
    rule_metrics,
    rule_weights,
    split_series,
)
from tests.conftest import make_candles


def test_split_keeps_last_twenty_percent_for_holdout() -> None:
    candles = make_candles(800)
    dev, holdout, split = split_series(candles, 0.8)
    assert split == 640
    assert len(dev) == 640
    assert len(holdout) == 160
    assert dev[-1].ts < holdout[0].ts
    assert holdout[-1].ts == candles[-1].ts


def test_buy_hold_rule_matches_buy_and_hold_after_cost() -> None:
    candles = make_candles(80, daily_return=0.001, volatility=0.0)
    candidate = next(c for c in CANDIDATES if c.name == "rule_buy_hold")
    metrics = rule_metrics(candles, rule_weights(candles, candidate), cost_rate=0.002)
    expected = 1.0
    for i in range(len(candles) - 1):
        daily = candles[i + 1].close / candles[i].close - 1
        if i == 0:
            daily -= 0.002
        expected *= 1 + daily
    assert metrics["net_return"] == pytest.approx(expected - 1)
    assert metrics["buy_and_hold"] == pytest.approx(candles[-1].close / candles[0].close - 1)


def test_gate_requires_all_criteria() -> None:
    good = {
        "log_loss": 0.680,
        "log_loss_baseline": 0.6931,
        "ece": 0.03,
        "sharpe": 0.8,
        "net_return": 0.10,
        "trades": 40.0,
        "max_drawdown": 0.08,
        "positive_months": 0.7,
        "assets_positive": 2.0,
        "assets_total": 2.0,
    }
    assert all(_gate(good, previous_best_log_loss=0.690).values())
    bad = replace_dict(good, log_loss=0.690)
    assert _gate(bad, previous_best_log_loss=0.690)["log_loss_margin"] is False


def replace_dict(source: dict, **updates):
    result = dict(source)
    result.update(updates)
    return result


def test_holdout_predictions_ignore_future_holdout_prices() -> None:
    candles = make_candles(900, daily_return=0.001, volatility=0.01)
    dev, holdout, split = split_series(candles, 0.8)
    candidate = next(c for c in CANDIDATES if c.name == "logit_v03_h1_t60")

    original = _holdout_predictions(dev, candles, split, candidate)

    # Cambiar precios futuros del holdout no debe alterar la primera predicción:
    # sus features solo miran el pasado y el modelo se entrenó con desarrollo.
    mutated = list(candles)
    for i in range(split + 1, len(mutated)):
        candle = mutated[i]
        mutated[i] = candle.model_copy(update={"close": candle.close * 1.5})
    changed = _holdout_predictions(dev, mutated, split, candidate)

    assert original[0].prob == pytest.approx(changed[0].prob)
    assert all(p.ts >= holdout[0].ts for p in original)
