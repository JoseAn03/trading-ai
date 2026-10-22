"""Interfaz común de proveedores de datos.

Cada adaptador devuelve velas ya normalizadas y *sin* inventar datos: si el
proveedor falla, se propaga el error y el sistema degrada de forma controlada.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime

from src.schemas.market import Candle


class DataProviderError(RuntimeError):
    """Error recuperable o definitivo de un proveedor."""


class MarketDataAdapter(ABC):
    name: str = "base"
    max_calls_per_minute: int = 30

    @abstractmethod
    def fetch_daily_candles(self, asset: str, days: int = 120) -> list[Candle]:
        """Devuelve velas diarias ordenadas cronológicamente, en UTC."""

    @abstractmethod
    def fetch_quote(self, asset: str) -> Candle:
        """Última cotización disponible (como vela de 1 punto)."""

    def health(self) -> bool:
        try:
            self.fetch_quote("BTC")
            return True
        except Exception:  # noqa: BLE001 - health nunca debe romper la app
            return False


class StaticAdapter(MarketDataAdapter):
    """Proveedor sintético para demo, tests y arranque sin red."""

    name = "static"

    def __init__(self, base_price: float = 50_000.0, days: int = 120) -> None:
        self.base_price = base_price
        self._days = days

    def _series(self, asset: str) -> list[Candle]:
        from datetime import timedelta

        seed = sum(ord(c) for c in asset)
        price = self.base_price * (1 + (seed % 10) / 100)
        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        candles: list[Candle] = []
        for i in range(self._days):
            drift = 0.0008 * ((i % 7) - 3) + 0.0003
            price = max(1.0, price * (1 + drift))
            ts = start - timedelta(days=self._days - 1 - i)
            candles.append(
                Candle(
                    asset=asset,
                    ts=ts,
                    open=price * 0.998,
                    high=price * 1.01,
                    low=price * 0.99,
                    close=price,
                    volume=1_000 * (1 + (i % 5) / 10),
                )
            )
        return candles

    def fetch_daily_candles(self, asset: str, days: int = 120) -> list[Candle]:
        return self._series(asset)[-days:]

    def fetch_quote(self, asset: str) -> Candle:
        return self._series(asset)[-1]
