"""Features deterministas: mismo input, mismo output."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.quant.features import InsufficientData, compute_features, expected_abs_move
from tests.conftest import make_candles

NOW = datetime.now(UTC)


def test_insufficient_data_raises() -> None:
    with pytest.raises(InsufficientData):
        compute_features(make_candles(10), now=NOW)


def test_empty_series_raises() -> None:
    with pytest.raises(InsufficientData):
        compute_features([], now=NOW)


def test_features_are_deterministic() -> None:
    candles = make_candles(120, daily_return=0.005, volatility=0.01)
    a = compute_features(candles, now=NOW)
    b = compute_features(candles, now=NOW)
    assert a == b


def test_uptrend_produces_positive_trend() -> None:
    candles = make_candles(120, daily_return=0.01, volatility=0.0)
    f = compute_features(candles, now=candles[-1].ts)
    assert f.trend > 0
    assert f.momentum_10d > 0
    assert f.distance_to_sma > 0
    assert f.vol_20d == pytest.approx(0.0, abs=1e-9)


def test_volatility_is_positive_on_choppy_series() -> None:
    candles = make_candles(120, daily_return=0.0, volatility=0.03)
    f = compute_features(candles, now=candles[-1].ts)
    assert f.vol_20d > 0.01


def test_data_age_is_measured_from_last_candle() -> None:
    candles = make_candles(60)
    f = compute_features(candles, now=candles[-1].ts)
    assert f.data_age_minutes == pytest.approx(0.0)


def test_expected_abs_move_has_floor() -> None:
    candles = make_candles(120, daily_return=0.01, volatility=0.0)
    f = compute_features(candles, now=candles[-1].ts)
    assert expected_abs_move(f) >= 0.005


def test_mixed_assets_rejected() -> None:
    candles = make_candles(60) + make_candles(5, asset="ETH")
    with pytest.raises(ValueError):
        compute_features(sorted(candles, key=lambda c: c.ts), now=NOW)
