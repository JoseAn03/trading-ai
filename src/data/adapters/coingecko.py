"""Adaptador CoinGecko (API pública, sin clave).

Endpoints usados:
- /coins/{id}/market_chart  -> precios y volúmenes históricos
- /simple/price            -> última cotización

Los datos crudos se guardan tal cual en `raw_provider_responses` (ver store.py)
para poder reprocesar y auditar sin volver a llamar al proveedor.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.data.adapters.base import DataProviderError, MarketDataAdapter
from src.schemas.market import Candle

COINGECKO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "ADA": "cardano",
    "XRP": "ripple",
    "DOGE": "dogecoin",
}


class CoinGeckoAdapter(MarketDataAdapter):
    name = "coingecko"
    max_calls_per_minute = 10
    base_url = "https://api.coingecko.com/api/v3"

    def __init__(self, timeout: float = 15.0, client: httpx.Client | None = None) -> None:
        headers = {"accept": "application/json"}
        api_key = os.getenv("COINGECKO_API_KEY", "").strip()
        if api_key:
            headers["x-cg-demo-api-key"] = api_key
        self._client = client or httpx.Client(timeout=timeout, headers=headers)

    def _coin_id(self, asset: str) -> str:
        key = asset.upper()
        if key not in COINGECKO_IDS:
            raise DataProviderError(f"activo no soportado por CoinGecko: {asset}")
        return COINGECKO_IDS[key]

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        reraise=True,
    )
    def _get(self, path: str, params: dict[str, str | int]) -> dict:
        response = self._client.get(f"{self.base_url}{path}", params=params)
        if response.status_code == 429:
            raise DataProviderError("rate limit de CoinGecko (429)")
        if response.status_code == 401:
            raise DataProviderError(
                "CoinGecko exige API key (401). Define COINGECKO_API_KEY o usa Binance."
            )
        response.raise_for_status()
        return response.json()

    def fetch_daily_candles(self, asset: str, days: int = 120) -> list[Candle]:
        coin = self._coin_id(asset)
        payload = self._get(
            f"/coins/{coin}/market_chart",
            {"vs_currency": "usd", "days": days},
        )
        prices = payload.get("prices") or []
        volumes = payload.get("total_volumes") or []

        if not prices:
            raise DataProviderError(f"respuesta sin precios para {asset}")

        candles: list[Candle] = []
        prev_close: float | None = None
        for i, (ts_ms, price) in enumerate(prices):
            ts = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
            vol = volumes[i][1] if i < len(volumes) else 0.0
            close = float(price)
            open_ = prev_close if prev_close is not None else close
            candles.append(
                Candle(
                    asset=asset.upper(),
                    ts=ts,
                    open=open_,
                    high=max(open_, close) * 1.001,
                    low=min(open_, close) * 0.999,
                    close=close,
                    volume=float(vol),
                )
            )
            prev_close = close

        # La última vela puede ser parcial: se descarta para no sesgar features.
        return candles[:-1] if len(candles) > 2 else candles

    def fetch_quote(self, asset: str) -> Candle:
        coin = self._coin_id(asset)
        payload = self._get(
            "/simple/price",
            {"ids": coin, "vs_currencies": "usd", "include_last_updated_at": "true"},
        )
        entry = payload.get(coin) or {}
        price = entry.get("usd")
        if price is None:
            raise DataProviderError(f"sin precio para {asset}")
        ts = entry.get("last_updated_at")
        stamp = (
            datetime.fromtimestamp(ts, tz=UTC)
            if ts
            else datetime.now(UTC)
        )
        return Candle(
            asset=asset.upper(),
            ts=stamp,
            open=price,
            high=price,
            low=price,
            close=price,
            volume=0.0,
        )
