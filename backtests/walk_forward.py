"""Backtesting walk-forward sin fuga de información.

Reglas:
- El modelo se entrena solo con datos anteriores al tramo evaluado.
- La etiqueta de la muestra t usa el retorno t -> t+1 (nunca el futuro).
- Las ventanas de test no se solapan cuando `step == test`.
- Se miden métricas de predicción y, aparte, el retorno neto tras costos.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from backtests.metrics import brier_score, log_loss
from src.models.baseline import LogisticBaseline
from src.quant.features import compute_features
from src.schemas.market import Candle


@dataclass(frozen=True)
class Sample:
    features: dict[str, float]
    label: int
    next_return: float
    ts: datetime


@dataclass(frozen=True)
class Prediction:
    window: int
    asset: str
    ts: datetime
    prob: float
    label: int
    next_return: float


@dataclass
class WindowResult:
    start: datetime
    end: datetime
    n: int
    log_loss: float
    brier: float
    accuracy: float
    log_loss_baseline: float
    brier_baseline: float


@dataclass
class WalkForwardResult:
    windows: list[WindowResult] = field(default_factory=list)
    model_weights: dict[str, float] = field(default_factory=dict)

    @property
    def mean_log_loss(self) -> float:
        return sum(w.log_loss for w in self.windows) / len(self.windows) if self.windows else math.nan

    @property
    def mean_brier(self) -> float:
        return sum(w.brier for w in self.windows) / len(self.windows) if self.windows else math.nan

    @property
    def mean_accuracy(self) -> float:
        return (
            sum(w.accuracy for w in self.windows) / len(self.windows) if self.windows else math.nan
        )

    @property
    def beats_baseline(self) -> bool:
        if not self.windows:
            return False
        base = sum(w.log_loss_baseline for w in self.windows) / len(self.windows)
        return self.mean_log_loss < base

    def summary(self) -> dict[str, float | bool | int]:
        return {
            "windows": len(self.windows),
            "mean_log_loss": round(self.mean_log_loss, 4),
            "mean_brier": round(self.mean_brier, 4),
            "mean_accuracy": round(self.mean_accuracy, 4),
            "beats_baseline": self.beats_baseline,
        }


def build_samples(candles: list[Candle], *, min_history: int = 31) -> list[Sample]:
    """Features en t, etiqueta y retorno de t a t+1."""
    ordered = sorted(candles, key=lambda c: c.ts)
    samples: list[Sample] = []

    for i in range(min_history, len(ordered) - 1):
        window = ordered[: i + 1]
        try:
            f = compute_features(window, now=ordered[i].ts)
        except ValueError:
            continue

        current, nxt = ordered[i].close, ordered[i + 1].close
        samples.append(
            Sample(
                features={
                    "trend": f.trend,
                    "momentum_10d": f.momentum_10d,
                    "distance_to_sma": f.distance_to_sma,
                    "volume_relative": f.volume_relative,
                    "vol_20d": f.vol_20d,
                },
                label=1 if nxt > current else 0,
                next_return=nxt / current - 1 if current else 0.0,
                ts=ordered[i].ts,
            )
        )
    return samples


def walk_forward_predictions(
    candles: list[Candle], *, train: int = 365, test: int = 60, step: int = 60
) -> list[Prediction]:
    """Predicciones fuera de muestra. Cada una se generó antes de conocer su resultado."""
    samples = build_samples(candles)
    if len(samples) < train + test:
        raise ValueError(
            f"muestras insuficientes: {len(samples)} (se requieren {train + test}). "
            "Aumenta --days o reduce --train/--test."
        )

    predictions: list[Prediction] = []
    start = 0
    window_idx = 0
    asset = candles[-1].asset if candles else "UNKNOWN"

    while start + train + test <= len(samples):
        train_slice = samples[start : start + train]
        test_slice = samples[start + train : start + train + test]

        model = LogisticBaseline(epochs=500, learning_rate=0.15).fit(
            [s.features for s in train_slice], [s.label for s in train_slice]
        )
        probs = model.predict_proba([s.features for s in test_slice])

        for s, p in zip(test_slice, probs, strict=True):
            predictions.append(
                Prediction(
                    window=window_idx,
                    asset=asset,
                    ts=s.ts,
                    prob=min(max(float(p), 0.0), 1.0),
                    label=s.label,
                    next_return=s.next_return,
                )
            )

        window_idx += 1
        start += step

    return predictions


def walk_forward(
    candles: list[Candle], *, train: int = 60, test: int = 20, step: int = 20
) -> WalkForwardResult:
    predictions = walk_forward_predictions(candles, train=train, test=test, step=step)

    result = WalkForwardResult()
    by_window: dict[int, list[Prediction]] = {}
    for p in predictions:
        by_window.setdefault(p.window, []).append(p)

    for idx in sorted(by_window):
        group = by_window[idx]
        probs = [p.prob for p in group]
        labels = [p.label for p in group]
        base = [0.5] * len(labels)

        result.windows.append(
            WindowResult(
                start=group[0].ts,
                end=group[-1].ts,
                n=len(group),
                log_loss=log_loss(probs, labels),
                brier=brier_score(probs, labels),
                accuracy=sum((p >= 0.5) == bool(y) for p, y in zip(probs, labels, strict=True))
                / len(labels),
                log_loss_baseline=log_loss(base, labels),
                brier_baseline=brier_score(base, labels),
            )
        )

    return result


def evaluate_with_costs(
    probs: list[float], returns: list[float], *, cost_rate: float = 0.002, threshold: float = 0.55
) -> dict[str, float]:
    """Retorno neto simulado de operar solo cuando p >= threshold (sin componer)."""
    if len(probs) != len(returns):
        raise ValueError("probs y retornos deben tener la misma longitud")
    trades = 0
    gross = 0.0
    for p, r in zip(probs, returns, strict=True):
        if p >= threshold:
            trades += 1
            gross += r
    fees = trades * cost_rate
    net = gross - fees
    return {
        "trades": float(trades),
        "gross_return": gross,
        "fees": fees,
        "net_return": net,
        "avg_net_per_trade": net / trades if trades else 0.0,
    }


if __name__ == "__main__":
    import argparse

    from src.data.adapters.base import StaticAdapter

    parser = argparse.ArgumentParser(description="Backtest walk-forward (datos sintéticos)")
    parser.add_argument("--asset", default="BTC")
    parser.add_argument("--days", type=int, default=400)
    args = parser.parse_args()

    series = StaticAdapter(days=args.days).fetch_daily_candles(args.asset, days=args.days)
    outcome = walk_forward(series, train=60, test=20, step=20)

    print(
        "AVISO: datos sinteticos y deterministas. Este resultado NO es evidencia "
        "de ventaja; solo valida que el pipeline corre."
    )
    for w in outcome.windows:
        print(
            f"{w.start:%Y-%m-%d} -> {w.end:%Y-%m-%d} | n={w.n:2d} | "
            f"logloss {w.log_loss:.4f} (base {w.log_loss_baseline:.4f}) | "
            f"acc {w.accuracy:.1%}"
        )
    print("Resumen:", outcome.summary())
