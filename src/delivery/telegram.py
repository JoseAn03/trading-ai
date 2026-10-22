"""Entrega opcional por Telegram.

El panel es la fuente de verdad; esto solo notifica. Se activa por entorno y
nunca se ejecuta si faltan credenciales.
"""

from __future__ import annotations

import logging

import httpx

from src.config.settings import Settings

log = logging.getLogger("telegram")

API_BASE = "https://api.telegram.org"
MAX_LENGTH = 4000


def format_message(subject: str, body: dict) -> str:
    lines = [subject, "", str(body.get("executive_summary", ""))]

    opportunities = body.get("top_opportunities") or []
    if opportunities:
        lines.append("")
        lines.append("Oportunidades:")
        for item in opportunities[:5]:
            lines.append(
                f"- {item.get('asset', '?')} ({item.get('signal', '?')}): "
                f"{item.get('why', '')}"
            )

    notes = body.get("no_trade_notes") or []
    if notes:
        lines.append("")
        lines.append("Sin operar:")
        for note in notes[:5]:
            lines.append(f"- {note}")

    lines += ["", str(body.get("disclaimer", "No es asesoria financiera."))]
    text = "\n".join(lines).strip()
    return text[:MAX_LENGTH]


def deliver(
    subject: str,
    body: dict,
    settings: Settings,
    *,
    transport: httpx.BaseTransport | None = None,
) -> bool:
    if not settings.telegram_enabled:
        return False
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        log.warning("Telegram habilitado sin token o chat_id; no se envia")
        return False

    url = f"{API_BASE}/bot{settings.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": format_message(subject, body),
        "disable_web_page_preview": True,
    }
    try:
        with httpx.Client(timeout=20.0, transport=transport) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("Telegram fallo, se continua sin entrega: %s", exc)
        return False
    return True
