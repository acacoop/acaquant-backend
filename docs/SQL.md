# SQL / Postgres (Supabase) — capa relacional analítica

Subdoc de `docs/ARQUITECTURA.md §5`. Postgres NO reemplaza a Mongo: es un **espejo
relacional de solo-lectura** del núcleo de negocio, para reportería con SQL real,
cruces baratos y, a futuro, BI/ML. Si Postgres se cae, la operación (Mongo) sigue
intacta. Proveedor elegido: **Supabase**.

## Estado
- **Fase A — esquema:** `sql/schema.sql` (v1). ✅ aplicado en Supabase (smoke OK).
- **Fase B — sync Mongo→PG:** `jobs/sync_postgres.py` + reconciliación. ✅ escrito.
  Pendiente: backfill inicial (`--full`, fuera de rueda) + cronear (B.2: `JobRunLogger`
  + `crontab.txt` + índice `ingestado_en` para el incremental).
- **Fase C — reportería sobre PG:** endpoints de reporting leen SQL. ⏳ después.
- **Fase D — PG fuente de verdad de lo relacional:** solo si C demuestra valor. RED.

## Vista OPERACIONES en SQL (primer feature de producto migrado)

`/api/operaciones/ops/*` (pestañas MOVIMIENTOS/ARANCELES/AGRO) corre dual-run Mongo↔SQL.
- **Servicio:** `api/services/operaciones_sql.py` (puro, lee del pool `core.postgres.get_pool`).
  Agrega EN VIVO (sin rollup): Postgres hace el GROUP BY de 490k filas en ms con índices.
- **Flag:** `api/routers/operaciones.py::_motor()`. Override por request `?_engine=sql|mongo`;
  si no, global env **`OPERACIONES_SQL=1`** → SQL, sino Mongo (default). El path Mongo queda
  intacto → rollback = sacar la env + restart.
- **Validación:** `scripts/compare_ops_sql_vs_mongo.py` corrió 32/32 (SQL == Mongo en toda la
  grilla: monedas, rangos, cross-filters, dim aranceles incl. operador, agro). Es el GATE.
- **Cutover:** agregar `OPERACIONES_SQL=1` al `.env` del Droplet (lo lee `load_dotenv` de
  core.postgres) + `systemctl restart api.service`. El restart limpia el cache in-process.
- `VolumenMercadoAgro` (share agro) sigue leyéndose de Mongo (chica, manual) → híbrido.
- Reglas de traducción blindadas: `etapa IS DISTINCT FROM 'solicitud'` (98% NULL), `es_cierre=false`,
  `to_char` (substr 0-based), `COALESCE`/`ABS`, `ultima_ingesta` ISO naive, label `'(sin)'` donde
  Mongo dejaba `''` (el sync hace `''→NULL`), `denominacion` de cuentas-list no-determinística.

## Mapeos no obvios (verificados con `scripts/diag_shapes_sync`, REGLA #2)
- **`operaciones.id_cuenta` ← Mongo `cuenta`** (Operaciones NO tiene `id_cuenta`).
- **`operadores` = SOLO los `operador_email` que aparecen en `Clientes.Comitentes`**
  (los que realmente manejan cartera), con `nombre` ← `operador_nombre`. NO se mezcla con
  `Manager.Users`: esos son *usuarios de la app* (otra entidad). Mezclarlos metía ruido en
  los reportes (filas con 0 cuentas). Si algún reporte necesita usuarios-app, va tabla aparte.
- **Limpieza de huérfanos**: el sync borra de las dimensiones las filas cuya PK ya no está
  en Mongo (el UPSERT nunca borra). PG es espejo descartable → bajo riesgo. Orden FK-safe:
  comitentes → cuentas/operadores; contrapartes aparte.
- **`comitentes.estado_comercial`** es DERIVADO (`comercial.py`) → NULL en la capa SQL.
- **`contrapartes.id_cuenta` ← Mongo `cuenta`**.
- **Fechas** (`concertacion`, `fecha`, `fecha_snapshot`) llegan como string `'YYYY-MM-DD'`
  → se castean a `date` en el sync.
- **Operaciones sin `boleto`** se saltean (boleto es la PK natural del upsert) y se cuentan.
- **`operaciones.etapa`** no apareció en la muestra de 200 docs → se mapea defensivo
  (puede quedar NULL).

## El esquema (`sql/schema.sql`)
- **Dimensiones** (PK natural): `operadores`, `cuentas`, `comitentes`, `contrapartes`.
- **Hechos** (id_cuenta indexado, sin FK dura): `operaciones`, `aum`, `negocio_movimientos`.
- **Por qué sin FK dura en los hechos:** la fuente Mongo tiene huérfanos (operaciones
  con `id_cuenta` que no está en Comitentes). Un FK duro los rechazaría; soft +
  indexado nos deja cargarlos Y auditarlos (`SELECT ... WHERE id_cuenta NOT IN (SELECT id_cuenta FROM comitentes)`)
  como feature de calidad de dato.
- **v1:** tipos/nullability marcados `TODO:validar` — confirmar contra un diag
  read-only de Mongo antes de cargar en serio (REGLA #2 — no asumir shapes).

## Cómo aplicar el esquema en Supabase (acción del user, una vez)
1. Crear proyecto en supabase.com → se crea un Postgres.
2. SQL Editor → pegar el contenido de `sql/schema.sql` → Run. (O `psql < sql/schema.sql`.)
3. Copiar la **connection string** (Settings → Database → Connection string, modo
   `session`/`transaction`) y ponerla en el `.env` del Droplet como
   **`POSTGRES_URI`** (variable nueva). NO commitear el valor (va al `.env`, no al repo).

## Fase B — sync (cuando esté la `POSTGRES_URI`)
`jobs/sync_postgres.py`: lee las colecciones fuente de Mongo y hace UPSERT por PK a
Postgres (incremental + idempotente), en orden de dependencia (dimensiones antes que
hechos). Cron fuera de rueda. Después: un diag de **reconciliación** que compara
totales/sumas Mongo vs PG por colección y avisa diferencias (no confiar a ciegas).

Dependencia Python a sumar en Fase B: `psycopg[binary]` (driver Postgres). Conexión
vía singleton, igual patrón que `core/mongo.py` (un pool, sin `close()` por llamada).
