# Modelo de datos SQL (Postgres/Supabase) — principios de diseño

Subdoc de `docs/SQL.md`. Acá viven los **principios** del modelo relacional; el
inventario de schemas/tablas y las notas por dominio están en `docs/SQL.md`.

> **Estado (2026-06-29):** la migración terminó. Postgres/Supabase es la única base
> y el modelo está implementado en `sql/schema.sql` (10 schemas de dominio). Este
> doc ya no describe un "plan a futuro" — documenta las reglas que siguió el diseño
> y que rigen cualquier tabla nueva.

## Principios de diseño (a raja tabla)

1. **Tipos reales**, no todo `text`: fechas `date`/`timestamptz`, plata `numeric`,
   flags `boolean`. Lo anidado que no vale la pena descomponer va a `jsonb`.
2. **Un dato vive una vez (normalización).** Nombres/denominaciones/segmentación
   viven en la dimensión (`clientes.comitentes`, `clientes.cuentas`); los hechos
   referencian por `id_cuenta`. Mata las inconsistencias de "misma cuenta, varias
   grafías".
3. **Schema = dominio.** Cada tabla vive en su schema (`mercado`, `operaciones`,
   `clientes`, …), no en `public`. El `search_path` resuelve sin calificar.
4. **Derivado = se calcula, no se cachea a mano** salvo cuando pesa. Lo que se puede
   agregar en vivo (volumen/aranceles de operaciones, estado comercial) se hace con
   `GROUP BY` + índices, NO con una tabla-cache mantenida por cron. Solo las dos
   vistas de Portfolio que recorren ~880 cuentas usan un cache precalculado
   (`valuaciones.consolidado`, `valuaciones.pnl_totales_cache`) porque no entran en
   una request HTTP.
5. **Índices por patrón de acceso** (medidos con `EXPLAIN`), no "por las dudas".
6. **Calidad de dato en la frontera**: al ingestar, limpiar lo sucio (espacios,
   decimales, mayúsculas, `''`→`NULL`).

## Hechos vs dimensiones

- **Dimensiones** (PK natural, estables): `clientes.{comitentes, cuentas, operadores,
  contrapartes}`, masters de mercado (`mercado.curvas`, catálogos), `portafolio.assets`.
- **Hechos** (`id_cuenta` indexado, **sin FK dura**): `operaciones.{operaciones,
  negocio_movimientos}`. Sin FK dura porque la fuente histórica trae huérfanos; soft +
  indexado deja cargarlos y auditarlos como calidad de dato.
- **Snapshots / streams / históricos**: estado live por clave (`*_snapshot`,
  `market_snapshot`), tape intradía (`timesales`, `cedears_time_sales`), cierres
  diarios (`*_hist`, `snapshots_cierre_hist`, `mercado_hist`).

## Convenciones de escritura

Escritura native-SQL vía `core/pg_mirror.py` (`write_native`/`append_native`/
`write_hist`/`replace_native`/`merge_jsonb_native`, `doc_iso` recursivo para jsonb) y
SQL crudo en los services. Lectura por dominio en `api/services/<dominio>_sql.py`
(puros, pool `core.postgres.get_pool`). Detalle completo en `docs/SQL.md §3`.
