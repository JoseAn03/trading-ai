# Plan de evaluación en vivo-paper

## Configuración que queda

- Modo: **paper trading en sombra**, sin claves, sin exchange y sin capital real.
- Activos: BTC y ETH; horizonte `24h`; día operativo UTC.
- Señal actual: score heurístico determinista (no calibrado), umbral
  `p ≥ 0.55`, confianza mínima `medium`; si no cumple, `NO_TRADE`.
- Riesgo: `RiskLimits` por defecto (1.5 % por operación, 10 % por activo, 3 %
  diario, 8 % semanal, 10 % drawdown, 5 operaciones/día).
- Entrada: precio vigente al momento de la corrida; se guarda también el cierre
  de la señal y se reporta el sesgo de entrada.
- No se promociona ninguna configuración de la Fase 2: el holdout del mejor
  candidato terminó con Sharpe −0.69 y retorno −7.35 %.

## Regla de no intervención

Durante la ventana de evaluación **no se cambian** pesos, umbrales, features,
horizonte ni costos. Cualquier cambio reinicia el conteo de operaciones y se
documenta como una configuración nueva. El objetivo es evitar el ajuste continuo
hasta que “parezca” que funciona.

## Muestra mínima

- **≥ 30 operaciones cerradas** o **≥ 12 semanas**, lo que ocurra después.
- Si la señal produce menos de 30 cierres por ser selectiva, se extiende la
  ventana; no se baja el umbral para llegar a 30.
- Se registra también el número de días con `NO_TRADE` y la cobertura.

## Métricas que se reportan

Por activo y agregadas:

| Métrica | Uso |
| --- | --- |
| Operaciones cerradas y abiertas | tamaño de muestra |
| Win rate | descriptivo, no criterio único |
| Retorno neto acumulado | después de costos congelados |
| Media neta por operación | magnitud de la ventaja |
| Max drawdown | riesgo operable |
| Sharpe de la curva de equity | retorno ajustado por riesgo |
| Meses positivos | consistencia temporal |
| Activos positivos | consistencia transversal |
| `avg_entry_delay_pct` | sesgo entre señal y ejecución |
| Brier / log loss del score | calibración descriptiva (el score es heurístico) |
| Buy & hold del mismo periodo | referencia obligatoria |
| Comparación vs baseline 50/50 | referencia de información |

## Criterio para cerrar la evaluación

**No se pasa a capital real en esta fase.** El paper trading solo sirve para
decidir si la hipótesis merece más investigación. Se considera “sin ventaja” si:

- retorno neto ≤ 0, o
- Sharpe ≤ 0, o
- drawdown > 10 %, o
- menos de 60 % de meses positivos, o
- solo un activo aporta.

Si alguna vez se busca promover a una fase posterior, además se exige una
muestra prospectiva independiente y revisión de costos reales; nunca se usa el
mismo periodo para elegir y confirmar.

## Disparadores de kill switch

- Pérdida diaria ≥ 3 % o semanal ≥ 8 %.
- Drawdown ≥ 10 %.
- 3 pérdidas consecutivas (el breaker ya bloquea nuevas entradas).
- Dato con antigüedad > 36 h en velas diarias, proveedor caído o calidad
  `invalid`.
- Cualquier error de esquema, migración o inconsistencia de equity.

Procedimiento: `python -m jobs.risk_control on --reason "..."`, revisar
`audit_events`, corregir y solo entonces `off`.

## Registro

Cada corrida diaria deja señales, trades, reportes y `audit_events` en SQLite.
El panel muestra las métricas y el sesgo de entrada. Al cerrar la ventana se
pega una tabla como esta en `docs/BACKTEST-RESULTS.md`:

| Fecha | Config | Trades | Retorno | Sharpe | Max DD | Meses + | Activos + | Decisión |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pendiente | actual | 0 | — | — | — | — | — | recolectando |
