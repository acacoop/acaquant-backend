# SQL / Postgres (Supabase) — capa relacional analítica

Subdoc de `docs/ARQUITECTURA.md §5`. Postgres NO reemplaza a Mongo: es un **espejo
relacional de solo-lectura** del núcleo de negocio, para reportería con SQL real,
cruces baratos y, a futuro, BI/ML. Si Postgres se cae, la operación (Mongo) sigue
intacta. Proveedor elegido: **Supabase**.

## Estado
- **Fase A — esquema:** `sql/schema.sql` (v1). ✅ escrito (este commit).
- **Fase B — sync Mongo→PG:** `jobs/sync_postgres.py` + reconciliación. ⏳ siguiente.
- **Fase C — reportería sobre PG:** endpoints de reporting leen SQL. ⏳ después.
- **Fase D — PG fuente de verdad de lo relacional:** solo si C demuestra valor. RED.

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
