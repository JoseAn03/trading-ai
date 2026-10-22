"""El contrato de señal no debe aceptar combinaciones incoherentes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from src.schemas.enums import Confidence, DataQuality, Signal
from src.schemas.signal import Costs, RiskMetrics, SignalBatch, SignalContract
from tests.conftest import make_signal

NOW = datetime.now(UTC)


def test_valid_signal_roundtrip() -> None:
    signal = make_signal()
    restored = SignalContract.model_validate_json(signal.model_dump_json())
    assert restored.asset == "BTC"
    assert restored.signal is Signal.LONG_BIAS
    assert restored.costs.total_rate == pytest.approx(0.002 + 0.0002)


def test_probabilities_must_sum_to_one() -> None:
    with pytest.raises(ValidationError):
        SignalContract(
            asset="BTC",
            horizon="24h",
            timestamp=NOW,
            data_timestamp=NOW,
            signal=Signal.LONG_BIAS,
            probability_up=0.7,
            probability_down=0.7,
            probability_net_positive=0.6,
            expected_return_net=0.01,
            confidence=Confidence.MEDIUM,
            rank_score=0.5,
            rank_position=1,
            costs=Costs(estimated_slippage_bps=4, commission_bps=6),
            risk=RiskMetrics(suggested_size_pct=1.0, max_drawdown_estimate=0.02, correlation_with_portfolio=0.1),
            data_quality=DataQuality.VALID,
            model_version="test",
            code_version="test",
            is_actionable=True,
        )


def test_no_trade_cannot_be_actionable() -> None:
    with pytest.raises(ValidationError):
        make_signal(signal=Signal.NO_TRADE, actionable=True)


def test_data_timestamp_cannot_be_in_the_future() -> None:
    with pytest.raises(ValidationError):
        make_signal(age_minutes=-5)


def test_actionable_requires_positive_edge() -> None:
    with pytest.raises(ValidationError):
        make_signal(p_net=0.45, actionable=True)


def test_batch_ranking_summary() -> None:
    a = make_signal(asset="BTC", p_net=0.6)
    b = make_signal(asset="ETH", signal=Signal.NO_TRADE, p_net=0.5, actionable=False, size=0.0)
    batch = SignalBatch(
        horizon="24h",
        signals=[a, b],
        model_version="test",
        code_version="test",
    )
    assert len(batch.actionable) == 1
    assert batch.summarise()["NO_TRADE"] == 1


def test_signal_age() -> None:
    signal = make_signal(age_minutes=12, ts=NOW)
    assert signal.age_minutes == pytest.approx(12.0)
    assert signal.timestamp - timedelta(minutes=12) == signal.data_timestamp
