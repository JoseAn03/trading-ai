# Operación diaria

## Qué dispara la corrida

Hay dos vías:

1. `schedule` de GitHub Actions: `15 * * * *` (cada hora al minuto 15 UTC).
2. `workflow_dispatch` manual, desde Actions o con `gh`:

```powershell
gh workflow run daily.yml --repo JoseAn03/trading-ai
# fuerza una corrida aunque ya haya señales de hoy:
gh workflow run daily.yml --repo JoseAn03/trading-ai -f force=true
gh run list --repo JoseAn03/trading-ai --limit 5
```

**Estado real del cron:** en las pruebas de octubre de 2026 GitHub no ejecutó el
`schedule` ni con un cron horario ni con uno temporal cada 5 minutos. Las ocho
corridas observadas fueron manuales. El workflow está activo y el disparo manual
funciona; no se debe asumir que la actualización automática está resuelta.

El botón **Actualizar** del panel abre el workflow. Sin `force=true`, la guardia
respeta los datos ya procesados de hoy y **no vuelve a publicar** el sitio. Para
forzar una actualización manual usar `force=true`.

## Día operativo: UTC, no Costa Rica

El día operativo es **UTC** porque las velas diarias de Binance cierran a las
00:00 UTC. En Costa Rica (UTC-6) ese corte ocurre a las **18:00 locales**. La
guardia del workflow usa `datetime.now(dt.UTC).date()` a propósito: si usara
`America/Costa_Rica`, a las 00:05 locales del día siguiente todavía no habría
cerrado la vela UTC correspondiente y el motor podría operar con el cierre
anterior. El panel muestra las horas en UTC.

Consecuencia práctica: la corrida que ve el cierre de un día UTC puede ocurrir
entre las 18:00 y las 23:59 de Costa Rica. No es un error de zona horaria.

## Qué debe revisar el dueño en Settings → Actions

No se puede cambiar desde el repositorio. Verificar:

- **Actions → General → Actions permissions**: que GitHub Actions esté permitido
  (no “Disabled”).
- **Actions → General → Workflow permissions**: el workflow pide permisos por
  job (`contents: read` para cómputo; `contents: write`, `pages: write` e
  `id-token: write` solo para publicación). Si la política del repositorio es
  “Read repository contents”, el job de publicación no podrá hacer commit ni
  desplegar.
- **Actions → General → Allow GitHub Actions to create and approve pull
  requests**: no es necesario para este flujo.
- **Actions → la pestaña del workflow `daily`**: que no esté deshabilitado
  manualmente y que el branch por defecto siga siendo `main`.
- **Settings → Pages**: Source = “GitHub Actions”; environment `github-pages`
  existente y sin reglas que bloqueen al bot.
- **Settings → Secrets and variables → Actions → Variables**: opcional
  `TRADING_KILL_SWITCH=true` para detener nuevas entradas sin tocar la BD.
- **Repositorio inactivo**: GitHub desactiva workflows programados tras 60 días
  sin actividad; si el repo queda quieto, revisar y reactivar.
- **Zona horaria**: no hay un ajuste de TZ en Settings; el workflow fija
  `TZ: UTC` explícitamente.

Si el `schedule` sigue sin disparar después de esto, la alternativa honesta es
un cron externo (por ejemplo, un servicio de scheduling que llame a
`workflow_dispatch` con un token de alcance mínimo) o seguir con el botón manual.
No se agregó ninguna cuenta ni cron externo en esta fase.

## Guardia y publicación

- La guardia lee `MAX(timestamp)` de `signals` y lo compara con la fecha UTC.
- Si no hay señal de hoy, o si se pasó `force=true`, corre ingesta, motor,
  snapshot de SQLite, exportación y publica.
- Si ya hay señal de hoy y no hay `force`, **no corre el motor, no exporta y no
  despliega**.
- El cómputo corre con `contents: read`; la publicación (commit del estado +
  deploy de Pages) es un job separado con permisos elevados y no ejecuta código
  Python del repositorio.

## Kill switch

Dos mecanismos, ambos sin LLM y sin red:

1. Variable de entorno `TRADING_KILL_SWITCH=true` (en Actions se lee de la
   variable de repositorio `TRADING_KILL_SWITCH`).
2. Bandera persistente en SQLite, tabla `system_controls`:

```powershell
python -m jobs.risk_control on --reason "revisión manual"
python -m jobs.risk_control status
python -m jobs.risk_control off --reason "causa revisada"
```

La corrida diaria combina ambos: si cualquiera está activo, `PortfolioState`
marca `manual_kill_switch=True` y **ninguna señal abre paper trade**. El evento
queda en `audit_events`.

## Diagnóstico rápido

```powershell
pytest
ruff check .
mypy src jobs backtests
python -m jobs.risk_control status
sqlite3 var/trading.db "select key,value,updated_at from system_controls;"
```

La BD versionada no se edita a mano: los cambios de esquema se aplican con
`Store.init_schema()` y los tests de migración.
