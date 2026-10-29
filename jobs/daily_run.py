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

import httpx

from src.config.settings import LOGS_DIR, ROOT, Settings
from src.data.adapters.base import DataProviderError, MarketDataAdapter, StaticAdapter
from src.data.ingestion import ingest_asset
from src.data.store import Store
from src.delivery.telegram import deliver
from src.paper_trading.shadow import (
    SignalToOpen,
    close_at,
    open_from_signals,
    paper_stats,
    resolve_due,
)
from src.paper_trading.state import portfolio_state_from_store
from src.quant.features import InsufficientData, compute_features
from src.quant.risk import CircuitBreaker, RiskDecision, RiskLimits
from src.quant.signals import build_signal, rank_signals
from src.reporting.daily_report import build_report
from src.reporting.explain import explain_daily
from src.schemas.enums import Signal
from src.schemas.market import Candle, DataQualityReport
from src.schemas.signal import SignalBatch, SignalContract

log = logging.getLogger("daily_run")


def _adapter(live: bool) -> MarketDataAdapter:
    if not live:
        return StaticAdapter()
    from src.data.adapters.binance import BinanceAdapter

    return BinanceAdapter()


def run(
    *,
    live: bool = False,
    settings: Settings | None = None,
    out_dir: Path | None = None,
    risk_limits: RiskLimits | None = None,
) -> dict:
    settings = settings or Settings.from_env()
    out_dir = out_dir or (ROOT / "var" / "reports")
    now = datetime.now(UTC)

    store = Store(settings.db_path)
    store.init_schema()

    adapter = _adapter(live)
    breaker = CircuitBreaker(
        risk_limits or RiskLimits(expected_interval_hours=settings.expected_interval_hours)
    )

    signals: list[SignalContract] = []
    decisions: dict[str, RiskDecision] = {}
    candles_by_asset: dict[str, list[Candle]] = {}
    qualities: dict[str, DataQualityReport] = {}
    live_prices: dict[str, float] = {}
    entry_sources: dict[str, str] = {}

    for asset in settings.assets:
        result = ingest_asset(asset, adapter, settings=settings, store=store, now=now)
        candles_by_asset[asset] = result.candles
        qualities[asset] = result.quality
        if not result.candles:
            continue

        try:
            quote = adapter.fetch_quote(asset)
            live_prices[asset] = quote.close
            entry_sources[asset] = "quote_at_run"
        except (DataProviderError, httpx.HTTPError) as exc:
            # Sin cotización viva se usa el último cierre conocido y se marca la
            # limitación; el paper trade queda auditado, no se inventa un precio.
            log.warning("%s: cotización viva no disponible (%s); uso último cierre", asset, exc)
            live_prices[asset] = result.candles[-1].close
            entry_sources[asset] = "last_close_fallback"

    # Cerrar vencidos antes de reconstruir el estado de cartera: el equity y las
    # pérdidas de hoy deben incluir los cierres de esta misma corrida.
    resolved = resolve_due(store, candles_by_asset, now=now)
    kill_switch = settings.kill_switch or store.control_flag("kill_switch")
    state = portfolio_state_from_store(store, now=now, kill_switch=kill_switch)

    for asset in settings.assets:
        candles = candles_by_asset.get(asset, [])
        if len(candles) < settings.min_history_days:
            log.warning("%s: histórico insuficiente (%s velas)", asset, len(candles))
            continue

        try:
            features = compute_features(candles, now=now)
        except InsufficientData as exc:
            log.warning("%s: %s", asset, exc)
            continue

        signal = build_signal(
            features,
            qualities[asset],
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
        elif decision.allowed and signal.is_actionable:
            # El tamaño autorizado por el breaker es el que se ejecuta, no el
            # tamaño que pidió el modelo de señal.
            signal = signal.model_copy(
                update={
                    "risk": signal.risk.model_copy(
                        update={"suggested_size_pct": decision.suggested_size_pct}
                    )
                }
            )
            state.exposure_by_asset_pct[asset] = (
                state.exposure_by_asset_pct.get(asset, 0.0) + decision.suggested_size_pct
            )
            state.trades_today += 1

        signals.append(signal)

    ranked = rank_signals(signals)
    signal_ids: dict[str, int] = {}
    for s in ranked:
        signal_ids[s.asset] = store.save_signal(s)

    candidates: list[SignalToOpen] = []
    for s in ranked:
        if not s.is_actionable:
            continue
        frozen = close_at(candles_by_asset.get(s.asset, []), s.data_timestamp)
        if frozen is None:
            log.warning("%s: sin cierre para congelar la entrada; no se abre paper trade", s.asset)
            continue
        execution = live_prices.get(s.asset) or frozen
        candidates.append(
            SignalToOpen(
                signal_id=signal_ids.get(s.asset, 0),
                asset=s.asset,
                horizon=s.horizon,
                data_timestamp=s.data_timestamp,
                entry_price=execution,
                size_pct=s.risk.suggested_size_pct,
                cost_rate=s.costs.total_rate,
                model_version=s.model_version,
                signal_price=frozen,
                entry_price_source=entry_sources.get(s.asset, "last_close_fallback"),
                opened_at=now,
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

    store.audit("daily_run", "report_generated", report_path.name)

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
        "reporte: %s | %s | paper abiertos=%s cerrados=%s equity=%.4f | "
        "explicacion=%s motivo=%s costo_usd=%.6f",
        report_path,
        batch.summarise(),
        stats.open_trades,
        stats.trades,
        stats.equity,
        explanation.kind,
        explanation.fallback_reason or "ok",
        explanation.cost_usd,
    )

    return {
        "batch": batch,
        "report": report,
        "report_path": report_path,
        "counts": batch.summarise(),
        "decisions": decisions,
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
