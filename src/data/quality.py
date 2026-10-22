"""Calidad de datos: frescura, gaps y outliers.

Un dato que no pasa estos controles no genera señal accionable. Es una regla del
plan, no una preferencia: el sistema prefiere NO_TRADE a operar con basura.
"""

from __future__ import annotations

import math
from datetime import datetime
from statistics import mean, pstdev

from src.schemas.enums import DataQuality
from src.schemas.market import Candle, DataQualityReport


def _returns(candles: list[Candle]) -> list[float]:
    return [
        candles[i].close / candles[i - 1].close - 1
        for i in range(1, len(candles))
        if candles[i - 1].close > 0
    ]


def validate_series(
    candles: list[Candle],
    *,
    now: datetime,
    max_age_minutes: int = 20,
    expected_interval_hours: float = 24.0,
    min_candles: int = 30,
) -> DataQualityReport:
    asset = candles[-1].asset if candles else "UNKNOWN"
    if not candles:
        return DataQualityReport(
            asset=asset,
            quality=DataQuality.INVALID.value,
            is_fresh=False,
            age_minutes=math.inf,
            candles=0,
            issues=["serie vacía"],
        )

    ordered = sorted(candles, key=lambda c: c.ts)
    age_minutes = max(0.0, (now - ordered[-1].ts).total_seconds() / 60.0)
    # Tolerancia: la edad máxima configurada o 1.5 intervalos esperados.
    tolerance = max(float(max_age_minutes), expected_interval_hours * 60 * 1.5)
    is_fresh = age_minutes <= tolerance

    gaps: list[str] = []
    expected = expected_interval_hours * 3600
    for prev, cur in zip(ordered, ordered[1:], strict=False):
        delta = (cur.ts - prev.ts).total_seconds()
        if delta > expected * 1.5:
            gaps.append(f"gap de {delta / 3600:.1f}h en {cur.ts.date()}")

    outliers: list[str] = []
    rets = _returns(ordered)
    if len(rets) >= 10:
        sd = pstdev(rets) or 1e-9
        mu = mean(rets)
        for i, r in enumerate(rets, start=1):
            if abs(r - mu) > 6 * sd:
                outliers.append(f"retorno anómalo {r * 100:+.1f}% en {ordered[i].ts.date()}")

    issues: list[str] = []
    if len(ordered) < min_candles:
        issues.append(f"histórico insuficiente ({len(ordered)} < {min_candles})")
    if not is_fresh:
        issues.append(f"dato con {age_minutes:.0f} min de antigüedad")
    if gaps:
        issues.append(f"{len(gaps)} gap(s) en la serie")
    if outliers:
        issues.append(f"{len(outliers)} outlier(s) detectado(s)")

    if issues and (not is_fresh or len(ordered) < min_candles):
        quality = DataQuality.INVALID
    elif issues:
        quality = DataQuality.DEGRADED
    else:
        quality = DataQuality.VALID

    return DataQualityReport(
        asset=asset,
        quality=quality.value,
        is_fresh=is_fresh,
        age_minutes=round(age_minutes, 2),
        candles=len(ordered),
        gaps=gaps[:5],
        outliers=outliers[:5],
        issues=issues,
    )
