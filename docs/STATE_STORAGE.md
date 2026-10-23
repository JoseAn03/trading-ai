# Almacenamiento del estado (solo análisis, sin migrar)

El repositorio es público y versiona `var/trading.db` y `var/reports/` con
`git add -f`. La BD creció a ~565 KB en dos días. Este documento compara opciones;
**no se cambió el mecanismo actual**.

## Opción A — Estado actual: SQLite + reportes en el repo público

**Pros:** simple; el workflow no necesita credenciales extra; el estado y los
reportes son auditables con `git log`; el sitio se regenera sin descargar nada.

**Contras:** el crecimiento es ilimitado y ensucia el historial; cualquiera lee
señales y operaciones; un repo clonado pesa cada vez más; no hay separación entre
código y datos; el commit automático puede competir con pushes humanos.

**Cuándo tiene sentido:** prototipo personal, datos no sensibles, pocos meses.

## Opción B — Artefacto privado de Actions + snapshot local

Guardar la BD como artifact de GitHub Actions (retención configurable) y bajar
un snapshot solo cuando haga falta; publicar al sitio un JSON agregado.

**Pros:** no crece el repo; los artifacts requieren autenticación; permisos
mínimos; se puede cifrar el artefacto con una clave en Secrets.

**Contras:** los artifacts expiran y no son una fuente de verdad permanente; el
sitio necesita un resumen público; recuperar el estado exige descargar el
artifact; la PWA no puede leer la BD directamente.

**Cuándo tiene sentido:** se quiere dejar de versionar la BD ya, manteniendo
GitHub como única infraestructura.

## Opción C — Release privado o repositorio privado de datos

Mover la BD a un repositorio privado (o a un release privado) y dejar el repo
público solo con código y sitio. El workflow de datos tendría un token de alcance
mínimo para escribir allí.

**Pros:** historial de datos privado y versionado; el sitio público solo recibe
un resumen; separación clara código/datos.

**Contras:** más complejidad de permisos y dos repos; el deploy de Pages sigue
siendo público; un token adicional que rotar; no elimina la necesidad de podar
histórico.

**Cuándo tiene sentido:** el proyecto se vuelve multiusuario o los datos dejan de
ser solo señales de mercado.

## Opción D — Almacenamiento externo (S3/R2/Supabase)

Subir snapshots a un bucket privado y leerlos desde el job de publicación.

**Pros:** escala y retención configurables; separación total; se puede cifrar y
poner políticas de ciclo de vida; sirve para 24/7.

**Contras:** nueva cuenta, credenciales y costo (aunque sea mínimo); más piezas
que pueden fallar; overkill para dos activos y velas diarias.

## Recomendación

**Corto plazo (sin migrar todavía):** mantener la Opción A pero poner un límite
explícito de retención: conservar `var/reports/` de los últimos 90 días y hacer
`VACUUM` mensual. Documentar que el repo es público.

**Siguiente paso, cuando la BD supere ~5 MB o haya datos que no deban ser
públicos:** Opción B (artifact privado + resumen público) es el menor cambio con
la mayor reducción de exposición. La Opción C es preferible si se quiere
historial permanente y privado; la D solo cuando exista operación 24/7.

Antes de migrar hay que definir: qué parte del estado es pública (señales,
equity agregada), qué parte es privada (BD completa), cuánto historial se
retiene y cómo se restaura un snapshot. No se tocó `var/trading.db` en esta fase.
