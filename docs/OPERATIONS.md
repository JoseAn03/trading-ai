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

**Estado real del cron (29 Oct 2026):** el `schedule` ya se disparó en las
corridas observadas del 24 al 29 de octubre y publicó estado diario; las
corridas recientes terminaron en verde. El workflow sigue teniendo el disparo
manual como respaldo por si GitHub deja de ejecutar el `schedule`.

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
- **Settings → Secrets and variables → Actions → Secrets**: opcional
  `TRADING_LLM_API_KEY` para activar la IA de explicación. Sin este secret, la
  corrida sigue con la explicación determinista.
- **Settings → Secrets and variables → Actions → Variables**: opcional
  `TRADING_KILL_SWITCH=true` para detener nuevas entradas sin tocar la BD, y las
  variables `TRADING_LLM_*` descritas abajo.
- **Repositorio inactivo**: GitHub desactiva workflows programados tras 60 días
  sin actividad; si el repo queda quieto, revisar y reactivar.
- **Zona horaria**: no hay un ajuste de TZ en Settings; el workflow fija
  `TZ: UTC` explícitamente.

Si el `schedule` sigue sin disparar después de esto, la alternativa honesta es
un cron externo (por ejemplo, un servicio de scheduling que llame a
`workflow_dispatch` con un token de alcance mínimo) o seguir con el botón manual.
No se agregó ninguna cuenta ni cron externo en esta fase.

## Activar la IA de explicación (opcional)

La IA **solo redacta** el JSON que ya calculó el motor. No calcula señales,
probabilidades, tamaños ni riesgo, y no puede cambiar el paper trading ni los
breakers. Si el proveedor falla, se publica la explicación determinista.

### En GitHub Actions (recomendado)

El workflow `daily` pasa el secret únicamente al paso `Run daily engine`; no
lo recibe el job de publicación ni el navegador.

1. Crea un token en un proveedor con endpoint compatible con OpenAI
   (`POST {base_url}/chat/completions`, autenticación `Authorization: Bearer`).
   Ejemplos de base URL: OpenAI `https://api.openai.com/v1`, Groq
   `https://api.groq.com/openai/v1`, DeepSeek `https://api.deepseek.com/v1`.
   Usa el ID de modelo exacto que te dé el proveedor.
2. En GitHub: **Settings → Secrets and variables → Actions → Secrets → New
   repository secret**. Nombre: `TRADING_LLM_API_KEY`. Valor: el token.
3. Opcionalmente, en **Variables**, define:

   | Variable | Valor por defecto | Uso |
   | --- | --- | --- |
   | `TRADING_LLM_ENABLED` | `true` | poner `false` para apagar la IA sin borrar el secret |
   | `TRADING_LLM_MODEL` | `gpt-4o-mini` | modelo del proveedor |
   | `TRADING_LLM_BASE_URL` | `https://api.openai.com/v1` | endpoint compatible |
   | `TRADING_LLM_MONTHLY_BUDGET_USD` | `1.0` | techo estimado por mes |
   | `TRADING_LLM_JSON_MODE` | `true` | `false` si el proveedor rechaza `response_format` |
   | `TRADING_LLM_MAX_OUTPUT_TOKENS` | `700` | límite de salida |
   | `TRADING_LLM_INPUT_USD_PER_1K` | `0.00015` | tarifa de entrada |
   | `TRADING_LLM_OUTPUT_USD_PER_1K` | `0.0006` | tarifa de salida |

   Ajusta las tarifas al proveedor elegido: el presupuesto es una **estimación**
   del sistema, no una cuota del proveedor. Configura también un límite de gasto
   en el panel del proveedor.
4. Ejecuta el workflow con `force=true`:

   ```powershell
   gh workflow run daily.yml --repo JoseAn03/trading-ai -f force=true
   gh run list --repo JoseAn03/trading-ai --limit 3
   ```

5. Verifica en la PWA, pestaña **Explicación**:
   - badge `LLM`: el token funcionó y la salida pasó la validación;
   - badge `determinista` con una nota `IA no aplicada (...)`: revisa el motivo
     (`llm_not_configured`, `llm_error`, `invalid_schema`, `budget_exceeded`, etc.);
   - en el log de Actions aparece `explicacion=... motivo=... costo_usd=...`.

### En local

Copia `.env.example` a `.env` (nunca lo commitees), completa el token y ejecuta:

```powershell
$env:TRADING_LLM_ENABLED = "true"
$env:TRADING_LLM_API_KEY = "tu-token"
$env:TRADING_LLM_MODEL = "tu-modelo"
$env:TRADING_LLM_BASE_URL = "https://api.openai.com/v1"
python -m jobs.daily_run --live --llm
```

Si no defines `TRADING_LLM_API_KEY`, el cliente acepta `OPENAI_API_KEY` como
compatibilidad. Sin clave, la corrida no falla: guarda la explicación
determinista y registra el motivo.

### Límites de esta integración

- El proveedor recibe el JSON de señales, costos, calidad de datos y paper
  trading; **no** recibe claves de exchange ni datos personales.
- El texto generado se publica en el sitio público de GitHub Pages. No incluyas
  información privada en el prompt ni en las variables.
- No pongas el token en `README`, en el workflow, en `dist/`, en el HTML de la
  PWA ni en una consulta desde el navegador. GitHub enmascara secretos en logs,
  pero tampoco hay que imprimirlos.
- La IA no analiza velas crudas ni decide: analiza el resultado del motor. Un
  contexto de mercado más rico requiere agregar un snapshot **precalculado** al
  payload, sin darle control sobre señales.

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
