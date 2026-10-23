"""Gestión de riesgo ejecutable: límites + circuit breakers (sección 8 del plan).

Todo lo que aquí se decide es código, nunca criterio del LLM ni del agente.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.data.quality import data_age_tolerance_minutes
from src.schemas.enums import Signal
from src.schemas.signal import SignalContract


@dataclass(frozen=True)
class RiskLimits:
    max_risk_per_trade_pct: float = 1.5
    max_exposure_per_asset_pct: float = 10.0
    max_daily_loss_pct: float = 3.0
    max_weekly_loss_pct: float = 8.0
    max_drawdown_pct: float = 10.0
    max_trades_per_day: int = 5
    max_correlation: float = 0.8
    max_data_age_minutes: int = 20
    # Cadencia esperada de la vela. Con 24 h la tolerancia efectiva es 36 h.
    # Se comparte con src.data.quality para que calidad y riesgo no discrepen.
    expected_interval_hours: float = 24.0

    @property
    def data_age_tolerance_minutes(self) -> float:
        return data_age_tolerance_minutes(self.max_data_age_minutes, self.expected_interval_hours)

    @classmethod
    def conservative(cls) -> RiskLimits:
        return cls(
            max_risk_per_trade_pct=0.5,
            max_exposure_per_asset_pct=5.0,
            max_daily_loss_pct=1.5,
            max_weekly_loss_pct=4.0,
            max_drawdown_pct=5.0,
            max_trades_per_day=3,
            max_correlation=0.7,
        )


@dataclass
class PortfolioState:
    """Estado suficiente para evaluar los breakers. Se reconstruye desde la BD."""

    equity: float = 10_000.0
    peak_equity: float = 10_000.0
    daily_pnl_pct: float = 0.0
    weekly_pnl_pct: float = 0.0
    trades_today: int = 0
    consecutive_losses: int = 0
    exposure_by_asset_pct: dict[str, float] = field(default_factory=dict)
    model_in_calibration_range: bool = True
    provider_ok: bool = True
    data_ok: bool = True
    manual_kill_switch: bool = False

    @property
    def drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, (self.peak_equity - self.equity) / self.peak_equity * 100)


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reasons: list[str]
    suggested_size_pct: float
    blocked_by: list[str] = field(default_factory=list)


class CircuitBreaker:
    def __init__(self, limits: RiskLimits | None = None) -> None:
        self.limits = limits or RiskLimits()

    def evaluate(
        self, signal: SignalContract, state: PortfolioState
    ) -> RiskDecision:
        blocks: list[str] = []
        notes: list[str] = []

        if state.manual_kill_switch:
            blocks.append("kill_switch manual activo")
        if not state.provider_ok:
            blocks.append("proveedor de datos caído")
        if not state.data_ok:
            blocks.append("datos no válidos u obsoletos")
        if not state.model_in_calibration_range:
            blocks.append("modelo fuera de rango de calibración")

        age = signal.age_minutes
        tolerance = self.limits.data_age_tolerance_minutes
        if age > tolerance:
            blocks.append(
                f"datos con {age:.0f} min de antigüedad (máx {tolerance:.0f} min "
                f"para velas de {self.limits.expected_interval_hours:g} h)"
            )

        if state.trades_today >= self.limits.max_trades_per_day:
            blocks.append(f"límite de {self.limits.max_trades_per_day} operaciones/día alcanzado")

        if state.drawdown_pct >= self.limits.max_drawdown_pct:
            blocks.append(f"drawdown {state.drawdown_pct:.1f}% ≥ límite {self.limits.max_drawdown_pct:.1f}%")

        if state.daily_pnl_pct <= -self.limits.max_daily_loss_pct:
            blocks.append(f"pérdida diaria {state.daily_pnl_pct:.1f}% ≥ límite")

        if state.weekly_pnl_pct <= -self.limits.max_weekly_loss_pct:
            blocks.append(f"pérdida semanal {state.weekly_pnl_pct:.1f}% ≥ límite")

        if state.consecutive_losses >= 3:
            blocks.append(f"{state.consecutive_losses} pérdidas consecutivas")

        if abs(signal.risk.correlation_with_portfolio) >= self.limits.max_correlation:
            blocks.append(
                f"correlación {signal.risk.correlation_with_portfolio:.2f} ≥ límite "
                f"{self.limits.max_correlation:.2f}"
            )

        current_exposure = state.exposure_by_asset_pct.get(signal.asset, 0.0)
        projected = current_exposure + signal.risk.suggested_size_pct
        if projected > self.limits.max_exposure_per_asset_pct:
            blocks.append(
                f"exposición proyectada en {signal.asset} {projected:.2f}% > "
                f"{self.limits.max_exposure_per_asset_pct:.2f}%"
            )

        if signal.signal is not Signal.LONG_BIAS:
            blocks.append(f"señal {signal.signal.value} no abre posición")
        if not signal.is_actionable:
            blocks.append("señal marcada como no accionable")

        if blocks:
            return RiskDecision(
                allowed=False,
                reasons=notes or ["operación bloqueada por gestión de riesgo"],
                suggested_size_pct=0.0,
                blocked_by=blocks,
            )

        # Presupuesto restante del día y del trade.
        daily_remaining = self.limits.max_daily_loss_pct + state.daily_pnl_pct
        size = min(
            signal.risk.suggested_size_pct,
            self.limits.max_risk_per_trade_pct,
            max(0.0, daily_remaining),
            max(0.0, self.limits.max_exposure_per_asset_pct - current_exposure),
        )
        size = round(size, 2)
        if size <= 0:
            return RiskDecision(
                allowed=False,
                reasons=["sin presupuesto de riesgo disponible"],
                suggested_size_pct=0.0,
                blocked_by=["presupuesto de riesgo agotado"],
            )

        notes.append(f"tamaño autorizado {size:.2f}% del capital")
        return RiskDecision(allowed=True, reasons=notes, suggested_size_pct=size)
