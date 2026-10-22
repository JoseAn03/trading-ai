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
| Antigüedad máxima del dato | 20 min (36 h en velas diarias) | `max_data_age_minutes` / `quality.py` |

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

Debe funcionar **sin LLM, sin agente y sin red**:

```python
state.manual_kill_switch = True   # bloquea toda operación nueva
```

Procedimiento operativo:

1. Activar el switch manual.
2. Cerrar posiciones simuladas abiertas (o dejarlas congeladas y documentarlo).
3. Registrar el evento en `audit_events` con motivo y hora.
4. No reactivar hasta documentar la causa y el cambio aplicado.

## Reglas de escalado

- 3 pérdidas consecutivas → pausa de 24 h y revisión del registro.
- Drawdown ≥ 50 % del límite → reducir tamaño a la mitad.
- Drawdown ≥ 100 % del límite → apagado automático, sin reanudación automática.
- Cambio de modelo o de features → re-backtest obligatorio antes de publicar señales.

## Revisión

- Semanal: ¿los límites se respetaron? ¿hubo bloqueos legítimos o falsos positivos?
- Mensual: ¿los costos estimados se parecen a los observados?
- Antes de dinero real: revisión legal/fiscal y limite de capital explícito (mínimo viable).
