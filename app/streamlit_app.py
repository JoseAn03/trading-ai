"""Panel Streamlit del MVP (fase 1).

Solo lectura: muestra lo que el motor calculó y guardó. No calcula señales ni
mantiene procesos de ingesta, tal como exige el plan.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from backtests.metrics import calibration_table, equity_curve
from backtests.walk_forward import Prediction, non_overlapping
from src.config.settings import ROOT, Settings
from src.data.store import Store
from src.quant.risk import RiskLimits

st.set_page_config(page_title="Trading AI — apoyo a decisiones", page_icon="📊", layout="wide")

settings = Settings.from_env()
store = Store(settings.db_path)

BACKTEST_REPORTS = (
    ("comparacion-*.md", "Comparación de features"),
    ("barrido-*.md", "Barrido de horizontes"),
    ("backtest-*.md", "Backtest walk-forward"),
)


def _backtest_reports() -> list[tuple[str, Path]]:
    """Reportes de backtest disponibles, los más recientes primero."""
    folder = ROOT / "var" / "reports"
    found = [
        (label, path) for pattern, label in BACKTEST_REPORTS for path in folder.glob(pattern)
    ]
    return sorted(found, key=lambda item: item[1].stat().st_mtime, reverse=True)


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _gates_line(
    gates: dict,
    *,
    assets_positive: int | None = None,
    assets_total: int | None = None,
) -> None:
    labels = {
        "beats_baseline": "supera baseline",
        "calibrated": "calibrado",
        "profitable": "rentable",
        "robust": "robusto",
    }
    text = " · ".join(
        f"{'✅' if gates.get(key) else '❌'} {label}" for key, label in labels.items()
    )
    if assets_positive is not None and assets_total:
        text += f" · activos positivos {assets_positive}/{assets_total}"
    st.caption(text)


def _metric_cards(metrics: dict) -> None:
    baseline = metrics.get("log_loss_baseline", 0.6931)
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric(
        "Log loss",
        f"{metrics['log_loss']:.4f}",
        f"{metrics['log_loss'] - baseline:+.4f} vs baseline",
        delta_color="inverse",
    )
    c2.metric("ECE", f"{metrics['ece']:.4f}")
    c3.metric("Retorno neto", f"{metrics['net_return'] * 100:+.2f}%")
    c4.metric("Trades", int(metrics["trades"]))
    c5.metric("Max drawdown", f"{metrics['max_drawdown'] * 100:.1f}%")
    c6.metric("Ventanas positivas", f"{metrics['positive_windows'] * 100:.0f}%")


def _predictions_from_csv(csv_paths: list[Path]) -> list[Prediction]:
    predictions: list[Prediction] = []
    for path in csv_paths:
        frame = pd.read_csv(path, parse_dates=["timestamp"])
        for row in frame.itertuples(index=False):
            predictions.append(
                Prediction(
                    window=int(row.window),
                    asset=str(row.asset),
                    ts=row.timestamp.to_pydatetime(),
                    prob=float(row.probability),
                    label=int(row.label),
                    next_return=float(row.next_return),
                    horizon=int(getattr(row, "horizon", 1)),
                    feature_set=str(getattr(row, "features", "v0.1")),
                )
            )
    return predictions


def _render_walk_forward(payload: dict, csv_paths: list[Path]) -> None:
    metrics = payload.get("pooled") or {}
    if not metrics:
        return
    verdict = payload.get("verdict") or {}
    _metric_cards(metrics)
    _gates_line(
        verdict,
        assets_positive=verdict.get("assets_positive"),
        assets_total=len(payload.get("assets") or {}),
    )

    threshold = float(payload.get("threshold", 0.55))
    cost_rate = float(payload.get("cost_rate", 0.002))
    horizon = int(payload.get("horizon", 1))
    predictions = _predictions_from_csv(csv_paths)

    if predictions:
        selected = non_overlapping(predictions, horizon)
        frames = []
        for asset in sorted({p.asset for p in selected}):
            series = sorted((p for p in selected if p.asset == asset), key=lambda p: p.ts)
            curve = equity_curve(
                [p.prob for p in series],
                [p.next_return for p in series],
                threshold=threshold,
                cost_rate=cost_rate,
            )
            frames.append(
                pd.DataFrame(
                    {"Operación": range(len(curve)), "Capital": curve, "Activo": asset}
                )
            )
        st.markdown("**Curva de capital simulada** (1,0 = capital inicial, sin apalancamiento)")
        st.line_chart(
            pd.concat(frames).pivot(index="Operación", columns="Activo", values="Capital"),
            height=320,
        )

        table = calibration_table(
            [p.prob for p in predictions], [p.label for p in predictions], bins=5
        )
        cal = pd.DataFrame(
            [
                {"Prob. media": b.mean_pred, "Acierto real": b.observed, "Muestras": b.count}
                for b in table
                if b.count
            ]
        )
        left, right = st.columns(2)
        left.markdown("**Calibración** (ideal: diagonal)")
        left.line_chart(cal.set_index("Prob. media")[["Acierto real"]], height=260)
        right.markdown("**Muestras por bin de probabilidad**")
        right.dataframe(cal, hide_index=True, width="stretch")


def _render_feature_comparison(payload: dict) -> None:
    sets = payload.get("sets") or []
    if not sets:
        return
    data = pd.DataFrame(
        [
            {
                "Set": s["feature_set"],
                "Log loss": s["pooled"]["log_loss"],
                "ECE": s["pooled"]["ece"],
                "Trades": int(s["pooled"]["trades"]),
                "Retorno %": s["pooled"]["net_return"] * 100,
                "Gates": sum(bool(v) for v in s["gates"].values()),
            }
            for s in sets
        ]
    )
    left, right = st.columns(2)
    left.markdown("**Log loss por set** (menor = mejor)")
    left.bar_chart(data.set_index("Set")[["Log loss"]], height=300)
    left.caption(f"Baseline 50/50: {sets[0]['pooled']['log_loss_baseline']:.4f}")
    right.markdown("**Retorno neto por set (%)**")
    right.bar_chart(data.set_index("Set")[["Retorno %"]], height=300)
    st.dataframe(data, hide_index=True, width="stretch")
    for s in sets:
        _gates_line(
            s["gates"],
            assets_positive=sum(1 for m in s["per_asset"].values() if m["net_return"] > 0),
            assets_total=len(s["per_asset"]),
        )


def _render_horizon_sweep(payload: dict) -> None:
    rows = payload.get("horizons") or []
    if not rows:
        return
    data = pd.DataFrame(
        [
            {
                "Horizonte": int(r["horizon"]),
                "Log loss": r["pooled"]["log_loss"],
                "Baseline": r["pooled"]["log_loss_baseline"],
                "Retorno %": r["pooled"]["net_return"] * 100,
                "ECE": r["pooled"]["ece"],
                "Trades": int(r["pooled"]["trades"]),
                "Ventanas + %": r["pooled"]["positive_windows"] * 100,
            }
            for r in rows
        ]
    )
    best = data.loc[data["Log loss"].idxmin()]
    c1, c2, c3 = st.columns(3)
    c1.metric("Mejor horizonte por log loss", f"{int(best['Horizonte'])} días")
    c2.metric(
        "Log loss",
        f"{best['Log loss']:.4f}",
        f"{best['Log loss'] - best['Baseline']:+.4f} vs baseline",
        delta_color="inverse",
    )
    c3.metric("Retorno con ese horizonte", f"{best['Retorno %']:+.2f}%")

    left, right = st.columns(2)
    left.markdown("**Log loss por horizonte** (menor = mejor)")
    left.line_chart(data.set_index("Horizonte")[["Log loss", "Baseline"]], height=300)
    right.markdown("**Retorno neto por horizonte (%)**")
    right.line_chart(data.set_index("Horizonte")[["Retorno %"]], height=300)
    st.dataframe(data, hide_index=True, width="stretch")


def _render_report(label: str, payload: dict, csv_paths: list[Path]) -> None:
    if not payload:
        st.info("No hay JSON asociado; se muestra solo el documento.")
        return
    if label.startswith("Comparación"):
        _render_feature_comparison(payload)
    elif label.startswith("Barrido"):
        _render_horizon_sweep(payload)
    else:
        _render_walk_forward(payload, csv_paths)

st.title("Trading AI — apoyo a decisiones")
st.caption(
    "Señales probabilísticas con opción explícita NO_TRADE. "
    "Análisis automatizado: no es asesoría financiera."
)

mode = "DEMO" if settings.demo_mode else "PRODUCCIÓN"
st.sidebar.header("Estado")
st.sidebar.metric("Modo", mode)
st.sidebar.metric("Activos", ", ".join(settings.assets))
st.sidebar.metric("Horizonte", settings.horizon)
st.sidebar.caption(f"BD: `{settings.db_path}`")
if settings.demo_mode:
    st.sidebar.info("Modo demo: los datos pueden ser sintéticos y no deben usarse para decidir.")

tab_resumen, tab_senales, tab_riesgo, tab_backtests, tab_auditoria = st.tabs(
    ["Resumen", "Señales", "Riesgo", "Backtests", "Auditoría"]
)

signals = []
if settings.db_path.exists():
    try:
        signals = store.latest_signals(limit=50)
    except Exception:  # noqa: BLE001 - la BD puede existir sin esquema
        signals = []

with tab_resumen:
    if not signals:
        st.warning("No hay señales registradas todavía.")
        st.code("python -m jobs.daily_run", language="bash")
        st.caption("Ejecuta la corrida diaria para poblar la base y generar el primer reporte.")
    else:
        actionable = [s for s in signals if s["is_actionable"]]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Señales", len(signals))
        c2.metric("Accionables", len(actionable))
        c3.metric("NO_TRADE", sum(1 for s in signals if s["signal"] == "NO_TRADE"))
        c4.metric("Última señal", signals[0]["timestamp"][:16].replace("T", " "))

        reports = sorted((ROOT / "var" / "reports").glob("reporte-*.md"), reverse=True)
        if reports:
            st.subheader("Último reporte")
            st.markdown(reports[0].read_text(encoding="utf-8"))

    st.subheader("Mercado")
    st.caption("Velas diarias guardadas en la base, con medias móviles de 30 y 200 días.")
    if settings.assets:
        for asset, market_tab in zip(settings.assets, st.tabs(list(settings.assets)), strict=False):
            with market_tab:
                candles = store.load_candles(asset, limit=365, source="binance")
                source_label = "binance"
                if not candles:
                    candles = store.load_candles(asset, limit=365)
                    source_label = "sin filtrar"
                if len(candles) < 2:
                    st.info(f"Sin velas guardadas para {asset}.")
                    continue

                frame = pd.DataFrame(
                    {"fecha": [c.ts for c in candles], "cierre": [c.close for c in candles]}
                ).sort_values("fecha")
                frame["SMA 30"] = frame["cierre"].rolling(30).mean()
                frame["SMA 200"] = frame["cierre"].rolling(200).mean()
                st.line_chart(
                    frame.set_index("fecha")[["cierre", "SMA 30", "SMA 200"]], height=300
                )

                last = candles[-1]
                age_hours = (datetime.now(UTC) - last.ts).total_seconds() / 3600
                st.caption(
                    f"Fuente `{source_label}` · última vela {last.ts:%Y-%m-%d} · "
                    f"antigüedad {age_hours:.1f} h"
                )
                if age_hours > 36:
                    st.warning(
                        "El dato tiene más de 36 h: revisar la ingesta antes de leer cualquier señal."
                    )

with tab_senales:
    if signals:
        st.dataframe(
            [
                {
                    "Activo": s["asset"],
                    "Señal": s["signal"],
                    "Prob. neta": round(s["probability_net"], 3),
                    "Ret. neto est.": f"{s['expected_ret_net'] * 100:+.2f}%",
                    "Confianza": s["confidence"],
                    "Rank": s["rank_position"],
                    "Accionable": bool(s["is_actionable"]),
                    "Calidad": s["data_quality"],
                    "Timestamp": s["timestamp"][:16].replace("T", " "),
                }
                for s in signals
            ],
            width="stretch",
            hide_index=True,
        )

        payload_files = sorted((ROOT / "var" / "reports").glob("reporte-*.json"), reverse=True)
        if payload_files:
            with st.expander("JSON de la última corrida"):
                st.json(json.loads(payload_files[0].read_text(encoding="utf-8")))
    else:
        st.info("Sin datos. Ejecuta la corrida diaria en modo demo.")

with tab_riesgo:
    limits = RiskLimits()
    st.subheader("Límites activos")
    st.table(
        {
            "Límite": [
                "Riesgo por operación",
                "Exposición por activo",
                "Pérdida diaria",
                "Pérdida semanal",
                "Drawdown máximo",
                "Operaciones por día",
                "Correlación máxima",
            ],
            "Valor": [
                f"{limits.max_risk_per_trade_pct}%",
                f"{limits.max_exposure_per_asset_pct}%",
                f"{limits.max_daily_loss_pct}%",
                f"{limits.max_weekly_loss_pct}%",
                f"{limits.max_drawdown_pct}%",
                f"{limits.max_trades_per_day}",
                f"{limits.max_correlation}",
            ],
        }
    )
    st.warning(
        "El kill switch debe poder activarse manualmente y no depende del LLM ni del agente. "
        "Ver RISK_LIMITS.md."
    )

with tab_backtests:
    st.subheader("Evidencia de backtesting")
    st.caption(
        "Reportes y predicciones guardadas, leídos tal cual. Las gráficas solo derivan "
        "métricas de esos datos; no recalculan señales ni deciden nada. "
        "Un backtest no predice el futuro: mide si el método habría tenido ventaja."
    )
    reports = _backtest_reports()
    if not reports:
        st.warning("No hay reportes de backtest todavía.")
        st.code(
            "python -m backtests.run_backtest --source live --days 1000 --assets BTC,ETH",
            language="bash",
        )
    else:
        labels = [f"{label} — {path.stem}" for label, path in reports]
        choice = st.selectbox(
            "Reporte", options=list(range(len(reports))), format_func=lambda i: labels[i]
        )
        label, path = reports[choice]
        stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        st.caption(f"{label} · `{path.relative_to(ROOT)}` · generado {stamp}")
        st.download_button(
            "Descargar Markdown",
            data=path.read_text(encoding="utf-8"),
            file_name=path.name,
            mime="text/markdown",
        )

        payload = _load_json(path.with_suffix(".json"))
        csv_paths = sorted(path.parent.glob(f"{path.stem}-*.csv"))
        try:
            _render_report(label, payload, csv_paths)
        except Exception as exc:  # noqa: BLE001 - el panel no debe caerse por un reporte
            st.warning(f"No se pudieron dibujar las métricas de este reporte: {exc}")

        with st.expander("Documento completo"):
            st.markdown(path.read_text(encoding="utf-8"))

        if payload:
            with st.expander("Datos crudos (JSON)"):
                st.json(payload)

with tab_auditoria:
    st.subheader("Trazabilidad")
    st.write(
        "Cada señal guarda: activo, horizonte, timestamp, features, versión de modelo y código, "
        "fuente de datos, costos, regla de riesgo aplicada y resultado posterior."
    )
    db_path = Path(settings.db_path)
    if db_path.exists():
        st.caption(f"Base de datos: `{db_path}` · {db_path.stat().st_size / 1024:.1f} KB")
    st.caption("Tablas: assets, market_candles, signals, paper_trades, data_quality_events, audit_events.")
