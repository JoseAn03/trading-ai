# Fase 2 — experimentos y holdout

## Objetivo

Buscar una configuración con mejor **retorno ajustado por riesgo** que la actual,
sin cambiar la meta del proyecto: paper trading, sin claves, sin exchange y sin
capital real. Una configuración solo se promociona si pasa el gate de abajo.

Este documento se escribe **antes** de abrir el holdout. Los candidatos y el gate
están congelados; después de ver el holdout no se añaden candidatos ni se cambia
el criterio.

## Datos y corte

- Fuente: Binance klines diarias (USDT ≈ USD), guardadas en `var/trading.db`.
- Activos: BTC y ETH.
- Periodo real: **2022-09-14 → 2026-10-22**, 1.500 velas por activo.
- Desarrollo: primeras **1.200** velas (80 %), hasta **2025-12-26**.
- Holdout: últimas **300** velas (20 %), desde **2025-12-27** hasta
  **2026-10-22**. No se usa para elegir nada.
- Las 120 velas `source=static` que también están en la BD son datos demo
  sintéticos de pruebas y **no** se cargan en estos experimentos (`source=binance`).

## Protocolo

- `build_samples` calcula features en t y etiqueta con el retorno t → t+h.
- Walk-forward: train 365 / test 60 / step 60; historia mínima **201** velas para
  todos los sets (así v0.1 y v0.3 ven el mismo periodo).
- Modelo base: regresión logística con estandarización y L2 (`LogisticBaseline`,
  500 épocas, lr 0.15, l2 0.01).
- Costo: **20 bps ida y vuelta** en todas las estrategias.
- Las reglas usan solo velas cerradas hasta t y aplican el peso al retorno t → t+1.
- Las probabilidades del modelo son OOS; el modelo de holdout se entrena solo con
  las 1.200 velas de desarrollo.
- No se usan datos de funding, open interest, on-chain ni order book.

### Candidatos congelados (14)

| Nombre | Tipo | Configuración |
| --- | --- | --- |
| `logit_v01_h1_t55` | logit | v0.1, h=1, p≥0.55 |
| `logit_v01_h1_t60` | logit | v0.1, h=1, p≥0.60 |
| `logit_v02_h1_t55` | logit | v0.2, h=1, p≥0.55 |
| `logit_v02_h1_t60` | logit | v0.2, h=1, p≥0.60 |
| `logit_v03_h1_t55` | logit | v0.3, h=1, p≥0.55 |
| `logit_v03_h1_t60` | logit | v0.3, h=1, p≥0.60 |
| `logit_v03_h3_t55` | logit | v0.3, h=3, p≥0.55 |
| `logit_v03_h5_t55` | logit | v0.3, h=5, p≥0.55 |
| `rule_buy_hold` | regla | comprar y mantener |
| `rule_momentum_50_20` | regla | close>SMA50 y ret20>0; salir al fallar |
| `rule_trend_200` | regla | close>SMA200 |
| `rule_meanrev_rsi30` | regla | RSI14<30 con close>SMA200; salir RSI>55 o 5 días |
| `rule_breakout_20` | regla | ruptura de máximo de 20 días; salir 10 días o mínimo de 10 |
| `rule_momentum_vol_target` | regla | momentum con peso min(1, 2 %/vol20) |

`v0.3` añade RSI14, retorno 20d, rango medio 20d, posición del cierre en el rango,
volatilidad 5d, ratio vol 5/20 y tendencia de volumen. Todo trailing.

## Gate de promoción

Un candidato solo se promociona si cumple **todo**:

1. **Log loss OOS** ≤ 0.6881 (baseline 0.6931 menos 0.005 de margen) y ≤ mejor
   `v0.1` menos 0.003.
2. **ECE ≤ 0.05** (calibración).
3. **Sharpe neto anualizado > 0.5**, con retorno neto > 0 y ≥ 30 operaciones.
4. **Max drawdown ≤ 10 %** (el límite de riesgo del sistema).
5. **≥ 60 % de meses positivos**.
6. **BTC y ETH ambos con retorno neto > 0**.

Las reglas sin probabilidad no pueden cumplir el punto 1; se reportan como
comparación, no como candidatas a promoción.

## Resultados de desarrollo (antes del holdout)

Comando:

```bash
python -m backtests.experiments dev --source cache --days 1620 --assets BTC,ETH
```

| Candidato | Log loss | ECE | Sharpe | Retorno neto | Max DD | Meses + | Activos + | Gate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `logit_v01_h1_t55` | 0.6986 | 0.0379 | 0.61 | +9.67 % | 26.8 % | 58 % | 1/2 | ❌ |
| `logit_v01_h1_t60` | 0.6986 | 0.0379 | −0.33 | −4.31 % | 15.7 % | 50 % | 0/2 | ❌ |
| `logit_v02_h1_t55` | 0.7031 | 0.0398 | −0.44 | −37.93 % | 60.3 % | 35 % | 0/2 | ❌ |
| `logit_v02_h1_t60` | 0.7031 | 0.0398 | 0.65 | +6.56 % | 22.4 % | 67 % | 1/2 | ❌ |
| `logit_v03_h1_t55` | 0.7032 | 0.0584 | 0.89 | +43.41 % | 39.7 % | 53 % | 2/2 | ❌ |
| `logit_v03_h1_t60` | 0.7032 | 0.0584 | **2.00** | **+56.68 %** | 22.4 % | 59 % | 2/2 | ❌ |
| `logit_v03_h3_t55` | 0.7190 | 0.0931 | 0.84 | +55.68 % | 46.7 % | 50 % | 1/2 | ❌ |
| `logit_v03_h5_t55` | 0.7356 | 0.1110 | 0.08 | −12.94 % | 53.5 % | 45 % | 1/2 | ❌ |
| `rule_buy_hold` | — | — | 0.89 | +204.69 % | 63.8 % | 50 % | 2/2 | ❌ |
| `rule_momentum_50_20` | — | — | 0.98 | +162.12 % | 46.9 % | 32 % | 2/2 | ❌ |
| `rule_trend_200` | — | — | 0.66 | +81.83 % | 38.7 % | 32 % | 2/2 | ❌ |
| `rule_meanrev_rsi30` | — | — | 0.67 | +22.93 % | 11.4 % | 22 % | 2/2 | ❌ |
| `rule_breakout_20` | — | — | 0.22 | +11.78 % | 49.8 % | 32 % | 1/2 | ❌ |
| `rule_momentum_vol_target` | — | — | 0.99 | +128.80 % | 37.5 % | 30 % | 2/2 | ❌ |

### Lectura de desarrollo

- **Ningún candidato pasa el gate.** El mejor log loss es 0.6986, todavía peor
  que el baseline 50/50 (0.6931). Añadir features v0.3 mejora el retorno aparente
  y el Sharpe, pero **empeora** log loss y calibración: es sobreajuste/ruido, no
  información direccional.
- `logit_v03_h1_t60` tiene el mejor Sharpe (2.00) y +56.7 % neto, pero log loss
  0.7032, ECE 0.058, drawdown 22.4 % y solo 59 % de meses positivos. No es
  promocionable.
- `buy & hold` gana mucho más (+204.7 %) pero con drawdown 63.8 %; no es una
  estrategia operable con el límite de 10 %.
- Las reglas de momentum mejoran el retorno, pero su consistencia mensual es baja
  (30–32 %); dependen de pocos meses de tendencia.
- Los horizontes 3 y 5 días empeoran, igual que en la Fase 1.

**Decisión de desarrollo:** no hay ganador que cumpla el gate. Para completar la
Fase 2 se congela el **mejor Sharpe** (`logit_v03_h1_t60`) y se evalúa **una sola
vez** en holdout como diagnóstico. No se promociona aunque el holdout sea
favorable, porque falló log loss/calibración/drawdown/consistencia en desarrollo.
El paper trading se queda con la configuración actual (señal heurística con
`NO_TRADE` por defecto y breakers).

## Holdout (una sola evaluación)

Comando (requiere confirmación explícita):

```bash
python -m backtests.experiments holdout --source cache --days 1620 \
  --assets BTC,ETH --candidate logit_v03_h1_t60 --confirm-holdout
```

Resultado: se completa en `var/reports/phase2-holdout-*.md` y en la sección
siguiente. **No se vuelve a ejecutar ni se ajusta el candidato después de verlo.**

## Resultado del holdout

| Métrica | Holdout | Gate |
| --- | --- | --- |
| Log loss | 0.6928 (baseline 0.6931) | ❌ no alcanza margen 0.005 |
| ECE | 0.0258 | ✅ |
| Sharpe neto | −0.69 | ❌ |
| Retorno neto | −7.35 % | ❌ |
| Max DD | 19.5 % | ❌ (>10 %) |
| Meses positivos | 50 % (4 meses) | ❌ |
| Activos positivos | 0/2 | ❌ |
| Operaciones | 12 | ❌ (<30) |

Por activo:

| Activo | Log loss | Sharpe | Retorno neto | Max DD | Meses + |
| --- | --- | --- | --- | --- | --- |
| BTC | 0.6932 | −0.13 | −2.20 % | 14.2 % | 50 % |
| ETH | 0.6923 | −1.32 | −5.27 % | 18.6 % | 0 % |

Lectura: el log loss del holdout queda **0.0004** por debajo del baseline, muy
lejos del margen de 0.005, y la estrategia pierde dinero con Sharpe negativo.
La calibración sí es buena, pero calibrar bien una moneda casi justa no genera
ventaja. El candidato queda **rechazado**. No se prueba otro candidato en el
holdout.

## Decisión final

**No se promociona ninguna configuración nueva.** El paper trading se queda con
la configuración actual: señal heurística determinista, `NO_TRADE` por defecto,
breakers reconstruidos desde SQLite, kill switch y entrada paper al precio de la
corrida. La razón no es pesimismo: los 14 candidatos pre-registrados fallaron el
gate en desarrollo y el único que se congeló por Sharpe también falló en holdout.

El resultado honesto de la Fase 2 es que **no hay evidencia de ventaja** con
OHLCV diario de BTC/ETH en este periodo. Añadir features, reglas de momentum o
cambiar el umbral produce retornos históricos que no superan el filtro de log
loss ni la consistencia. El siguiente valor no está en seguir ajustando el mismo
dataset, sino en recolectar evidencia prospectiva con paper trading y, si se
quiere otra hipótesis, incorporar datos nuevos (funding, open interest, on-chain
u order book) con su propio backtest.

## KRONOS-01 — experimento zero-shot (adenda)

### Hipótesis

Kronos, un modelo fundacional preentrenado sobre secuencias K-line, puede
producir señales zero-shot para BTC/ETH en velas diarias con ventaja fuera de
muestra frente al baseline trivial y a `baseline-v0.1`, medida con **este**
walk-forward y **después** de costos. Es una hipótesis, no un hecho: las
métricas del paper (RankIC, MAE de volatilidad) no son rentabilidad, y el
pipeline de ejemplo del repositorio no es un sistema de producción.

### Modelos y pines exactos

Los pesos **no** se commitean. Se descargan a `.cache/kronos` (ignorado por
git) con `python scripts/setup_kronos.py --model small|base`.

| Elemento | Repositorio | Revisión fijada |
| --- | --- | --- |
| Código | `shiyu-coder/Kronos` | commit `67b630e67f6a18c9e9be918d9b4337c960db1e9a` (HEAD verificado el 2026-10-23) |
| Tokenizador | `NeoQuasar/Kronos-Tokenizer-base` | `0e0117387f39004a9016484a186a908917e22426` |
| Modelo primario | `NeoQuasar/Kronos-small` (24.7M) | `901c26c1332695a2a8f243eb2f37243a37bea320` |
| Modelo secundario | `NeoQuasar/Kronos-base` (102.3M) | `2b554741eca47781b64468546e77fef3e85130e6` |

El adaptador (`src/models/kronos_adapter.py`) verifica el commit del checkout
si es un repositorio git. Si falta el código o los pesos, falla con instrucciones
en vez de usar `main` o una versión distinta.

SHA256 de los `model.safetensors` descargados: small
`b082dfcbd8e8c142a725c8bbb99781802f38fec81210e13479effb32b3c3e020`,
base `abff193acab6db1a0368e9773e75799d11403b6d054ee6d5f0a11aeabc5f4b83`,
tokenizer `59d85f6af76a2c3b8240ea06cb21db4213b4eeca053f246b23e29cf832fc6bee`.

### Configuración congelada

| Parámetro | Valor |
| --- | --- |
| Entrada | OHLCV diario cerrado hasta `t`; `amount = volume × precio medio` |
| Lookback | **128 velas** (≤ 512; elegido para que la corrida completa quepa en CPU) |
| Horizonte | **1 día** |
| Trayectorias | **30** por fecha |
| Temperatura `T` | **1.0** |
| `top_p` / `top_k` | **0.9** / **0** |
| Semilla | `20261023 + ordinal(fecha)` por fecha |
| Probabilidad de subida | `(n_subidas + 0.5) / (30 + 1)` (Jeffreys) |
| Retorno esperado | mediana de `close_h / close_t - 1` en las 30 trayectorias |
| Umbral operable | `p ≥ 0.55` |
| Costo | 20 bps ida y vuelta |
| Splits | dev 80 % (1.200 velas), holdout 20 % (300 velas) |
| Walk-forward | train 365 / test 60 / step 60; historia mínima 201 |
| Evaluación OOS dev | mismas 10 ventanas y 600 muestras por activo que el baseline |

No hay fine-tuning, ni búsqueda de semillas, ni cambio de umbral/lookback
después de ver desarrollo. `Kronos-base` solo se evalúa si su coste CPU es
razonable; se mide antes de comprometer la corrida. Regla de selección
pre-registrada: si más de un modelo supera el pre-gate de desarrollo, se elige el
de mejor log loss de desarrollo; si ninguno lo supera, se evalúa `kronos-small`
una sola vez en holdout como diagnóstico, igual que se hizo con el candidato de
la Fase 2. Solo hay **un** holdout para KRONOS-01.

### Sin lookahead

`predict_at(candles, t)` corta el contexto a `candles[t-127 : t+1]` y no pasa
ningún precio posterior. El test rápido `test_mutating_future_prices_does_not_change_prediction_at_t`
altera precios futuros y comprueba que la señal de `t` no cambia. El test de
integración `KRONOS_INTEGRATION=1` repite la comprobación con los pesos reales.
En el holdout, cada predicción usa como contexto las velas **ya cerradas** hasta
ese día (incluido el tramo de holdout anterior); no se usa ningún dato posterior
para ajustar nada, porque no hay ajuste. Es la misma simulación walk-forward que
se aplicaría en paper.

### Gate de promoción (idéntico a la Fase 2)

Kronos solo se integra si cumple **todo**: log loss OOS ≤ 0.6881 y ≤
`baseline-v0.1` dev − 0.003; ECE ≤ 0.05; Sharpe neto > 0.5; retorno neto > 0 con
≥ 30 operaciones; max drawdown ≤ 10 %; ≥ 60 % de meses positivos; BTC y ETH
ambos positivos; y repite Sharpe positivo en el holdout. El holdout se evalúa
**una vez** con la configuración congelada y no se reutiliza. El pre-gate de
desarrollo es ese mismo conjunto de criterios **sin** el holdout.

Limitación estadística: el RankIC es cross-sectional y solo hay dos activos por
fecha, así que toma valores ±1 o queda indefinido. Se reporta como diagnóstico,
no como prueba de significancia. La evidencia principal es log loss, retorno
neto después de costos, drawdown y consistencia temporal.

Alcance de ejecución: el backtest usa la misma evaluación de costos y gate que
Fase 2. Los breakers y el kill switch pertenecen a la ejecución paper y no se
simulan en el backtest, igual que en el baseline; como Kronos no se integra, no
hay una ruta de ejecución nueva ni cambios en esos controles.


### Coste computacional

Se cronometra cada inferencia en CPU. El criterio operativo es que una corrida
diaria de BTC+ETH (dos predicciones) quepa con holgura en GitHub Actions y que
la evaluación completa pueda reproducirse. El tiempo medido y la conclusión
sobre viabilidad se registran abajo.

### Reproducción

```powershell
# Dependencias opcionales (CPU)
pip install -r requirements-kronos.txt

# Código y pesos pineados, fuera de git
python scripts/setup_kronos.py --model small
# python scripts/setup_kronos.py --model base   # opcional, ~409 MB

# Desarrollo: mismas 10 ventanas OOS que el baseline
python -m backtests.kronos_experiment dev `
  --source cache --days 1620 --assets BTC,ETH `
  --model small --lookback 128 --sample-count 30 --threads 8

# Holdout: una sola vez, con el candidato congelado
python -m backtests.kronos_experiment holdout `
  --source cache --days 1620 --assets BTC,ETH `
  --model small --lookback 128 --sample-count 30 --threads 8 `
  --confirm-holdout
```

El adaptador no usa el `KronosPredictor` de conveniencia para la probabilidad:
conserva las 30 trayectorias y aplica el suavizado Jeffreys fijado. El
`KronosPredictor` oficial promedia las muestras y perdería esa información.

Nota de transparencia: antes de la corrida completa se hizo una prueba de humo
de infraestructura con `lookback=32` y `sample_count=4` para verificar que el
flujo, el caché y el reporte funcionaran. No se usó para elegir la configuración
ni se reporta como evidencia de ventaja; sus salidas quedaron en
`var/reports/_smoke` (ignorado por git).

### Resultado de desarrollo

Corrida real: **1.200 predicciones** (600 BTC + 600 ETH), las mismas 10
ventanas OOS y fechas que el baseline. CPU local de 8 hilos, sin GPU, con
`torch==2.9.1+cpu`.

| Estrategia | Log loss dev | ECE | RankIC | Sharpe | Retorno neto | Max DD | Meses + | Activos + |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Baseline trivial (0.5) | 0.6931 | — | — | — | — | — | — | — |
| `baseline-v0.1` (mejor de Fase 2) | 0.6986 | 0.0379 | — | 0.61 | +9.67 % | 26.8 % | 58 % | 1/2 |
| `logit_v03_h1_t60` (mejor Sharpe Fase 2) | 0.7032 | 0.0584 | — | 2.00 | +56.68 % | 22.4 % | 59 % | 2/2 |
| Buy & hold | — | — | — | 0.89 | +204.69 % | 63.8 % | 50 % | 2/2 |
| **Kronos-small zero-shot** | **0.8069** | **0.1705** | **0.028** | **−1.09** | **−64.01 %** | **77.0 %** | **45 %** | **0/2** |

Kronos-small queda **0.1138** peor que el baseline trivial y **0.1083** peor que
`baseline-v0.1` en log loss; el margen exigido era 0.005 y 0.003. No es un empate:
la calibración observada es casi plana aunque el modelo declare probabilidades
entre 0.125 y 0.873.

| Bin de probabilidad | n | Prob. media | Frecuencia observada de subida |
| --- | --- | --- | --- |
| 0.0–0.2 | 102 | 0.125 | 0.510 |
| 0.2–0.4 | 313 | 0.299 | 0.508 |
| 0.4–0.6 | 467 | 0.500 | 0.512 |
| 0.6–0.8 | 262 | 0.695 | 0.462 |
| 0.8–1.0 | 56 | 0.873 | 0.625 |

La probabilidad media fue 0.476 y la tasa base 0.505. La correlación temporal
(Spearman) entre probabilidad y retorno siguiente fue ≈ −0.02 en BTC y ≈ +0.01
en ETH: no hay señal direccional aprovechable, y la probabilidad no está
calibrada. Invertir la señal sería una hipótesis nueva y post-hoc; no se hace.

#### Por activo

| Activo | Log loss | ECE | Sharpe | Retorno neto | Max DD | Trades | Acierto bruto | Meses + |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| BTC | 0.8223 | 0.1808 | −1.73 | −43.67 % | 47.4 % | 229 | 49.8 % | 35 % |
| ETH | 0.7914 | 0.1603 | −0.69 | −36.11 % | 58.6 % | 215 | 50.2 % | 40 % |

#### Folds (BTC+ETH)

| Fold | Inicio | Fin | Log loss | RankIC | Sharpe | Retorno | Max DD | Trades | Acierto |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 2024-04-02 | 2024-05-31 | 0.7868 | 0.085 | −3.33 | −16.12 % | 19.9 % | 40 | 55 % |
| 1 | 2024-06-01 | 2024-07-30 | 0.7941 | 0.088 | −4.69 | −22.32 % | 28.6 % | 48 | 40 % |
| 2 | 2024-07-31 | 2024-09-28 | 0.7831 | 0.037 | −0.88 | −14.61 % | 31.4 % | 56 | 52 % |
| 3 | 2024-09-29 | 2024-11-27 | 0.9007 | −0.143 | 0.06 | −0.93 % | 7.1 % | 27 | 44 % |
| 4 | 2024-11-28 | 2025-01-26 | 0.7984 | 0.071 | 0.61 | +2.15 % | 15.8 % | 42 | 60 % |
| 5 | 2025-01-27 | 2025-03-27 | 0.8165 | −0.158 | −4.93 | −34.80 % | 36.2 % | 54 | 41 % |
| 6 | 2025-03-28 | 2025-05-26 | 0.7830 | −0.051 | 2.82 | +26.63 % | 20.5 % | 43 | 60 % |
| 7 | 2025-05-27 | 2025-07-25 | 0.9077 | −0.017 | −1.96 | −10.28 % | 22.9 % | 40 | 45 % |
| 8 | 2025-07-26 | 2025-09-23 | 0.7338 | 0.263 | 1.05 | +7.35 % | 16.0 % | 52 | 52 % |
| 9 | 2025-09-24 | 2025-11-22 | 0.7647 | 0.107 | −3.72 | −19.62 % | 23.6 % | 42 | 52 % |

Ningún fold tiene log loss por debajo del trivial; el patrón no es consistente
entre meses ni activos. El detalle mensual está en
`var/reports/kronos-01-dev-2026-10-23.md` (local, ignorado por git).

#### Gate de desarrollo

| Criterio | Resultado | ¿Pasa? |
| --- | --- | --- |
| Log loss ≤ 0.6881 y ≤ 0.6986 − 0.003 | 0.8069 | ❌ |
| ECE ≤ 0.05 | 0.1705 | ❌ |
| Sharpe neto > 0.5 | −1.09 | ❌ |
| Retorno neto > 0 y ≥ 30 trades | −64.01 %, 444 trades | ❌ |
| Max drawdown ≤ 10 % | 77.0 % | ❌ |
| ≥ 60 % meses positivos | 45 % | ❌ |
| BTC y ETH ambos positivos | 0/2 | ❌ |

**Pre-gate de desarrollo: no superado.** Se cumple el mínimo de 30 operaciones,
pero fallan log loss, calibración, Sharpe, retorno neto, drawdown, meses positivos
y consistencia por activo. Por la regla pre-registrada, se evalúa `kronos-small`
una sola vez en holdout como diagnóstico; no se cambia lookback, umbral, semilla
ni modelo después de esto.

#### Tiempo de inferencia y viabilidad

| Modelo / medición | Predicciones | Media | Total |
| --- | --- | --- | --- |
| Kronos-small, BTC | 600 | 2.13 s | 1.276 s (21.3 min) |
| Kronos-small, ETH | 600 | 1.93 s | 1.158 s (19.3 min) |
| Kronos-small, ambos | 1.200 | 2.03 s | 2.434 s (40.6 min) |
| Kronos-base, benchmark de 1 contexto | 1 | 4.7–5.7 s | — |

Dos activos al día cuestan ≈ **4.1 s** de inferencia en este CPU (sin contar
carga del modelo, ~0.4 s, ni descarga). Eso es viable para una corrida diaria y
para Actions; la corrida completa de desarrollo costó 40.6 min local. En un
runner de 2 núcleos sería más lenta, pero el límite de 6 h de Actions es
holgado. No se puso la corrida completa en Actions porque el resultado ya era
concluyente.

`Kronos-base` se midió con una predicción real (128 de contexto, 30 muestras):
**≈5 s/predicción**, es decir, 1.6–1.9 h solo para las 1.200 predicciones de
desarrollo. No se ejecutó completo: small falló el pre-gate por un margen grande y
no había una razón pre-registrada para gastar esa corrida. Esto
es una decisión de coste, no evidencia de que base fuera a fallar; simplemente
no se probó.

#### ¿Propuesta de fine-tuning?

**No.** El zero-shot no supera el pre-gate de desarrollo (ni el log loss, ni la
calibración, ni el retorno). La regla del experimento era proponer fine-tuning
en GPU solo si superaba un pre-gate razonable en validación; no ocurrió. Añadir
GPU no arregla por sí solo un problema de calibración/señal en velas diarias, y
un fine-tuning sobre el mismo dataset tendría que pre-registrarse como
experimento nuevo.

### Resultado del holdout (una sola evaluación)

El holdout se ejecutó **una vez** con el candidato congelado: 598 predicciones
(299 BTC + 299 ETH), del **2025-12-27** al **2026-10-21** (última etiqueta; los
datos llegan hasta el 2026-10-22). El marcador
`var/reports/kronos-01-holdout-consumed.json` quedó creado. No se vuelve a
ejecutar, no se reintentan semillas y no se cambia el modelo.

| Métrica | Holdout | Gate | ¿Pasa? |
| --- | --- | --- | --- |
| Log loss | **0.7936** (baseline 0.6931) | ≤ 0.6881 y ≤ 0.6956 | ❌ |
| ECE | **0.1527** | ≤ 0.05 | ❌ |
| RankIC | −0.081 | diagnóstico | — |
| Sharpe neto | **−0.001** | > 0.5 | ❌ |
| Retorno neto | **−10.63 %** | > 0 | ❌ |
| Max DD | **39.7 %** | ≤ 10 % | ❌ |
| Trades | 214 | ≥ 30 | ✅ |
| Acierto bruto de señal | 55.1 % | — | — |
| Acierto neto (después de costos) | 48.6 % | — | — |
| Meses positivos | **45 %** (5/11) | ≥ 60 % | ❌ |
| Activos positivos | **0/2** | 2/2 | ❌ |

Por activo:

| Activo | Log loss | ECE | Sharpe | Retorno neto | Max DD | Trades | Acierto bruto | Meses + |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| BTC | 0.7828 | 0.1658 | −0.24 | −7.73 % | 21.6 % | 112 | 55.4 % | 45 % |
| ETH | 0.8044 | 0.1689 | +0.19 | −3.15 % | 29.5 % | 102 | 54.9 % | 45 % |

La calibración sigue siendo casi plana, igual que en desarrollo:

| Bin de probabilidad | n | Prob. media | Frecuencia observada de subida |
| --- | --- | --- | --- |
| 0.0–0.2 | 51 | 0.143 | 0.490 |
| 0.2–0.4 | 157 | 0.304 | 0.529 |
| 0.4–0.6 | 238 | 0.494 | 0.483 |
| 0.6–0.8 | 113 | 0.695 | 0.522 |
| 0.8–1.0 | 39 | 0.870 | 0.487 |

El acierto bruto de 55.1 % no se traduce en dinero: después de 20 bps el acierto
neto cae a 48.6 %, el profit factor es 0.9998 y el retorno compuesto es −10.63 %.
La media neta por operación es prácticamente cero (−0.0002 %), pero los meses
malos (−21.05 % en junio, −17.41 % en enero) y la volatilidad producen un
drawdown de 39.7 %. Es exactamente el tipo de señal que se ve bien en dirección
bruta y desaparece con costos.

Meses (neto después de costos):

| Mes | Retorno | Trades | Acierto |
| --- | --- | --- | --- |
| 2025-12 | +2.27 % | 6 | 67 % |
| 2026-01 | −17.41 % | 22 | 45 % |
| 2026-02 | −0.19 % | 34 | 53 % |
| 2026-03 | +11.50 % | 28 | 61 % |
| 2026-04 | +15.31 % | 7 | 86 % |
| 2026-05 | −6.46 % | 13 | 38 % |
| 2026-06 | −21.05 % | 36 | 39 % |
| 2026-07 | +10.99 % | 18 | 61 % |
| 2026-08 | +13.22 % | 18 | 78 % |
| 2026-09 | −9.04 % | 21 | 62 % |
| 2026-10 | −2.34 % | 11 | 55 % |

El holdout de Kronos es peor que el del candidato de Fase 2
(`logit_v03_h1_t60`: log loss 0.6928, ECE 0.0258, neto −7.35 %, 12 trades), y
mucho peor que el baseline trivial en log loss. No hay ningún criterio de
promoción que se cumpla, salvo el mínimo de 30 operaciones.

#### Tiempo del holdout

598 predicciones en **1.170,3 s** (19,5 min), media **1,96 s/predicción**
(mediana 1,95 s); BTC 1,97 s y ETH 1,95 s. Dos activos al día ≈ **3,9 s**. El
coste de inferencia es aceptable para paper, pero la calidad de la señal no.

## Decisión final de KRONOS-01

**KRONOS-01 no se promociona.** Falla el pre-gate de desarrollo en todos sus
bloques y falla el holdout en log loss, calibración, Sharpe, retorno neto,
drawdown, meses positivos y consistencia por activo; solo se cumple el mínimo de
30 operaciones. La conclusión operativa es la misma que la de Fase 2: el paper
trading se queda con la señal heurística determinista, `NO_TRADE` por defecto,
breakers reconstruidos, kill switch y entrada paper al precio de la corrida. No
se integra Kronos, no se añade una etiqueta de modelo en la PWA y no se toca la
corrida diaria.

`Kronos-base` **no se ejecutó completo**: el benchmark de una predicción dio
≈5 s/predicción y 1,6–1,9 h para las 1.200 predicciones de desarrollo; small
falló por 0,11 de log loss y no mostró señal direccional. No se justifica la
corrida completa y no se usa como rescate post-hoc. El adaptador, los tests, los
pines y esta evidencia negativa se conservan para que el experimento sea
reproducible. No se propone fine-tuning: el zero-shot no superó el pre-gate y el
problema observado es de calibración/señal en velas diarias, no solo de capacidad.
No se reintenta el holdout ni se prueban semillas alternativas.

La evidencia no dice que Kronos sea inútil en general; dice que **esta**
configuración zero-shot (`small`, lookback 128, BTC/ETH diario, 2024–2026,
después de 20 bps) no supera al baseline. Cualquier variante (lookback 512,
otro horizonte, fine-tuning o datos adicionales) sería un experimento nuevo con
pre-registro propio, no una iteración sobre este holdout.
