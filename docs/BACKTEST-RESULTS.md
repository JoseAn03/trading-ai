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
2. Filtro de régimen (tendencia vs rango) para separar periodos.
3. Features de volatilidad realizada y volumen relativo con ventanas más largas.
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
