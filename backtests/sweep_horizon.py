"""Barrido de horizontes con el mismo protocolo y los mismos datos.

Hipótesis del plan (fase 4): quizá 24 h es demasiado corto para que el momentum
persista. Se repite el backtest cambiando solo el horizonte, sin tocar features
ni modelo, para aislar el efecto.

Uso:
    python -m backtests.sweep_horizon --source live --days 1000 --horizons 1,2,3,5,7
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


def _pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def main() -> None:
    parser = argparse.ArgumentParser(description="Barrido de horizontes (misma serie, mismo modelo)")
    parser.add_argument("--source", choices=["demo", "live", "cache"], default="live")
    parser.add_argument("--days", type=int, default=1000)
    parser.add_argument("--assets", default="BTC,ETH")
    parser.add_argument("--horizons", default="1,2,3,5,7")
    parser.add_argument("--train", type=int, default=365)
    parser.add_argument("--test", type=int, default=60)
    parser.add_argument("--step", type=int, default=60)
    parser.add_argument("--threshold", type=float, default=0.55)
    parser.add_argument("--cost-bps", type=float, default=20.0)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    store = Store(Settings.from_env().db_path)
    store.init_schema()
    out_dir = args.out or (ROOT / "var" / "reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(UTC)
    cost_rate = args.cost_bps / 10_000
    horizons = [int(h) for h in args.horizons.split(",") if h.strip()]

    assets = [a.strip().upper() for a in args.assets.split(",") if a.strip()]
    series = {}
    for asset in assets:
        candles, _effective = load_series(asset, source=args.source, days=args.days, store=store)
        series[asset] = candles

    rows: list[dict] = []
    for horizon in horizons:
        all_preds: list[Prediction] = []
        per_asset: dict[str, dict[str, float]] = {}
        for asset, candles in series.items():
            preds = walk_forward_predictions(
                candles,
                train=args.train,
                test=args.test,
                step=args.step,
                horizon=horizon,
            )
            per_asset[asset] = evaluate(
                preds, threshold=args.threshold, cost_rate=cost_rate, horizon=horizon
            )
            all_preds.extend(preds)

        pooled = evaluate(
            all_preds, threshold=args.threshold, cost_rate=cost_rate, horizon=horizon
        )
        assets_positive = sum(1 for m in per_asset.values() if m["net_return"] > 0)
        robust = pooled["positive_windows"] >= 0.6 and assets_positive == len(per_asset)
        rows.append(
            {
                "horizon": horizon,
                "pooled": pooled,
                "per_asset": per_asset,
                "assets_positive": assets_positive,
                "beats_baseline": pooled["log_loss"] < pooled["log_loss_baseline"],
                "calibrated": pooled["ece"] <= 0.05,
                "profitable": pooled["net_return"] > 0 and pooled["trades"] >= 30,
                "robust": robust,
            }
        )

    best = min(rows, key=lambda r: r["pooled"]["log_loss"])

    lines = [
        f"# Barrido de horizontes — {generated_at:%Y-%m-%d}",
        "",
        f"**Fuente:** `{args.source}` · **Días:** {args.days} · **Activos:** {', '.join(assets)} · "
        f"**Ventanas:** train {args.train} / test {args.test} / step {args.step} · "
        f"**Umbral:** {args.threshold:.2f} · **Costo:** {cost_rate * 100:.2f}%",
        "",
        "> Se cambia solo el horizonte de evaluación. Features, modelo, costos y protocolo quedan fijos.",
        "",
        "| Horizonte | Muestras | Log loss | Baseline | Brier | ECE | Trades | Cobertura | Retorno neto | Media/trade | Max DD | Ventanas + | Activos + |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        m = r["pooled"]
        lines.append(
            f"| {r['horizon']}d | {int(m['samples'])} | {m['log_loss']:.4f} | "
            f"{m['log_loss_baseline']:.4f} | {m['brier']:.4f} | {m['ece']:.4f} | "
            f"{int(m['trades'])} | {m['coverage'] * 100:.1f}% | {_pct(m['net_return'])} | "
            f"{_pct(m['avg_net_per_trade'])} | {m['max_drawdown'] * 100:.1f}% | "
            f"{m['positive_windows'] * 100:.0f}% | {r['assets_positive']}/{len(per_asset)} |"
        )

    lines += [
        "",
        "## Lectura",
        "",
        f"- Mejor log loss: **{best['horizon']}d** ({best['pooled']['log_loss']:.4f} vs "
        f"baseline {best['pooled']['log_loss_baseline']:.4f}).",
        "- Ningún horizonte se declara bueno por ganar en una sola métrica: el gate exige "
        "superar baseline, estar calibrado, ser rentable tras costos y ser robusto.",
        "",
        "| Horizonte | ¿Supera baseline? | ¿Calibrado? | ¿Rentable? | ¿Robusto? |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            f"| {r['horizon']}d | {'✅' if r['beats_baseline'] else '❌'} | "
            f"{'✅' if r['calibrated'] else '❌'} | {'✅' if r['profitable'] else '❌'} | "
            f"{'✅' if r['robust'] else '❌'} |"
        )

    lines += [
        "",
        "## Por activo",
        "",
        "| Horizonte | Activo | Log loss | ECE | Trades | Retorno neto |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        for asset, m in r["per_asset"].items():
            lines.append(
                f"| {r['horizon']}d | {asset} | {m['log_loss']:.4f} | {m['ece']:.4f} | "
                f"{int(m['trades'])} | {_pct(m['net_return'])} |"
            )

    lines += [
        "",
        "## Reproducir",
        "",
        f"```bash\npython -m backtests.sweep_horizon --source {args.source} --days {args.days} "
        f"--assets {','.join(assets)} --horizons {args.horizons} --train {args.train} "
        f"--test {args.test} --step {args.step} --threshold {args.threshold}\n```",
        "",
    ]

    md_path = out_dir / f"barrido-horizontes-{generated_at:%Y-%m-%d}.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    (out_dir / f"barrido-horizontes-{generated_at:%Y-%m-%d}.json").write_text(
        json.dumps(
            {
                "generated_at": generated_at.isoformat(),
                "source": args.source,
                "days": args.days,
                "assets": assets,
                "threshold": args.threshold,
                "horizons": [
                    {
                        "horizon": r["horizon"],
                        "pooled": r["pooled"],
                        "per_asset": r["per_asset"],
                        "gates": {
                            "beats_baseline": r["beats_baseline"],
                            "calibrated": r["calibrated"],
                            "profitable": r["profitable"],
                            "robust": r["robust"],
                        },
                    }
                    for r in rows
                ],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store.audit("sweep_horizon", "run", f"horizons={args.horizons} days={args.days}")

    print(f"Reporte: {md_path}")
    for r in rows:
        m = r["pooled"]
        print(
            f"{r['horizon']}d | logloss {m['log_loss']:.4f} (base {m['log_loss_baseline']:.4f}) | "
            f"ECE {m['ece']:.4f} | trades {int(m['trades'])} | net {_pct(m['net_return'])} | "
            f"ventanas+ {m['positive_windows'] * 100:.0f}% | activos+ {r['assets_positive']}/{len(per_asset)}"
        )


if __name__ == "__main__":
    main()