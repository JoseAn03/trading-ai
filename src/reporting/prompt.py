"""Plantillas de prompt para la capa de explicación.

Reglas duras:
- El LLM recibe un JSON ya calculado. No calcula nada.
- Debe devolver JSON validable. Si no valida, no se publica.
- Nunca puede inventar cifras que no estén en el payload.
"""

from __future__ import annotations

PROMPT_VERSION = "explain-signal-v1"

SYSTEM_PROMPT = """Eres un redactor técnico de reportes de trading.
Recibes un JSON con señales ya calculadas por un motor determinista.

Reglas obligatorias:
1. No inventes cifras. Usa solo números presentes en el JSON y cópialos tal cual.
2. No prometas rentabilidad ni uses lenguaje de certeza.
3. No recomiendes operar: describes probabilidades, costos, riesgos e invalidadores.
4. Si un dato falta o está degradado, dilo explícitamente.
5. Si no hay señales accionables, deja `top_opportunities` vacío y explica por qué no se opera.
6. Menciona solo activos y señales presentes en el JSON.
7. Devuelve exclusivamente JSON con el esquema pedido, sin texto adicional.
"""

USER_TEMPLATE = """Genera el reporte diario a partir de este JSON:

{payload}

Esquema de salida:
{{
  "subject": "string (máx 90 caracteres)",
  "executive_summary": "string (máx 500 caracteres)",
  "top_opportunities": [
    {{"asset": "string", "signal": "string", "why": "string (máx 200 caracteres)"}}
  ],
  "no_trade_notes": ["string"],
  "data_health_note": "string",
  "disclaimer": "string"
}}
"""


def build_user_prompt(payload_json: str) -> str:
    return USER_TEMPLATE.format(payload=payload_json)


def build_messages(payload_json: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(payload_json)},
    ]
