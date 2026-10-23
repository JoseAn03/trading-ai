"""Motor de señal determinista (baseline v0.1, score heurístico).

No hay LLM, ni red, ni aleatoriedad. La misma `FeatureSet` produce siempre la
misma señal, lo que permite testearla y auditarla.

El score sale de un logit con pesos fijos a mano; **no es una probabilidad
calibrada**. La regresión entrenada solo se evalúa en backtests. La UI debe
etiquetarlo como score heurístico hasta que un modelo calibrado se conecte en la
Fase 2.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from src.config.settings import Settings
from src.quant.features import FeatureSet, expected_abs_move
from src.schemas.enums import Confidence, DataQuality, Signal
from src.schemas.market import DataQualityReport
from src.schemas.signal import Costs, RiskMetrics, SignalContract

MODEL_VERSION = "baseline-v0.1"

# Pesos del score logit. Ajustables y versionados junto al modelo.
WEIGHTS = {
    "trend": 6.0,
    "momentum": 4.0,
    "distance": 2.0,
    "volume": 0.6,
    "volatility": -1.5,
}

CONFIDENCE_ORDER = {Confidence.LOW: 0, Confidence.MEDIUM: 1, Confidence.HIGH: 2}


def _logistic(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def raw_score(f: FeatureSet) -> float:
    """Score adimensional antes de la logística."""
    return (
        WEIGHTS["trend"] * f.trend
        + WEIGHTS["momentum"] * f.momentum_10d
        + WEIGHTS["distance"] * f.distance_to_sma
        + WEIGHTS["volume"] * (f.volume_relative - 1.0)
        + WEIGHTS["volatility"] * f.vol_20d
    )


def estimate_probability_up(f: FeatureSet) -> float:
    """Probabilidad de retorno bruto positivo, en (0, 1)."""
    p = _logistic(raw_score(f))
    return min(max(p, 0.01), 0.99)


def _confidence(f: FeatureSet, quality: DataQualityReport) -> Confidence:
    if quality.quality != DataQuality.VALID or not quality.is_fresh:
        return Confidence.LOW
    if f.observations >= 90 and f.vol_20d < 0.05 and f.volume_relative >= 0.8:
        return Confidence.HIGH
    if f.observations >= 60 and f.vol_20d < 0.09:
        return Confidence.MEDIUM
    return Confidence.LOW


def _rank_score(
    probability_net_positive: float,
    confidence: Confidence,
    f: FeatureSet,
    correlation: float,
) -> float:
    vol_penalty = min(f.vol_20d / 0.15, 1.0) * 0.3
    corr_penalty = max(0.0, correlation) * 0.3
    score = probability_net_positive * confidence.weight * (1 - vol_penalty) * (1 - corr_penalty)
    return round(min(max(score, 0.0), 1.0), 4)


def _reason_list(f: FeatureSet) -> list[str]:
    reasons: list[str] = []
    if f.trend > 0:
        reasons.append(f"Tendencia alcista (SMA rápida {f.trend * 100:+.2f}% sobre lenta)")
    elif f.trend < 0:
        reasons.append(f"Tendencia bajista (SMA rápida {f.trend * 100:+.2f}% bajo lenta)")
    if f.momentum_10d > 0.02:
        reasons.append(f"Momentum 10d positivo ({f.momentum_10d * 100:+.1f}%)")
    if f.volume_relative > 1.2:
        reasons.append(f"Volumen relativo {f.volume_relative:.2f}x sobre su media")
    if abs(f.distance_to_sma) > 0.03:
        side = "por encima" if f.distance_to_sma > 0 else "por debajo"
        reasons.append(f"Precio {side} de la SMA lenta ({f.distance_to_sma * 100:+.1f}%)")
    return reasons[:4] or ["Sin señales destacadas en las features actuales"]


def _invalidators(f: FeatureSet, p_up: float) -> list[str]:
    out: list[str] = []
    if f.sma_slow:
        out.append(f"Cierre por debajo de {f.sma_slow * 0.98:.2f} (2% bajo SMA lenta)")
    out.append(f"Volatilidad 20d por encima de {max(f.vol_20d * 1.8, 0.05) * 100:.1f}%")
    if p_up > 0.5:
        out.append("Gap de volumen > 15 min sin explicación")
    return out[:3]


def build_signal(
    features: FeatureSet,
    quality: DataQualityReport,
    *,
    settings: Settings,
    code_version: str = "dev",
    correlation: float = 0.0,
    now: datetime | None = None,
) -> SignalContract:
    """Construye una señal que cumple el contrato. Determinista."""
    stamp = now or datetime.now(UTC)
    p_up = estimate_probability_up(features)
    p_down = 1.0 - p_up

    costs = Costs(
        estimated_slippage_bps=settings.slippage_bps,
        commission_bps=settings.commission_bps,
        funding_impact=settings.funding_impact,
    )

    move = expected_abs_move(features)
    cost_drag = min(0.5, costs.total_rate / (2 * move))
    p_net = round(min(max(p_up - cost_drag, 0.0), 1.0), 4)

    gross = move * (p_up - p_down)
    net = gross - costs.total_rate
    confidence = _confidence(features, quality)

    reasons = _reason_list(features)
    invalidators = _invalidators(features, p_up)

    is_valid = quality.quality == DataQuality.VALID and quality.is_fresh
    try:
        min_conf = Confidence(str(settings.min_confidence).lower())
    except ValueError:
        min_conf = Confidence.MEDIUM
    enough_confidence = CONFIDENCE_ORDER[confidence] >= CONFIDENCE_ORDER[min_conf]
    min_drawdown = round(min(max(features.vol_20d * 2.0, 0.01), 0.2), 4)

    if not is_valid:
        return SignalContract(
            asset=features.asset,
            horizon=settings.horizon,
            timestamp=stamp,
            data_timestamp=features.as_of,
            signal=Signal.NO_TRADE,
            probability_up=p_up,
            probability_down=p_down,
            probability_net_positive=p_net,
            expected_return_net=round(net, 6),
            confidence=Confidence.LOW,
            rank_score=0.0,
            rank_position=1,
            reasons=["Datos no válidos o desactualizados"],
            invalidators=list(quality.issues) or ["Calidad de datos insuficiente"],
            costs=costs,
            risk=RiskMetrics(
                suggested_size_pct=0.0,
                max_drawdown_estimate=min_drawdown,
                correlation_with_portfolio=correlation,
            ),
            data_quality=DataQuality(quality.quality),
            model_version=MODEL_VERSION,
            code_version=code_version,
            is_actionable=False,
        )

    actionable = (
        p_net >= settings.min_probability_net_positive
        and net > 0
        and enough_confidence
        and features.volume_relative >= 0.5
    )

    if actionable:
        sig = Signal.LONG_BIAS
    elif p_up <= 0.45 and net < 0:
        sig = Signal.RISK_OFF
    else:
        sig = Signal.NO_TRADE

    size = 0.0
    if actionable:
        # Tamaño proporcional a la ventaja, acotado por el límite de riesgo.
        edge = max(0.0, p_net - settings.min_probability_net_positive)
        size = round(min(1.5, max(0.25, edge * 20)), 2)

    rank_position = 1  # lo reasigna el ranking del batch

    return SignalContract(
        asset=features.asset,
        horizon=settings.horizon,
        timestamp=stamp,
        data_timestamp=features.as_of,
        signal=sig,
        probability_up=round(p_up, 4),
        probability_down=round(p_down, 4),
        probability_net_positive=p_net,
        expected_return_net=round(net, 6),
        confidence=confidence,
        rank_score=_rank_score(p_net, confidence, features, correlation),
        rank_position=rank_position,
        reasons=reasons,
        invalidators=invalidators,
        costs=costs,
        risk=RiskMetrics(
            suggested_size_pct=size,
            max_drawdown_estimate=min_drawdown,
            correlation_with_portfolio=correlation,
        ),
        data_quality=DataQuality(quality.quality),
        model_version=MODEL_VERSION,
        code_version=code_version,
        is_actionable=actionable,
    )


def rank_signals(signals: list[SignalContract]) -> list[SignalContract]:
    """Ordena por accionabilidad y rank_score, y renumera `rank_position`."""
    ordered = sorted(signals, key=lambda s: (s.is_actionable, s.rank_score), reverse=True)
    return [s.model_copy(update={"rank_position": i + 1}) for i, s in enumerate(ordered)]
