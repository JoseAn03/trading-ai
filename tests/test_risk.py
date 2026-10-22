"""Circuit breakers: si el riesgo bloquea, no hay operación."""

from __future__ import annotations

from src.quant.risk import CircuitBreaker, PortfolioState, RiskLimits
from src.schemas.enums import Signal
from tests.conftest import make_signal


def test_clean_state_is_allowed() -> None:
    breaker = CircuitBreaker()
    decision = breaker.evaluate(make_signal(size=1.0), PortfolioState())
    assert decision.allowed is True
    assert 0 < decision.suggested_size_pct <= 1.5


def test_kill_switch_blocks_everything() -> None:
    breaker = CircuitBreaker()
    decision = breaker.evaluate(make_signal(), PortfolioState(manual_kill_switch=True))
    assert decision.allowed is False
    assert any("kill_switch" in b for b in decision.blocked_by)


def test_drawdown_blocks() -> None:
    breaker = CircuitBreaker(RiskLimits(max_drawdown_pct=5.0))
    state = PortfolioState(equity=9_000, peak_equity=10_000)
    decision = breaker.evaluate(make_signal(), state)
    assert decision.allowed is False
    assert any("drawdown" in b for b in decision.blocked_by)


def test_daily_loss_blocks() -> None:
    breaker = CircuitBreaker()
    decision = breaker.evaluate(make_signal(), PortfolioState(daily_pnl_pct=-3.5))
    assert decision.allowed is False


def test_exposure_cap_blocks() -> None:
    breaker = CircuitBreaker(RiskLimits(max_exposure_per_asset_pct=5.0))
    state = PortfolioState(exposure_by_asset_pct={"BTC": 4.8})
    decision = breaker.evaluate(make_signal(size=1.0), state)
    assert decision.allowed is False
    assert any("exposición" in b for b in decision.blocked_by)


def test_size_is_clamped_to_limits() -> None:
    breaker = CircuitBreaker(RiskLimits(max_risk_per_trade_pct=0.75))
    decision = breaker.evaluate(make_signal(size=5.0), PortfolioState())
    assert decision.allowed is True
    assert decision.suggested_size_pct == 0.75


def test_correlation_blocks() -> None:
    breaker = CircuitBreaker(RiskLimits(max_correlation=0.8))
    decision = breaker.evaluate(make_signal(correlation=0.95), PortfolioState())
    assert decision.allowed is False


def test_non_actionable_signal_blocked() -> None:
    breaker = CircuitBreaker()
    signal = make_signal(signal=Signal.NO_TRADE, actionable=False, size=0.0)
    decision = breaker.evaluate(signal, PortfolioState())
    assert decision.allowed is False


def test_stale_data_blocks() -> None:
    breaker = CircuitBreaker(RiskLimits(max_data_age_minutes=5))
    decision = breaker.evaluate(make_signal(age_minutes=30), PortfolioState())
    assert decision.allowed is False


def test_no_trades_left_today_blocks() -> None:
    breaker = CircuitBreaker(RiskLimits(max_trades_per_day=2))
    decision = breaker.evaluate(make_signal(), PortfolioState(trades_today=2))
    assert decision.allowed is False


def test_consecutive_losses_block() -> None:
    breaker = CircuitBreaker()
    decision = breaker.evaluate(make_signal(), PortfolioState(consecutive_losses=3))
    assert decision.allowed is False


def test_provider_down_blocks() -> None:
    breaker = CircuitBreaker()
    decision = breaker.evaluate(make_signal(), PortfolioState(provider_ok=False, data_ok=False))
    assert decision.allowed is False
