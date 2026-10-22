"""Features deterministas calculadas sobre velas diarias.

Regla del plan (fase 3): misma entrada -> misma señal. Nada aquí consulta red,
reloj ni aleatoriedad: `as_of` y `now` se inyectan desde fuera.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from statistics import mean, pstdev

from src.schemas.market import Candle


class InsufficientData(ValueError):
    """No hay velas suficientes para calcular features con sentido."""


@dataclass(frozen=True)
class FeatureSet:
    asset: str
    as_of: datetime
    close: float
    ret_1d: float
    ret_5d: float
    vol_20d: float
    volume_relative: float
    sma_fast: float
    sma_slow: float
    trend: float
    momentum_10d: float
    distance_to_sma: float
    data_age_minutes: float
    observations: int

    def as_dict(self) -> dict[str, float | str | datetime]:
        return {
            "asset": self.asset,
            "as_of": self.as_of,
            "close": self.close,
            "ret_1d": self.ret_1d,
            "ret_5d": self.ret_5d,
            "vol_20d": self.vol_20d,
            "volume_relative": self.volume_relative,
            "sma_fast": self.sma_fast,
            "sma_slow": self.sma_slow,
            "trend": self.trend,
            "momentum_10d": self.momentum_10d,
            "distance_to_sma": self.distance_to_sma,
            "data_age_minutes": self.data_age_minutes,
            "observations": self.observations,
        }


def _returns(closes: list[float]) -> list[float]:
    return [
        (closes[i] / closes[i - 1]) - 1.0
        for i in range(1, len(closes))
        if closes[i - 1] > 0
    ]


def _safe_div(a: float, b: float, default: float = 1.0) -> float:
    return a / b if b else default


def compute_features(
    candles: list[Candle],
    *,
    now: datetime,
    fast: int = 10,
    slow: int = 30,
    vol_window: int = 20,
) -> FeatureSet:
    """Calcula el set de features sobre velas ordenadas cronológicamente."""
    if not candles:
        raise InsufficientData("no hay velas")
    if len(candles) < slow + 1:
        raise InsufficientData(f"se requieren al menos {slow + 1} velas, hay {len(candles)}")

    ordered = sorted(candles, key=lambda c: c.ts)
    last = ordered[-1]
    if len({c.asset for c in ordered}) > 1:
        raise ValueError("las velas mezclan activos distintos")

    closes = [c.close for c in ordered]
    volumes = [c.volume for c in ordered]
    rets = _returns(closes)

    window = rets[-vol_window:] if len(rets) >= vol_window else rets
    vol = pstdev(window) if len(window) > 1 else 0.0

    sma_fast = mean(closes[-fast:])
    sma_slow = mean(closes[-slow:])

    ret_1d = closes[-1] / closes[-2] - 1.0
    ret_5d = closes[-1] / closes[-6] - 1.0 if len(closes) > 6 else 0.0
    momentum_10d = closes[-1] / closes[-11] - 1.0 if len(closes) > 11 else ret_5d

    vol_baseline = mean(volumes[-20:]) if len(volumes) >= 20 else mean(volumes)
    volume_relative = _safe_div(volumes[-1], vol_baseline, 0.0)

    age_minutes = max(0.0, (now - last.ts).total_seconds() / 60.0)

    return FeatureSet(
        asset=last.asset,
        as_of=last.ts,
        close=closes[-1],
        ret_1d=ret_1d,
        ret_5d=ret_5d,
        vol_20d=vol,
        volume_relative=volume_relative,
        sma_fast=sma_fast,
        sma_slow=sma_slow,
        trend=_safe_div(sma_fast, sma_slow, 1.0) - 1.0,
        momentum_10d=momentum_10d,
        distance_to_sma=closes[-1] / sma_slow - 1.0,
        data_age_minutes=age_minutes,
        observations=len(ordered),
    )


def expected_abs_move(features: FeatureSet, horizon_days: float = 1.0) -> float:
    """Movimiento absoluto esperado, con un suelo para evitar divisiones por cero."""
    return max(features.vol_20d * math.sqrt(horizon_days), 0.005)
