"""Corrida diaria end-to-end: ingesta -> features -> señal -> riesgo -> reporte -> paper.

Uso:
    python -m jobs.daily_run                # demo con datos sintéticos
    python -m jobs.daily_run --live         # Binance (requiere red)
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from src.config.settings import LOGS_DIR, ROOT, Settings
from src.data.adapters.base import MarketDataAdapter, StaticAdapter
from src.data.ingestion import ingest_asset
from src.data.store import Store
from src.delivery.telegram import deliver
from src.paper_trading.engine import PaperTradingEngine
from src.paper_trading.shadow import (
    SignalToOpen,
    close_at,
    open_from_signals,
    paper_stats,
    resolve_due,
)
from src.quant.features import InsufficientData, compute_features
from src.quant.risk import CircuitBreaker, RiskDecision
from src.quant.signals import build_signal, rank_signals
from src.reporting.daily_report import build_report
from src.reporting.explain import explain_daily
from src.schemas.enums import Signal
from src.schemas.market import Candle
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
    candles_by_asset: dict[str, list[Candle]] = {}

    for asset in settings.assets:
        result = ingest_asset(asset, adapter, settings=settings, store=store, now=now)
        candles_by_asset[asset] = result.candles
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
    signal_ids: dict[str, int] = {}
    for s in ranked:
        signal_ids[s.asset] = store.save_signal(s)

    # Paper trading en sombra: primero cerrar lo vencido, luego abrir lo nuevo.
    resolved = resolve_due(store, candles_by_asset, now=now)

    candidates: list[SignalToOpen] = []
    for s in ranked:
        if not s.is_actionable:
            continue
        entry = close_at(candles_by_asset.get(s.asset, []), s.data_timestamp)
        if entry is None:
            log.warning("%s: sin cierre para congelar la entrada; no se abre paper trade", s.asset)
            continue
        candidates.append(
            SignalToOpen(
                signal_id=signal_ids.get(s.asset, 0),
                asset=s.asset,
                horizon=s.horizon,
                data_timestamp=s.data_timestamp,
                entry_price=entry,
                size_pct=s.risk.suggested_size_pct,
                cost_rate=s.costs.total_rate,
                model_version=s.model_version,
            )
        )
    opened = open_from_signals(store, candidates)
    stats = paper_stats(store)
    store.save_performance_snapshot(
        equity=stats.equity, drawdown=stats.max_drawdown_pct, trades=stats.trades
    )

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

    explanation = explain_daily(
        batch=batch,
        decisions=decisions,
        paper=stats,
        store=store,
        settings=settings,
        generated_at=now,
    )
    if settings.telegram_enabled:
        delivered = deliver(explanation.subject, explanation.body, settings)
        store.audit(
            "daily_run",
            "telegram",
            f"explanation={explanation.id} delivered={delivered}",
        )

    log.info(
        "reporte: %s | %s | paper abiertos=%s cerrados=%s equity=%.4f | explicacion=%s",
        report_path,
        batch.summarise(),
        stats.open_trades,
        stats.trades,
        stats.equity,
        explanation.kind,
    )

    return {
        "batch": batch,
        "report": report,
        "report_path": report_path,
        "counts": batch.summarise(),
        "paper": {
            "opened": opened,
            "resolved": resolved,
            "stats": stats,
        },
        "explanation": explanation,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Corrida diaria del sistema de señales")
    parser.add_argument("--live", action="store_true", help="usar Binance en lugar de datos sintéticos")
    parser.add_argument("--out", type=Path, default=None, help="carpeta de salida de reportes")
    parser.add_argument(
        "--llm",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="activar/desactivar la explicacion con LLM para esta corrida",
    )
    args = parser.parse_args()

    settings = Settings.from_env()
    if args.llm is not None:
        settings = replace(settings, llm_enabled=args.llm)
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
    paper = result["paper"]["stats"]
    print(
        f"Paper: abiertos={paper.open_trades} cerrados={paper.trades} "
        f"equity={paper.equity:.4f} retorno={paper.total_return_pct:+.2f}%"
    )
    explanation = result["explanation"]
    print(
        f"Explicacion: {explanation.kind} "
        f"(motivo={explanation.fallback_reason or 'ok'})"
    )


if __name__ == "__main__":
    main()
