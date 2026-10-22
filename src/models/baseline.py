"""Baseline de modelado: regresión logística con numpy.

Se usa como referencia mínima en el backtesting (fase 4). No pretende ser un
modelo avanzado: si un modelo futuro no le gana a esto después de costos, no
justifica su complejidad.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

FEATURE_ORDER = ("trend", "momentum_10d", "distance_to_sma", "volume_relative", "vol_20d")


@dataclass
class LogisticBaseline:
    learning_rate: float = 0.1
    epochs: int = 300
    l2: float = 0.01
    feature_order: tuple[str, ...] = FEATURE_ORDER
    weights: np.ndarray | None = None
    bias: float = 0.0
    mu: np.ndarray | None = None
    sigma: np.ndarray | None = None
    history: list[float] = field(default_factory=list)

    def _matrix(self, rows: list[dict[str, float]]) -> np.ndarray:
        return np.array(
            [[float(r[k]) for k in self.feature_order] for r in rows], dtype=float
        )

    def fit(self, rows: list[dict[str, float]], labels: list[int]) -> LogisticBaseline:
        if len(rows) != len(labels):
            raise ValueError("features y labels deben tener la misma longitud")
        if len(rows) < 10:
            raise ValueError("se requieren al menos 10 observaciones para entrenar")

        X = self._matrix(rows)
        y = np.asarray(labels, dtype=float)

        self.mu = X.mean(axis=0)
        self.sigma = X.std(axis=0)
        self.sigma[self.sigma == 0] = 1.0
        Xn = (X - self.mu) / self.sigma

        n, d = Xn.shape
        self.weights = np.zeros(d)
        self.bias = 0.0

        for _ in range(self.epochs):
            z = Xn @ self.weights + self.bias
            p = 1.0 / (1.0 + np.exp(-z))
            error = p - y
            grad_w = (Xn.T @ error) / n + self.l2 * self.weights
            grad_b = error.mean()

            self.weights -= self.learning_rate * grad_w
            self.bias -= self.learning_rate * grad_b

            loss = -np.mean(y * np.log(p + 1e-12) + (1 - y) * np.log(1 - p + 1e-12))
            self.history.append(float(loss))

        return self

    def predict_proba(self, rows: list[dict[str, float]]) -> np.ndarray:
        if self.weights is None or self.mu is None or self.sigma is None:
            raise RuntimeError("el modelo no está entrenado")
        X = self._matrix(rows)
        Xn = (X - self.mu) / self.sigma
        z = Xn @ self.weights + self.bias
        return 1.0 / (1.0 + np.exp(-z))

    def describe(self) -> dict[str, float]:
        if self.weights is None:
            return {}
        return {
            name: float(w)
            for name, w in zip(self.feature_order, self.weights, strict=True)
        }
