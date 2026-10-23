"""Adaptador Binance (API pública de klines, sin clave ni registro).

Ventajas frente a CoinGecko para este proyecto:
- OHLCV real (CoinGecko market_chart solo da cierres y volúmenes agregados).
- Hasta 1000 velas por petición, paginando hacia atrás.
- Sin clave ni límite agresivo para uso personal.

Los pares se cotizan en USDT; para el backtest se asume USDT ≈ USD (anotarlo en
el reporte y revisarlo si se usa para decisiones reales).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.data.adapters.base import DataProviderError, MarketDataAdapter
from src.schemas.market import Candle


class BinanceAdapter(MarketDataAdapter):
    name = "binance"
    max_calls_per_minute = 60
    default_base_url = "https://api.binance.com/api/v3"

    def __init__(self, timeout: float = 20.0, client: httpx.Client | None = None) -> None:
        self.base_url = os.getenv("BINANCE_BASE_URL", self.default_base_url).rstrip("/")
        self._client = client or httpx.Client(
            timeout=timeout, headers={"accept": "application/json", "user-agent": "trading-ai/0.1"}
        )

    def _symbol(self, asset: str) -> str:
        return f"{asset.upper().strip()}USDT"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        reraise=True,
    )
    def _get(self, path: str, params: dict[str, str | int]) -> list | dict:
        response = self._client.get(f"{self.base_url}{path}", params=params)
        if response.status_code == 429:
            raise DataProviderError("rate limit de Binance (429)")
        if response.status_code == 451:
            raise DataProviderError("Binance bloqueado geográficamente (451)")
        response.raise_for_status()
        return response.json()

    def fetch_daily_candles(self, asset: str, days: int = 120) -> list[Candle]:
        symbol = self._symbol(asset)
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        end_ms = now_ms
        rows: list[list] = []

        while len(rows) < days:
            wanted = min(1000, days - len(rows))
            batch = self._get(
                "/klines",
                {"symbol": symbol, "interval": "1d", "limit": wanted, "endTime": end_ms},
            )
            if not isinstance(batch, list) or not batch:
                break
            rows = batch + rows
            earliest_open = int(batch[0][0])
            end_ms = earliest_open - 1
            if len(batch) < wanted:
                break  # no hay más historia disponible

        candles: list[Candle] = []
        seen: set[int] = set()
        for row in rows:
            open_time = int(row[0])
            if open_time in seen:
                continue
            seen.add(open_time)
            close_time = int(row[6])
            if close_time > now_ms:
                continue  # vela en curso: no se usa para features
            candles.append(
                Candle(
                    asset=asset.upper(),
                    ts=datetime.fromtimestamp(close_time / 1000, tz=UTC),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
            )

        if not candles:
            raise DataProviderError(f"Binance no devolvió velas para {symbol}")
        return candles[-days:]

    def fetch_quote(self, asset: str) -> Candle:
        symbol = self._symbol(asset)
        payload = self._get("/ticker/price", {"symbol": symbol})
        if not isinstance(payload, dict):
            raise DataProviderError(f"Binance devolvió un payload inesperado para {symbol}")
        price = float(payload["price"])
        now = datetime.now(UTC)
        return Candle(asset=asset.upper(), ts=now, open=price, high=price, low=price, close=price, volume=0.0)
