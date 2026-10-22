"""Fixtures y fábricas de objetos de prueba."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.config.settings import Settings
from src.schemas.enums import Confidence, DataQuality, Signal
from src.schemas.market import Candle, DataQualityReport
from src.schemas.signal import Costs, RiskMetrics, SignalContract


def make_candles(
    n: int = 120,
    *,
    start_price: float = 100.0,
    daily_return: float = 0.01,
    volatility: float = 0.0,
    asset: str = "BTC",
    end: datetime | None = None,
    volume: float = 1_000.0,
) -> list[Candle]:
    """Serie sintética reproducible. `volatility` alterna el signo del retorno."""
    stamp = end or datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    price = start_price
    candles: list[Candle] = []
    for i in range(n):
        sign = 1 if i % 2 == 0 else -1
        r = daily_return + sign * volatility
        price = max(1.0, price * (1 + r))
        ts = stamp - timedelta(days=n - 1 - i)
        candles.append(
            Candle(
                asset=asset,
                ts=ts,
                open=price * 0.999,
                high=price * 1.002,
                low=price * 0.998,
                close=price,
                volume=volume * (1 + (i % 4) * 0.05),
            )
        )
    return candles


def make_quality(
    *,
    asset: str = "BTC",
    quality: DataQuality = DataQuality.VALID,
    fresh: bool = True,
    age_minutes: float = 1.0,
    candles: int = 120,
) -> DataQualityReport:
    return DataQualityReport(
        asset=asset,
        quality=quality.value,
        is_fresh=fresh,
        age_minutes=age_minutes,
        candles=candles,
        issues=[] if quality is DataQuality.VALID else ["dato degradado"],
    )


def make_signal(
    *,
    asset: str = "BTC",
    signal: Signal = Signal.LONG_BIAS,
    p_up: float = 0.61,
    p_net: float = 0.57,
    actionable: bool = True,
    age_minutes: int = 1,
    size: float = 1.0,
    correlation: float = 0.1,
    rank_score: float = 0.7,
    ts: datetime | None = None,
) -> SignalContract:
    stamp = ts or datetime.now(UTC)
    return SignalContract(
        asset=asset,
        horizon="24h",
        timestamp=stamp,
        data_timestamp=stamp - timedelta(minutes=age_minutes),
        signal=signal,
        probability_up=p_up,
        probability_down=1 - p_up,
        probability_net_positive=p_net,
        expected_return_net=0.0085,
        confidence=Confidence.MEDIUM,
        rank_score=rank_score,
        rank_position=1,
        reasons=["tendencia alcista"],
        invalidators=["cierre bajo media"],
        costs=Costs(estimated_slippage_bps=4, commission_bps=6, funding_impact=0.0002),
        risk=RiskMetrics(
            suggested_size_pct=size,
            max_drawdown_estimate=0.03,
            correlation_with_portfolio=correlation,
        ),
        data_quality=DataQuality.VALID,
        model_version="test-v1",
        code_version="test-sha",
        is_actionable=actionable,
    )


@pytest.fixture
def settings() -> Settings:
    return Settings(demo_mode=True)


@pytest.fixture
def trending_candles() -> list[Candle]:
    return make_candles(120, daily_return=0.01, volatility=0.0)


@pytest.fixture
def volatile_candles() -> list[Candle]:
    return make_candles(120, daily_return=0.0, volatility=0.03)
