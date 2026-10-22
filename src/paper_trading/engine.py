"""Paper trading: simulación explícita, sin dinero y sin exchange.

Objetivo de la fase 5: congelar la señal antes del resultado, medir slippage y
producir un historial auditable. Nada aquí toca claves ni órdenes reales.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from src.quant.risk import PortfolioState


@dataclass
class Position:
    asset: str
    entry_price: float
    size_pct: float
    opened_at: datetime
    signal_id: int = 0
    signal_timestamp: datetime | None = None


@dataclass
class ClosedTrade:
    asset: str
    entry_price: float
    exit_price: float
    size_pct: float
    opened_at: datetime
    closed_at: datetime
    gross_return: float
    net_return: float
    fees: float


@dataclass
class PaperTradingEngine:
    capital: float = 10_000.0
    cost_rate: float = 0.002  # ida y vuelta, fracción del notional
    positions: dict[str, Position] = field(default_factory=dict)
    closed: list[ClosedTrade] = field(default_factory=list)
    peak_equity: float = field(default=0.0)
    consecutive_losses: int = 0
    daily_start_equity: float = field(default=0.0)
    weekly_start_equity: float = field(default=0.0)

    def __post_init__(self) -> None:
        if self.peak_equity <= 0:
            self.peak_equity = self.capital
        if self.daily_start_equity <= 0:
            self.daily_start_equity = self.capital
        if self.weekly_start_equity <= 0:
            self.weekly_start_equity = self.capital

    # --------------------------- estado ---------------------------
    @property
    def equity(self) -> float:
        return self.capital

    @property
    def drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, (self.peak_equity - self.capital) / self.peak_equity * 100)

    def portfolio_state(self) -> PortfolioState:
        exposure = {a: p.size_pct for a, p in self.positions.items()}
        return PortfolioState(
            equity=self.capital,
            peak_equity=self.peak_equity,
            daily_pnl_pct=(self.capital / self.daily_start_equity - 1) * 100
            if self.daily_start_equity
            else 0.0,
            weekly_pnl_pct=(self.capital / self.weekly_start_equity - 1) * 100
            if self.weekly_start_equity
            else 0.0,
            consecutive_losses=self.consecutive_losses,
            exposure_by_asset_pct=exposure,
        )

    def reset_period(self, *, daily: bool = True, weekly: bool = False) -> None:
        if daily:
            self.daily_start_equity = self.capital
        if weekly:
            self.weekly_start_equity = self.capital

    # --------------------------- acciones ---------------------------
    def open(self, *, asset: str, price: float, size_pct: float, at: datetime | None = None, signal_id: int = 0) -> Position:
        if size_pct <= 0:
            raise ValueError("size_pct debe ser > 0")
        if price <= 0:
            raise ValueError("precio inválido")
        if asset in self.positions:
            raise ValueError(f"ya hay una posición abierta en {asset}")

        position = Position(
            asset=asset,
            entry_price=price,
            size_pct=size_pct,
            opened_at=at or datetime.now(UTC),
            signal_id=signal_id,
        )
        self.positions[asset] = position
        return position

    def close(self, asset: str, *, price: float, at: datetime | None = None) -> ClosedTrade:
        position = self.positions.pop(asset, None)
        if position is None:
            raise KeyError(f"no hay posición abierta en {asset}")
        if price <= 0:
            raise ValueError("precio inválido")

        notional = self.capital * (position.size_pct / 100)
        gross = (price / position.entry_price - 1) * notional
        fees = notional * self.cost_rate
        net = gross - fees

        self.capital += net
        self.peak_equity = max(self.peak_equity, self.capital)
        if net < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

        trade = ClosedTrade(
            asset=asset,
            entry_price=position.entry_price,
            exit_price=price,
            size_pct=position.size_pct,
            opened_at=position.opened_at,
            closed_at=at or datetime.now(UTC),
            gross_return=gross / notional if notional else 0.0,
            net_return=net / notional if notional else 0.0,
            fees=fees,
        )
        self.closed.append(trade)
        return trade

    def metrics(self) -> dict[str, float]:
        if not self.closed:
            return {"trades": 0, "win_rate": 0.0, "avg_net_return": 0.0, "drawdown_pct": self.drawdown_pct}
        wins = [t for t in self.closed if t.net_return > 0]
        gross_win = sum(t.net_return for t in wins)
        gross_loss = -sum(t.net_return for t in self.closed if t.net_return < 0)
        profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")
        return {
            "trades": float(len(self.closed)),
            "win_rate": len(wins) / len(self.closed),
            "avg_net_return": sum(t.net_return for t in self.closed) / len(self.closed),
            "profit_factor": profit_factor,
            "drawdown_pct": self.drawdown_pct,
            "equity": self.capital,
        }
