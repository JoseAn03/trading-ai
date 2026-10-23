"""Integración de la corrida diaria: frescura realista, cartera y kill switch."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jobs.daily_run import run
from src.config.settings import Settings
from src.data.adapters.base import MarketDataAdapter
from src.data.store import Store
from src.quant.risk import RiskLimits
from src.schemas.market import Candle
from tests.conftest import make_candles


class FakeAdapter(MarketDataAdapter):
    """Velas diarias con cierre horas atrás y una cotización viva distinta."""

    name = "fake"

    def __init__(self, candles_by_asset: dict[str, list[Candle]], quote_move: float = 0.01) -> None:
        self.candles_by_asset = candles_by_asset
        self.quote_move = quote_move

    def fetch_daily_candles(self, asset: str, days: int = 120) -> list[Candle]:
        return self.candles_by_asset[asset][-days:]

    def fetch_quote(self, asset: str) -> Candle:
        last = self.candles_by_asset[asset][-1]
        return Candle(
            asset=last.asset,
            ts=last.ts + timedelta(hours=6),
            open=last.close,
            high=last.close * (1 + self.quote_move),
            low=last.close,
            close=last.close * (1 + self.quote_move),
            volume=last.volume,
        )


def _settings(tmp_path, **overrides) -> Settings:
    base = {
        "assets": ("BTC",),
        "db_path": tmp_path / "daily.db",
        "min_history_days": 60,
    }
    return Settings(**{**base, **overrides})


def _candles(now: datetime, *, hours_old: int = 6) -> dict[str, list[Candle]]:
    return {
        "BTC": make_candles(
            120,
            daily_return=0.01,
            volatility=0.0,
            asset="BTC",
            end=now - timedelta(hours=hours_old),
        )
    }


def _fake(monkeypatch, adapter: FakeAdapter) -> None:
    monkeypatch.setattr("jobs.daily_run._adapter", lambda live: adapter)


def test_realistic_daily_candle_opens_paper_trade(tmp_path, monkeypatch) -> None:
    now = datetime.now(UTC)
    candles = _candles(now, hours_old=6)
    adapter = FakeAdapter(candles)
    _fake(monkeypatch, adapter)
    settings = _settings(tmp_path)

    result = run(live=False, settings=settings, out_dir=tmp_path)

    assert len(result["paper"]["opened"]) == 1
    signal = result["batch"].signals[0]
    assert signal.is_actionable is True
    assert result["decisions"]["BTC"].allowed is True

    row = Store(settings.db_path).open_paper_trades()[0]
    assert row["signal_price"] == pytest.approx(candles["BTC"][-1].close)
    assert row["entry_price"] == pytest.approx(candles["BTC"][-1].close * 1.01)
    assert row["entry_price_source"] == "quote_at_run"
    assert row["opened_at"] >= now.isoformat()
    assert row["due_at"] > row["opened_at"]


def test_really_stale_daily_candle_does_not_open(tmp_path, monkeypatch) -> None:
    now = datetime.now(UTC)
    candles = _candles(now, hours_old=40)
    _fake(monkeypatch, FakeAdapter(candles))
    settings = _settings(tmp_path)

    result = run(live=False, settings=settings, out_dir=tmp_path)

    assert result["paper"]["opened"] == []
    assert result["batch"].signals[0].signal.value == "NO_TRADE"
    assert result["decisions"]["BTC"].allowed is False


def test_seeded_daily_loss_blocks_new_trade(tmp_path, monkeypatch) -> None:
    now = datetime.now(UTC)
    settings = _settings(tmp_path)
    store = Store(settings.db_path)
    store.init_schema()
    trade_id = store.save_paper_trade(
        asset="BTC",
        signal_id=999,
        opened_at=now - timedelta(hours=2),
        entry_price=100.0,
        size_pct=100.0,
        horizon_days=1,
        due_at=now,
        cost_rate=0.002,
        model_version="seed",
    )
    assert trade_id is not None
    store.close_paper_trade(
        trade_id,
        exit_price=96.0,
        closed_at=now - timedelta(hours=1),
        gross_return=-0.04,
        net_return=-0.04,
        fees=0.002,
    )

    _fake(monkeypatch, FakeAdapter(_candles(now)))
    result = run(live=False, settings=settings, out_dir=tmp_path)

    assert result["paper"]["opened"] == []
    decision = result["decisions"]["BTC"]
    assert decision.allowed is False
    assert any("pérdida diaria" in reason for reason in decision.blocked_by)
    assert result["batch"].signals[0].signal.value == "NO_TRADE"


def test_db_kill_switch_blocks_everything(tmp_path, monkeypatch) -> None:
    now = datetime.now(UTC)
    settings = _settings(tmp_path)
    store = Store(settings.db_path)
    store.init_schema()
    store.set_control_flag("kill_switch", True)

    _fake(monkeypatch, FakeAdapter(_candles(now)))
    result = run(live=False, settings=settings, out_dir=tmp_path)

    assert result["paper"]["opened"] == []
    assert any("kill_switch" in reason for reason in result["decisions"]["BTC"].blocked_by)


def test_authorized_size_is_applied_to_paper_trade(tmp_path, monkeypatch) -> None:
    now = datetime.now(UTC)
    settings = _settings(tmp_path)
    _fake(monkeypatch, FakeAdapter(_candles(now)))

    result = run(
        live=False,
        settings=settings,
        out_dir=tmp_path,
        risk_limits=RiskLimits(max_risk_per_trade_pct=0.3, expected_interval_hours=24.0),
    )

    assert len(result["paper"]["opened"]) == 1
    assert result["batch"].signals[0].risk.suggested_size_pct == pytest.approx(0.3)
    row = Store(settings.db_path).open_paper_trades()[0]
    assert row["size_pct"] == pytest.approx(0.3)
