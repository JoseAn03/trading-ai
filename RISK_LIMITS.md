# RISK_LIMITS

Límites **ejecutables por código** (`src/quant/risk.py`). Si este documento y el código discrepan, gana el código y se corrige el documento el mismo día.

> Capital de referencia: simulado. No hay dinero real en esta etapa.

## Límites operativos

| Límite | Valor | Dónde se aplica |
| --- | --- | --- |
| Riesgo por operación | 1.5 % del capital | `max_risk_per_trade_pct` |
| Exposición por activo | 10 % del capital | `max_exposure_per_asset_pct` |
| Pérdida diaria máxima | 3 % | `max_daily_loss_pct` |
| Pérdida semanal máxima | 8 % | `max_weekly_loss_pct` |
| Drawdown máximo | 10 % | `max_drawdown_pct` |
| Operaciones por día | 5 | `max_trades_per_day` |
| Correlación máxima con cartera | 0.80 | `max_correlation` |
| Antigüedad máxima del dato | 20 min o 1.5 × intervalo de vela (36 h en velas diarias) | `RiskLimits.data_age_tolerance_minutes` / `quality.data_age_tolerance_minutes` |

Perfil conservador disponible para las primeras semanas de paper trading:
`RiskLimits.conservative()` → 0.5 % por operación, 5 % por activo, 1.5 % diario, 5 % drawdown, 3 operaciones/día.

## Cálculo del tamaño

```
tamaño = min( tamaño_sugerido_por_la_señal,
              riesgo_máximo_por_operación,
              presupuesto_diario_restante,
              exposición_disponible_del_activo )
```

Si el resultado es ≤ 0, la operación se bloquea. No hay excepciones discrecionales.
El tamaño que devuelve el breaker (`RiskDecision.suggested_size_pct`) es el que se
usa al abrir el paper trade; no se ignora el recorte de riesgo.

## Estado de cartera reconstruido

La corrida diaria no usa un portafolio vacío en memoria. Antes de evaluar señales:

1. Cierra las posiciones vencidas con las velas disponibles.
2. Lee `paper_trades` y reconstruye equity, pico, PnL diario/semanal, exposición,
   operaciones de hoy y pérdidas consecutivas (`src/paper_trading/state.py`).
3. Recién entonces pasa cada señal por el breaker.

El día operativo es **UTC**, igual que la guardia del workflow. En Costa Rica el
corte es a las 18:00 locales porque la vela diaria de Binance cierra a 00:00 UTC.

## Frescura del dato

La tolerancia no es un número fijo de 20 minutos. Se comparte con
`src/data/quality.py`:

```
tolerancia_min = max(max_data_age_minutes, 1.5 × expected_interval_hours × 60)
```

- Velas diarias (24 h): `max(20, 2160) = 2160 min = 36 h`.
- Velas de 1 h: `max(20, 90) = 90 min`.
- Intervalo 0 (tests): solo el límite configurado.

Un cierre de vela de hace 10 h es normal y **no** bloquea; 37 h sí bloquea.

## Circuit breakers (apagan señales)

Se activa el bloqueo cuando:

1. La última cotización supera la edad permitida o hay un gap no explicado.
2. El proveedor devuelve datos anómalos o está caído.
3. El drawdown o una secuencia de pérdidas supera el límite.
4. El modelo está fuera de su rango de calibración.
5. Cambia la versión del modelo sin aprobación, o falla la base de datos.
6. El reporte no puede auditarse.
7. El kill switch manual está activo.

Cada bloqueo devuelve `blocked_by` con la razón exacta, que se guarda en la señal y se muestra en el reporte.

## Kill switch

Debe funcionar **sin LLM, sin agente y sin red**. Hay dos vías y la corrida
diaria combina ambas:

```powershell
# Variable de entorno (en Actions: repository variable TRADING_KILL_SWITCH)
$env:TRADING_KILL_SWITCH = "true"

# Bandera persistente en la tabla system_controls
python -m jobs.risk_control on --reason "revisión manual"
python -m jobs.risk_control status
python -m jobs.risk_control off --reason "causa revisada"
```

Con cualquiera activa, `PortfolioState.manual_kill_switch=True` y ninguna señal
abre un paper trade. El evento queda registrado en `audit_events`.

Procedimiento operativo:

1. Activar el switch (env o BD) y verificar con `status`.
2. Cerrar posiciones simuladas abiertas (o dejarlas congeladas y documentarlo).
3. Registrar el motivo en `audit_events` (el CLI ya lo hace).
4. No reactivar hasta documentar la causa y el cambio aplicado.

## Reglas de escalado

Estado actual del código, sin promesas que no estén implementadas:

- 3 pérdidas consecutivas → el breaker bloquea nuevas entradas hasta que una
  operación cerrada sea ganadora o se intervenga manualmente.
- Drawdown ≥ 100 % del límite (10 %) → bloqueo automático; no hay reanudación
  automática.
- Reducir el tamaño a la mitad al 50 % del drawdown **no** está implementado;
  queda como propuesta para la Fase 2. Hoy el tamaño se recorta por el límite
  por operación y el presupuesto diario.
- Cambio de modelo o de features → re-backtest obligatorio antes de publicar
  señales.

## Revisión

- Semanal: ¿los límites se respetaron? ¿hubo bloqueos legítimos o falsos positivos?
- Mensual: ¿los costos estimados se parecen a los observados?
- Antes de dinero real: revisión legal/fiscal y limite de capital explícito (mínimo viable).
