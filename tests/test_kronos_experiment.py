"""KRONOS-01: mismos índices OOS que el baseline y RankIC sin lookahead."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backtests.experiments import rank_ic, split_series
from backtests.kronos_experiment import (
    MARKER_NAME,
    _dev_indices,
    _holdout_indices,
    _samples,
    run_holdout,
)
from backtests.walk_forward import Prediction
from src.models.kronos_adapter import KronosConfig
from tests.conftest import make_candles


def test_dev_indices_match_baseline_walk_forward_windows() -> None:
    candles = make_candles(1500, volatility=0.01)
    dev, holdout, split = split_series(candles)
    samples = _samples(dev)

    positions = _dev_indices(samples, dev)

    assert len(positions) == 600
    assert {window for window, _ in positions} == set(range(10))
    # Las fechas evaluadas son exactamente las de las ventanas 365/60/60.
    expected = {
        samples[365 + window * 60 + offset].ts
        for window in range(10)
        for offset in range(60)
    }
    observed = {dev[index].ts for _, index in positions}
    assert observed == expected
    assert all(ts <= dev[-1].ts for ts in observed)
    assert holdout[0].ts > dev[-1].ts


def test_holdout_indices_start_after_dev() -> None:
    candles = make_candles(1500, volatility=0.01)
    dev, holdout, split = split_series(candles)
    samples = _samples(candles)

    positions = _holdout_indices(samples, candles, split)

    assert len(positions) == 299
    assert min(candles[index].ts for _, index in positions) >= holdout[0].ts
    assert max(candles[index].ts for _, index in positions) <= candles[-2].ts


def _prediction(ts: datetime, asset: str, prob: float, ret: float) -> Prediction:
    return Prediction(
        window=0,
        asset=asset,
        ts=ts,
        prob=prob,
        label=1 if ret > 0 else 0,
        next_return=ret,
        horizon=1,
        feature_set="kronos",
    )


def test_rank_ic_is_positive_when_probability_orders_returns() -> None:
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    predictions = [
        _prediction(stamp, "BTC", 0.7, 0.02),
        _prediction(stamp, "ETH", 0.3, -0.01),
    ]
    assert rank_ic(predictions) == pytest.approx(1.0)


def test_rank_ic_ignores_dates_with_one_asset() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    predictions = [_prediction(start + timedelta(days=i), "BTC", 0.6, 0.01) for i in range(3)]
    assert rank_ic(predictions) != rank_ic(predictions)  # NaN


def test_holdout_marker_blocks_a_second_evaluation(tmp_path) -> None:
    (tmp_path / MARKER_NAME).write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit, match="Holdout ya consumido"):
        run_holdout({}, config=KronosConfig(), out_dir=tmp_path)
