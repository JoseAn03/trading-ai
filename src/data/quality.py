"""Calidad de datos: frescura, gaps y outliers.

Un dato que no pasa estos controles no genera señal accionable. Es una regla del
plan, no una preferencia: el sistema prefiere NO_TRADE a operar con basura.
"""

from __future__ import annotations

import math
from datetime import datetime
from statistics import median

from src.schemas.enums import DataQuality
from src.schemas.market import Candle, DataQualityReport

# Un outlier solo veta el presente si está en las últimas velas. La referencia
# es robusta (mediana/MAD) para que un evento viejo no contamine la escala.
OUTLIER_RECENT_WINDOW = 5
OUTLIER_LOOKBACK = 60
OUTLIER_ROBUST_Z = 6.0
OUTLIER_MIN_ABS_MOVE = 0.03


def data_age_tolerance_minutes(
    max_age_minutes: float, expected_interval_hours: float
) -> float:
    """Tolerancia de frescura, compartida por calidad y circuit breakers.

    Para velas diarias, 20 minutos serían absurdos: el cierre siempre tiene al
    menos unas horas. Se permite el mayor valor entre el límite configurado y
    1.5 intervalos de vela. Un intervalo <= 0 desactiva la extensión (tests y
    datos con cadencia arbitraria).
    """
    interval_allowance = max(0.0, expected_interval_hours) * 60 * 1.5
    return max(float(max_age_minutes), interval_allowance)


def _returns(candles: list[Candle]) -> list[float]:
    return [
        candles[i].close / candles[i - 1].close - 1
        for i in range(1, len(candles))
        if candles[i - 1].close > 0
    ]


def _recent_outliers(ordered: list[Candle]) -> list[str]:
    """Detecta anomalías solo en las últimas velas, contra una base robusta.

    La media/desviación global castigaba para siempre un movimiento viejo ya
    absorbido por el precio. Aquí la base es mediana/MAD de los retornos previos
    y la inspección se limita a las últimas `OUTLIER_RECENT_WINDOW` velas.
    """
    rets = _returns(ordered)
    if len(rets) < 10:
        return []

    outliers: list[str] = []
    first = max(0, len(rets) - OUTLIER_RECENT_WINDOW)
    for i in range(first, len(rets)):
        baseline = rets[max(0, i - OUTLIER_LOOKBACK) : i]
        if len(baseline) < 10:
            continue
        center = median(baseline)
        mad = median(abs(r - center) for r in baseline) * 1.4826
        deviation = abs(rets[i] - center)
        if deviation > OUTLIER_ROBUST_Z * max(mad, 1e-9) and deviation >= OUTLIER_MIN_ABS_MOVE:
            outliers.append(f"retorno anómalo {rets[i] * 100:+.1f}% en {ordered[i + 1].ts.date()}")
    return outliers


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
    tolerance = data_age_tolerance_minutes(max_age_minutes, expected_interval_hours)
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
        outliers = _recent_outliers(ordered)

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
