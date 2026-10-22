"""Contrato de datos de mercado: velas, cotizaciones y salud de la fuente."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(UTC)


class Candle(BaseModel):
    """Vela OHLCV normalizada. `ts` es el cierre de la vela, en UTC."""

    model_config = ConfigDict(frozen=True)

    asset: str
    ts: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)

    @field_validator("asset")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("ts")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return v if v.tzinfo else v.replace(tzinfo=UTC)


class Quote(BaseModel):
    model_config = ConfigDict(frozen=True)

    asset: str
    price: float = Field(gt=0)
    ts: datetime
    source: str


class DataQualityReport(BaseModel):
    """Resultado de validar una serie antes de calcular señales."""

    asset: str
    quality: str
    is_fresh: bool
    age_minutes: float
    candles: int
    gaps: list[str] = Field(default_factory=list)
    outliers: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
