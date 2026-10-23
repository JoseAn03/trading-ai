"""Paper trading persistente (fase 5), en modo sombra y sin capital real.

Reglas:
- El precio de la señal (`signal_price`) se congela al cierre de la vela con la
  que se calculó. La operación paper se ejecuta al precio vigente al momento de
  la corrida (`entry_price`), que puede ser horas posterior; ambos se guardan
  para medir el sesgo de entrada.
- El resultado se resuelve con el primer cierre en o después de `due_at`; nunca
  se usa una vela posterior a `now`.
- Cada operación es idempotente por `signal_id` y solo puede haber una posición
  abierta por activo.
- El costo se congela al abrir (`costs.total_rate`), no se recalcula después.

Este módulo no habla con exchanges ni mueve dinero: solo escribe en SQLite.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import mean

from src.data.store import Store
from src.schemas.market import Candle


@dataclass(frozen=True)
class SignalToOpen:
    signal_id: int
    asset: str
    horizon: str
    data_timestamp: datetime
    entry_price: float
    size_pct: float
    cost_rate: float
    model_version: str
    signal_price: float | None = None
    entry_price_source: str = "signal_close"
    opened_at: datetime | None = None


@dataclass(frozen=True)
class PaperStats:
    trades: int
    open_trades: int
    win_rate: float
    equity: float
    total_return_pct: float
    avg_net_return: float
    max_drawdown_pct: float
    avg_entry_delay_pct: float = 0.0


def horizon_to_days(horizon: str) -> int:
    """Convierte '24h', '72h', '1d', '3d'... a días enteros (mínimo 1)."""
    text = (horizon or "").strip().lower()
    try:
        if text.endswith("h"):
            return max(1, round(int(text[:-1]) / 24))
        if text.endswith("d"):
            return max(1, int(text[:-1]))
        return max(1, int(text))
    except ValueError:
        return 1


def close_at(candles: list[Candle], ts: datetime) -> float | None:
    """Cierre de la última vela en o antes de `ts` (entrada congelada)."""
    for candle in sorted(candles, key=lambda c: c.ts, reverse=True):
        if candle.ts <= ts:
            return candle.close
    return None


def open_from_signals(store: Store, candidates: list[SignalToOpen]) -> list[int]:
    """Abre posiciones simuladas para señales accionables. Idempotente."""
    opened: list[int] = []
    for candidate in candidates:
        if candidate.signal_id <= 0 or candidate.entry_price <= 0 or candidate.size_pct <= 0:
            continue
        days = horizon_to_days(candidate.horizon)
        opened_at = candidate.opened_at or candidate.data_timestamp
        due_at = opened_at + timedelta(days=days)
        signal_price = (
            candidate.signal_price if candidate.signal_price and candidate.signal_price > 0
            else candidate.entry_price
        )
        trade_id = store.save_paper_trade(
            asset=candidate.asset,
            signal_id=candidate.signal_id,
            opened_at=opened_at,
            entry_price=candidate.entry_price,
            size_pct=candidate.size_pct,
            horizon_days=days,
            due_at=due_at,
            cost_rate=candidate.cost_rate,
            model_version=candidate.model_version,
            signal_price=signal_price,
            entry_price_source=candidate.entry_price_source,
        )
        if trade_id is None:
            continue
        opened.append(trade_id)
        store.audit(
            "paper",
            "open",
            f"trade={trade_id} asset={candidate.asset} entry={candidate.entry_price:.2f} "
            f"signal_price={signal_price:.2f} source={candidate.entry_price_source} "
            f"due={due_at.isoformat()} signal={candidate.signal_id}",
        )
    return opened


def _due_at(row: dict) -> datetime | None:
    if row.get("due_at"):
        return datetime.fromisoformat(row["due_at"])
    if row.get("opened_at"):
        opened = datetime.fromisoformat(row["opened_at"])
        return opened + timedelta(days=int(row.get("horizon_days") or 1))
    return None


def resolve_due(
    store: Store,
    candles_by_asset: dict[str, list[Candle]],
    *,
    now: datetime,
) -> list[dict]:
    """Cierra posiciones cuyo horizonte ya venció y hay vela de salida."""
    closed: list[dict] = []
    for row in store.open_paper_trades():
        due = _due_at(row)
        if due is None or due > now:
            continue

        candles = sorted(candles_by_asset.get(row["asset"], []), key=lambda c: c.ts)
        exit_candle = next((c for c in candles if due <= c.ts <= now), None)
        if exit_candle is None:
            continue  # aún no hay cierre posterior al vencimiento

        entry = float(row["entry_price"])
        cost = float(row["cost_rate"] or 0.0)
        gross = exit_candle.close / entry - 1.0
        net = gross - cost

        store.close_paper_trade(
            row["id"],
            exit_price=exit_candle.close,
            closed_at=exit_candle.ts,
            gross_return=gross,
            net_return=net,
            fees=cost,
        )
        store.audit(
            "paper",
            "close",
            f"trade={row['id']} asset={row['asset']} net={net:.4f} exit={exit_candle.close:.2f}",
        )
        closed.append(
            {
                **row,
                "exit_price": exit_candle.close,
                "closed_at": exit_candle.ts.isoformat(),
                "gross_return": gross,
                "net_return": net,
            }
        )
    return closed


def _closed_rows(store: Store) -> list[dict]:
    rows = store.paper_trades(status="closed", limit=10_000)
    return sorted(rows, key=lambda r: r["closed_at"] or r["opened_at"])


def equity_points(store: Store) -> list[dict]:
    """Curva de capital (base 1.0) de las operaciones cerradas, en orden temporal."""
    rows = _closed_rows(store)
    points: list[dict] = []
    equity = 1.0
    if rows:
        points.append({"at": datetime.fromisoformat(rows[0]["opened_at"]), "equity": equity})
    for row in rows:
        equity *= 1 + float(row["net_return"] or 0.0) * float(row["size_pct"]) / 100
        points.append({"at": datetime.fromisoformat(row["closed_at"]), "equity": equity})
    return points


def paper_stats(store: Store) -> PaperStats:
    rows = _closed_rows(store)
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    wins = 0
    net_returns: list[float] = []
    entry_delays: list[float] = []

    for row in rows:
        net_return = float(row["net_return"] or 0.0)
        equity *= 1 + net_return * float(row["size_pct"]) / 100
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)
        if net_return > 0:
            wins += 1
        net_returns.append(net_return)
        signal_price = float(row["signal_price"] or 0.0)
        if signal_price > 0:
            entry_delays.append((float(row["entry_price"]) / signal_price - 1) * 100)

    return PaperStats(
        trades=len(rows),
        open_trades=len(store.open_paper_trades()),
        win_rate=wins / len(rows) if rows else 0.0,
        equity=equity,
        total_return_pct=(equity - 1) * 100,
        avg_net_return=mean(net_returns) if net_returns else 0.0,
        max_drawdown_pct=max_drawdown * 100,
        avg_entry_delay_pct=mean(entry_delays) if entry_delays else 0.0,
    )
