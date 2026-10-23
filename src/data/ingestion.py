"""Ingesta: proveedor -> normalización -> calidad -> almacenamiento.

Degradación controlada: si el proveedor falla, se usa la última serie guardada y
la calidad queda marcada como no fresca (lo que bloquea señales accionables).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from src.config.settings import Settings
from src.data.adapters.base import DataProviderError, MarketDataAdapter
from src.data.quality import validate_series
from src.data.store import Store
from src.schemas.market import Candle, DataQualityReport

log = logging.getLogger(__name__)


@dataclass
class IngestionResult:
    asset: str
    candles: list[Candle]
    quality: DataQualityReport
    source: str
    from_cache: bool = False


def ingest_asset(
    asset: str,
    adapter: MarketDataAdapter,
    *,
    settings: Settings,
    store: Store | None = None,
    now: datetime | None = None,
    days: int = 120,
) -> IngestionResult:
    stamp = now or datetime.now(UTC)
    asset = asset.upper()

    candles: list[Candle] = []
    from_cache = False
    source = adapter.name

    try:
        candles = adapter.fetch_daily_candles(asset, days=days)
        if store:
            store.save_candles(candles, source=adapter.name)
    except (DataProviderError, httpx.HTTPError) as exc:
        # Fallo esperado del proveedor (red, rate limit, HTTP). Un bug de
        # programación no debe quedar oculto por este except.
        log.warning("proveedor %s falló para %s: %s", adapter.name, asset, exc)
        if store:
            candles = store.load_candles(asset, limit=max(days, 60))
        from_cache = True
        source = f"{adapter.name}:cache"

    quality = validate_series(
        candles,
        now=stamp,
        max_age_minutes=settings.max_data_age_minutes,
        expected_interval_hours=settings.expected_interval_hours,
        min_candles=settings.min_history_days,
    )
    if store:
        store.save_quality(quality)

    return IngestionResult(asset=asset, candles=candles, quality=quality, source=source, from_cache=from_cache)
