"""Configuración central. Sin secretos en código: todo por variables de entorno."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "var"
LOGS_DIR = DATA_DIR / "logs"


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    """Parámetros del sistema. Los umbrales viven aquí, no dispersos en el código."""

    assets: tuple[str, ...] = ("BTC", "ETH")
    horizon: str = "24h"
    quote_currency: str = "USD"

    # Umbrales de accionabilidad (ver PRODUCT_DECISIONS.md)
    min_probability_net_positive: float = 0.55
    min_confidence: str = "medium"
    max_data_age_minutes: int = 20
    min_history_days: int = 60

    # Costos de fricción, en puntos básicos
    commission_bps: float = 6.0
    slippage_bps: float = 4.0
    funding_impact: float = 0.0002

    # Infraestructura
    db_path: Path = DATA_DIR / "trading.db"
    log_level: str = "INFO"
    demo_mode: bool = True

    # IA de explicación (opcional; nunca decide)
    llm_enabled: bool = False
    llm_model: str = "gpt-4o-mini"

    @classmethod
    def from_env(cls) -> Settings:
        assets = tuple(
            a.strip().upper()
            for a in os.getenv("TRADING_ASSETS", "BTC,ETH").split(",")
            if a.strip()
        )
        return cls(
            assets=assets or ("BTC", "ETH"),
            horizon=os.getenv("TRADING_HORIZON", "24h"),
            min_probability_net_positive=_float("TRADING_MIN_PROB_NET", 0.55),
            min_confidence=os.getenv("TRADING_MIN_CONFIDENCE", "medium").lower(),
            max_data_age_minutes=_int("TRADING_MAX_DATA_AGE_MIN", 20),
            min_history_days=_int("TRADING_MIN_HISTORY_DAYS", 60),
            commission_bps=_float("TRADING_COMMISSION_BPS", 6.0),
            slippage_bps=_float("TRADING_SLIPPAGE_BPS", 4.0),
            funding_impact=_float("TRADING_FUNDING_IMPACT", 0.0002),
            db_path=Path(os.getenv("TRADING_DB_PATH", str(DATA_DIR / "trading.db"))),
            log_level=os.getenv("TRADING_LOG_LEVEL", "INFO").upper(),
            demo_mode=_bool("TRADING_DEMO_MODE", True),
            llm_enabled=_bool("TRADING_LLM_ENABLED", False),
            llm_model=os.getenv("TRADING_LLM_MODEL", "gpt-4o-mini"),
        )

    @property
    def total_cost_rate(self) -> float:
        """Costo ida y vuelta expresado como fracción (1 bps = 0.0001)."""
        return (2 * (self.commission_bps + self.slippage_bps)) / 10_000
