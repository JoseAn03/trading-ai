"""Motor de señal: determinismo, NO_TRADE y ranking."""

from __future__ import annotations

from datetime import timedelta

from src.quant.features import compute_features
from src.quant.signals import build_signal, estimate_probability_up, rank_signals
from src.schemas.enums import DataQuality, Signal
from tests.conftest import make_candles, make_quality, make_signal


def _signal_from(candles, *, settings, quality=None, now=None):
    stamp = now or candles[-1].ts + timedelta(minutes=1)
    features = compute_features(candles, now=stamp)
    return build_signal(
        features,
        quality or make_quality(),
        settings=settings,
        code_version="test",
        now=stamp,
    )


def test_same_input_same_signal(settings) -> None:
    candles = make_candles(120, daily_return=0.01, volatility=0.0)
    a = _signal_from(candles, settings=settings)
    b = _signal_from(candles, settings=settings)
    assert a.model_dump() == b.model_dump()


def test_strong_uptrend_is_long_bias_and_actionable(settings) -> None:
    candles = make_candles(120, daily_return=0.01, volatility=0.0)
    signal = _signal_from(candles, settings=settings)
    assert signal.signal is Signal.LONG_BIAS
    assert signal.is_actionable is True
    assert signal.probability_net_positive >= settings.min_probability_net_positive
    assert signal.risk.suggested_size_pct > 0
    assert signal.expected_return_net > 0


def test_probability_is_bounded(settings) -> None:
    for vol in (0.0, 0.02, 0.08):
        candles = make_candles(120, daily_return=0.003, volatility=vol)
        features = compute_features(candles, now=candles[-1].ts)
        p = estimate_probability_up(features)
        assert 0.0 < p < 1.0


def test_stale_data_forces_no_trade(settings) -> None:
    candles = make_candles(120, daily_return=0.01, volatility=0.0)
    now = candles[-1].ts + timedelta(days=2)
    quality = make_quality(quality=DataQuality.INVALID, fresh=False, age_minutes=2880)
    signal = _signal_from(candles, settings=settings, quality=quality, now=now)
    assert signal.signal is Signal.NO_TRADE
    assert signal.is_actionable is False
    assert signal.risk.suggested_size_pct == 0.0
    assert signal.rank_score == 0.0
    assert signal.data_quality is DataQuality.INVALID


def test_degraded_quality_blocks_actionable(settings) -> None:
    candles = make_candles(120, daily_return=0.01, volatility=0.0)
    quality = make_quality(quality=DataQuality.DEGRADED, fresh=True)
    signal = _signal_from(candles, settings=settings, quality=quality)
    assert signal.is_actionable is False
    assert signal.signal is Signal.NO_TRADE


def test_rank_signals_orders_and_renumbers() -> None:
    a = make_signal(asset="BTC", p_net=0.60, rank_score=0.70)
    b = make_signal(asset="ETH", p_net=0.70, rank_score=0.90)
    c = make_signal(
        asset="SOL", signal=Signal.NO_TRADE, p_net=0.50, actionable=False, size=0.0, rank_score=0.0
    )
    ranked = rank_signals([a, c, b])
    assert [s.asset for s in ranked] == ["ETH", "BTC", "SOL"]
    assert [s.rank_position for s in ranked] == [1, 2, 3]
    assert ranked[0].rank_score >= ranked[1].rank_score


def test_high_correlation_lowers_rank(settings) -> None:
    candles = make_candles(120, daily_return=0.01, volatility=0.0)
    stamp = candles[-1].ts + timedelta(minutes=1)
    features = compute_features(candles, now=stamp)
    low = build_signal(features, make_quality(), settings=settings, code_version="t", correlation=0.0, now=stamp)
    high = build_signal(features, make_quality(), settings=settings, code_version="t", correlation=0.9, now=stamp)
    assert high.rank_score < low.rank_score


def test_novolatilty_series_still_produces_signal(settings) -> None:
    candles = [c for c in make_candles(90, daily_return=0.0, volatility=0.0)]
    signal = _signal_from(candles, settings=settings)
    assert signal.signal in {Signal.NO_TRADE, Signal.RISK_OFF, Signal.LONG_BIAS}
    assert 0.0 <= signal.probability_net_positive <= 1.0
