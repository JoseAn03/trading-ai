"""Configuración central. Sin secretos en código: todo por variables de entorno."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
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
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = field(default="", repr=False)
    llm_prompt_version: str = "explain-signal-v1"
    llm_monthly_budget_usd: float = 1.0
    llm_max_output_tokens: int = 700
    llm_input_usd_per_1k: float = 0.00015
    llm_output_usd_per_1k: float = 0.0006
    llm_json_mode: bool = True

    # Entrega (opcional). El panel es la fuente; Telegram solo notifica.
    telegram_enabled: bool = False
    telegram_bot_token: str = field(default="", repr=False)
    telegram_chat_id: str = ""

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
            llm_base_url=os.getenv("TRADING_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            llm_api_key=os.getenv("TRADING_LLM_API_KEY") or os.getenv("OPENAI_API_KEY", ""),
            llm_prompt_version=os.getenv("TRADING_LLM_PROMPT_VERSION", "explain-signal-v1"),
            llm_monthly_budget_usd=_float("TRADING_LLM_MONTHLY_BUDGET_USD", 1.0),
            llm_max_output_tokens=_int("TRADING_LLM_MAX_OUTPUT_TOKENS", 700),
            llm_input_usd_per_1k=_float("TRADING_LLM_INPUT_USD_PER_1K", 0.00015),
            llm_output_usd_per_1k=_float("TRADING_LLM_OUTPUT_USD_PER_1K", 0.0006),
            llm_json_mode=_bool("TRADING_LLM_JSON_MODE", True),
            telegram_enabled=_bool("TRADING_TELEGRAM_ENABLED", False),
            telegram_bot_token=os.getenv("TRADING_TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TRADING_TELEGRAM_CHAT_ID", ""),
        )

    @property
    def total_cost_rate(self) -> float:
        """Costo ida y vuelta expresado como fracción (1 bps = 0.0001)."""
        return (2 * (self.commission_bps + self.slippage_bps)) / 10_000
