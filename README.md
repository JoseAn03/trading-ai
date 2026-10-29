# trading-ai

Plataforma personal de **apoyo a decisiones** para cripto. Calcula un **score heurístico** (logit con pesos fijos, no una probabilidad calibrada) de que el retorno neto después de costos sea positivo en un horizonte, ordena las oportunidades y dice explícitamente **cuándo no operar**.

> Análisis automatizado. No es asesoría financiera ni garantía de rentabilidad.
> **No ejecuta órdenes reales** y no almacena claves de exchange.

## Estado actual

| Fase del plan | Estado |
| --- | --- |
| 0. Definición y alcance | ✅ `PRODUCT_DECISIONS.md`, `RISK_LIMITS.md` |
| 1. Esqueleto y panel | ✅ Streamlit + Docker + CI |
| 2. Ingesta y calidad de datos | ✅ Binance (OHLCV real) + CoinGecko opcional + validación + SQLite |
| 3. Motor cuantitativo mínimo | ✅ features + señal determinista + circuit breakers |
| 4. Backtesting | ✅ walk-forward real, calibración (ECE) y gate de robustez; Fase 2 + KRONOS-01 (2026-10-23): 14 candidatos y zero-shot, **sin promoción** (`docs/EXPERIMENTS.md`) |
| 5. Paper trading | ✅ persistente en sombra: congela la entrada, resuelve al vencer y se ve en el panel |
| 6. IA de explicación | ✅ determinista + LLM validado con guardia de cifras y fallback; entrega Telegram opcional |
| 7. Operación 24/7 + agente | ⛔ no empezado (a propósito) |

## App en la nube (sin PC)

El panel también se publica como **PWA** en GitHub Pages:

**https://josean03.github.io/trading-ai/**

- GitHub Actions corre la ingesta, el motor, el paper trading y el exportador,
  y publica el sitio. No necesita la PC encendida ni la misma red.
- El workflow tiene `schedule` **cada hora a los :15 UTC** y un paso de control
  que solo ejecuta el motor si todavía no hay datos de hoy. Así evita duplicados
  y, si GitHub demora un intento, el siguiente lo reintenta. Si no hay corrida,
  tampoco vuelve a desplegar el sitio.
- Costo: **USD 0** de infraestructura (repositorio público; GitHub Actions y
  Pages incluidos). Si activas la IA, el proveedor cobra aparte; el sistema
  estima el gasto y aplica un techo mensual.
- Es una **vista móvil de solo lectura** generada desde la misma SQLite. El panel
  Streamlit completo sigue disponible en local.
- No incluye claves ni ejecución real. El token de IA, si lo configuras, vive
  como secret de GitHub Actions y nunca llega al navegador. Sin token, la
  explicación es determinista.

> **Estado del cron (23 Oct 2026):** GitHub no disparó el `schedule` en las
> pruebas iniciales de este repositorio nuevo. El workflow quedó activo y el
> disparo manual funciona; por eso el panel incluye el enlace **Actualizar** y
> esta guía. El cron se reintenta cada hora cuando GitHub lo active. Checklist
> de Settings en `docs/OPERATIONS.md`.

### Actualizar desde el celular

1. En el panel, toca **Actualizar** (arriba a la derecha), o abre
   <https://github.com/JoseAn03/trading-ai/actions/workflows/daily.yml> con tu cuenta.
2. Toca **Run workflow** → rama `main` → **Run workflow**. Si ya hay datos de
   hoy y quieres forzar una actualización, marca `force=true`; sin eso la
   guardia no corre el motor ni vuelve a publicar.
3. Espera cerca de un minuto y recarga la PWA. El control por fecha evita
   duplicar los datos del día.


### Instalar en el celular

1. Abrir la URL en Chrome (Android) o Safari (iOS).
2. Menú del navegador → **Agregar a la pantalla de inicio** / **Instalar app**.
3. Queda como icono, abre en pantalla completa y funciona con conexión a internet.
   El service worker guarda la última versión para consulta sin conexión.

### Cómo se actualiza

- Workflow: `.github/workflows/daily.yml` (`schedule` cada hora + `workflow_dispatch`).
- El paso de control lee `var/trading.db`: si ya hay una señal con fecha UTC de
  hoy, omite motor, exportación y despliegue. No duplica datos ni publica de más.
- El `schedule` no se disparó en las pruebas iniciales; el botón **Actualizar** y
  el comando `gh workflow run` son la vía confiable por ahora. Sin `force`, una
  corrida manual respeta el control y no publica; para actualizar a mano hay que
  usar `-f force=true`.
- **Día operativo UTC:** las velas diarias de Binance cierran a 00:00 UTC, que en
  Costa Rica son las 18:00. La guardia usa fecha UTC a propósito; no es un bug.
  Ver `docs/OPERATIONS.md` y la lista de ajustes de Actions que debe revisar el
  dueño.
- Corre `python -m jobs.daily_run --live` con `BINANCE_BASE_URL` apuntando al
  mirror público `https://data-api.binance.vision/api/v3` (evita bloqueos
  geográficos de los runners).
- Compacta SQLite con `VACUUM INTO` y versiona `var/trading.db` y los reportes
  diarios con `git add -f` (están en `.gitignore` a propósito para el uso local).
  Aún es un repo público: ver opciones en `docs/STATE_STORAGE.md`.
- El cómputo corre con `contents: read`; un job de publicación separado hace el
  commit del estado y el deploy de Pages con permisos elevados.
- Genera `dist/` con `python -m jobs.export_static --out dist` y lo publica con
  `actions/deploy-pages`.

Disparo manual:

```powershell
gh workflow run daily.yml --repo JoseAn03/trading-ai
# forzar una corrida aunque ya existan datos de hoy:
gh workflow run daily.yml --repo JoseAn03/trading-ai -f force=true
gh run list --repo JoseAn03/trading-ai --limit 3
```

Vista local del sitio:

```powershell
python -m jobs.export_static --out dist
python -m http.server 8000 --directory dist
# http://localhost:8000
```

> **Privacidad:** el repositorio y el sitio son públicos y no tienen login.
> Cualquiera con la URL puede leer señales, paper y backtests. No hay claves ni
> datos personales, pero no subir secretos al repo. Si se quiere acceso privado,
> hay que agregar autenticación o usar un hosting privado.

## Arranque rápido (Windows)

Requiere **Python 3.11+**. En este equipo no está instalado todavía:

```powershell
winget install -e --id Python.Python.3.11
```

Luego, desde `C:\Users\Andres\trading-ai`:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# 1) tests
pytest

# 2) corrida demo (datos sintéticos, sin red)
python -m jobs.daily_run

# 3) corrida real (Binance, sin API key)
python -m jobs.daily_run --live

# 3b) corrida real con explicación LLM (requiere TRADING_LLM_API_KEY)
python -m jobs.daily_run --live --llm

# 4) backtest con datos reales + calibración
python -m backtests.run_backtest --source live --days 1000 --assets BTC,ETH

# 5) panel
streamlit run app/streamlit_app.py
```

### Kronos zero-shot (experimento opcional, CPU)

Kronos **no** forma parte del paper trading ni de la corrida diaria. Es un
experimento de la Fase 2 para comprobar si un modelo fundacional preentrenado
aporta ventaja zero-shot con las mismas velas, folds y costos. Requiere pesos
aparte (no se commitean) y deja resultados en `var/reports/`:

```powershell
pip install -r requirements-kronos.txt
python scripts/setup_kronos.py --model small
python -m backtests.kronos_experiment dev `
  --source cache --days 1620 --assets BTC,ETH `
  --model small --lookback 128 --sample-count 30 --threads 8
```

El holdout solo se ejecuta una vez y el comando exige `--confirm-holdout`; el
resultado y el veredicto están en `docs/EXPERIMENTS.md`. No se promociona si no
pasa el gate pre-registrado.

Salidas en `var/reports/`: reporte diario (`.md`/`.json`), backtest (`.md`/`.json`)
y predicciones auditables (`.csv`). Base en `var/trading.db`.

## Acceso desde el celular (misma Wi-Fi)

1. PC y teléfono en la misma red.
2. Lanzar el panel escuchando en la red local:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app/streamlit_app.py --server.address 0.0.0.0 --server.port 8501
```

3. Abrir en el navegador del teléfono `http://<IP-de-la-PC>:8501`. La IP se ve con
   `ipconfig` (IPv4 de la interfaz Wi-Fi) y también aparece en la barra lateral del panel.

Firewall de Windows (una sola vez, PowerShell como administrador). Permite el puerto
**solo desde la subred local**, no desde internet:

```powershell
New-NetFirewallRule -DisplayName "Trading AI Streamlit 8501" -Direction Inbound `
  -Action Allow -Protocol TCP -LocalPort 8501 -Profile Private,Public `
  -RemoteAddress 192.168.1.0/24
```

El panel **no tiene login**: cualquier dispositivo de la misma red puede leerlo. No
exponer el puerto a internet. Para acceso fuera de casa, usar la app en la nube
(sección anterior) o una VPN privada como Tailscale en la PC y el teléfono.

## Explicación y entrega (fase 6)

Cada corrida guarda una explicación en la tabla `explanations`, siempre en dos capas:

1. **Determinista** (siempre): se arma con las cifras ya calculadas por el motor.
2. **LLM** (opcional): reescribe ese JSON y se valida con Pydantic. Si inventa una cifra,
   un activo o una señal, o usa lenguaje de garantía, se descarta y se publica la
   determinista con el motivo en `fallback_reason`.

El LLM no calcula, no decide y no cambia señales, tamaños ni riesgo. El costo estimado
de cada llamada se registra y hay un techo mensual (`TRADING_LLM_MONTHLY_BUDGET_USD`).
La explicación se ve en la pestaña **Explicación** del panel.

### Activar la IA con un token

La IA no corre en el navegador ni recibe claves de exchange. GitHub Actions
ejecuta el motor, guarda el JSON ya calculado y, si hay token, pide al proveedor
un texto validado. El token va en **Settings → Secrets and variables → Actions**
como `TRADING_LLM_API_KEY`; nunca en `.env` versionado, en `dist/` ni en el código.

1. Crea un token en un proveedor con endpoint compatible con OpenAI
   (`/chat/completions`).
2. Agrega el secret `TRADING_LLM_API_KEY`.
3. Opcionalmente define variables: `TRADING_LLM_MODEL`,
   `TRADING_LLM_BASE_URL`, `TRADING_LLM_MONTHLY_BUDGET_USD`,
   `TRADING_LLM_JSON_MODE` y las tarifas de costo.
4. Ejecuta el workflow con `force=true` y abre **Explicación** en la PWA.
   El badge debe decir `LLM`; si dice `determinista`, la PWA muestra el
   `fallback_reason`.
5. Local: copia `.env.example` a `.env`, activa `TRADING_LLM_ENABLED=true`,
   pon el token y corre `python -m jobs.daily_run --live --llm`.

La IA solo redacta el JSON que ya calculó el motor: no calcula señales,
probabilidades, tamaños ni riesgo. Si el proveedor falla, se publica la
explicación determinista. Guía detallada: `docs/OPERATIONS.md`.

Con `TRADING_TELEGRAM_ENABLED=true`, `TRADING_TELEGRAM_BOT_TOKEN` y
`TRADING_TELEGRAM_CHAT_ID`, también se envía por Telegram. Sin credenciales no se envía nada.

Docker:

```powershell
docker build -t trading-ai .
docker run --rm -p 8501:8501 trading-ai
```

## Estructura

```text
trading-ai/
├── app/                    # panel Streamlit (solo lectura)
├── src/
│   ├── config/             # settings por entorno
│   ├── data/               # adaptadores, ingesta, calidad, SQLite
│   ├── quant/              # features, señales, riesgo
│   ├── models/             # baseline logístico (numpy)
│   ├── reporting/          # reporte diario + explicación determinista/LLM
│   ├── llm/                # cliente compatible OpenAI + guardias de salida
│   ├── delivery/           # notificación opcional (Telegram)
│   ├── paper_trading/      # simulación persistente (modo sombra)
│   └── schemas/            # contratos Pydantic
├── jobs/daily_run.py       # corrida diaria end-to-end
├── jobs/export_static.py   # exportador HTML/PWA para GitHub Pages
├── backtests/              # walk-forward, métricas y CLI de backtest
├── tests/                  # unitarias + integración
├── prompts/                # prompts versionados
├── .github/workflows/      # CI + corrida diaria que publica en Pages
├── var/                    # estado local (SQLite + reportes); se versiona con git add -f
└── docs/                   # arquitectura y decisiones
```

## Invariantes (no negociables)

1. **Determinismo:** misma entrada → misma señal. Sin red, reloj ni azar dentro del motor.
2. **NO_TRADE es una respuesta válida**, no un fallo.
3. **Sin datos frescos no hay señal accionable.** La calidad se valida antes de calcular.
4. **El LLM y los agentes nunca calculan ni deciden:** reciben JSON ya calculado.
5. **Riesgo ejecutable por código:** circuit breakers y kill switch manual, fuera del LLM.
6. **Todo es auditable:** señal, features, versiones de modelo/código, costos y resultado posterior.
7. **Ningún secreto en Git.** Todo por variables de entorno.

## Comandos útiles

```powershell
pytest -q                       # tests
pytest --cov=src --cov-report=term-missing
ruff check .                    # lint
ruff format .                   # formato
python -m backtests.walk_forward    # humo con datos sintéticos
python -m backtests.run_backtest --source live --days 1000 --assets BTC,ETH
python -m backtests.compare_features --source live --days 1500 --feature-sets v0.1,v0.2
python -m backtests.experiments dev --source cache --days 1500 --assets BTC,ETH
```

## Riesgos conocidos de esta versión

- El baseline es una **regresión logística simple sobre 5 features**: sirve para medir, no para confiar. En vivo el score es **heurístico y no calibrado**; la regresión entrenada solo vive en los backtests (decisión de Fase 2).
- Los costos son estáticos (bps fijos); no modelan profundidad de libro ni impacto de mercado.
- El paper trade usa el precio vigente al momento de la corrida como entrada ejecutada y guarda el cierre de la señal por separado. La diferencia se reporta como **sesgo de entrada**; no es una entrada al cierre mágica.
- La BD y los reportes siguen versionados en un repo público; el crecimiento es ilimitado. Opciones y recomendación en `docs/STATE_STORAGE.md`.
- Binance devuelve pares **USDT**: se asume USDT ≈ USD en el backtest.
- CoinGecko ahora exige API key (`COINGECKO_API_KEY`); Binance es la fuente principal.
- **Backtest 2026-10-22 (1.000 días, BTC/ETH): sin ventaja demostrada.** Log loss 0,6978 vs 0,6931 del baseline. Ver `docs/BACKTEST-RESULTS.md`.
- **Barrido de horizontes 1–7 días: también sin ventaja.** El deterioro es monótono; no era un problema de horizonte.
- **A/B de features de régimen (v0.2): peor que v0.1** en log loss, calibración y retorno. Ver `docs/BACKTEST-RESULTS.md`.
- **Fase 2 (2026-10-23): 14 candidatos pre-registrados, ninguno pasa el gate.** El mejor Sharpe de desarrollo (`logit_v03_h1_t60`, 2.00) falló en el holdout único: Sharpe −0.69, retorno −7.35 % y 0/2 activos positivos. El paper se queda con la configuración actual; plan en `docs/PAPER-EVALUATION.md` y detalle en `docs/EXPERIMENTS.md`.
- **KRONOS-01 (2026-10-23): zero-shot `Kronos-small` tampoco pasa.** Log loss 0.8069 en desarrollo y 0.7936 en holdout (baseline 0.6931), ECE 0.17/0.15, retorno neto −64 %/−10.6 % y 0/2 activos. No se integra, no se toca el paper y no se propone fine-tuning; el adaptador y los tests quedan como evidencia reproducible en `docs/EXPERIMENTS.md`.
- **GitHub Pages es público y sin login.** Cualquiera con la URL puede leer el panel.
- **GitHub no disparó el `schedule` en las pruebas iniciales.** El workflow quedó activo; el botón **Actualizar** y el comando manual funcionan. Checklist de Settings en `docs/OPERATIONS.md`. Si el cron se activa, se reintenta cada hora y el sitio avisa si el dato supera la tolerancia (36 h en velas diarias).
- **La PWA no es una app nativa:** es un sitio instalable; necesita internet para actualizarse.

## Próximos pasos

1. Mantener el **paper trading en sombra** acumulando historial prospectivo con la configuración actual; no se promociona mientras no haya edge. Plan: ≥30 operaciones cerradas o ≥12 semanas, sin cambiar parámetros (`docs/PAPER-EVALUATION.md`).
2. Si se sigue investigando, cambiar de **datos** (funding, open interest, on-chain), no de features de OHLCV; los holdouts de Fase 2 y KRONOS-01 ya se consumieron y no se reutilizan.
3. Revisar el historial de **explicaciones y su costo**; ajustar el prompt no puede cambiar el cálculo.
4. Operación 24/7 (fase 7) solo con el historial y los límites revisados.
5. Revisar que el workflow diario siga verde y que el sitio muestre datos frescos.
