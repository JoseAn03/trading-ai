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


# Sets versionados: cada experimento declara exactamente qué columnas usa el modelo.
# v0.1 = línea base original (5 features de corto plazo).
# v0.2 = añade régimen de largo plazo: tendencia 50/200, distancia a SMA200,
#        ratio de volatilidad 20/60 y drawdown desde el máximo de 60 días.
FEATURE_SETS: dict[str, tuple[str, ...]] = {
    "v0.1": ("trend", "momentum_10d", "distance_to_sma", "volume_relative", "vol_20d"),
    "v0.2": (
        "trend",
        "momentum_10d",
        "distance_to_sma",
        "volume_relative",
        "vol_20d",
        "trend_long",
        "distance_to_sma200",
        "vol_ratio",
        "drawdown_60d",
    ),
    # v0.3 = régimen + reversión/estiraje de corto plazo. Todo trailing; no usa
    # el retorno futuro. Se declara antes de mirar el holdout.
    "v0.3": (
        "trend",
        "momentum_10d",
        "distance_to_sma",
        "vol_ratio",
        "drawdown_60d",
        "rsi_14",
        "ret_20d",
        "range_20d",
        "close_location_20d",
        "vol_5d",
        "vol_ratio_5_20",
        "volume_trend",
    ),
}

# Historia mínima para que cada set tenga sentido (200 días + 1 para el retorno).
REQUIRED_HISTORY: dict[str, int] = {"v0.1": 31, "v0.2": 201, "v0.3": 201}


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
    vol_60d: float
    vol_ratio: float
    sma_long: float
    trend_long: float
    distance_to_sma200: float
    drawdown_60d: float
    rsi_14: float
    ret_20d: float
    range_20d: float
    close_location_20d: float
    vol_5d: float
    vol_ratio_5_20: float
    volume_trend: float
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
            "vol_60d": self.vol_60d,
            "vol_ratio": self.vol_ratio,
            "sma_long": self.sma_long,
            "trend_long": self.trend_long,
            "distance_to_sma200": self.distance_to_sma200,
            "drawdown_60d": self.drawdown_60d,
            "rsi_14": self.rsi_14,
            "ret_20d": self.ret_20d,
            "range_20d": self.range_20d,
            "close_location_20d": self.close_location_20d,
            "vol_5d": self.vol_5d,
            "vol_ratio_5_20": self.vol_ratio_5_20,
            "volume_trend": self.volume_trend,
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


def _rsi(closes: list[float], period: int = 14) -> float:
    """RSI simple sobre los últimos `period` deltas, sin mirar el futuro."""
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(len(closes) - period, len(closes))]
    gains = [max(delta, 0.0) for delta in deltas]
    losses = [max(-delta, 0.0) for delta in deltas]
    avg_gain = mean(gains)
    avg_loss = mean(losses)
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    relative_strength = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + relative_strength)


def compute_features(
    candles: list[Candle],
    *,
    now: datetime,
    fast: int = 10,
    slow: int = 30,
    vol_window: int = 20,
    long_window: int = 200,
    vol_long_window: int = 60,
) -> FeatureSet:
    """Calcula el set de features sobre velas ordenadas cronológicamente.

    Todas las ventanas miran hacia atrás: nunca se usa una vela posterior a `now`.
    Las features de largo plazo se calculan con la historia disponible; quien las
    use en un modelo debe exigir `REQUIRED_HISTORY`.
    """
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

    window_short = rets[-5:] if len(rets) >= 5 else rets
    vol_5 = pstdev(window_short) if len(window_short) > 1 else 0.0

    window_long = rets[-vol_long_window:] if len(rets) >= vol_long_window else rets
    vol_60 = pstdev(window_long) if len(window_long) > 1 else 0.0

    sma_fast = mean(closes[-fast:])
    sma_slow = mean(closes[-slow:])
    sma_medium = mean(closes[-50:]) if len(closes) >= 50 else mean(closes)
    sma_long = mean(closes[-long_window:]) if len(closes) >= long_window else mean(closes)

    ret_1d = closes[-1] / closes[-2] - 1.0
    ret_5d = closes[-1] / closes[-6] - 1.0 if len(closes) > 6 else 0.0
    momentum_10d = closes[-1] / closes[-11] - 1.0 if len(closes) > 11 else ret_5d

    vol_baseline = mean(volumes[-20:]) if len(volumes) >= 20 else mean(volumes)
    volume_relative = _safe_div(volumes[-1], vol_baseline, 0.0)

    peak_60 = max(closes[-vol_long_window:])
    drawdown_60d = closes[-1] / peak_60 - 1.0 if peak_60 > 0 else 0.0

    recent = ordered[-20:]
    high_20 = max(c.high for c in recent)
    low_20 = min(c.low for c in recent)
    range_20d = mean((c.high - c.low) / c.close for c in recent if c.close > 0)
    close_location_20d = (
        (closes[-1] - low_20) / (high_20 - low_20) if high_20 > low_20 else 0.5
    )
    ret_20d = closes[-1] / closes[-21] - 1.0 if len(closes) > 21 else ret_5d
    volume_trend = _safe_div(mean(volumes[-5:]), mean(volumes[-20:]), 1.0) - 1.0

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
        vol_60d=vol_60,
        vol_ratio=_safe_div(vol, vol_60, 1.0) if vol_60 > 0 else 1.0,
        sma_long=sma_long,
        trend_long=_safe_div(sma_medium, sma_long, 1.0) - 1.0,
        distance_to_sma200=closes[-1] / sma_long - 1.0 if sma_long > 0 else 0.0,
        drawdown_60d=drawdown_60d,
        rsi_14=_rsi(closes),
        ret_20d=ret_20d,
        range_20d=range_20d,
        close_location_20d=close_location_20d,
        vol_5d=vol_5,
        vol_ratio_5_20=_safe_div(vol_5, vol, 1.0) if vol > 0 else 1.0,
        volume_trend=volume_trend,
        data_age_minutes=age_minutes,
        observations=len(ordered),
    )


def expected_abs_move(features: FeatureSet, horizon_days: float = 1.0) -> float:
    """Movimiento absoluto esperado, con un suelo para evitar divisiones por cero."""
    return max(features.vol_20d * math.sqrt(horizon_days), 0.005)
