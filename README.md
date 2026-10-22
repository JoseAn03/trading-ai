# trading-ai

Plataforma personal de **apoyo a decisiones** para cripto. No adivina el precio: estima la probabilidad de que el retorno neto (después de costos) sea positivo en un horizonte, ordena las mejores oportunidades y dice explícitamente **cuándo no operar**.

> Análisis automatizado. No es asesoría financiera ni garantía de rentabilidad.
> **No ejecuta órdenes reales** y no almacena claves de exchange.

## Estado actual

| Fase del plan | Estado |
| --- | --- |
| 0. Definición y alcance | ✅ `PRODUCT_DECISIONS.md`, `RISK_LIMITS.md` |
| 1. Esqueleto y panel | ✅ Streamlit + Docker + CI |
| 2. Ingesta y calidad de datos | ✅ Binance (OHLCV real) + CoinGecko opcional + validación + SQLite |
| 3. Motor cuantitativo mínimo | ✅ features + señal determinista + circuit breakers |
| 4. Backtesting | ✅ walk-forward real, calibración (ECE) y gate de robustez |
| 5. Paper trading | ✅ persistente en sombra: congela la entrada, resuelve al vencer y se ve en el panel |
| 6. IA de explicación | ✅ determinista + LLM validado con guardia de cifras y fallback; entrega Telegram opcional |
| 7. Operación 24/7 + agente | ⛔ no empezado (a propósito) |

## Arranque rápido (Windows)

Requiere **Python 3.11+**. En este equipo no está instalado todavía:

```powershell
winget install -e --id Python.Python.3.11
```

Luego, desde `C:\Users\Andres\trading-ai`:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# 1) tests
pytest

# 2) corrida demo (datos sintéticos, sin red)
python -m jobs.daily_run

# 3) corrida real (Binance, sin API key)
python -m jobs.daily_run --live

# 3b) corrida real con explicación LLM (requiere TRADING_LLM_API_KEY)
python -m jobs.daily_run --live --llm

# 4) backtest con datos reales + calibración
python -m backtests.run_backtest --source live --days 1000 --assets BTC,ETH

# 5) panel
streamlit run app/streamlit_app.py
```

Salidas en `var/reports/`: reporte diario (`.md`/`.json`), backtest (`.md`/`.json`)
y predicciones auditables (`.csv`). Base en `var/trading.db`.

## Explicación y entrega (fase 6)

Cada corrida guarda una explicación en la tabla `explanations`, siempre en dos capas:

1. **Determinista** (siempre): se arma con las cifras ya calculadas por el motor.
2. **LLM** (opcional): reescribe ese JSON y se valida con Pydantic. Si inventa una cifra,
   un activo o una señal, o usa lenguaje de garantía, se descarta y se publica la
   determinista con el motivo en `fallback_reason`.

El LLM no calcula, no decide y no cambia señales, tamaños ni riesgo. El costo estimado
de cada llamada se registra y hay un techo mensual (`TRADING_LLM_MONTHLY_BUDGET_USD`).
La explicación se ve en la pestaña **Explicación** del panel.

Con `TRADING_TELEGRAM_ENABLED=true`, `TRADING_TELEGRAM_BOT_TOKEN` y
`TRADING_TELEGRAM_CHAT_ID`, también se envía por Telegram. Sin credenciales no se envía nada.

Docker:

```powershell
docker build -t trading-ai .
docker run --rm -p 8501:8501 trading-ai
```

## Estructura

```text
trading-ai/
├── app/                    # panel Streamlit (solo lectura)
├── src/
│   ├── config/             # settings por entorno
│   ├── data/               # adaptadores, ingesta, calidad, SQLite
│   ├── quant/              # features, señales, riesgo
│   ├── models/             # baseline logístico (numpy)
│   ├── reporting/          # reporte diario + explicación determinista/LLM
│   ├── llm/                # cliente compatible OpenAI + guardias de salida
│   ├── delivery/           # notificación opcional (Telegram)
│   ├── paper_trading/      # simulación persistente (modo sombra)
│   └── schemas/            # contratos Pydantic
├── jobs/daily_run.py       # corrida diaria end-to-end
├── backtests/              # walk-forward, métricas y CLI de backtest
├── tests/                  # unitarias + integración
├── prompts/                # prompts versionados
└── docs/                   # arquitectura y decisiones
```

## Invariantes (no negociables)

1. **Determinismo:** misma entrada → misma señal. Sin red, reloj ni azar dentro del motor.
2. **NO_TRADE es una respuesta válida**, no un fallo.
3. **Sin datos frescos no hay señal accionable.** La calidad se valida antes de calcular.
4. **El LLM y los agentes nunca calculan ni deciden:** reciben JSON ya calculado.
5. **Riesgo ejecutable por código:** circuit breakers y kill switch manual, fuera del LLM.
6. **Todo es auditable:** señal, features, versiones de modelo/código, costos y resultado posterior.
7. **Ningún secreto en Git.** Todo por variables de entorno.

## Comandos útiles

```powershell
pytest -q                       # tests
pytest --cov=src --cov-report=term-missing
ruff check .                    # lint
ruff format .                   # formato
python -m backtests.walk_forward    # humo con datos sintéticos
python -m backtests.run_backtest --source live --days 1000 --assets BTC,ETH
python -m backtests.compare_features --source live --days 1500 --feature-sets v0.1,v0.2
```

## Riesgos conocidos de esta versión

- El baseline es una **regresión logística simple sobre 5 features**: sirve para medir, no para confiar.
- Los costos son estáticos (bps fijos); no modelan profundidad de libro ni impacto de mercado.
- Binance devuelve pares **USDT**: se asume USDT ≈ USD en el backtest.
- CoinGecko ahora exige API key (`COINGECKO_API_KEY`); Binance es la fuente principal.
- **Backtest 2026-10-22 (1.000 días, BTC/ETH): sin ventaja demostrada.** Log loss 0,6978 vs 0,6931 del baseline. Ver `docs/BACKTEST-RESULTS.md`.
- **Barrido de horizontes 1–7 días: también sin ventaja.** El deterioro es monótono; no era un problema de horizonte.
- **A/B de features de régimen (v0.2): peor que v0.1** en log loss, calibración y retorno. Ver `docs/BACKTEST-RESULTS.md`.

## Próximos pasos

1. Mantener el **paper trading en sombra** acumulando historial prospectivo; no se promociona mientras no haya edge.
2. Si se sigue investigando, cambiar de **datos** (funding, open interest, on-chain), no de features de OHLCV.
3. Revisar el historial de **explicaciones y su costo**; ajustar el prompt no puede cambiar el cálculo.
4. Operación 24/7 (fase 7) solo con el historial y los límites revisados.
