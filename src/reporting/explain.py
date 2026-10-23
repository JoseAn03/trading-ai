"""Explicacion diaria en dos capas (fase 6).

1. Determinista: se construye con los datos ya calculados. Siempre existe.
2. LLM opcional: reescribe esa explicacion en JSON. Se valida el esquema, se
   comprueba que no invente cifras/activos/senales y, si falla, se descarta.

El LLM nunca toca el motor: recibe un payload cerrado y su salida no se usa
para calcular senales, tamano ni decisiones de riesgo.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime

from src.config.settings import Settings
from src.data.store import Store
from src.llm.client import ChatClient, LLMConfig, LLMError
from src.llm.guard import clean_json_text, guard_explanation
from src.paper_trading.shadow import PaperStats
from src.quant.risk import RiskDecision
from src.reporting.daily_report import LLMReport, validate_llm_output
from src.reporting.prompt import build_messages
from src.schemas.signal import SignalBatch, SignalContract

log = logging.getLogger("explain")

DISCLAIMER = "Análisis generado por un sistema automatizado. No es asesoría financiera."


@dataclass(frozen=True)
class Explanation:
    id: int
    kind: str
    subject: str
    body: dict
    payload: dict
    fallback_reason: str | None
    provider: str
    model: str
    prompt_version: str
    cost_usd: float
    input_tokens: int
    output_tokens: int


def _signal_payload(signal: SignalContract, decision: RiskDecision | None) -> dict:
    return {
        "asset": signal.asset,
        "signal": signal.signal.value,
        "horizon": signal.horizon,
        "probability_up": round(signal.probability_up, 4),
        "probability_down": round(signal.probability_down, 4),
        "probability_net_positive": round(signal.probability_net_positive, 4),
        "probability_net_positive_pct": round(signal.probability_net_positive * 100, 2),
        "expected_return_net": round(signal.expected_return_net, 6),
        "expected_return_net_pct": round(signal.expected_return_net * 100, 3),
        "confidence": signal.confidence.value,
        "rank_score": round(signal.rank_score, 4),
        "rank_position": signal.rank_position,
        "reasons": list(signal.reasons),
        "invalidators": list(signal.invalidators),
        "costs": {
            "commission_bps": signal.costs.commission_bps,
            "estimated_slippage_bps": signal.costs.estimated_slippage_bps,
            "funding_impact": signal.costs.funding_impact,
            "total_rate": round(signal.costs.total_rate, 6),
            "total_bps": round(signal.costs.total_rate * 10_000, 2),
        },
        "suggested_size_pct": signal.risk.suggested_size_pct,
        "risk_allowed": decision.allowed if decision else None,
        "risk_blocked_by": list(decision.blocked_by) if decision else [],
        "data_quality": signal.data_quality.value,
        "age_minutes": round(signal.age_minutes, 1),
        "data_timestamp": signal.data_timestamp.isoformat(),
        "is_actionable": signal.is_actionable,
    }


def build_payload(
    batch: SignalBatch,
    decisions: dict[str, RiskDecision],
    paper: PaperStats,
    *,
    generated_at: datetime,
) -> dict:
    """Hechos cerrados que el LLM puede redactar. Nada se calcula alla."""
    signals = [_signal_payload(s, decisions.get(s.asset)) for s in batch.signals]
    return {
        "generated_at": generated_at.isoformat(),
        "horizon": batch.horizon,
        "model_version": batch.model_version,
        "code_version": batch.code_version,
        "summary": batch.summarise(),
        "assets_count": len(signals),
        "actionable_count": sum(1 for s in signals if s["is_actionable"]),
        "signals": signals,
        "paper": {
            "mode": "shadow",
            "trades": paper.trades,
            "open_trades": paper.open_trades,
            "win_rate": round(paper.win_rate, 4),
            "equity": round(paper.equity, 6),
            "total_return_pct": round(paper.total_return_pct, 3),
            "avg_net_return": round(paper.avg_net_return, 6),
            "max_drawdown_pct": round(paper.max_drawdown_pct, 3),
            "avg_entry_delay_pct": round(paper.avg_entry_delay_pct, 3),
        },
        "constraints": [
            "Sin ejecucion de ordenes reales.",
            "Sin claves de exchange.",
            "El LLM no calcula probabilidades, tamanos ni decisiones.",
            "NO_TRADE es una decision valida.",
        ],
    }


def build_deterministic(payload: dict, *, generated_at: datetime) -> dict:
    signals = payload["signals"]
    actionable = [s for s in signals if s["is_actionable"]]
    count = len(actionable)

    subject = (
        f"Reporte Trading AI — {generated_at:%d %b %Y} | "
        f"{count} oportunidad{'es' if count != 1 else ''} accionable"
        f"{'s' if count != 1 else ''}"
    )

    summary = [f"Se revisaron {len(signals)} activos con horizonte {payload['horizon']}."]
    if actionable:
        names = ", ".join(s["asset"] for s in actionable)
        summary.append(f"Accionables tras costos: {count} ({names}).")
    else:
        summary.append("Ninguna señal supera los umbrales de accionabilidad tras costos.")

    paper = payload["paper"]
    if paper["trades"] or paper["open_trades"]:
        summary.append(
            f"Paper en sombra: {paper['trades']} cerradas, {paper['open_trades']} abiertas, "
            f"capital {paper['equity']:.4f}."
        )
    else:
        summary.append("Paper en sombra sin operaciones: no hubo señal accionable.")

    top_opportunities = [
        {
            "asset": s["asset"],
            "signal": s["signal"],
            "why": (
                f"score heurístico {s['probability_net_positive_pct']:.1f}%, "
                f"retorno neto esperado {s['expected_return_net_pct']:+.2f}%, "
                f"tamaño sugerido {s['suggested_size_pct']:.2f}%"
            ),
        }
        for s in actionable
    ]

    no_trade_notes = [
        f"{s['asset']} — {s['signal']}: "
        f"{s['invalidators'][0] if s['invalidators'] else 'sin ventaja neta suficiente'}"
        for s in signals
        if not s["is_actionable"]
    ]

    degraded = [s for s in signals if s["data_quality"] != "valid"]
    max_age = max((s["age_minutes"] for s in signals), default=0.0)
    if degraded:
        detail = ", ".join(f"{s['asset']} {s['data_quality']}" for s in degraded)
        health = (
            f"Calidad de datos degradada en {detail}. Antigüedad máxima {max_age:.0f} min. "
            "No se debe operar con datos no válidos."
        )
    else:
        health = (
            f"Calidad de datos válida en todos los activos. "
            f"Antigüedad máxima {max_age:.0f} min."
        )

    return {
        "subject": subject,
        "executive_summary": " ".join(summary),
        "top_opportunities": top_opportunities,
        "no_trade_notes": no_trade_notes,
        "data_health_note": health,
        "disclaimer": DISCLAIMER,
    }


def _month_start(stamp: datetime) -> datetime:
    return stamp.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def explain_daily(
    *,
    batch: SignalBatch,
    decisions: dict[str, RiskDecision],
    paper: PaperStats,
    store: Store,
    settings: Settings,
    generated_at: datetime,
    client: ChatClient | None = None,
) -> Explanation:
    """Genera, valida y persiste la explicacion del dia. Nunca falla el flujo."""
    payload = build_payload(batch, decisions, paper, generated_at=generated_at)
    body = build_deterministic(payload, generated_at=generated_at)

    kind = "deterministic"
    provider = "rules"
    model = "deterministic"
    reason: str | None = None
    raw: str | None = None
    input_tokens = 0
    output_tokens = 0
    cost_usd = 0.0

    config = LLMConfig.from_settings(settings)
    if not settings.llm_enabled:
        reason = "llm_disabled"
    elif not config.configured:
        reason = "llm_not_configured"
    else:
        prompt_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        messages = build_messages(prompt_json)
        spent = store.llm_spend_since(_month_start(generated_at))
        worst_case = config.worst_case_cost(
            len(prompt_json) + sum(len(m["content"]) for m in messages)
        )
        if spent + worst_case > settings.llm_monthly_budget_usd:
            reason = "budget_exceeded"
        else:
            try:
                response = (client or ChatClient(config)).chat_json(messages)
            except LLMError as exc:
                log.warning("LLM no disponible, se publica determinista: %s", exc)
                reason = "llm_error"
            else:
                raw = response.text
                input_tokens = response.input_tokens
                output_tokens = response.output_tokens
                cost_usd = config.estimate_cost(input_tokens, output_tokens)

                parsed = validate_llm_output(clean_json_text(raw))
                if parsed is None:
                    reason = "invalid_schema"
                else:
                    rejected = guard_explanation(
                        parsed, payload, generated_at=generated_at
                    )
                    if rejected:
                        reason = rejected
                    else:
                        kind = "llm"
                        provider = config.base_url
                        model = response.model
                        body = parsed.model_dump()

    explanation_id = store.save_explanation(
        generated_at=generated_at,
        kind=kind,
        provider=provider,
        model=model,
        prompt_version=settings.llm_prompt_version,
        subject=str(body["subject"]),
        body=body,
        raw_response=raw,
        valid=True,
        fallback_reason=reason,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
    )
    store.audit(
        "explain",
        kind,
        f"id={explanation_id} reason={reason or 'ok'} cost_usd={cost_usd:.6f}",
    )

    return Explanation(
        id=explanation_id,
        kind=kind,
        subject=str(body["subject"]),
        body=body,
        payload=payload,
        fallback_reason=reason,
        provider=provider,
        model=model,
        prompt_version=settings.llm_prompt_version,
        cost_usd=cost_usd,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


__all__ = [
    "DISCLAIMER",
    "Explanation",
    "LLMReport",
    "build_deterministic",
    "build_payload",
    "explain_daily",
]
