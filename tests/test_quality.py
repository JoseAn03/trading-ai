"""Calidad de datos: frescura, gaps y outliers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.data.quality import data_age_tolerance_minutes, validate_series
from src.schemas.market import Candle
from tests.conftest import make_candles


def test_age_tolerance_is_shared_and_scales_with_interval() -> None:
    assert data_age_tolerance_minutes(20, 24.0) == 2160
    assert data_age_tolerance_minutes(20, 1.0) == 90
    assert data_age_tolerance_minutes(20, 0) == 20
    assert data_age_tolerance_minutes(120, 24.0) == 2160


def test_fresh_complete_series_is_valid() -> None:
    candles = make_candles(60)
    report = validate_series(candles, now=candles[-1].ts, min_candles=30)
    assert report.quality == "valid"
    assert report.is_fresh is True
    assert report.candles == 60


def test_stale_series_is_invalid() -> None:
    candles = make_candles(60)
    now = candles[-1].ts + timedelta(hours=40)
    report = validate_series(candles, now=now, min_candles=30)
    assert report.quality == "invalid"
    assert report.is_fresh is False
    assert any("antigüedad" in i for i in report.issues)


def test_short_history_is_invalid() -> None:
    candles = make_candles(10)
    report = validate_series(candles, now=candles[-1].ts, min_candles=30)
    assert report.quality == "invalid"
    assert any("insuficiente" in i for i in report.issues)


def test_gap_is_reported() -> None:
    candles = make_candles(60)
    with_gap = candles[:30] + candles[33:]
    report = validate_series(with_gap, now=with_gap[-1].ts, min_candles=30)
    assert report.gaps
    assert report.quality == "degraded"


def test_outlier_is_reported() -> None:
    candles = make_candles(60, daily_return=0.0, volatility=0.001)
    spiked = list(candles)
    last = spiked[-1]
    spiked[-1] = Candle(
        asset=last.asset,
        ts=last.ts,
        open=last.open,
        high=last.close * 3,
        low=last.low,
        close=last.close * 3,
        volume=last.volume,
    )
    report = validate_series(spiked, now=spiked[-1].ts, min_candles=30)
    assert report.outliers
    assert report.quality == "degraded"


def test_old_outlier_does_not_degrade_recent_series() -> None:
    candles = make_candles(80, daily_return=0.0, volatility=0.001)
    spiked = list(candles)
    old = spiked[20]
    spiked[20] = Candle(
        asset=old.asset,
        ts=old.ts,
        open=old.open,
        high=old.close * 1.5,
        low=old.low,
        close=old.close * 1.5,
        volume=old.volume,
    )
    report = validate_series(spiked, now=spiked[-1].ts, min_candles=30)
    assert report.outliers == []
    assert report.quality == "valid"


def test_recent_outlier_still_degrades_series() -> None:
    candles = make_candles(60, daily_return=0.0, volatility=0.001)
    spiked = list(candles)
    last = spiked[-1]
    spiked[-1] = last.model_copy(update={"close": last.close * 1.2, "high": last.close * 1.2})
    report = validate_series(spiked, now=spiked[-1].ts, min_candles=30)
    assert report.outliers
    assert report.quality == "degraded"


def test_empty_series_invalid() -> None:
    report = validate_series([], now=datetime.now(UTC))
    assert report.quality == "invalid"
    assert report.candles == 0
