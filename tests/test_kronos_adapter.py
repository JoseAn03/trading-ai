"""KRONOS-01: pines, contexto sin lookahead y señal derivada de trayectorias."""

from __future__ import annotations

import os

import numpy as np
import pytest

from src.models.kronos_adapter import (
    KRONOS_CODE_COMMIT,
    KRONOS_MODELS,
    KRONOS_TOKENIZER,
    KronosAdapter,
    KronosConfig,
)
from tests.conftest import make_candles


class RecordingSampler:
    """Sampler determinista que solo mira el contexto que recibe."""

    def __init__(self, sample_count: int = 30, up: int = 20) -> None:
        self.sample_count = sample_count
        self.up = up
        self.calls: list[list[list]] = []

    def __call__(self, contexts, horizon, seed):  # noqa: ANN001
        self.calls.append([list(context) for context in contexts])
        paths = np.zeros((len(contexts), self.sample_count, horizon, 6), dtype=float)
        for i, context in enumerate(contexts):
            close = context[-1].close
            for sample in range(self.sample_count):
                factor = 1.01 if sample < self.up else 0.99
                paths[i, sample, :, 3] = close * factor
        return paths


def test_kronos_pins_are_exact() -> None:
    assert KRONOS_CODE_COMMIT == "67b630e67f6a18c9e9be918d9b4337c960db1e9a"
    assert KRONOS_MODELS["kronos-small"]["revision"] == (
        "901c26c1332695a2a8f243eb2f37243a37bea320"
    )
    assert KRONOS_MODELS["kronos-base"]["revision"] == (
        "2b554741eca47781b64468546e77fef3e85130e6"
    )
    assert KRONOS_TOKENIZER["revision"] == (
        "0e0117387f39004a9016484a186a908917e22426"
    )


def test_context_is_capped_to_lookback_and_excludes_future() -> None:
    candles = make_candles(300, volatility=0.01)
    sampler = RecordingSampler()
    adapter = KronosAdapter(KronosConfig(lookback=64), path_sampler=sampler)

    prediction = adapter.predict_at(candles, 220, seed=123)

    assert prediction.ts == candles[220].ts
    assert len(sampler.calls) == 1
    context = sampler.calls[0][0]
    assert len(context) == 64
    assert context[-1].ts == candles[220].ts
    assert all(candle.ts <= candles[220].ts for candle in context)
    assert context[-1].close == candles[220].close


def test_mutating_future_prices_does_not_change_prediction_at_t() -> None:
    candles = make_candles(300, volatility=0.01)
    sampler = RecordingSampler()
    adapter = KronosAdapter(KronosConfig(lookback=64), path_sampler=sampler)
    index = 220

    original = adapter.predict_at(candles, index, seed=123)

    mutated = list(candles)
    for i in range(index + 1, len(mutated)):
        candle = mutated[i]
        mutated[i] = candle.model_copy(
            update={
                "open": candle.open * 3.0,
                "high": candle.high * 3.0,
                "low": candle.low * 3.0,
                "close": candle.close * 3.0,
                "volume": candle.volume * 10.0,
            }
        )
    changed = adapter.predict_at(mutated, index, seed=123)

    assert original.prob_up == pytest.approx(changed.prob_up)
    assert original.expected_return_median == pytest.approx(
        changed.expected_return_median
    )
    assert original.expected_return_mean == pytest.approx(changed.expected_return_mean)
    assert all(candle.ts <= candles[index].ts for candle in sampler.calls[-1][0])
    assert [c.close for c in sampler.calls[0][0]] == [
        c.close for c in sampler.calls[-1][0]
    ]


def test_probability_uses_jeffreys_smoothing_and_median_path() -> None:
    candles = make_candles(100, volatility=0.01)
    adapter = KronosAdapter(
        KronosConfig(lookback=32, sample_count=30), path_sampler=RecordingSampler(up=20)
    )

    prediction = adapter.predict_at(candles, 80, seed=7)

    assert prediction.up_count == 20
    assert prediction.prob_up == pytest.approx((20 + 0.5) / 31)
    assert prediction.expected_return_median == pytest.approx(0.01)
    assert prediction.expected_return_mean == pytest.approx(20 / 30 * 0.01 - 10 / 30 * 0.01)


def test_config_rejects_context_over_512() -> None:
    with pytest.raises(ValueError):
        KronosConfig(lookback=513)


@pytest.mark.skipif(
    os.getenv("KRONOS_INTEGRATION") != "1",
    reason="integración real desactivada; usar KRONOS_INTEGRATION=1",
)
def test_real_kronos_does_not_change_with_future_prices() -> None:
    """Test lento con pesos reales; el test rápido usa un sampler determinista."""
    candles = make_candles(170, volatility=0.01)
    config = KronosConfig(lookback=64, sample_count=8, threads=8)
    adapter = KronosAdapter(config)
    index = 150

    original = adapter.predict_at(candles, index, seed=99)
    mutated = list(candles)
    for i in range(index + 1, len(mutated)):
        candle = mutated[i]
        mutated[i] = candle.model_copy(
            update={
                "open": candle.open * 2.0,
                "high": candle.high * 2.0,
                "low": candle.low * 2.0,
                "close": candle.close * 2.0,
            }
        )
    changed = adapter.predict_at(mutated, index, seed=99)

    assert original.prob_up == pytest.approx(changed.prob_up)
    assert original.expected_return_median == pytest.approx(
        changed.expected_return_median
    )
