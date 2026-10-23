"""Fase 2: experimentos walk-forward con holdout final intocable.

Protocolo pre-registrado en `docs/EXPERIMENTS.md`:

1. El 80 % inicial de cada serie es desarrollo. Solo ahí se comparan candidatos.
2. El 20 % final es holdout. Se evalúa **una vez** con el candidato congelado.
3. Las etiquetas de una muestra en t usan el retorno t -> t+h; nunca datos
   posteriores. El modelo de holdout se entrena solo con el tramo de desarrollo.
4. Los costos (20 bps ida y vuelta) se aplican en todas las estrategias.

Este módulo no decide por sí solo: produce números y un gate explícito. Si nada
pasa, el paper trading se queda con la configuración actual.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

from backtests.metrics import (
    buy_and_hold,
    monthly_consistency,
    sharpe_ratio,
    strategy_stats,
)
from backtests.run_backtest import evaluate, load_series
from backtests.walk_forward import (
    Prediction,
    build_samples,
    non_overlapping,
    walk_forward_predictions,
)
from src.config.settings import ROOT, Settings
from src.data.store import Store
from src.models.baseline import LogisticBaseline
from src.quant.features import FEATURE_SETS
from src.schemas.market import Candle

DEV_FRACTION = 0.80
COST_RATE = 0.002
MIN_HISTORY = 201
TRAIN, TEST, STEP = 365, 60, 60


@dataclass(frozen=True)
class Candidate:
    name: str
    kind: str  # logit | rule
    feature_set: str | None = None
    horizon: int = 1
    threshold: float = 0.55
    rule: str | None = None
    params: tuple[tuple[str, float], ...] = ()


# Lista cerrada antes de mirar el holdout. No se añaden candidatos después de
# ver los resultados de desarrollo.
CANDIDATES: tuple[Candidate, ...] = (
    Candidate("logit_v01_h1_t55", "logit", "v0.1", 1, 0.55),
    Candidate("logit_v01_h1_t60", "logit", "v0.1", 1, 0.60),
    Candidate("logit_v02_h1_t55", "logit", "v0.2", 1, 0.55),
    Candidate("logit_v02_h1_t60", "logit", "v0.2", 1, 0.60),
    Candidate("logit_v03_h1_t55", "logit", "v0.3", 1, 0.55),
    Candidate("logit_v03_h1_t60", "logit", "v0.3", 1, 0.60),
    Candidate("logit_v03_h3_t55", "logit", "v0.3", 3, 0.55),
    Candidate("logit_v03_h5_t55", "logit", "v0.3", 5, 0.55),
    Candidate("rule_buy_hold", "rule", horizon=1, rule="buy_hold"),
    Candidate("rule_momentum_50_20", "rule", rule="momentum", params=(("fast", 50.0), ("mom", 20.0))),
    Candidate("rule_trend_200", "rule", rule="trend", params=(("window", 200.0),)),
    Candidate("rule_meanrev_rsi30", "rule", rule="meanrev", params=(("rsi_entry", 30.0), ("max_hold", 5.0))),
    Candidate("rule_breakout_20", "rule", rule="breakout", params=(("lookback", 20.0), ("max_hold", 10.0))),
    Candidate(
        "rule_momentum_vol_target",
        "rule",
        rule="momentum_vol",
        params=(("fast", 50.0), ("mom", 20.0), ("target_vol", 0.02)),
    ),
)


def split_series(candles: list[Candle], fraction: float = DEV_FRACTION) -> tuple[list[Candle], list[Candle], int]:
    ordered = sorted(candles, key=lambda c: c.ts)
    split = int(len(ordered) * fraction)
    if split < MIN_HISTORY + TRAIN + TEST:
        raise ValueError(f"serie demasiado corta para el split: {len(ordered)} velas")
    return ordered[:split], ordered[split:], split


def _candidate(name: str) -> Candidate:
    for candidate in CANDIDATES:
        if candidate.name == name:
            return candidate
    raise KeyError(f"candidato desconocido: {name}")


def _net_returns(predictions: list[Prediction], threshold: float, cost_rate: float) -> list[float]:
    return [p.next_return - cost_rate for p in predictions if p.prob >= threshold]


def _strategy_diagnostics(
    predictions: list[Prediction], *, threshold: float, cost_rate: float, horizon: int
) -> dict[str, float]:
    selected = non_overlapping(predictions, horizon)
    stats = strategy_stats(
        [p.prob for p in selected],
        [p.next_return for p in selected],
        threshold=threshold,
        cost_rate=cost_rate,
        periods_per_year=365.0 / max(1, horizon),
    )
    traded = [p for p in selected if p.prob >= threshold]
    monthly = monthly_consistency(
        [(p.ts, p.next_return - cost_rate) for p in traded]
    )
    return {
        "sharpe": stats.sharpe,
        "max_drawdown": stats.max_drawdown,
        "profit_factor": stats.profit_factor,
        "avg_net_per_trade": stats.avg_net_per_trade,
        "trades": float(stats.trades),
        "positive_months": monthly["positive_ratio"],
        "months": monthly["months"],
        "worst_month": monthly["worst_month"],
        "best_month": monthly["best_month"],
    }


def evaluate_predictions(
    predictions: list[Prediction], *, threshold: float, cost_rate: float, horizon: int
) -> dict[str, float]:
    metrics = evaluate(
        predictions, threshold=threshold, cost_rate=cost_rate, horizon=horizon
    )
    metrics.update(
        _strategy_diagnostics(
            predictions, threshold=threshold, cost_rate=cost_rate, horizon=horizon
        )
    )
    return metrics


def _sma(values: list[float], window: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    running = 0.0
    for i, value in enumerate(values):
        running += value
        if i >= window:
            running -= values[i - window]
        if i >= window - 1:
            result[i] = running / window
    return result


def _rsi_series(closes: list[float], period: int = 14) -> list[float]:
    result: list[float] = [50.0] * len(closes)
    for i in range(period, len(closes)):
        gains = [max(closes[j] - closes[j - 1], 0.0) for j in range(i - period + 1, i + 1)]
        losses = [max(-(closes[j] - closes[j - 1]), 0.0) for j in range(i - period + 1, i + 1)]
        avg_gain, avg_loss = mean(gains), mean(losses)
        if avg_loss == 0:
            result[i] = 100.0 if avg_gain > 0 else 50.0
        else:
            rs = avg_gain / avg_loss
            result[i] = 100.0 - 100.0 / (1.0 + rs)
    return result


def rule_weights(candles: list[Candle], candidate: Candidate) -> list[float]:
    """Peso para el retorno t -> t+1, calculado solo con datos <= t."""
    ordered = sorted(candles, key=lambda c: c.ts)
    closes = [c.close for c in ordered]
    highs = [c.high for c in ordered]
    lows = [c.low for c in ordered]
    params = dict(candidate.params)
    kind = candidate.rule or ""
    n = len(ordered)
    weights = [0.0] * n

    if kind == "buy_hold":
        return [1.0] * n

    sma_fast = _sma(closes, int(params.get("fast", 50.0)))
    sma_200 = _sma(closes, int(params.get("window", 200.0)))
    rsi = _rsi_series(closes)
    rets_20 = [
        closes[i] / closes[i - 20] - 1.0 if i >= 20 and closes[i - 20] > 0 else 0.0
        for i in range(n)
    ]
    vol_20 = [
        pstdev([closes[j] / closes[j - 1] - 1.0 for j in range(i - 19, i + 1)])
        if i >= 20
        else 0.0
        for i in range(n)
    ]

    held = 0
    for i in range(n):
        if kind in {"momentum", "momentum_vol"}:
            fast = sma_fast[i]
            active = fast is not None and closes[i] > fast and rets_20[i] > 0
            if kind == "momentum":
                weights[i] = 1.0 if active else 0.0
            else:
                target = params.get("target_vol", 0.02)
                weights[i] = min(1.0, target / vol_20[i]) if active and vol_20[i] > 0 else 0.0
        elif kind == "trend":
            fast = sma_200[i]
            weights[i] = 1.0 if fast is not None and closes[i] > fast else 0.0
        elif kind == "meanrev":
            long_ok = sma_200[i] is not None and closes[i] > float(sma_200[i] or 0.0)
            if weights[i - 1] == 0 and long_ok and rsi[i] < params.get("rsi_entry", 30.0):
                held = 0
                weights[i] = 1.0
            elif weights[i - 1] > 0:
                held += 1
                weights[i] = 0.0 if (rsi[i] > 55.0 or held >= params.get("max_hold", 5.0)) else 1.0
        elif kind == "breakout":
            lookback = int(params.get("lookback", 20.0))
            if i >= lookback:
                prior_high = max(highs[i - lookback : i])
                prior_low = min(lows[max(0, i - 10) : i]) if i >= 10 else min(lows[:i])
                if weights[i - 1] == 0 and closes[i] > prior_high:
                    held = 0
                    weights[i] = 1.0
                elif weights[i - 1] > 0:
                    held += 1
                    weights[i] = 0.0 if (held >= params.get("max_hold", 10.0) or closes[i] < prior_low) else 1.0
    return weights


def rule_metrics(
    candles: list[Candle], weights: list[float], *, cost_rate: float = COST_RATE
) -> dict[str, float]:
    ordered = sorted(candles, key=lambda c: c.ts)
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    daily: list[float] = []
    dated: list[tuple[datetime, float]] = []
    trades = 0
    exposure = 0.0
    for i in range(len(ordered) - 1):
        previous = weights[i - 1] if i > 0 else 0.0
        turnover = abs(weights[i] - previous)
        if weights[i] > 0 and previous == 0:
            trades += 1
        gross = weights[i] * (ordered[i + 1].close / ordered[i].close - 1.0)
        net = gross - cost_rate * turnover
        equity *= 1 + net
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak if peak > 0 else 0.0)
        daily.append(net)
        dated.append((ordered[i + 1].ts, net))
        exposure += weights[i]
    monthly = monthly_consistency(dated)
    raw_returns = [
        ordered[i + 1].close / ordered[i].close - 1.0 for i in range(len(ordered) - 1)
    ]
    return {
        "trades": float(trades),
        "net_return": equity - 1.0,
        "avg_net_per_trade": sum(daily) / len(daily) if daily else 0.0,
        "win_rate": sum(1 for value in daily if value > 0) / len(daily) if daily else 0.0,
        "max_drawdown": max_dd,
        "sharpe": sharpe_ratio(daily, periods_per_year=365.0),
        "exposure": exposure / max(1, len(ordered) - 1),
        "positive_months": monthly["positive_ratio"],
        "months": monthly["months"],
        "worst_month": monthly["worst_month"],
        "best_month": monthly["best_month"],
        "buy_and_hold": buy_and_hold(raw_returns),
    }


def _dev_logit(
    candles: list[Candle], candidate: Candidate
) -> list[Prediction]:
    assert candidate.feature_set is not None
    return walk_forward_predictions(
        candles,
        train=TRAIN,
        test=TEST,
        step=STEP,
        horizon=candidate.horizon,
        feature_set=candidate.feature_set,
        min_history=MIN_HISTORY,
    )


def _holdout_predictions(
    dev: list[Candle],
    full: list[Candle],
    split_index: int,
    candidate: Candidate,
) -> list[Prediction]:
    assert candidate.feature_set is not None
    train_samples = build_samples(
        dev,
        min_history=MIN_HISTORY,
        horizon=candidate.horizon,
        feature_set=candidate.feature_set,
    )
    model = LogisticBaseline(
        epochs=500,
        learning_rate=0.15,
        feature_order=FEATURE_SETS[candidate.feature_set],
    ).fit(
        [sample.features for sample in train_samples],
        [sample.label for sample in train_samples],
    )
    split_ts = full[split_index].ts
    test_samples = [
        sample
        for sample in build_samples(
            full,
            min_history=MIN_HISTORY,
            horizon=candidate.horizon,
            feature_set=candidate.feature_set,
        )
        if sample.ts >= split_ts
    ]
    probs = model.predict_proba([sample.features for sample in test_samples])
    asset = full[-1].asset
    return [
        Prediction(
            window=0,
            asset=asset,
            ts=sample.ts,
            prob=min(max(float(prob), 0.0), 1.0),
            label=sample.label,
            next_return=sample.next_return,
            horizon=candidate.horizon,
            feature_set=candidate.feature_set,
        )
        for sample, prob in zip(test_samples, probs, strict=True)
    ]


def _aggregate_predictions(
    predictions: dict[str, list[Prediction]], candidate: Candidate, cost_rate: float
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    per_asset = {
        asset: evaluate_predictions(
            preds, threshold=candidate.threshold, cost_rate=cost_rate, horizon=candidate.horizon
        )
        for asset, preds in predictions.items()
    }
    pooled: list[Prediction] = [p for preds in predictions.values() for p in preds]
    pool_metrics: dict[str, Any] = evaluate_predictions(
        pooled, threshold=candidate.threshold, cost_rate=cost_rate, horizon=candidate.horizon
    )
    pool_metrics["assets_positive"] = float(sum(1 for m in per_asset.values() if m["net_return"] > 0))
    pool_metrics["assets_total"] = float(len(per_asset))
    return per_asset, pool_metrics


def _gate(metrics: dict[str, Any], *, previous_best_log_loss: float | None) -> dict[str, bool]:
    margin = 0.005
    beats = metrics["log_loss"] <= metrics["log_loss_baseline"] - margin
    if previous_best_log_loss is not None:
        beats = beats and metrics["log_loss"] <= previous_best_log_loss - 0.003
    return {
        "log_loss_margin": beats,
        "calibration": metrics["ece"] <= 0.05,
        "net_sharpe_positive": metrics["sharpe"] > 0.5,
        "profitable": metrics["net_return"] > 0 and metrics["trades"] >= 30,
        "drawdown": metrics["max_drawdown"] <= 0.10,
        "monthly_consistency": metrics["positive_months"] >= 0.60,
        "asset_consistency": metrics["assets_positive"] == metrics["assets_total"],
    }


def _summarize(
    name: str, kind: str, per_asset: dict[str, dict[str, Any]], pooled: dict[str, Any]
) -> dict:
    return {
        "name": name,
        "kind": kind,
        "pooled": pooled,
        "per_asset": per_asset,
    }


def run_dev(
    series: dict[str, list[Candle]], *, source: str, out_dir: Path, cost_rate: float = COST_RATE
) -> dict:
    dev_series = {}
    split_info = {}
    for asset, candles in series.items():
        dev, holdout, split = split_series(candles)
        dev_series[asset] = dev
        split_info[asset] = {
            "dev_start": dev[0].ts.isoformat(),
            "dev_end": dev[-1].ts.isoformat(),
            "holdout_start": holdout[0].ts.isoformat(),
            "holdout_end": holdout[-1].ts.isoformat(),
            "dev_candles": len(dev),
            "holdout_candles": len(holdout),
            "split_index": split,
        }

    results: list[dict] = []
    previous_best: float | None = None
    for candidate in CANDIDATES:
        if candidate.kind == "logit":
            predictions = {asset: _dev_logit(candles, candidate) for asset, candles in dev_series.items()}
            per_asset, pooled = _aggregate_predictions(predictions, candidate, cost_rate)
            pooled["gate"] = _gate(pooled, previous_best_log_loss=previous_best)
            previous_best = min(previous_best or 1.0, pooled["log_loss"])
            results.append(_summarize(candidate.name, candidate.kind, per_asset, pooled))
        else:
            per_asset = {
                asset: rule_metrics(candles, rule_weights(candles, candidate), cost_rate=cost_rate)
                for asset, candles in dev_series.items()
            }
            # Para reglas no hay probabilidad ni log loss; se reporta el retorno
            # y se deja claro que no pueden cumplir el gate probabilístico.
            pooled_returns = [m["net_return"] for m in per_asset.values()]
            pooled_rule: dict[str, Any] = {
                "log_loss": math.nan,
                "log_loss_baseline": math.nan,
                "ece": math.nan,
                "sharpe": mean(m["sharpe"] for m in per_asset.values()),
                "net_return": mean(pooled_returns),
                "trades": sum(m["trades"] for m in per_asset.values()),
                "max_drawdown": max(m["max_drawdown"] for m in per_asset.values()),
                "positive_months": min(m["positive_months"] for m in per_asset.values()),
                "assets_positive": float(sum(1 for value in pooled_returns if value > 0)),
                "assets_total": float(len(per_asset)),
                "buy_and_hold": mean(m["buy_and_hold"] for m in per_asset.values()),
            }
            pooled_rule["gate"] = {
                "log_loss_margin": False,
                "calibration": False,
                "net_sharpe_positive": pooled_rule["sharpe"] > 0.5,
                "profitable": pooled_rule["net_return"] > 0 and pooled_rule["trades"] >= 30,
                "drawdown": pooled_rule["max_drawdown"] <= 0.10,
                "monthly_consistency": pooled_rule["positive_months"] >= 0.60,
                "asset_consistency": pooled_rule["assets_positive"] == pooled_rule["assets_total"],
            }
            results.append(_summarize(candidate.name, candidate.kind, per_asset, pooled_rule))

    payload = {
        "stage": "dev",
        "generated_at": datetime.now(UTC).isoformat(),
        "source": source,
        "cost_rate": cost_rate,
        "protocol": {
            "dev_fraction": DEV_FRACTION,
            "train": TRAIN,
            "test": TEST,
            "step": STEP,
            "min_history": MIN_HISTORY,
            "candidates": len(CANDIDATES),
        },
        "split": split_info,
        "results": results,
    }
    _write_outputs(payload, out_dir, "dev")
    return payload


def run_holdout(
    series: dict[str, list[Candle]],
    *,
    candidate_name: str,
    source: str,
    out_dir: Path,
    cost_rate: float = COST_RATE,
) -> dict:
    candidate = _candidate(candidate_name)
    dev_series: dict[str, list[Candle]] = {}
    full_series: dict[str, list[Candle]] = {}
    splits: dict[str, int] = {}
    for asset, candles in series.items():
        ordered = sorted(candles, key=lambda c: c.ts)
        dev, _holdout, split = split_series(ordered)
        dev_series[asset] = dev
        full_series[asset] = ordered
        splits[asset] = split

    if candidate.kind == "logit":
        predictions = {
            asset: _holdout_predictions(
                dev_series[asset], full_series[asset], splits[asset], candidate
            )
            for asset in series
        }
        per_asset, pooled = _aggregate_predictions(predictions, candidate, cost_rate)
        pooled["gate"] = _gate(pooled, previous_best_log_loss=None)
    else:
        per_asset = {}
        for asset, candles in full_series.items():
            weights = rule_weights(candles, candidate)
            split = splits[asset]
            # La regla solo puede usar el pasado en cada t; se evalúa el tramo
            # holdout sin reentrenar ni cambiar parámetros.
            sliced_candles = candles[split - 1 :]
            sliced_weights = weights[split - 1 :]
            per_asset[asset] = rule_metrics(sliced_candles, sliced_weights, cost_rate=cost_rate)
        pooled_returns = [m["net_return"] for m in per_asset.values()]
        pooled_rule: dict[str, Any] = {
            "log_loss": math.nan,
            "log_loss_baseline": math.nan,
            "ece": math.nan,
            "sharpe": mean(m["sharpe"] for m in per_asset.values()),
            "net_return": mean(pooled_returns),
            "trades": sum(m["trades"] for m in per_asset.values()),
            "max_drawdown": max(m["max_drawdown"] for m in per_asset.values()),
            "positive_months": min(m["positive_months"] for m in per_asset.values()),
            "assets_positive": float(sum(1 for value in pooled_returns if value > 0)),
            "assets_total": float(len(per_asset)),
            "buy_and_hold": mean(m["buy_and_hold"] for m in per_asset.values()),
        }
        pooled_rule["gate"] = {
            "log_loss_margin": False,
            "calibration": False,
            "net_sharpe_positive": pooled_rule["sharpe"] > 0.5,
            "profitable": pooled_rule["net_return"] > 0 and pooled_rule["trades"] >= 30,
            "drawdown": pooled_rule["max_drawdown"] <= 0.10,
            "monthly_consistency": pooled_rule["positive_months"] >= 0.60,
            "asset_consistency": pooled_rule["assets_positive"] == pooled_rule["assets_total"],
        }
        pooled = pooled_rule

    payload = {
        "stage": "holdout",
        "generated_at": datetime.now(UTC).isoformat(),
        "source": source,
        "candidate": candidate_name,
        "kind": candidate.kind,
        "cost_rate": cost_rate,
        "split": {
            asset: {
                "split_index": splits[asset],
                "dev_end": full_series[asset][splits[asset] - 1].ts.isoformat(),
                "holdout_start": full_series[asset][splits[asset]].ts.isoformat(),
                "holdout_end": full_series[asset][-1].ts.isoformat(),
            }
            for asset in series
        },
        "pooled": pooled,
        "per_asset": per_asset,
        "gate_passed": all(pooled["gate"].values()),
    }
    _write_outputs(payload, out_dir, "holdout")
    return payload


def _write_outputs(payload: dict, out_dir: Path, stage: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    json_path = out_dir / f"phase2-{stage}-{stamp}.json"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    lines = [
        f"# Fase 2 — {stage} ({stamp})",
        "",
        f"Fuente: `{payload['source']}` · costo ida y vuelta: {payload['cost_rate'] * 100:.2f}%",
        "",
    ]
    if stage == "dev":
        lines += [
            f"Protocolo: dev {DEV_FRACTION:.0%} · train {TRAIN} / test {TEST} / step {STEP} · "
            f"historia mínima {MIN_HISTORY} · {len(payload['results'])} candidatos congelados.",
            "",
            "| Candidato | Log loss | ECE | Sharpe | Retorno neto | Max DD | Meses + | Activos + | Gate |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for result in payload["results"]:
            m = result["pooled"]
            gate = all(m["gate"].values())
            lines.append(
                f"| `{result['name']}` | {m['log_loss']:.4f} | {m['ece']:.4f} | "
                f"{m['sharpe']:.2f} | {m['net_return'] * 100:+.2f}% | "
                f"{m['max_drawdown'] * 100:.1f}% | {m['positive_months'] * 100:.0f}% | "
                f"{int(m['assets_positive'])}/{int(m['assets_total'])} | {'✅' if gate else '❌'} |"
            )
    else:
        m = payload["pooled"]
        lines += [
            f"Candidato congelado: `{payload['candidate']}` ({payload['kind']}).",
            "",
            "| Métrica | Holdout |",
            "| --- | --- |",
            f"| Log loss | {m['log_loss']:.4f} (baseline {m['log_loss_baseline']:.4f}) |",
            f"| ECE | {m['ece']:.4f} |",
            f"| Sharpe | {m['sharpe']:.2f} |",
            f"| Retorno neto | {m['net_return'] * 100:+.2f}% |",
            f"| Max DD | {m['max_drawdown'] * 100:.1f}% |",
            f"| Meses + | {m['positive_months'] * 100:.0f}% |",
            f"| Activos + | {int(m['assets_positive'])}/{int(m['assets_total'])} |",
            f"| Gate completo | {'✅' if payload['gate_passed'] else '❌'} |",
            "",
            "Gate: log loss con margen, calibración, Sharpe > 0.5, retorno neto con ≥30 trades, "
            "DD ≤ 10%, ≥60% meses positivos y todos los activos positivos.",
        ]
    markdown_path = out_dir / f"phase2-{stage}-{stamp}.md"
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Reporte: {markdown_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Experimentos Fase 2 con holdout")
    parser.add_argument("stage", choices=["dev", "holdout"])
    parser.add_argument("--candidate", default="", help="nombre congelado para holdout")
    parser.add_argument("--source", choices=["demo", "live", "cache"], default="cache")
    parser.add_argument("--days", type=int, default=1620)
    parser.add_argument("--assets", default="BTC,ETH")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--confirm-holdout", action="store_true", help="requerido para stage=holdout")
    args = parser.parse_args()

    if args.stage == "holdout" and not args.confirm_holdout:
        raise SystemExit("Holdout bloqueado: añade --confirm-holdout cuando el candidato esté congelado.")
    if args.stage == "holdout" and not args.candidate:
        raise SystemExit("Falta --candidate para evaluar el holdout.")

    settings = Settings.from_env()
    store = Store(settings.db_path)
    store.init_schema()
    series: dict[str, list[Candle]] = {}
    for asset in [a.strip().upper() for a in args.assets.split(",") if a.strip()]:
        candles, _source = load_series(asset, source=args.source, days=args.days, store=store)
        series[asset] = candles
    out_dir = args.out or (ROOT / "var" / "reports")
    if args.stage == "dev":
        payload = run_dev(series, source=args.source, out_dir=out_dir)
        best = max(
            (r for r in payload["results"] if all(r["pooled"]["gate"].values())),
            key=lambda r: r["pooled"]["sharpe"],
            default=None,
        )
        print("Mejor candidato que pasa el gate:", best["name"] if best else "ninguno")
    else:
        payload = run_holdout(
            series,
            candidate_name=args.candidate,
            source=args.source,
            out_dir=out_dir,
        )
        print("Gate holdout:", "PASA" if payload["gate_passed"] else "NO PASA")


if __name__ == "__main__":
    main()
