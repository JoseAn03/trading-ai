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

1. Horizonte distinto (3–7 días) donde el momentum pueda persistir más.
2. Filtro de régimen (tendencia vs rango) para separar periodos.
3. Features de volatilidad realizada y volumen relativo con ventanas más largas.
4. Incluir costos variables por volatilidad (slippage proporcional).

**Cómo reproducir**

```bash
python -m backtests.run_backtest --source live --days 1000 --assets BTC,ETH \
  --threshold 0.55 --train 365 --test 60 --step 60
```
