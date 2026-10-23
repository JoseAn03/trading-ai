"""KRONOS-01: experimento zero-shot con el mismo protocolo de la Fase 2.

Se evalúa en desarrollo con los mismos folds que el baseline logístico:
train 365 / test 60 / step 60, historia mínima 201, costo 20 bps y holdout
final de 300 velas. Kronos no se entrena ni se ajusta con estos datos.

El holdout se ejecuta **una sola vez** y queda bloqueado por un marcador en
``var/reports/kronos-01-holdout-consumed.json``. No hay búsqueda de semillas ni
de umbrales después de ver resultados.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median
from typing import Any

from backtests.experiments import (
    COST_RATE,
    MIN_HISTORY,
    STEP,
    TEST,
    TRAIN,
    _gate,
    evaluate_predictions,
    split_series,
)
from backtests.run_backtest import load_series
from backtests.walk_forward import Prediction, build_samples
from src.config.settings import ROOT, Settings
from src.data.store import Store
from src.models.kronos_adapter import KronosAdapter, KronosConfig
from src.schemas.market import Candle

THRESHOLD = 0.55
HORIZON = 1
DEV_FRACTION = 0.80
# Valores congelados en docs/EXPERIMENTS.md antes de ver el holdout de Kronos.
BEST_V01_DEV_LOG_LOSS = 0.6986
PRIOR_HOLDOUT_LOG_LOSS = 0.6928
PRIOR_HOLDOUT_CANDIDATE = "logit_v03_h1_t60"
MARKER_NAME = "kronos-01-holdout-consumed.json"


def _samples(candles: list[Candle]) -> list[Any]:
    return build_samples(
        candles, min_history=MIN_HISTORY, horizon=HORIZON, feature_set="v0.1"
    )


def _dev_indices(samples: list[Any], candles: list[Candle]) -> list[tuple[int, int]]:
    """Posiciones de muestra OOS que usa el walk-forward del baseline."""
    windows = max(0, (len(samples) - TRAIN - TEST) // STEP + 1)
    if windows == 0:
        raise ValueError(
            f"muestras insuficientes para dev: {len(samples)}; se requieren al menos "
            f"{TRAIN + TEST}"
        )
    index_by_ts = {candle.ts: index for index, candle in enumerate(candles)}
    positions: list[tuple[int, int]] = []
    for window in range(windows):
        for offset in range(TEST):
            sample_position = TRAIN + window * STEP + offset
            if sample_position >= len(samples):
                break
            sample = samples[sample_position]
            positions.append((window, index_by_ts[sample.ts]))
    return positions


def _holdout_indices(
    samples: list[Any], candles: list[Candle], split_index: int
) -> list[tuple[int, int]]:
    split_ts = candles[split_index].ts
    index_by_ts = {candle.ts: index for index, candle in enumerate(candles)}
    return [
        (0, index_by_ts[sample.ts])
        for sample in samples
        if sample.ts >= split_ts
    ]


def _config_hash(config: KronosConfig) -> str:
    payload = json.dumps(config.describe(), sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def _cache_path(out_dir: Path, model: str, stage: str) -> Path:
    return out_dir / f"kronos-01-predictions-{model.replace('kronos-', '')}-{stage}.json"


def _load_cache(path: Path, config: KronosConfig) -> list[dict[str, Any]] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("config_hash") != _config_hash(config):
        return None
    return payload.get("rows")


def _save_cache(path: Path, config: KronosConfig, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "config": config.describe(),
        "config_hash": _config_hash(config),
        "saved_at": datetime.now(UTC).isoformat(),
        "rows": rows,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _predict(
    candles_by_asset: dict[str, list[Candle]],
    model_samples: dict[str, list[Any]],
    indices_by_asset: dict[str, list[tuple[int, int]]],
    *,
    config: KronosConfig,
    stage: str,
    out_dir: Path,
    resume: bool,
    cache_dir: Path | None,
    code_dir: Path | None,
) -> tuple[dict[str, list[Prediction]], dict[str, Any]]:
    cache_path = _cache_path(out_dir, config.model, stage)
    cached = _load_cache(cache_path, config) if resume else None
    if cached is not None:
        print(f"Reutilizando caché: {cache_path}")
        rows = cached
    else:
        adapter = KronosAdapter(config, cache_dir=cache_dir, code_dir=code_dir)
        rows = []
        for asset, candles in candles_by_asset.items():
            samples = model_samples[asset]
            positions = indices_by_asset[asset]
            print(f"{asset}: {len(positions)} predicciones zero-shot", flush=True)
            for count, (window, index) in enumerate(positions, start=1):
                started = time.perf_counter()
                signal = adapter.predict_at(candles, index)
                elapsed = time.perf_counter() - started
                sample = next(s for s in samples if s.ts == candles[index].ts)
                rows.append(
                    {
                        "asset": asset,
                        "window": window,
                        "index": index,
                        "ts": signal.ts.isoformat(),
                        "prob": signal.prob_up,
                        "expected_return_median": signal.expected_return_median,
                        "expected_return_mean": signal.expected_return_mean,
                        "label": sample.label,
                        "next_return": sample.next_return,
                        "inference_seconds": elapsed,
                        "cached": False,
                    }
                )
                if count % 25 == 0:
                    print(f"  {count}/{len(positions)} ({elapsed:.2f}s última)", flush=True)
            _save_cache(cache_path, config, rows)
            print(f"caché: {cache_path}", flush=True)

    predictions: dict[str, list[Prediction]] = {}
    timings: dict[str, list[float]] = {}
    for row in rows:
        pred = Prediction(
            window=int(row["window"]),
            asset=row["asset"],
            ts=datetime.fromisoformat(row["ts"]),
            prob=float(row["prob"]),
            label=int(row["label"]),
            next_return=float(row["next_return"]),
            horizon=HORIZON,
            feature_set="kronos",
        )
        predictions.setdefault(row["asset"], []).append(pred)
        timings.setdefault(row["asset"], []).append(float(row.get("inference_seconds", 0.0)))

    timing_summary: dict[str, Any] = {}
    all_times = [value for values in timings.values() for value in values if value > 0]
    for asset, values in timings.items():
        usable = [value for value in values if value > 0]
        timing_summary[asset] = {
            "predictions": len(values),
            "seconds_total": sum(usable),
            "seconds_mean": mean(usable) if usable else math.nan,
            "seconds_median": median(usable) if usable else math.nan,
        }
    timing_summary["all"] = {
        "predictions": len(all_times),
        "seconds_total": sum(all_times),
        "seconds_mean": mean(all_times) if all_times else math.nan,
        "seconds_median": median(all_times) if all_times else math.nan,
        "estimated_daily_btc_eth_seconds": 2 * (mean(all_times) if all_times else math.nan),
    }
    return predictions, timing_summary


def _fold_results(predictions: dict[str, list[Prediction]]) -> list[dict[str, Any]]:
    pooled = [p for values in predictions.values() for p in values]
    by_window: dict[int, list[Prediction]] = {}
    for prediction in pooled:
        by_window.setdefault(prediction.window, []).append(prediction)
    rows = []
    for window, group in sorted(by_window.items()):
        metrics = evaluate_predictions(
            group, threshold=THRESHOLD, cost_rate=COST_RATE, horizon=HORIZON
        )
        rows.append(
            {
                "window": window,
                "start": min(p.ts for p in group).isoformat(),
                "end": max(p.ts for p in group).isoformat(),
                "samples": len(group),
                "log_loss": metrics["log_loss"],
                "log_loss_baseline": metrics["log_loss_baseline"],
                "rank_ic": metrics["rank_ic"],
                "sharpe": metrics["sharpe"],
                "net_return": metrics["net_return"],
                "max_drawdown": metrics["max_drawdown"],
                "trades": metrics["trades"],
                "signal_hit_rate": metrics["signal_hit_rate"],
                "positive_months": metrics["positive_months"],
            }
        )
    return rows


def _monthly_results(predictions: dict[str, list[Prediction]]) -> list[dict[str, Any]]:
    pooled = [p for values in predictions.values() for p in values]
    by_month: dict[str, list[Prediction]] = {}
    for prediction in pooled:
        by_month.setdefault(prediction.ts.strftime("%Y-%m"), []).append(prediction)
    rows = []
    for month, group in sorted(by_month.items()):
        metrics = evaluate_predictions(
            group, threshold=THRESHOLD, cost_rate=COST_RATE, horizon=HORIZON
        )
        rows.append(
            {
                "month": month,
                "samples": len(group),
                "log_loss": metrics["log_loss"],
                "rank_ic": metrics["rank_ic"],
                "net_return": metrics["net_return"],
                "trades": metrics["trades"],
                "signal_hit_rate": metrics["signal_hit_rate"],
            }
        )
    return rows


def _evaluate(
    predictions: dict[str, list[Prediction]],
    *,
    stage: str,
    config: KronosConfig,
    timing: dict[str, Any],
    folds: list[dict[str, Any]],
    months: list[dict[str, Any]],
) -> dict[str, Any]:
    per_asset = {
        asset: evaluate_predictions(
            values, threshold=THRESHOLD, cost_rate=COST_RATE, horizon=HORIZON
        )
        for asset, values in predictions.items()
    }
    pooled_predictions = [p for values in predictions.values() for p in values]
    pooled = evaluate_predictions(
        pooled_predictions, threshold=THRESHOLD, cost_rate=COST_RATE, horizon=HORIZON
    )
    pooled["assets_positive"] = float(
        sum(1 for metrics in per_asset.values() if metrics["net_return"] > 0)
    )
    pooled["assets_total"] = float(len(per_asset))
    gate = _gate(
        pooled,
        previous_best_log_loss=BEST_V01_DEV_LOG_LOSS if stage == "dev" else None,
    )
    return {
        "experiment": "KRONOS-01",
        "stage": stage,
        "generated_at": datetime.now(UTC).isoformat(),
        "model": config.describe(),
        "protocol": {
            "dev_fraction": DEV_FRACTION,
            "train": TRAIN,
            "test": TEST,
            "step": STEP,
            "min_history": MIN_HISTORY,
            "horizon": HORIZON,
            "threshold": THRESHOLD,
            "cost_rate": COST_RATE,
            "samples_pooled": len(pooled_predictions),
        },
        "pooled": pooled,
        "per_asset": per_asset,
        "folds": folds,
        "months": months,
        "timing": timing,
        "gate": gate,
        "gate_passed": all(gate.values()),
        "comparisons": {
            "trivial_log_loss": 0.6931,
            "baseline_v01_dev": {
                "log_loss": 0.6986,
                "ece": 0.0379,
                "sharpe": 0.61,
                "net_return": 0.0967,
                "max_drawdown": 0.268,
                "positive_months": 0.58,
                "assets_positive": 1,
            },
            "best_logit_v03_dev": {
                "log_loss": 0.7032,
                "ece": 0.0584,
                "sharpe": 2.00,
                "net_return": 0.5668,
                "max_drawdown": 0.224,
                "positive_months": 0.59,
                "assets_positive": 2,
            },
            "buy_and_hold_dev": {
                "sharpe": 0.89,
                "net_return": 2.0469,
                "max_drawdown": 0.638,
                "positive_months": 0.50,
                "assets_positive": 2,
            },
            "prior_holdout": {
                "candidate": PRIOR_HOLDOUT_CANDIDATE,
                "log_loss": PRIOR_HOLDOUT_LOG_LOSS,
            },
        },
    }


def _write_report(payload: dict[str, Any], out_dir: Path, stage: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    json_path = out_dir / f"kronos-01-{stage}-{stamp}.json"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    pooled = payload["pooled"]
    timing = payload["timing"]["all"]
    lines = [
        f"# KRONOS-01 — {stage} ({stamp})",
        "",
        f"Modelo: `{payload['model']['model']}` "
        f"({payload['model']['parameters']}) · revisión `{payload['model']['revision']}` · "
        f"código `{payload['model']['code_commit']}`",
        "",
        f"Protocolo: lookback {payload['model']['lookback']} · "
        f"sample_count {payload['model']['sample_count']} · T {payload['model']['temperature']} · "
        f"top_p {payload['model']['top_p']} · umbral {THRESHOLD:.2f} · "
        f"costo {COST_RATE * 100:.2f}% · h={HORIZON}.",
        "",
        "| Métrica | Valor |",
        "| --- | --- |",
        f"| Muestras | {int(pooled['samples'])} |",
        f"| Log loss | {pooled['log_loss']:.4f} (baseline {pooled['log_loss_baseline']:.4f}) |",
        f"| ECE | {pooled['ece']:.4f} |",
        f"| RankIC | {pooled['rank_ic']:.4f} |",
        f"| Sharpe neto | {pooled['sharpe']:.2f} |",
        f"| Retorno neto | {pooled['net_return'] * 100:+.2f}% |",
        f"| Max DD | {pooled['max_drawdown'] * 100:.1f}% |",
        f"| Trades | {int(pooled['trades'])} |",
        f"| Acierto señales | {pooled['signal_hit_rate'] * 100:.1f}% |",
        f"| Meses + | {pooled['positive_months'] * 100:.0f}% |",
        f"| Activos + | {int(pooled['assets_positive'])}/{int(pooled['assets_total'])} |",
        f"| Gate | {'✅' if payload['gate_passed'] else '❌'} |",
        "",
        "## Tiempo de inferencia CPU",
        "",
        f"- Total: **{timing['seconds_total']:.1f} s** para {timing['predictions']} predicciones.",
        f"- Media: **{timing['seconds_mean']:.2f} s/predicción** "
        f"(mediana {timing['seconds_median']:.2f} s).",
        f"- Dos activos al día: **{timing['estimated_daily_btc_eth_seconds']:.1f} s** "
        "solo de inferencia, sin descarga de modelo.",
        "",
        "| Activo | Predicciones | Media s/pred | Total s |",
        "| --- | --- | --- | --- |",
    ]
    for asset, asset_timing in payload["timing"].items():
        if asset == "all":
            continue
        lines.append(
            f"| {asset} | {int(asset_timing['predictions'])} | "
            f"{asset_timing['seconds_mean']:.2f} | {asset_timing['seconds_total']:.1f} |"
        )
    lines += [
        "",
        "## Folds",
        "",
        "| Fold | Inicio | Fin | Log loss | RankIC | Sharpe | Retorno | Max DD | Trades | Acierto |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for fold in payload["folds"]:
        lines.append(
            f"| {fold['window']} | {fold['start'][:10]} | {fold['end'][:10]} | "
            f"{fold['log_loss']:.4f} | {fold['rank_ic']:.3f} | {fold['sharpe']:.2f} | "
            f"{fold['net_return'] * 100:+.2f}% | {fold['max_drawdown'] * 100:.1f}% | "
            f"{int(fold['trades'])} | {fold['signal_hit_rate'] * 100:.0f}% |"
        )
    lines += [
        "",
        "## Meses",
        "",
        "| Mes | Log loss | RankIC | Retorno | Trades | Acierto |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for month in payload["months"]:
        lines.append(
            f"| {month['month']} | {month['log_loss']:.4f} | {month['rank_ic']:.3f} | "
            f"{month['net_return'] * 100:+.2f}% | {int(month['trades'])} | "
            f"{month['signal_hit_rate'] * 100:.0f}% |"
        )
    lines += [
        "",
        "## Por activo",
        "",
        "| Activo | Log loss | RankIC | Sharpe | Retorno neto | Max DD | Trades | Acierto |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for asset, metrics in payload["per_asset"].items():
        rank_ic = metrics["rank_ic"]
        lines.append(
            f"| {asset} | {metrics['log_loss']:.4f} | "
            f"{'—' if math.isnan(rank_ic) else f'{rank_ic:.3f}'} | "
            f"{metrics['sharpe']:.2f} | {metrics['net_return'] * 100:+.2f}% | "
            f"{metrics['max_drawdown'] * 100:.1f}% | {int(metrics['trades'])} | "
            f"{metrics['signal_hit_rate'] * 100:.0f}% |"
        )
    md_path = out_dir / f"kronos-01-{stage}-{stamp}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Reporte: {md_path}")
    return json_path


def run_dev(
    series: dict[str, list[Candle]],
    *,
    config: KronosConfig,
    out_dir: Path,
    resume: bool = False,
    cache_dir: Path | None = None,
    code_dir: Path | None = None,
) -> dict[str, Any]:
    candles_by_asset: dict[str, list[Candle]] = {}
    samples_by_asset: dict[str, list[Any]] = {}
    indices_by_asset: dict[str, list[tuple[int, int]]] = {}
    for asset, candles in series.items():
        dev, _holdout, _split = split_series(candles)
        candles_by_asset[asset] = dev
        samples = _samples(dev)
        samples_by_asset[asset] = samples
        indices_by_asset[asset] = _dev_indices(samples, dev)
    predictions, timing = _predict(
        candles_by_asset,
        samples_by_asset,
        indices_by_asset,
        config=config,
        stage="dev",
        out_dir=out_dir,
        resume=resume,
        cache_dir=cache_dir,
        code_dir=code_dir,
    )
    payload = _evaluate(
        predictions,
        stage="dev",
        config=config,
        timing=timing,
        folds=_fold_results(predictions),
        months=_monthly_results(predictions),
    )
    _write_report(payload, out_dir, "dev")
    print(
        "Gate dev:",
        "PASA" if payload["gate_passed"] else "NO PASA",
        "| log loss",
        f"{payload['pooled']['log_loss']:.4f}",
        "| Sharpe",
        f"{payload['pooled']['sharpe']:.2f}",
    )
    return payload


def _holdout_marker(out_dir: Path) -> Path:
    return out_dir / MARKER_NAME


def run_holdout(
    series: dict[str, list[Candle]],
    *,
    config: KronosConfig,
    out_dir: Path,
    resume: bool = False,
    cache_dir: Path | None = None,
    code_dir: Path | None = None,
) -> dict[str, Any]:
    marker = _holdout_marker(out_dir)
    if marker.exists():
        raise SystemExit(
            f"Holdout ya consumido en {marker}. No se repite ni se ajusta el candidato."
        )
    candles_by_asset: dict[str, list[Candle]] = {}
    samples_by_asset: dict[str, list[Any]] = {}
    indices_by_asset: dict[str, list[tuple[int, int]]] = {}
    split_info: dict[str, Any] = {}
    for asset, candles in series.items():
        ordered = sorted(candles, key=lambda c: c.ts)
        dev, holdout, split = split_series(ordered)
        candles_by_asset[asset] = ordered
        samples = _samples(ordered)
        samples_by_asset[asset] = samples
        indices_by_asset[asset] = _holdout_indices(samples, ordered, split)
        split_info[asset] = {
            "dev_end": dev[-1].ts.isoformat(),
            "holdout_start": holdout[0].ts.isoformat(),
            "holdout_end": holdout[-1].ts.isoformat(),
            "holdout_candles": len(holdout),
        }
    predictions, timing = _predict(
        candles_by_asset,
        samples_by_asset,
        indices_by_asset,
        config=config,
        stage="holdout",
        out_dir=out_dir,
        resume=resume,
        cache_dir=cache_dir,
        code_dir=code_dir,
    )
    payload = _evaluate(
        predictions,
        stage="holdout",
        config=config,
        timing=timing,
        folds=_fold_results(predictions),
        months=_monthly_results(predictions),
    )
    payload["split"] = split_info
    _write_report(payload, out_dir, "holdout")
    marker.write_text(
        json.dumps(
            {
                "consumed_at": datetime.now(UTC).isoformat(),
                "model": config.describe(),
                "gate_passed": payload["gate_passed"],
                "log_loss": payload["pooled"]["log_loss"],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print("Gate holdout:", "PASA" if payload["gate_passed"] else "NO PASA")
    print(f"Marcador de holdout consumido: {marker}")
    return payload


def _config_from_args(args: argparse.Namespace) -> KronosConfig:
    model = args.model if args.model.startswith("kronos-") else f"kronos-{args.model}"
    return KronosConfig(
        model=model,
        lookback=args.lookback,
        horizon=HORIZON,
        sample_count=args.sample_count,
        temperature=args.temperature,
        top_p=args.top_p,
        top_k=0,
        seed=args.seed,
        threads=args.threads,
        batch_size=1,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="KRONOS-01 zero-shot")
    parser.add_argument("stage", choices=["dev", "holdout"])
    parser.add_argument("--model", default="small", choices=["small", "base", "kronos-small", "kronos-base"])
    parser.add_argument("--source", choices=["demo", "live", "cache"], default="cache")
    parser.add_argument("--days", type=int, default=1620)
    parser.add_argument("--assets", default="BTC,ETH")
    parser.add_argument("--lookback", type=int, default=128)
    parser.add_argument("--sample-count", type=int, default=30)
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--top-p", type=float, default=0.9)
    parser.add_argument("--seed", type=int, default=20261023)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--cache-dir", type=Path, default=None)
    parser.add_argument("--code-dir", type=Path, default=None)
    parser.add_argument("--resume", action="store_true", help="reutiliza predicciones cacheadas")
    parser.add_argument("--confirm-holdout", action="store_true")
    args = parser.parse_args()

    if args.stage == "holdout" and not args.confirm_holdout:
        raise SystemExit("Holdout bloqueado: añade --confirm-holdout.")
    config = _config_from_args(args)
    settings = Settings.from_env()
    store = Store(settings.db_path)
    store.init_schema()
    series: dict[str, list[Candle]] = {}
    for asset in [a.strip().upper() for a in args.assets.split(",") if a.strip()]:
        candles, _source = load_series(asset, source=args.source, days=args.days, store=store)
        series[asset] = candles
    out_dir = args.out or (ROOT / "var" / "reports")
    if args.stage == "dev":
        run_dev(
            series,
            config=config,
            out_dir=out_dir,
            resume=args.resume,
            cache_dir=args.cache_dir,
            code_dir=args.code_dir,
        )
    else:
        run_holdout(
            series,
            config=config,
            out_dir=out_dir,
            resume=args.resume,
            cache_dir=args.cache_dir,
            code_dir=args.code_dir,
        )


if __name__ == "__main__":
    main()
