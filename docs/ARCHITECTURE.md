# Arquitectura

## Flujo

```
Proveedor (CoinGecko / sintético)
        ↓
Ingesta + normalización (src/data/ingestion.py)
        ↓
Calidad de datos: frescura, gaps, outliers (src/data/quality.py)  ──► data_quality_events
        ↓
SQLite: market_candles, raw_provider_responses
        ↓
Features deterministas (src/quant/features.py)
        ↓
Motor de señal (src/quant/signals.py)  ──► SignalContract (Pydantic)
        ↓
Gestión de riesgo / circuit breakers (src/quant/risk.py)
        ↓
Ranking + contrato de señal  ──► signals (payload JSON completo)
        ↓
Reporte diario determinista (src/reporting/daily_report.py)  ──► var/reports/*.md|json
        ↓
[Opcional] LLM redacta explicación → se valida con Pydantic → si falla, se descarta
        ↓
Panel Streamlit / Telegram (futuro)
```

## Módulos y responsabilidad única

| Módulo | Hace | No hace |
| --- | --- | --- |
| `src/data/adapters` | Habla con proveedores | No normaliza reglas de negocio |
| `src/data/quality` | Decide si el dato sirve | No calcula señales |
| `src/quant/features` | Calcula variables | No decide |
| `src/quant/signals` | Convierte features en señal | No dimensiona riesgo final |
| `src/quant/risk` | Bloquea y dimensiona | No explica |
| `src/reporting` | Redacta y estructura | No calcula |
| `src/paper_trading` | Simula y contabiliza | No toca exchanges |
| `jobs/daily_run` | Orquesta el flujo | No contiene lógica de dominio |

## Invariantes técnicas

1. `SignalContract` es el único formato válido de señal; si no valida, no se publica.
2. `data_timestamp ≤ timestamp`, siempre.
3. `NO_TRADE` nunca es accionable (validado en el modelo, no a mano).
4. Toda escritura en BD va con versión de modelo y código.
5. El motor no usa `datetime.now()` internamente: el reloj se inyecta (`now`), lo que hace los tests reproducibles.
6. Los proveedores pueden fallar: la ingesta degrada a caché y la calidad marca el dato como no fresco.

## Decisiones de diseño

- **SQLite primero**, Postgres cuando haya worker 24/7. Evita infraestructura prematura.
- **numpy para el baseline**, sin scikit-learn: menos dependencias y el modelo es explícito y auditable.
- **Streamlit solo lee** la base; no mantiene procesos ni calcula señales.
- **Prompts versionados** en `prompts/`, no embebidos en la lógica.
- **Sin ORM**: SQL explícito y tablas visibles, más fácil de auditar en esta etapa.
