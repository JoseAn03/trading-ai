"""Métricas de backtest: calibración y retorno neto después de costos.

Dos preguntas distintas:
1. ¿Las probabilidades están calibradas? (¿un 60 % acierta ~60 %?)
2. ¿La estrategia gana dinero después de costos y con muestra suficiente?
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from statistics import mean, stdev


@dataclass(frozen=True)
class CalibrationBin:
    lower: float
    upper: float
    count: int
    mean_pred: float
    observed: float


@dataclass(frozen=True)
class StrategyStats:
    trades: int
    total_net_return: float
    avg_net_per_trade: float
    win_rate: float
    profit_factor: float
    max_drawdown: float
    final_equity: float
    sharpe: float = 0.0


def calibration_table(
    probs: list[float], labels: list[int], bins: int = 5
) -> list[CalibrationBin]:
    if len(probs) != len(labels):
        raise ValueError("probs y labels deben tener la misma longitud")
    if not probs:
        return []

    groups: list[list[tuple[float, int]]] = [[] for _ in range(bins)]
    for p, y in zip(probs, labels, strict=True):
        idx = min(int(p * bins), bins - 1)
        groups[idx].append((p, y))

    table: list[CalibrationBin] = []
    for i, group in enumerate(groups):
        lower, upper = i / bins, (i + 1) / bins
        if not group:
            table.append(CalibrationBin(lower, upper, 0, math.nan, math.nan))
            continue
        mean_pred = sum(p for p, _ in group) / len(group)
        observed = sum(y for _, y in group) / len(group)
        table.append(CalibrationBin(lower, upper, len(group), mean_pred, observed))
    return table


def expected_calibration_error(
    probs: list[float], labels: list[int], bins: int = 10
) -> float:
    """ECE ponderado por número de muestras por bin."""
    if not probs:
        return math.nan
    total = len(probs)
    ece = 0.0
    for b in calibration_table(probs, labels, bins=bins):
        if b.count:
            ece += (b.count / total) * abs(b.observed - b.mean_pred)
    return ece


def brier_score(probs: list[float], labels: list[int]) -> float:
    return sum((p - y) ** 2 for p, y in zip(probs, labels, strict=True)) / len(labels)


def log_loss(probs: list[float], labels: list[int]) -> float:
    eps = 1e-12
    return -sum(
        y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps))
        for p, y in zip(probs, labels, strict=True)
    ) / len(labels)


def sharpe_ratio(returns: list[float], *, periods_per_year: float = 365.0) -> float:
    """Sharpe sin tasa libre de riesgo y con desvío muestral.

    `returns` son los retornos netos de las operaciones seleccionadas. Para
    horizontes de h días se pasa `periods_per_year=365/h`; así no se anualiza
    como si hubiera una operación diaria cuando no la hay.
    """
    if len(returns) < 2:
        return 0.0
    dispersion = stdev(returns)
    if dispersion <= 0:
        return 0.0
    return mean(returns) / dispersion * math.sqrt(periods_per_year)


def monthly_consistency(
    dated_returns: list[tuple[datetime, float]],
) -> dict[str, float]:
    """Retornos mensuales compuestos y fracción de meses positivos.

    `dated_returns` usa la fecha de cierre de cada operación. Es una medida de
    consistencia, no de significancia: con pocos meses no prueba nada.
    """
    buckets: dict[str, list[float]] = {}
    for stamp, value in dated_returns:
        buckets.setdefault(f"{stamp.year:04d}-{stamp.month:02d}", []).append(value)

    monthly: dict[str, float] = {}
    for key, values in sorted(buckets.items()):
        equity = 1.0
        for value in values:
            equity *= 1 + value
        monthly[key] = equity - 1.0

    positive = sum(1 for value in monthly.values() if value > 0)
    return {
        "months": float(len(monthly)),
        "positive_months": float(positive),
        "positive_ratio": positive / len(monthly) if monthly else 0.0,
        "worst_month": min(monthly.values()) if monthly else 0.0,
        "best_month": max(monthly.values()) if monthly else 0.0,
    }


def strategy_stats(
    probs: list[float],
    forward_returns: list[float],
    *,
    threshold: float = 0.55,
    cost_rate: float = 0.002,
    starting_equity: float = 1.0,
    periods_per_year: float = 365.0,
) -> StrategyStats:
    """Opera largo solo cuando p >= threshold; aplica costo ida y vuelta por trade."""
    if len(probs) != len(forward_returns):
        raise ValueError("probs y retornos deben tener la misma longitud")

    equity = starting_equity
    peak = starting_equity
    max_dd = 0.0
    trades = 0
    wins = 0
    gross_win = 0.0
    gross_loss = 0.0
    total_net = 0.0
    net_returns: list[float] = []

    for p, r in zip(probs, forward_returns, strict=True):
        if p < threshold:
            continue
        net = r - cost_rate
        equity *= 1 + net
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak if peak > 0 else 0.0)

        trades += 1
        total_net += net
        net_returns.append(net)
        if net > 0:
            wins += 1
            gross_win += net
        else:
            gross_loss -= net

    return StrategyStats(
        trades=trades,
        total_net_return=equity - starting_equity,
        avg_net_per_trade=total_net / trades if trades else 0.0,
        win_rate=wins / trades if trades else 0.0,
        profit_factor=gross_win / gross_loss if gross_loss > 0 else math.inf,
        max_drawdown=max_dd,
        final_equity=equity,
        sharpe=sharpe_ratio(net_returns, periods_per_year=periods_per_year),
    )


def buy_and_hold(returns: list[float]) -> float:
    equity = 1.0
    for r in returns:
        equity *= 1 + r
    return equity - 1.0


def equity_curve(
    probs: list[float],
    forward_returns: list[float],
    *,
    threshold: float = 0.55,
    cost_rate: float = 0.002,
    starting_equity: float = 1.0,
) -> list[float]:
    """Curva de capital compuesta; solo opera cuando p >= threshold.

    Devuelve un punto por operacion mas el inicial. Es la misma regla que usa
    `strategy_stats`, expuesta aparte para poder dibujarla en el panel.
    """
    if len(probs) != len(forward_returns):
        raise ValueError("probs y retornos deben tener la misma longitud")

    equity = starting_equity
    curve = [equity]
    for p, r in zip(probs, forward_returns, strict=True):
        if p < threshold:
            continue
        equity *= 1 + (r - cost_rate)
        curve.append(equity)
    return curve
