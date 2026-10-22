"""Backtesting walk-forward sin fuga de información.

Reglas:
- El modelo se entrena solo con datos anteriores al tramo evaluado.
- Se aplican costos en la capa de señal; aquí se miden métricas de predicción
  (log loss, Brier, accuracy) contra el baseline trivial 50/50.
- Si el modelo no le gana al baseline después de costos, no justifica existir.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from src.models.baseline import LogisticBaseline
from src.quant.features import compute_features
from src.schemas.market import Candle


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


@dataclass(frozen=True)
class Sample:
    features: dict[str, float]
    label: int
    ts: datetime


def build_samples(candles: list[Candle], *, min_history: int = 31) -> list[Sample]:
    """Features en t, etiqueta = retorno de t a t+1 (nunca usa el futuro)."""
    ordered = sorted(candles, key=lambda c: c.ts)
    samples: list[Sample] = []

    for i in range(min_history, len(ordered) - 1):
        window = ordered[: i + 1]
        try:
            f = compute_features(window, now=ordered[i].ts)
        except ValueError:
            continue

        samples.append(
            Sample(
                features={
                    "trend": f.trend,
                    "momentum_10d": f.momentum_10d,
                    "distance_to_sma": f.distance_to_sma,
                    "volume_relative": f.volume_relative,
                    "vol_20d": f.vol_20d,
                },
                label=1 if ordered[i + 1].close > ordered[i].close else 0,
                ts=ordered[i].ts,
            )
        )
    return samples


def _metrics(probs: list[float], labels: list[int]) -> tuple[float, float, float]:
    if not labels:
        return math.nan, math.nan, math.nan
    eps = 1e-12
    n = len(labels)
    log_loss = -sum(
        y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps))
        for p, y in zip(probs, labels, strict=True)
    ) / n
    brier = sum((p - y) ** 2 for p, y in zip(probs, labels, strict=True)) / n
    accuracy = sum((p >= 0.5) == bool(y) for p, y in zip(probs, labels, strict=True)) / n
    return log_loss, brier, accuracy


def walk_forward(
    candles: list[Candle], *, train: int = 60, test: int = 20, step: int = 20
) -> WalkForwardResult:
    samples = build_samples(candles)
    if len(samples) < train + test:
        raise ValueError(f"muestras insuficientes: {len(samples)} (se requieren {train + test})")

    result = WalkForwardResult()
    start = 0

    while start + train + test <= len(samples):
        train_slice = samples[start : start + train]
        test_slice = samples[start + train : start + train + test]

        model = LogisticBaseline(epochs=400, learning_rate=0.15).fit(
            [s.features for s in train_slice], [s.label for s in train_slice]
        )
        probs = [float(p) for p in model.predict_proba([s.features for s in test_slice])]
        labels = [s.label for s in test_slice]

        log_loss, brier, accuracy = _metrics(probs, labels)
        base_log_loss, base_brier, _ = _metrics([0.5] * len(labels), labels)

        result.windows.append(
            WindowResult(
                start=test_slice[0].ts,
                end=test_slice[-1].ts,
                n=len(labels),
                log_loss=log_loss,
                brier=brier,
                accuracy=accuracy,
                log_loss_baseline=base_log_loss,
                brier_baseline=base_brier,
            )
        )
        result.model_weights = model.describe()
        start += step

    return result


def evaluate_with_costs(
    probs: list[float], returns: list[float], *, cost_rate: float = 0.002, threshold: float = 0.55
) -> dict[str, float]:
    """Retorno neto simulado de operar solo cuando p >= threshold."""
    assert len(probs) == len(returns)
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
    print("Pesos:", {k: round(v, 4) for k, v in outcome.model_weights.items()})
