"""Guardias de la salida del LLM.

Principio: el modelo solo puede reescribir hechos ya presentes en el payload.
Si inventa una cifra, un activo o una senal, se descarta y se publica la
explicacion determinista.
"""

from __future__ import annotations

import re
from datetime import datetime

from src.reporting.daily_report import LLMReport

NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
FORBIDDEN_RE = re.compile(
    r"\bgarantizad[oa]s?\b|\bsin riesgo\b|\briesgo cero\b|\bdebes comprar\b|"
    r"\bdeber[ií]as comprar\b|\brecomiendo comprar\b|\bcompra ahora\b|"
    r"\binvierte ahora\b|\bapuesta\b",
    re.IGNORECASE,
)


def clean_json_text(text: str) -> str:
    """Quita cercas de Markdown que algunos proveedores agregan igualmente."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _norm(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def allowed_numbers(payload: object, *, generated_at: datetime) -> set[str]:
    """Todos los numeros que el LLM puede citar, en varias representaciones."""
    values: list[float] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for item in node.values():
                walk(item)
        elif isinstance(node, list | tuple):
            for item in node:
                walk(item)
        elif isinstance(node, bool):
            return
        elif isinstance(node, int | float):
            values.append(float(node))
        elif isinstance(node, str):
            for token in NUMBER_RE.findall(node):
                values.append(float(token.replace(",", ".")))

    walk(payload)
    for part in (
        generated_at.year,
        generated_at.month,
        generated_at.day,
        generated_at.hour,
        generated_at.minute,
    ):
        values.append(float(part))

    allowed: set[str] = set()
    for value in values:
        for scale in (1.0, 100.0):
            scaled = value * scale
            for decimals in range(5):
                allowed.add(_norm(round(scaled, decimals)))
    return allowed


def invented_numbers(text: str, allowed: set[str]) -> list[str]:
    bad: list[str] = []
    for token in NUMBER_RE.findall(text):
        try:
            value = float(token.replace(",", "."))
        except ValueError:
            bad.append(token)
            continue
        if _norm(value) not in allowed:
            bad.append(token)
    return sorted(set(bad))


def guard_explanation(
    report: LLMReport,
    payload: dict,
    *,
    generated_at: datetime,
) -> str | None:
    """Devuelve el motivo del rechazo, o None si la explicacion es publicable."""
    parts = [
        report.subject,
        report.executive_summary,
        report.data_health_note,
        report.disclaimer,
        *report.no_trade_notes,
    ]
    parts += [f"{item.get('asset', '')} {item.get('signal', '')} {item.get('why', '')}" for item in report.top_opportunities]
    text = " ".join(parts)

    if FORBIDDEN_RE.search(text):
        return "frase_prohibida"

    bad = invented_numbers(text, allowed_numbers(payload, generated_at=generated_at))
    if bad:
        return "cifras_inventadas:" + ",".join(bad[:5])

    signals = payload.get("signals") or []
    by_asset = {str(s["asset"]).upper(): str(s["signal"]) for s in signals}
    for item in report.top_opportunities:
        asset = str(item.get("asset", "")).upper()
        if asset not in by_asset:
            return f"activo_inventado:{item.get('asset', '')}"
        claimed = str(item.get("signal", ""))
        if claimed and claimed != by_asset[asset]:
            return f"senal_inventada:{asset}"

    actionable = sum(1 for s in signals if s.get("is_actionable"))
    if actionable == 0 and report.top_opportunities:
        return "oportunidad_sin_senal_accionable"

    return None
