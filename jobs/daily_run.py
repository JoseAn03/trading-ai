"""Corrida diaria end-to-end: ingesta -> features -> señal -> riesgo -> reporte.

Uso:
    python -m jobs.daily_run                # demo con datos sintéticos
    python -m jobs.daily_run --live         # CoinGecko (requiere red)
"""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime
from pathlib import Path

from src.config.settings import LOGS_DIR, ROOT, Settings
from src.data.adapters.base import MarketDataAdapter, StaticAdapter
from src.data.ingestion import ingest_asset
from src.data.store import Store
from src.paper_trading.engine import PaperTradingEngine
from src.quant.features import InsufficientData, compute_features
from src.quant.risk import CircuitBreaker, RiskDecision
from src.quant.signals import build_signal, rank_signals
from src.reporting.daily_report import build_report
from src.schemas.enums import Signal
from src.schemas.signal import SignalBatch, SignalContract

log = logging.getLogger("daily_run")


def _adapter(live: bool) -> MarketDataAdapter:
    if not live:
        return StaticAdapter()
    from src.data.adapters.binance import BinanceAdapter

    return BinanceAdapter()


def run(*, live: bool = False, settings: Settings | None = None, out_dir: Path | None = None) -> dict:
    settings = settings or Settings.from_env()
    out_dir = out_dir or (ROOT / "var" / "reports")
    now = datetime.now(UTC)

    store = Store(settings.db_path)
    store.init_schema()

    adapter = _adapter(live)
    breaker = CircuitBreaker()
    paper = PaperTradingEngine(cost_rate=settings.total_cost_rate)
    state = paper.portfolio_state()

    signals: list[SignalContract] = []
    decisions: dict[str, RiskDecision] = {}

    for asset in settings.assets:
        result = ingest_asset(asset, adapter, settings=settings, store=store, now=now)
        if len(result.candles) < settings.min_history_days:
            log.warning("%s: histórico insuficiente (%s velas)", asset, len(result.candles))
            continue

        try:
            features = compute_features(result.candles, now=now)
        except InsufficientData as exc:
            log.warning("%s: %s", asset, exc)
            continue

        signal = build_signal(
            features,
            result.quality,
            settings=settings,
            code_version="dev",
            correlation=state.exposure_by_asset_pct.get(asset, 0.0) / 100,
            now=now,
        )
        decision = breaker.evaluate(signal, state)
        decisions[asset] = decision

        # Si el riesgo bloquea, la señal se convierte en NO_TRADE y deja de ser accionable.
        if not decision.allowed and signal.is_actionable:
            signal = signal.model_copy(
                update={
                    "signal": Signal.NO_TRADE,
                    "is_actionable": False,
                    "risk": signal.risk.model_copy(update={"suggested_size_pct": 0.0}),
                    "invalidators": [*signal.invalidators, *decision.blocked_by][:6],
                }
            )

        signals.append(signal)

    ranked = rank_signals(signals)
    for s in ranked:
        store.save_signal(s)
    batch = SignalBatch(
        generated_at=now,
        horizon=settings.horizon,
        signals=ranked,
        model_version=ranked[0].model_version if ranked else "n/a",
        code_version=ranked[0].code_version if ranked else "n/a",
    )

    report = build_report(batch, decisions=decisions, generated_at=now)
    report_path = out_dir / f"reporte-{now:%Y-%m-%d}.md"
    report.save(report_path)
    (out_dir / f"reporte-{now:%Y-%m-%d}.json").write_text(
        batch.model_dump_json(indent=2), encoding="utf-8"
    )

    store.audit("daily_run", "report_generated", str(report_path))
    log.info("reporte: %s | %s", report_path, batch.summarise())

    return {
        "batch": batch,
        "report": report,
        "report_path": report_path,
        "counts": batch.summarise(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Corrida diaria del sistema de señales")
    parser.add_argument("--live", action="store_true", help="usar CoinGecko en lugar de datos sintéticos")
    parser.add_argument("--out", type=Path, default=None, help="carpeta de salida de reportes")
    args = parser.parse_args()

    settings = Settings.from_env()
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(LOGS_DIR / "daily_run.log", encoding="utf-8"),
        ],
    )

    result = run(live=args.live, settings=settings, out_dir=args.out)
    print(f"Reporte: {result['report_path']}")
    print(f"Resumen: {result['counts']}")


if __name__ == "__main__":
    main()
