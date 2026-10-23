"""Reconstrucción del estado de cartera desde SQLite.

El motor en memoria es útil para tests unitarios, pero la corrida diaria debe
evaluar los breakers contra la cartera real del paper trading persistente. Este
módulo es la única traducción entre `paper_trades` y `PortfolioState`.

Supuestos explícitos:
- El día operativo es UTC (las velas diarias de Binance cierran a las 00:00 UTC).
- La curva de equity es la de las operaciones cerradas, base 1.0, y cada trade
  arriesga `size_pct` del capital.
- `trades_today` cuenta entradas abiertas hoy, no cierres.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from src.data.store import Store
from src.quant.risk import PortfolioState


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    return _as_utc(datetime.fromisoformat(value))


def portfolio_state_from_store(
    store: Store,
    *,
    now: datetime,
    kill_switch: bool = False,
    equity_base: float = 10_000.0,
    provider_ok: bool = True,
    data_ok: bool = True,
) -> PortfolioState:
    """Reconstruye exposición, equity, PnL y rachas desde `paper_trades`."""
    stamp = _as_utc(now)
    day_start = datetime.combine(stamp.date(), time.min, tzinfo=UTC)
    week_start = day_start - timedelta(days=stamp.weekday())

    rows = store.paper_trades(limit=100_000)
    rows.sort(key=lambda r: r.get("closed_at") or r.get("opened_at") or "")

    closed = [
        row
        for row in rows
        if row.get("status") == "closed" and (_parse(row.get("closed_at")) or stamp) <= stamp
    ]

    equity_factor = 1.0
    peak_factor = 1.0
    day_start_factor: float | None = None
    week_start_factor: float | None = None
    consecutive_losses = 0

    for row in closed:
        closed_at = _parse(row.get("closed_at"))
        if closed_at is None:
            continue
        if day_start_factor is None and closed_at >= day_start:
            day_start_factor = equity_factor
        if week_start_factor is None and closed_at >= week_start:
            week_start_factor = equity_factor

        net_return = float(row.get("net_return") or 0.0)
        size_pct = float(row.get("size_pct") or 0.0)
        equity_factor *= 1.0 + net_return * size_pct / 100.0
        peak_factor = max(peak_factor, equity_factor)
        consecutive_losses = consecutive_losses + 1 if net_return < 0 else 0

    # Sin cierres hoy/semana el PnL del período es cero, aunque la equity
    # acumulada venga de días anteriores.
    if day_start_factor is None:
        day_start_factor = equity_factor
    if week_start_factor is None:
        week_start_factor = equity_factor

    exposure: dict[str, float] = {}
    for row in rows:
        if row.get("status") != "open":
            continue
        asset = str(row["asset"])
        exposure[asset] = exposure.get(asset, 0.0) + float(row.get("size_pct") or 0.0)

    trades_today = sum(
        1
        for row in rows
        if (opened := _parse(row.get("opened_at"))) is not None and opened.date() == stamp.date()
    )

    daily_pnl_pct = (
        (equity_factor / day_start_factor - 1.0) * 100.0 if day_start_factor > 0 else 0.0
    )
    weekly_pnl_pct = (
        (equity_factor / week_start_factor - 1.0) * 100.0 if week_start_factor > 0 else 0.0
    )

    return PortfolioState(
        equity=equity_base * equity_factor,
        peak_equity=equity_base * peak_factor,
        daily_pnl_pct=daily_pnl_pct,
        weekly_pnl_pct=weekly_pnl_pct,
        trades_today=trades_today,
        consecutive_losses=consecutive_losses,
        exposure_by_asset_pct=exposure,
        provider_ok=provider_ok,
        data_ok=data_ok,
        manual_kill_switch=kill_switch,
    )
