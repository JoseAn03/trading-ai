"""Reporte diario accionable (sección 10.2 del plan).

Se genera siempre en dos capas:
1. Determinista (Markdown + JSON), sin LLM. Es la fuente de verdad.
2. Opcional: explicación redactada por LLM, validada con Pydantic.

Si el LLM falla o devuelve algo inválido, se publica la capa determinista.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, Field, ValidationError

from src.quant.risk import RiskDecision
from src.schemas.signal import SignalBatch, SignalContract


class LLMReport(BaseModel):
    subject: str = Field(max_length=120)
    executive_summary: str = Field(max_length=800)
    top_opportunities: list[dict[str, str]] = Field(default_factory=list)
    no_trade_notes: list[str] = Field(default_factory=list)
    data_health_note: str = ""
    disclaimer: str = (
        "Análisis generado por un sistema automatizado. No es asesoría financiera."
    )


@dataclass
class DailyReport:
    subject: str
    markdown: str
    payload: dict

    def save(self, path) -> None:
        from pathlib import Path

        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.markdown, encoding="utf-8")


def _fmt_pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def _table_row(s: SignalContract, decision: RiskDecision | None) -> str:
    size = decision.suggested_size_pct if decision else 0.0
    return (
        f"| {s.asset} | {s.signal.value} | {s.probability_net_positive:.2f} | "
        f"{_fmt_pct(s.expected_return_net)} | {s.confidence.value} | {s.horizon} | "
        f"{size:.2f}% | {s.reasons[0] if s.reasons else '—'} |"
    )


def build_report(
    batch: SignalBatch,
    *,
    decisions: dict[str, RiskDecision] | None = None,
    generated_at: datetime | None = None,
    llm_text: LLMReport | None = None,
) -> DailyReport:
    decisions = decisions or {}
    stamp = generated_at or batch.generated_at
    actionable = [s for s in batch.signals if s.is_actionable]
    no_trade = [s for s in batch.signals if not s.is_actionable]
    counts = batch.summarise()

    subject = (
        f"Reporte Trading AI — {stamp:%d %b %Y} | "
        f"{len(actionable)} oportunidad{'es' if len(actionable) != 1 else ''} accionable"
        f"{'s' if len(actionable) != 1 else ''}"
    )

    lines: list[str] = [f"# {llm_text.subject if llm_text else subject}", ""]
    lines += [
        f"**Generado:** {stamp.isoformat(timespec='minutes')} · "
        f"**Horizonte:** {batch.horizon} · **Modelo:** `{batch.model_version}` · "
        f"**Código:** `{batch.code_version}`",
        "",
        "> Análisis automatizado. No es asesoría financiera ni garantía de rentabilidad.",
        "",
        "## Resumen ejecutivo",
        "",
    ]

    if llm_text:
        lines += [llm_text.executive_summary, ""]
    else:
        lines += [
            f"Se revisaron **{len(batch.signals)} activos**: {counts.get('LONG_BIAS', 0)} con sesgo largo, "
            f"{counts.get('RISK_OFF', 0)} en risk-off y {counts.get('NO_TRADE', 0)} sin operar. "
            f"Accionables tras costos: **{len(actionable)}**.",
            "",
        ]

    lines += [
        "## Top oportunidades",
        "",
    ]
    if actionable:
        lines += [
            "| Activo | Señal | Score heurístico | Ret. neto est. | Conf. | Horizonte | Tamaño | Razón principal |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        lines += [_table_row(s, decisions.get(s.asset)) for s in actionable]
    else:
        lines.append("_Ninguna señal supera los umbrales de accionabilidad hoy._")
    lines.append("")

    if llm_text and llm_text.top_opportunities:
        lines += ["## Detalle", ""]
        for item in llm_text.top_opportunities:
            lines.append(f"- **{item.get('asset', '?')}** ({item.get('signal', '?')}): {item.get('why', '')}")
        lines.append("")

    lines += ["## No operar (NO_TRADE / RISK_OFF)", ""]
    if no_trade:
        for s in no_trade:
            reason = s.invalidators[0] if s.invalidators else "sin ventaja neta suficiente"
            lines.append(f"- **{s.asset}** — {s.signal.value}: {reason}")
    else:
        lines.append("- _Ninguno._")
    lines.append("")

    lines += ["## Salud de los datos", ""]
    for s in batch.signals:
        lines.append(
            f"- **{s.asset}**: calidad `{s.data_quality.value}`, "
            f"antigüedad {s.age_minutes:.0f} min, "
            f"costos {s.costs.total_rate * 100:.2f}% ida y vuelta"
        )
    if llm_text and llm_text.data_health_note:
        lines += ["", llm_text.data_health_note]
    lines.append("")

    lines += ["## Criterios de cancelación", ""]
    if actionable:
        for s in actionable:
            for inv in s.invalidators[:3]:
                lines.append(f"- **{s.asset}**: {inv}")
    else:
        lines.append("_Sin operaciones abiertas: no hay condiciones que vigilar._")
    lines.append("")

    lines += [
        "---",
        "",
        llm_text.disclaimer if llm_text else "Análisis generado por un sistema automatizado. No es asesoría financiera.",
    ]

    payload = {
        "subject": llm_text.subject if llm_text else subject,
        "generated_at": stamp.isoformat(),
        "horizon": batch.horizon,
        "model_version": batch.model_version,
        "code_version": batch.code_version,
        "summary": counts,
        "signals": [s.model_dump(mode="json") for s in batch.signals],
    }

    return DailyReport(subject=subject, markdown="\n".join(lines), payload=payload)


def validate_llm_output(raw: str) -> LLMReport | None:
    """Valida la salida del LLM. Si no cumple el esquema, se descarta."""
    try:
        return LLMReport.model_validate_json(raw)
    except (ValidationError, ValueError):
        return None
