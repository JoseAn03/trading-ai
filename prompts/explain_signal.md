# Prompt: explicación de señal (capa 6)

Versión: `explain-signal-v1` · Última revisión: 2026-10-22

## Reglas duras

1. El LLM recibe un JSON ya calculado. **No calcula nada**, no estima precios, no reordena por criterio propio.
2. No inventa cifras: todo número debe existir en el payload.
3. No promete rentabilidad ni usa "seguro", "garantizado", "sin riesgo".
4. No recomienda operar: describe probabilidad, costos, riesgos e invalidadores.
5. Si `data_quality != "valid"`, debe decirlo en la primera frase.
6. Salida **solo JSON**, sin markdown ni texto adicional.
7. Si no puede cumplir el esquema, debe devolver `{}` — el sistema publicará la versión determinista.

## Entrada (resumen del contrato)

```json
{
  "asset": "BTC-USD",
  "horizon": "24h",
  "signal": "LONG_BIAS",
  "probability_up": 0.61,
  "probability_net_positive": 0.57,
  "expected_return_net": 0.0085,
  "confidence": "medium",
  "rank_score": 0.72,
  "reasons": ["..."],
  "invalidators": ["..."],
  "costs": {"estimated_slippage_bps": 4, "commission_bps": 6, "funding_impact": 0.0002},
  "data_quality": "valid",
  "is_actionable": true
}
```

## Salida esperada

```json
{
  "subject": "Reporte Trading AI — 21 Oct 2026 | 2 oportunidades accionables",
  "executive_summary": "…",
  "top_opportunities": [{"asset": "BTC", "signal": "LONG_BIAS", "why": "…"}],
  "no_trade_notes": ["…"],
  "data_health_note": "…",
  "disclaimer": "Análisis automatizado. No es asesoría financiera."
}
```

## Manejo de fallos

| Caso | Acción |
| --- | --- |
| JSON inválido | Descartar y publicar reporte determinista |
| Cifra inventada | Descartar (revisión manual del prompt) |
| Timeout del proveedor LLM | Publicar determinista, registrar en `audit_events` |
| Costo del LLM por encima del techo mensual | Desactivar `TRADING_LLM_ENABLED` hasta el mes siguiente |

## Nota de implementación

El prompt vive en código (`src/reporting/prompt.py`) y se versiona junto al modelo. Cambiar el prompt **no puede** cambiar el cálculo cuantitativo: los tests de regresión lo verifican.
