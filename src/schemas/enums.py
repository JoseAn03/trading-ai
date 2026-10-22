"""Enums del dominio. Un solo lugar para los valores válidos del contrato."""

from __future__ import annotations

from enum import StrEnum


class Signal(StrEnum):
    LONG_BIAS = "LONG_BIAS"
    RISK_OFF = "RISK_OFF"
    NO_TRADE = "NO_TRADE"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def weight(self) -> float:
        """Peso usado en el ranking (no es probabilidad)."""
        return {Confidence.LOW: 0.5, Confidence.MEDIUM: 0.8, Confidence.HIGH: 1.0}[self]

    def __ge__(self, other: Confidence) -> bool:  # type: ignore[override]
        order = {Confidence.LOW: 0, Confidence.MEDIUM: 1, Confidence.HIGH: 2}
        return order[self] >= order[other]


class DataQuality(StrEnum):
    VALID = "valid"
    DEGRADED = "degraded"
    INVALID = "invalid"


class HealthStatus(StrEnum):
    OK = "ok"
    STALE = "stale"
    ERROR = "error"
