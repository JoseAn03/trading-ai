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

<!-- RESULTADO_HOLDOUT -->

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

