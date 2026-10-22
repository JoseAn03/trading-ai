"""Contrato de señal: la pieza que no debe romperse nunca.

Toda señal publicada cumple este esquema. Si un campo falta o es incoherente,
el sistema falla en la validación en lugar de publicar una señal dudosa.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.enums import Confidence, DataQuality, Signal


class Costs(BaseModel):
    model_config = ConfigDict(frozen=True)

    estimated_slippage_bps: float = Field(ge=0)
    commission_bps: float = Field(ge=0)
    funding_impact: float = Field(default=0.0)

    @property
    def total_rate(self) -> float:
        """Costo ida y vuelta como fracción del notional."""
        return (2 * (self.estimated_slippage_bps + self.commission_bps)) / 10_000 + abs(
            self.funding_impact
        )


class RiskMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    suggested_size_pct: float = Field(ge=0, le=100)
    max_drawdown_estimate: float = Field(ge=0)
    correlation_with_portfolio: float = Field(ge=-1, le=1)


class SignalContract(BaseModel):
    """Contrato mínimo de una señal (sección 3.1 del plan)."""

    model_config = ConfigDict(frozen=True, use_enum_values=False)

    asset: str
    horizon: str
    timestamp: datetime
    data_timestamp: datetime
    signal: Signal

    probability_up: float = Field(ge=0, le=1)
    probability_down: float = Field(ge=0, le=1)
    probability_net_positive: float = Field(ge=0, le=1)
    expected_return_net: float

    confidence: Confidence
    rank_score: float = Field(ge=0, le=1)
    rank_position: int = Field(ge=1)

    reasons: list[str] = Field(default_factory=list, max_length=6)
    invalidators: list[str] = Field(default_factory=list, max_length=6)
    costs: Costs
    risk: RiskMetrics
    data_quality: DataQuality

    model_version: str
    code_version: str
    is_actionable: bool

    @model_validator(mode="after")
    def _consistency(self) -> Self:
        total = self.probability_up + self.probability_down
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"probability_up + probability_down = {total:.3f}, se espera 1.0")

        if self.data_timestamp > self.timestamp:
            raise ValueError("data_timestamp no puede ser posterior a timestamp")

        if self.signal is Signal.NO_TRADE and self.is_actionable:
            raise ValueError("NO_TRADE nunca es accionable")

        if self.is_actionable and self.probability_net_positive < 0.5:
            raise ValueError("is_actionable=true exige probabilidad neta > 0.5")

        if self.risk.suggested_size_pct == 0 and self.is_actionable:
            raise ValueError("una señal accionable necesita tamaño sugerido > 0")

        return self

    @property
    def age_minutes(self) -> float:
        return (self.timestamp - self.data_timestamp).total_seconds() / 60

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)


class SignalBatch(BaseModel):
    """Conjunto de señales de una corrida, ya ordenado por rank_score."""

    model_config = ConfigDict(frozen=True)

    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    horizon: str
    signals: list[SignalContract]
    model_version: str
    code_version: str

    @property
    def actionable(self) -> list[SignalContract]:
        return [s for s in self.signals if s.is_actionable]

    def summarise(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for s in self.signals:
            counts[s.signal.value] = counts.get(s.signal.value, 0) + 1
        counts["actionable"] = len(self.actionable)
        return counts


__all__ = ["Costs", "RiskMetrics", "SignalBatch", "SignalContract"]
