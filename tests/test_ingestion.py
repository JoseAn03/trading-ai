"""Ingesta: los fallos esperados degradan; los bugs no se esconden."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.config.settings import Settings
from src.data.adapters.base import DataProviderError, MarketDataAdapter
from src.data.ingestion import ingest_asset
from src.data.store import Store
from src.schemas.market import Candle
from tests.conftest import make_candles


class FailingAdapter(MarketDataAdapter):
    name = "failing"

    def __init__(self, error: Exception) -> None:
        self.error = error

    def fetch_daily_candles(self, asset: str, days: int = 120) -> list[Candle]:
        raise self.error

    def fetch_quote(self, asset: str) -> Candle:
        raise self.error


def test_expected_provider_error_falls_back_to_cache(tmp_path) -> None:
    now = datetime.now(UTC)
    candles = make_candles(80, end=now)
    store = Store(tmp_path / "ingestion.db")
    store.init_schema()
    store.save_candles(candles, source="seed")

    result = ingest_asset(
        "BTC",
        FailingAdapter(DataProviderError("sin red")),
        settings=Settings(db_path=store.db_path),
        store=store,
        now=now,
    )

    assert result.from_cache is True
    assert result.source == "failing:cache"
    assert len(result.candles) == 80


def test_programming_error_is_not_swallowed(tmp_path) -> None:
    store = Store(tmp_path / "ingestion.db")
    store.init_schema()

    with pytest.raises(RuntimeError, match="bug de programación"):
        ingest_asset(
            "BTC",
            FailingAdapter(RuntimeError("bug de programación")),
            settings=Settings(db_path=store.db_path),
            store=store,
        )
