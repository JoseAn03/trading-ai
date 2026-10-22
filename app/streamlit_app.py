"""Panel Streamlit del MVP (fase 1).

Solo lectura: muestra lo que el motor calculó y guardó. No calcula señales ni
mantiene procesos de ingesta, tal como exige el plan.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import streamlit as st

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
        "Reportes generados por los CLIs de backtest. Se leen tal cual, sin recalcular nada. "
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
        st.markdown(path.read_text(encoding="utf-8"))

        json_path = path.with_suffix(".json")
        if json_path.exists():
            with st.expander("Datos crudos (JSON)"):
                st.json(json.loads(json_path.read_text(encoding="utf-8")))

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
