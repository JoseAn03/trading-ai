"""Backtest reproducible: calibración + retorno neto sobre datos reales.

Uso:
    python -m backtests.run_backtest --source live --days 1000 --assets BTC,ETH
    python -m backtests.run_backtest --source cache --days 1000
    python -m backtests.run_backtest --source demo          # sintético, solo humo

Salida:
    var/reports/backtest-YYYY-MM-DD.md
    var/reports/backtest-YYYY-MM-DD.json
    var/reports/backtest-YYYY-MM-DD-<ACTIVO>.csv   (predicciones auditables)

Advertencia: un backtest no prueba el futuro. Prueba si el método, con estos
datos y estos costos, habría tenido ventaja. Nada más.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from backtests.metrics import (
    CalibrationBin,
    brier_score,
    buy_and_hold,
    calibration_table,
    expected_calibration_error,
    log_loss,
    strategy_stats,
)
from backtests.walk_forward import Prediction, walk_forward_predictions
from src.config.settings import ROOT, Settings
from src.data.adapters.base import DataProviderError, StaticAdapter
from src.data.adapters.binance import BinanceAdapter
from src.data.store import Store
from src.schemas.market import Candle


@dataclass
class AssetResult:
    asset: str
    source: str
    candles: int
    period_start: str
    period_end: str
    predictions: list[Prediction]

    @property
    def probs(self) -> list[float]:
        return [p.prob for p in self.predictions]

    @property
    def labels(self) -> list[int]:
        return [p.label for p in self.predictions]

    @property
    def returns(self) -> list[float]:
        return [p.next_return for p in self.predictions]

    @property
    def windows(self) -> int:
        return len({p.window for p in self.predictions})


def load_series(
    asset: str, *, source: str, days: int, store: Store
) -> tuple[list[Candle], str]:
    """Devuelve velas y la fuente efectiva (puede degradar de live a cache)."""
    if source == "live":
        try:
            candles = BinanceAdapter().fetch_daily_candles(asset, days=days)
            store.save_candles(candles, source="binance")
            return candles, "binance"
        except (DataProviderError, Exception) as exc:  # noqa: BLE001
            print(f"[aviso] live falló para {asset}: {exc}. Intento caché...")
            source = "cache"

    if source == "cache":
        cached = store.load_candles(asset, limit=days, source="binance")
        if cached:
            return cached, "cache(binance)"
        print(f"[aviso] sin caché real para {asset}; uso datos sintéticos.")
        source = "demo"

    demo = StaticAdapter(days=max(days, 120)).fetch_daily_candles(asset, days=max(days, 120))
    return demo, "demo"


def evaluate(
    predictions: list[Prediction], *, threshold: float, cost_rate: float, horizon: int = 1
) -> dict[str, float]:
    probs = [p.prob for p in predictions]
    labels = [p.label for p in predictions]
    returns = [p.next_return for p in predictions]

    by_window: dict[tuple[str, int], list[Prediction]] = {}
    for p in predictions:
        by_window.setdefault((p.asset, p.window), []).append(p)

    # La estrategia opera sin solapar posiciones: una entrada cada `horizon` días.
    selected: list[Prediction] = []
    for group in by_window.values():
        selected.extend(group[:: max(1, horizon)])

    strat = strategy_stats(
        [p.prob for p in selected],
        [p.next_return for p in selected],
        threshold=threshold,
        cost_rate=cost_rate,
    )
    base = [0.5] * len(labels)

    window_nets = [
        strategy_stats(
            [p.prob for p in group[:: max(1, horizon)]],
            [p.next_return for p in group[:: max(1, horizon)]],
            threshold=threshold,
            cost_rate=cost_rate,
        ).total_net_return
        for group in by_window.values()
    ]

    return {
        "samples": float(len(labels)),
        "windows": float(len(by_window)),
        "positive_windows": (
            sum(1 for n in window_nets if n > 0) / len(window_nets) if window_nets else 0.0
        ),
        "log_loss": log_loss(probs, labels),
        "log_loss_baseline": log_loss(base, labels),
        "brier": brier_score(probs, labels),
        "accuracy": sum((p >= 0.5) == bool(y) for p, y in zip(probs, labels, strict=True))
        / len(labels),
        "ece": expected_calibration_error(probs, labels, bins=10),
        "mean_prob": sum(probs) / len(probs),
        "base_rate": sum(labels) / len(labels),
        "trades": float(strat.trades),
        "coverage": strat.trades / len(selected) if selected else 0.0,
        "net_return": strat.total_net_return,
        "avg_net_per_trade": strat.avg_net_per_trade,
        "win_rate": strat.win_rate,
        "profit_factor": strat.profit_factor,
        "max_drawdown": strat.max_drawdown,
        "buy_and_hold": buy_and_hold(returns),
    }


def _fmt(value: float, kind: str = "num") -> str:
    if kind == "pct":
        return f"{value * 100:+.2f}%"
    if kind == "pct0":
        return f"{value * 100:.1f}%"
    if kind == "x":
        return "∞" if value == float("inf") else f"{value:.2f}"
    return f"{value:.4f}"


def _calibration_md(table: list[CalibrationBin]) -> str:
    lines = ["| Rango | Muestras | Prob. media | Acierto real | Desvío |", "| --- | --- | --- | --- | --- |"]
    for b in table:
        if b.count == 0:
            lines.append(f"| {b.lower:.1f}–{b.upper:.1f} | 0 | — | — | — |")
        else:
            lines.append(
                f"| {b.lower:.1f}–{b.upper:.1f} | {b.count} | {b.mean_pred:.3f} | "
                f"{b.observed:.3f} | {b.observed - b.mean_pred:+.3f} |"
            )
    return "\n".join(lines)


def _verdict(
    metrics: dict[str, float], *, assets_positive: int, assets_total: int
) -> tuple[bool, bool, bool, bool, str]:
    beats = metrics["log_loss"] < metrics["log_loss_baseline"]
    calibrated = metrics["ece"] <= 0.05
    profitable = metrics["net_return"] > 0 and metrics["trades"] >= 30
    robust = metrics["positive_windows"] >= 0.6 and assets_positive == assets_total

    if profitable and beats and calibrated and robust:
        text = (
            "Pasa los cuatro filtros mínimos. Aun así, exige paper trading prolongado "
            "antes de considerar capital real."
        )
    elif not beats:
        text = (
            "No supera al baseline trivial: el modelo no aporta información útil. "
            "Cualquier ganancia es ruido o suerte de una muestra concreta."
        )
    elif not profitable:
        text = "No gana después de costos: la ventaja estadística no cubre la fricción."
    elif not calibrated:
        text = "Gana en esta muestra pero mal calibrado: no se puede confiar en el tamaño de la probabilidad."
    elif not robust:
        text = (
            "Ganancia concentrada: depende de pocos activos o ventanas. No pasa el gate "
            "de robustez del plan; no se promociona a paper trading todavía."
        )
    else:
        text = "Resultado mixto. Documentar y repetir en otro periodo antes de concluir nada."
    return beats, calibrated, profitable, robust, text


def build_report(
    results: list[AssetResult],
    *,
    args: argparse.Namespace,
    cost_rate: float,
    generated_at: datetime,
) -> tuple[str, dict]:
    per_asset = {}
    pooled: list[Prediction] = []
    for r in results:
        per_asset[r.asset] = evaluate(
            r.predictions, threshold=args.threshold, cost_rate=cost_rate, horizon=args.horizon
        )
        pooled.extend(r.predictions)

    pooled_metrics = (
        evaluate(pooled, threshold=args.threshold, cost_rate=cost_rate, horizon=args.horizon)
        if pooled
        else {}
    )
    assets_positive = sum(1 for m in per_asset.values() if m["net_return"] > 0)
    if pooled:
        beats, calibrated, profitable, robust, verdict = _verdict(
            pooled_metrics, assets_positive=assets_positive, assets_total=len(per_asset)
        )
    else:
        beats = calibrated = profitable = robust = False
        verdict = "Sin datos."

    lines: list[str] = [
        f"# Backtest walk-forward — {generated_at:%Y-%m-%d}",
        "",
        f"**Fuente:** `{args.source}` · **Periodo:** {args.days} días solicitados · "
        f"**Horizonte:** {args.horizon} día(s) · **Umbral:** {args.threshold:.2f} · "
        f"**Costo ida y vuelta:** {cost_rate * 100:.2f}% · "
        f"**Ventanas:** train {args.train} / test {args.test} / step {args.step}",
        "",
        "> Un backtest no predice el futuro. Mide si este método, con estos datos y "
        "estos costos, habría tenido ventaja. No es asesoría financiera.",
        "",
        "## Resumen por activo",
        "",
        "| Activo | Fuente | Velas | Muestras | Ventanas | Ventanas + | Log loss | Brier | ECE | Trades | Cobertura | Retorno neto | Media/trade | Max DD |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for r in results:
        m = per_asset[r.asset]
        lines.append(
            f"| {r.asset} | {r.source} | {r.candles} | {int(m['samples'])} | {r.windows} | "
            f"{m['positive_windows'] * 100:.0f}% | "
            f"{m['log_loss']:.4f} | {m['brier']:.4f} | {m['ece']:.4f} | {int(m['trades'])} | "
            f"{m['coverage'] * 100:.1f}% | {_fmt(m['net_return'], 'pct')} | "
            f"{_fmt(m['avg_net_per_trade'], 'pct')} | {m['max_drawdown'] * 100:.1f}% |"
        )

    lines += [
        "",
        "## Agregado (todos los activos)",
        "",
        f"- Muestras fuera de muestra: **{int(pooled_metrics['samples'])}**",
        f"- Log loss: **{pooled_metrics['log_loss']:.4f}** vs baseline 50/50 "
        f"**{pooled_metrics['log_loss_baseline']:.4f}**",
        f"- Brier: **{pooled_metrics['brier']:.4f}** · ECE: **{pooled_metrics['ece']:.4f}**",
        f"- Prob. media predicha: **{pooled_metrics['mean_prob']:.3f}** vs tasa base real "
        f"**{pooled_metrics['base_rate']:.3f}**",
        f"- Trades: **{int(pooled_metrics['trades'])}** "
        f"(cobertura {pooled_metrics['coverage'] * 100:.1f}%) · win rate "
        f"**{pooled_metrics['win_rate'] * 100:.1f}%** · profit factor "
        f"**{_fmt(pooled_metrics['profit_factor'], 'x')}**",
        f"- Ventanas positivas: **{pooled_metrics['positive_windows'] * 100:.0f}%** · "
        f"activos con retorno positivo: **{assets_positive}/{len(per_asset)}**",
        f"- Retorno neto acumulado: **{_fmt(pooled_metrics['net_return'], 'pct')}** · "
        f"media por trade **{_fmt(pooled_metrics['avg_net_per_trade'], 'pct')}** · "
        f"max drawdown **{pooled_metrics['max_drawdown'] * 100:.1f}%**",
        f"- Buy & hold del mismo periodo (referencia): "
        f"**{_fmt(pooled_metrics['buy_and_hold'], 'pct')}**",
        "",
        "## Veredicto",
        "",
        "| Filtro | Resultado |",
        "| --- | --- |",
        f"| Supera baseline (log loss) | {'✅ sí' if beats else '❌ no'} |",
        f"| Calibrado (ECE ≤ 0.05) | {'✅ sí' if calibrated else '❌ no'} |",
        f"| Rentable tras costos (≥30 trades) | {'✅ sí' if profitable else '❌ no'} |",
        f"| Robusto (≥60% ventanas + y todos los activos) | {'✅ sí' if robust else '❌ no'} |",
        "",
        f"**{verdict}**",
        "",
        "## Calibración por activo",
        "",
    ]

    for r in results:
        lines += [f"### {r.asset}", "", _calibration_md(calibration_table(r.probs, r.labels, bins=5)), ""]

    lines += [
        "## Limitaciones de esta medición",
        "",
        "- Pares **USDT** asumidos como USD; el desvío suele ser pequeño pero existe.",
        "- Costos **fijos** (comisión + slippage); no modelan profundidad ni impacto de mercado.",
        "- Solo **largos**; sin funding, sin apalancamiento, sin short.",
        "- El baseline es una regresión logística con 5 features; un modelo más complejo "
        "debe ganarle a **esto** después de costos.",
        "- Un periodo favorable no es evidencia de ventaja persistente.",
        "",
        "## Reproducibilidad",
        "",
        f"```bash\npython -m backtests.run_backtest --source {args.source} --days {args.days} "
        f"--assets {','.join(r.asset for r in results)} --horizon {args.horizon} "
        f"--threshold {args.threshold} "
        f"--train {args.train} --test {args.test} --step {args.step}\n```",
        "",
        "Predicciones completas: `backtest-YYYY-MM-DD-<ACTIVO>.csv` en esta misma carpeta.",
        "",
    ]

    payload = {
        "generated_at": generated_at.isoformat(),
        "source": args.source,
        "days": args.days,
        "threshold": args.threshold,
        "horizon": args.horizon,
        "cost_rate": cost_rate,
        "windows": {"train": args.train, "test": args.test, "step": args.step},
        "assets": {
            r.asset: {
                "source": r.source,
                "candles": r.candles,
                "windows": r.windows,
                "metrics": per_asset[r.asset],
            }
            for r in results
        },
        "pooled": pooled_metrics,
        "verdict": {
            "beats_baseline": beats,
            "calibrated": calibrated,
            "profitable": profitable,
            "robust": robust,
            "assets_positive": assets_positive,
            "text": verdict,
        },
    }
    return "\n".join(lines), payload


def _write_predictions_csv(path: Path, predictions: list[Prediction]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = ["asset,window,horizon,timestamp,probability,label,next_return"]
    rows += [
        f"{p.asset},{p.window},{p.horizon},{p.ts.isoformat()},{p.prob:.6f},{p.label},{p.next_return:.8f}"
        for p in predictions
    ]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest walk-forward con calibración y costos")
    parser.add_argument("--source", choices=["demo", "live", "cache"], default="demo")
    parser.add_argument("--days", type=int, default=1000)
    parser.add_argument("--assets", default="BTC,ETH")
    parser.add_argument("--horizon", type=int, default=1, help="días hasta el resultado evaluado")
    parser.add_argument("--threshold", type=float, default=0.55)
    parser.add_argument("--train", type=int, default=365)
    parser.add_argument("--test", type=int, default=60)
    parser.add_argument("--step", type=int, default=60)
    parser.add_argument("--cost-bps", type=float, default=20.0, help="costo ida y vuelta total")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    settings = Settings.from_env()
    store = Store(settings.db_path)
    store.init_schema()

    cost_rate = args.cost_bps / 10_000
    out_dir = args.out or (ROOT / "var" / "reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(UTC)

    results: list[AssetResult] = []
    for asset in [a.strip().upper() for a in args.assets.split(",") if a.strip()]:
        candles, effective_source = load_series(
            asset, source=args.source, days=args.days, store=store
        )
        if len(candles) < args.train + args.test + 40:
            print(f"[aviso] {asset}: pocas velas ({len(candles)}); se omite.")
            continue
        try:
            predictions = walk_forward_predictions(
                candles, train=args.train, test=args.test, step=args.step, horizon=args.horizon
            )
        except ValueError as exc:
            print(f"[aviso] {asset}: {exc}")
            continue

        result = AssetResult(
            asset=asset,
            source=effective_source,
            candles=len(candles),
            period_start=candles[0].ts.date().isoformat(),
            period_end=candles[-1].ts.date().isoformat(),
            predictions=predictions,
        )
        results.append(result)
        _write_predictions_csv(out_dir / f"backtest-{generated_at:%Y-%m-%d}-{asset}.csv", predictions)
        store.audit(
            "backtest",
            "run",
            f"asset={asset} source={effective_source} days={args.days} preds={len(predictions)}",
        )

    if not results:
        raise SystemExit("No se pudo evaluar ningún activo. Revisa --days, --source o la red.")

    markdown, payload = build_report(
        results, args=args, cost_rate=cost_rate, generated_at=generated_at
    )
    md_path = out_dir / f"backtest-{generated_at:%Y-%m-%d}.md"
    md_path.write_text(markdown, encoding="utf-8")
    (out_dir / f"backtest-{generated_at:%Y-%m-%d}.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"Reporte: {md_path}")
    verdict = payload["verdict"]
    print(f"Veredicto: beats_baseline={verdict['beats_baseline']} "
          f"calibrated={verdict['calibrated']} profitable={verdict['profitable']} "
          f"robust={verdict['robust']}")


if __name__ == "__main__":
    main()
