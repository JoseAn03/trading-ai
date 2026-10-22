# PRODUCT_DECISIONS

Documento vivo. Cualquier cambio de alcance se anota aquí **antes** de tocar código.

Última revisión: 2026-10-22 · Responsable: propietario del proyecto

## Decisiones cerradas

| Decisión | Valor | Motivo |
| --- | --- | --- |
| Usuario | Solo el propietario | Evita autenticación, soporte y riesgo regulatorio en la v1 |
| Mercado | Criptomonedas líquidas | Datos gratuitos, 24/7, sin horarios de bolsa |
| Activos iniciales | BTC, ETH | Máxima liquidez y menor riesgo de manipulación |
| Horizonte | 24 h | Primer horizonte cuantificable y auditable |
| Frecuencia | Ingesta periódica, reporte diario | Menos ruido, menos costo de LLM, decisiones meditadas |
| Salidas | Probabilidad, señal, incertidumbre, ranking, razones, invalidadores | El plan exige señales explicables y accionables |
| Señales | `LONG_BIAS`, `RISK_OFF`, `NO_TRADE` | Tres estados simples; `NO_TRADE` no es error |
| Panel | Streamlit | Rapidez de iteración; se reemplaza si molesta |
| Base de datos | SQLite → PostgreSQL | SQLite valida el modelo de datos; Postgres para 24/7 |
| IA de explicación | LLM con salida JSON validada | Redacta, no calcula |
| Entrega | Panel primero; Telegram después | Menor superficie y menos dependencias |
| Trading real | Bloqueado | Requiere todos los gates del plan |
| Costos por defecto | 6 bps comisión + 4 bps slippage | Conservador para cripto líquido |
| Umbral accionable | p(neto) ≥ 0.55 y retorno neto > 0 | Debe superar costos con margen |

## Lo que la v1 explícitamente NO hace

- No ejecuta órdenes ni guarda claves de exchange.
- No publica señales a terceros ni cobra por ellas.
- No promete rentabilidad ni usa lenguaje de certeza.
- No deja que un LLM o agente calcule indicadores, probabilidades o tamaño.
- No opera con datos obsoletos, degradados o proveedor caído.
- No sigue cientos de activos: dos, bien medidos.

## Supuestos que hay que revisar

1. **Horizonte 24 h** puede no ser donde exista ventaja; el backtest decidirá.
2. **Costos fijos** subestiman el impacto en momentos de estrés de liquidez.
3. **Features clásicas** (tendencia, momentum, volumen, volatilidad) pueden no tener señal neta.
4. **CoinGecko** es suficiente para empezar, insuficiente para producción seria.

## Preguntas abiertas

- ¿Qué drawdown máximo es tolerable **psicológicamente**, no solo en el papel?
- ¿El reporte diario debe llegar por email, Telegram o solo panel?
- ¿Se acepta operar fines de semana cripto o se filtran por liquidez?
- ¿Qué evidencia haría abandonar el proyecto? (definir antes de empezar)
- ¿Se necesita revisión fiscal antes del paper trading? (recomendado: sí, antes de dinero real)

## Registro de cambios

| Fecha | Cambio | Motivo |
| --- | --- | --- |
| 2026-10-22 | Creación inicial a partir del plan v2.0 | Arranque del repositorio |
| 2026-10-22 | Costos fijados en 6+4 bps ida y vuelta | Alinear con exchange minorista típico |
