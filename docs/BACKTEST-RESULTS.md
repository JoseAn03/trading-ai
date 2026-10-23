# Resultados de backtest

Registro de corridas relevantes. Los reportes completos y las predicciones crudas
viven en `var/reports/` (ignorado por Git); aquí queda la conclusión fechada.

---

## 2026-10-22 — primer backtest con datos reales

**Configuración**

| Parámetro | Valor |
| --- | --- |
| Fuente | Binance klines (USDT) |
| Activos | BTC, ETH |
| Periodo | 1.000 velas diarias (999 útiles) |
| Ventanas | walk-forward, train 365 / test 60 / step 60 |
| Muestras fuera de muestra | 1.200 (600 por activo, 10 ventanas) |
| Umbral accionable | p ≥ 0.55 |
| Costo ida y vuelta | 0.20 % (20 bps) |
| Modelo | baseline-v0.1 (logística, 5 features) |

**Resultado**

| Métrica | Valor | Referencia |
| --- | --- | --- |
| Log loss | 0.6978 | 0.6931 (50/50) |
| Brier | 0.2523 | 0.2500 (50/50) |
| ECE | 0.0318 | ≤ 0.05 aceptable |
| Trades | 103 (cobertura 8.6 %) | — |
| Win rate | 48.5 % | — |
| Retorno neto | +7.20 % | — |
| Max drawdown | 23.9 % | — |
| Ventanas positivas | 50 % | gate: ≥ 60 % |
| Activos positivos | 1/2 (BTC −8.82 %, ETH +17.57 %) | gate: 2/2 |

**Veredicto: sin ventaja demostrada.**

- El modelo **no supera al baseline trivial** en log loss (0.6978 > 0.6931): las
  features no aportan información predictiva en este periodo.
- La ganancia agregada se concentra en ETH y no es consistente entre ventanas.
- La calibración es razonable (ECE 0.032), es decir: las probabilidades no mienten
  demasiado, pero tampoco sirven para ganar.

**Consecuencia:** no se promociona a paper trading con este modelo. El siguiente
paso del plan es probar hipótesis con evidencia, no añadir complejidad a ciegas.

**Hipótesis a probar (una a la vez, mismo protocolo)**

1. ❌ **Horizonte distinto (3–7 días)** — probada el 2026-10-22: empeora (ver abajo).
2. ❌ **Filtro de régimen (tendencia vs rango)** — probada el 2026-10-22 junto con la 3: empeora.
3. ❌ **Features de volatilidad realizada y volumen relativo con ventanas más largas** — probada: empeora.
4. Incluir costos variables por volatilidad (slippage proporcional).

**Cómo reproducir**

```bash
python -m backtests.run_backtest --source live --days 1000 --assets BTC,ETH \
  --threshold 0.55 --train 365 --test 60 --step 60
```

---

## 2026-10-22 — Barrido de horizontes (hipótesis 1: rechazada)

**Pregunta:** ¿el momentum persiste mejor en 2–7 días que en 24 h?

**Método:** idéntico protocolo; se cambia solo el horizonte de evaluación
(`build_samples(..., horizon=h)`). La estrategia opera sin solapar posiciones
(una entrada cada `h` días) para no contar tres veces la misma apuesta.

**Resultado**

| Horizonte | Log loss | Baseline | ECE | Trades | Retorno neto | Ventanas + | Activos + |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1d | 0.6978 | 0.6931 | 0.0318 | 103 | +7.20 % | 50 % | 1/2 |
| 2d | 0.7001 | 0.6931 | 0.0450 | 120 | −24.51 % | 50 % | 0/2 |
| 3d | 0.7051 | 0.6931 | 0.0531 | 105 | −39.05 % | 35 % | 0/2 |
| 5d | 0.7206 | 0.6931 | 0.1204 | 64 | −56.91 % | 17 % | 0/2 |
| 7d | 0.7253 | 0.6931 | 0.1496 | 49 | −52.07 % | 22 % | 0/2 |

**Lectura**

- El deterioro es **monótono** en log loss y calibración: 1d → 7d empeora en las
  dos dimensiones. No hay un horizonte donde aparezca señal.
- A 2 días desaparece la única ganancia (ETH) y ningún activo queda positivo.
- A 5–7 días el ECE (0.12–0.15) ya indica probabilidades mal calibradas: el modelo
  se vuelve más confiado cuando está más equivocado.

**Conclusión:** la hipótesis "24 h es demasiado corto" **queda rechazada**. El
problema no es el horizonte, es la información de entrada: 5 features de OHLCV
diario no contienen señal direccional aprovechable. Probar más horizontes sería
ajustar ruido.

**Implicación para el plan:** el siguiente experimento debe cambiar **qué sabe el
modelo** (features de régimen y ventanas largas), no cuánto tiempo mira hacia
adelante. Si eso también falla, el resultado honesto es mantener el sistema en
`NO_TRADE` por defecto y no pasar a paper trading.

**Reporte completo:** `var/reports/barrido-horizontes-2026-10-22.md`

---

## 2026-10-22 — Features de régimen y ventanas largas (hipótesis 2 y 3: rechazadas)

**Pregunta:** ¿añadir información de régimen de largo plazo (tendencia 50/200,
distancia a SMA200, ratio de volatilidad 20/60 y drawdown desde el máximo de 60
días) mejora la predicción frente a las 5 features de corto plazo?

**Método:** 1.500 días de Binance, BTC y ETH. Ambos sets usan **las mismas
muestras, ventanas, umbral, costos y modelo** (logística, 500 épocas, l2 0.01);
solo cambia qué columnas ve el modelo. Historia mínima forzada a 201 velas para
igualar el periodo evaluado.

- `v0.1` (base): trend, momentum_10d, distance_to_sma, volume_relative, vol_20d.
- `v0.2` (régimen): las 5 anteriores + trend_long, distance_to_sma200,
  vol_ratio, drawdown_60d.

**Resultado (1.800 muestras fuera de muestra por set)**

| Set | Log loss | ECE | Trades | Retorno neto | Max DD | Ventanas + | Activos + |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v0.1 (5 features) | 0.6995 | 0.0312 | 226 | +6.04 % | 22.4 % | 53 % | 2/2 |
| v0.2 (9 features) | 0.7061 | 0.0511 | 483 | −65.69 % | 80.5 % | 37 % | 0/2 |

Delta v0.2 vs v0.1: **log loss +0.0065**, **retorno −71.73 %**, **ECE +0.0199**.

**Lectura**

- El set de régimen **empeora el log loss** y duplica los trades: el modelo
  encuentra "señales" en más sitios, pero son ruido.
- La calibración se degrada (ECE 0.031 → 0.051) y el drawdown sube a 80 %:
  más operaciones y más confianza sin más información.
- v0.1 con 1.500 días deja 2/2 activos positivos y +6 %, pero su log loss sigue
  siendo peor que el baseline 50/50 y solo el 53 % de las ventanas son positivas.
  El gate lo rechaza correctamente: **ganancia sin capacidad predictiva es suerte**.

**Conclusión:** hipótesis rechazada. Con OHLCV diario, ni las features de corto
plazo ni las de régimen contienen señal direccional aprovechable después de
costos. Añadir más features a este mismo dataset solo aumenta el sobreajuste.

**Implicación:** dejar de iterar sobre features de OHLCV diario. Alternativas
honestas, en orden de esfuerzo:

1. **No operar** y usar el sistema como auditor/registro (`NO_TRADE` por defecto).
2. **Datos distintos**: funding rates, open interest, on-chain u order book;
   exige ingesta nueva y su propio backtest.
3. **Paper trading en sombra** para recolectar evidencia prospectiva, sin capital
   y sin promover el modelo actual.

**Reporte completo:** `var/reports/comparacion-features-2026-10-22.md`

---

## 2026-10-23 — Fase 2: 14 candidatos y holdout único (sin promoción)

**Pregunta:** ¿alguna combinación de features, umbral, horizonte o regla supera
el baseline y produce retorno ajustado por riesgo, con consistencia, en datos
no usados para elegir?

**Protocolo pre-registrado:** 1.500 velas reales por activo; 1.200 de desarrollo
(hasta 2025-12-26) y 300 de holdout (2025-12-27 → 2026-10-22). Train 365 / test
60 / step 60; costos 20 bps; 14 candidatos congelados; gate con log loss ≥0.005
mejor que 50/50, ECE ≤0.05, Sharpe >0.5, ≥30 trades, DD ≤10 %, ≥60 % meses
positivos y BTC/ETH positivos. Detalle completo en `docs/EXPERIMENTS.md`.

**Desarrollo:** ningún candidato pasó el gate. El mejor log loss fue 0.6986
(peor que 0.6931). El mejor Sharpe fue `logit_v03_h1_t60` (2.00, +56.7 %), pero
con log loss 0.7032, ECE 0.058, DD 22.4 % y 59 % de meses positivos. Las reglas
de momentum tuvieron Sharpe 0.98–0.99 y retornos altos, pero consistencia mensual
de 30–32 % y drawdowns de 37–47 %. Buy & hold: +204.7 % con DD 63.8 %.

**Holdout único de `logit_v03_h1_t60`:** log loss 0.6928 vs 0.6931 (mejora de
0.0004, muy por debajo del margen), ECE 0.0258, Sharpe −0.69, retorno −7.35 %,
DD 19.5 %, 50 % meses positivos, 0/2 activos positivos y 12 trades. **No pasa.**

**Veredicto:** no hay ventaja demostrada con OHLCV diario en este periodo. No se
promociona ninguna configuración nueva; el paper trading queda con la
configuración actual y el plan de `docs/PAPER-EVALUATION.md` (≥30 operaciones
cerradas, sin cambiar parámetros). Añadir más features al mismo dataset no es
una hipótesis justificada; el siguiente experimento requiere datos nuevos
(funding, open interest, on-chain u order book) o mantener `NO_TRADE`.
