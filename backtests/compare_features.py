"""Comparacion A/B de sets de features sobre las mismas muestras y ventanas.

Hipotesis (fase 4): las features de regimen de largo plazo (tendencia 50/200,
distancia a SMA200, ratio de volatilidad 20/60, drawdown 60d) aportan informacion
que las 5 features de corto plazo no tienen.

Regla de honestidad: se comparan con **identico** periodo, ventanas, umbral,
costos y modelo. Solo cambia el set de features. Si no mejora el log loss y no es
rentable y robusto a la vez, la hipotesis se rechaza.

Uso:
    python -m backtests.compare_features --source live --days 1500 \
        --assets BTC,ETH --feature-sets v0.1,v0.2 --min-history 201
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from backtests.run_backtest import evaluate, load_series
from backtests.walk_forward import Prediction, walk_forward_predictions
from src.config.settings import ROOT, Settings
from src.data.store import Store
from src.quant.features import FEATURE_SETS


def _pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def _gates(metrics: dict[str, float], assets_positive: int, assets_total: int) -> dict[str, bool]:
    return {
        "beats_baseline": metrics["log_loss"] < metrics["log_loss_baseline"],
        "calibrated": metrics["ece"] <= 0.05,
        "profitable": metrics["net_return"] > 0 and metrics["trades"] >= 30,
        "robust": metrics["positive_windows"] >= 0.6 and assets_positive == assets_total,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Comparacion A/B de sets de features")
    parser.add_argument("--source", choices=["demo", "live", "cache"], default="live")
    parser.add_argument("--days", type=int, default=1500)
    parser.add_argument("--assets", default="BTC,ETH")
    parser.add_argument("--feature-sets", default="v0.1,v0.2")
    parser.add_argument("--horizon", type=int, default=1)
    parser.add_argument("--train", type=int, default=365)
    parser.add_argument("--test", type=int, default=60)
    parser.add_argument("--step", type=int, default=60)
    parser.add_argument("--threshold", type=float, default=0.55)
    parser.add_argument("--cost-bps", type=float, default=20.0)
    parser.add_argument(
        "--min-history",
        type=int,
        default=201,
        help="historia minima forzada; iguala el periodo entre sets",
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    store = Store(Settings.from_env().db_path)
    store.init_schema()
    out_dir = args.out or (ROOT / "var" / "reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(UTC)
    cost_rate = args.cost_bps / 10_000

    assets = [a.strip().upper() for a in args.assets.split(",") if a.strip()]
    sets = [s.strip() for s in args.feature_sets.split(",") if s.strip()]
    unknown = [s for s in sets if s not in FEATURE_SETS]
    if unknown:
        raise SystemExit(f"feature sets desconocidos: {unknown}; validos: {sorted(FEATURE_SETS)}")

    series: dict[str, list] = {}
    for asset in assets:
        candles, _source = load_series(asset, source=args.source, days=args.days, store=store)
        series[asset] = candles

    rows: list[dict] = []
    for feature_set in sets:
        per_asset: dict[str, dict[str, float]] = {}
        pooled: list[Prediction] = []
        for asset, candles in series.items():
            preds = walk_forward_predictions(
                candles,
                train=args.train,
                test=args.test,
                step=args.step,
                horizon=args.horizon,
                feature_set=feature_set,
                min_history=args.min_history,
            )
            per_asset[asset] = evaluate(
                preds, threshold=args.threshold, cost_rate=cost_rate, horizon=args.horizon
            )
            pooled.extend(preds)

        metrics = evaluate(
            pooled, threshold=args.threshold, cost_rate=cost_rate, horizon=args.horizon
        )
        assets_positive = sum(1 for m in per_asset.values() if m["net_return"] > 0)
        rows.append(
            {
                "feature_set": feature_set,
                "features": list(FEATURE_SETS[feature_set]),
                "pooled": metrics,
                "per_asset": per_asset,
                "assets_positive": assets_positive,
                "gates": _gates(metrics, assets_positive, len(per_asset)),
            }
        )

    base = rows[0]
    lines = [
        f"# Comparacion de features — {generated_at:%Y-%m-%d}",
        "",
        f"**Fuente:** `{args.source}` · **Dias:** {args.days} · **Activos:** {', '.join(assets)} · "
        f"**Horizonte:** {args.horizon}d · **Ventanas:** train {args.train} / test {args.test} / "
        f"step {args.step} · **Umbral:** {args.threshold:.2f} · "
        f"**Costo:** {cost_rate * 100:.2f}% · **Historia minima:** {args.min_history}",
        "",
        "> Mismas muestras, mismas ventanas, mismo modelo. Solo cambia el set de features.",
        "",
        "## Resumen",
        "",
        "| Set | Features | Muestras | Log loss | Brier | ECE | Trades | Retorno neto | "
        "Media/trade | Max DD | Ventanas + | Activos + |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        m = r["pooled"]
        lines.append(
            f"| `{r['feature_set']}` | {len(r['features'])} | {int(m['samples'])} | "
            f"{m['log_loss']:.4f} | {m['brier']:.4f} | {m['ece']:.4f} | "
            f"{int(m['trades'])} | {_pct(m['net_return'])} | "
            f"{_pct(m['avg_net_per_trade'])} | {m['max_drawdown'] * 100:.1f}% | "
            f"{m['positive_windows'] * 100:.0f}% | {r['assets_positive']}/{len(r['per_asset'])} |"
        )

    lines += [
        "",
        "## Diferencia contra el set base",
        "",
        "| Set | Delta log loss | Delta retorno neto | Delta ECE |",
        "| --- | --- | --- | --- |",
    ]
    for r in rows[1:]:
        m, b = r["pooled"], base["pooled"]
        lines.append(
            f"| `{r['feature_set']}` | {m['log_loss'] - b['log_loss']:+.4f} | "
            f"{_pct(m['net_return'] - b['net_return'])} | {m['ece'] - b['ece']:+.4f} |"
        )

    lines += [
        "",
        "## Gates",
        "",
        "| Set | Supera baseline | Calibrado | Rentable | Robusto |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        g = r["gates"]
        lines.append(
            f"| `{r['feature_set']}` | {'✅' if g['beats_baseline'] else '❌'} | "
            f"{'✅' if g['calibrated'] else '❌'} | {'✅' if g['profitable'] else '❌'} | "
            f"{'✅' if g['robust'] else '❌'} |"
        )

    lines += [
        "",
        "## Por activo",
        "",
        "| Set | Activo | Log loss | ECE | Trades | Retorno neto | Ventanas + |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        for asset, m in r["per_asset"].items():
            lines.append(
                f"| `{r['feature_set']}` | {asset} | {m['log_loss']:.4f} | {m['ece']:.4f} | "
                f"{int(m['trades'])} | {_pct(m['net_return'])} | "
                f"{m['positive_windows'] * 100:.0f}% |"
            )

    best = min(rows, key=lambda r: r["pooled"]["log_loss"])
    passed = [r for r in rows if all(r["gates"].values())]
    passed_text = ", ".join("`" + r["feature_set"] + "`" for r in passed) if passed else "ninguno"
    lines += [
        "",
        "## Lectura",
        "",
        f"- Mejor log loss: `{best['feature_set']}` ({best['pooled']['log_loss']:.4f} vs "
        f"baseline {best['pooled']['log_loss_baseline']:.4f}).",
        f"- Sets que pasan los cuatro gates: {passed_text}.",
        "",
        "**Criterio:** un set nuevo solo se adopta si mejora el log loss del set base "
        "**y** pasa calibracion, rentabilidad y robustez. Mejorar solo el retorno puede "
        "ser suerte; mejorar solo el log loss puede no cubrir costos.",
        "",
        "## Reproducir",
        "",
        f"```bash\npython -m backtests.compare_features --source {args.source} "
        f"--days {args.days} --assets {','.join(assets)} "
        f"--feature-sets {','.join(sets)} --min-history {args.min_history} "
        f"--horizon {args.horizon} --threshold {args.threshold}\n```",
        "",
    ]

    md_path = out_dir / f"comparacion-features-{generated_at:%Y-%m-%d}.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    (out_dir / f"comparacion-features-{generated_at:%Y-%m-%d}.json").write_text(
        json.dumps(
            {
                "generated_at": generated_at.isoformat(),
                "source": args.source,
                "days": args.days,
                "assets": assets,
                "horizon": args.horizon,
                "threshold": args.threshold,
                "cost_rate": cost_rate,
                "min_history": args.min_history,
                "sets": [
                    {
                        "feature_set": r["feature_set"],
                        "features": r["features"],
                        "pooled": r["pooled"],
                        "per_asset": r["per_asset"],
                        "gates": r["gates"],
                    }
                    for r in rows
                ],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store.audit("compare_features", "run", f"sets={args.feature_sets} days={args.days}")

    print(f"Reporte: {md_path}")
    for r in rows:
        m = r["pooled"]
        print(
            f"{r['feature_set']} | logloss {m['log_loss']:.4f} "
            f"(base {m['log_loss_baseline']:.4f}) | ECE {m['ece']:.4f} | "
            f"trades {int(m['trades'])} | net {_pct(m['net_return'])} | "
            f"ventanas+ {m['positive_windows'] * 100:.0f}% | "
            f"activos+ {r['assets_positive']}/{len(r['per_asset'])}"
        )


if __name__ == "__main__":
    main()
