"""Panel Streamlit del MVP (fase 1).

Solo lectura: muestra lo que el motor calculó y guardó. No calcula señales ni
mantiene procesos de ingesta, tal como exige el plan.
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from src.config.settings import ROOT, Settings
from src.data.store import Store
from src.quant.risk import RiskLimits

st.set_page_config(page_title="Trading AI — apoyo a decisiones", page_icon="📊", layout="wide")

settings = Settings.from_env()
store = Store(settings.db_path)

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

tab_resumen, tab_senales, tab_riesgo, tab_auditoria = st.tabs(
    ["Resumen", "Señales", "Riesgo", "Auditoría"]
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
            use_container_width=True,
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
                limits.max_trades_per_day,
                limits.max_correlation,
            ],
        }
    )
    st.warning(
        "El kill switch debe poder activarse manualmente y no depende del LLM ni del agente. "
        "Ver RISK_LIMITS.md."
    )

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
