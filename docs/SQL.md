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

## Capa MERCADO (curvas, bonos, snapshots, macro) — data layer + escritura

Espejo SQL de todo lo que alimenta la vista de mercados (diseñado, NO copiado 1:1
de Mongo — ver comentarios en `sql/schema.sql §CAPA MERCADO`):

| Tabla | Fuente Mongo | Diseño |
|---|---|---|
| `series_macro` | Trading.{CER, DOLAR, BADLAR, TAMAR, RiesgoPais, InflacionMensual, InflacionInteranual} | 7 colecciones `{fecha, valor}` colapsan en UNA tabla larga (`serie` = nombre de la colección) |
| `rem` | Trading.REM | columnar, PK (informe, periodo, periodo_tipo) |
| `curvas` | Trading.Curvas | PK `ticker_corto`; lo consultable columnar + `flujos` y doc completo en jsonb |
| `bonds_master` | Trading.BondsMaster | PK `asset`; ídem (tickers/flujos jsonb) |
| `market_snapshot` | Trading.MarketSnapshot | **columnar a propósito**: dos motores escriben el mismo doc con `$set` parcial — cada uno upsertea SOLO sus columnas (un jsonb compartido pisaría al otro motor) |
| `snapshots_cierre_hist` | Trading.SnapshotsCierre | HISTÓRICO completo, PK (fecha, curva, ticker). `snapshots_cierre` (último por ticker, PnL) se mantiene aparte: otro grano/consumidor |
| `canje_cierre` | Trading.CanjeCierre | PK (ticker, fecha) |

**Escritura — dos caminos complementarios:**
1. **`jobs/sync_postgres.py`** (fases nuevas, no-críticas hasta que una vista las lea):
   baseline horario + backfill `--full`. Incrementales por fecha donde aplica.
2. **Dual-write best-effort** (`core/pg_mirror.py`, NUNCA levanta — Mongo sigue siendo
   la base operativa): los escritores espejan su write a PG con flags default OFF:
   - `MERCADO_SQL_WRITE=1` → jobs batch: `jobs.bcra`, `jobs.argentina_datos`,
     `jobs.snapshot_cierre`, `jobs.cierre_canje`, y las ingestas de home:
     `jobs.market_quotes` + `jobs.market_anchors` (espejan el watchlist COMPLETO
     re-leyendo Mongo — los datetimes vuelven naive/ms, idéntico al sync),
     `jobs.economic_calendar` (ídem) y `jobs.news_ingesta` (+ `prune_job` retención).
     **Sin este flag, MARKET_SQL/NEWS_SQL servirían datos con hasta 1h de atraso**
     (quotes se actualiza cada minuto; el sync corre cada hora).
   - `SNAPSHOT_SQL=1` → motores live: `engines/valores.py` (book/precios, throttle 5s
     porque re-escribe el estado completo) y `engines/curvas.py` (analíticos, SIN
     throttle porque escribe deltas). `pg_mirror` replica el `$set` parcial: upsertea
     solo las columnas presentes en cada fila.
   - Curvas/BondsMaster NO llevan dual-write: son masters chicos editados a mano
     (Manager) → el sync horario alcanza; menos código en el path de la API.
   - jsonb siempre vía `pg_mirror.doc_iso` (conversión datetime→ISO **recursiva** —
     los datetimes anidados, ej. flujos de BondsMaster, rompen `json.dumps` si solo
     se convierte el nivel top; incidente del primer backfill 2026-06-11).
3. **NO migran (decisión):** `TimeSales` y `OrderBookL2` — streams append-only de alto
   volumen; espejarlos duplicaría el costo del M10 sin consumidor SQL. Se revisa
   cuando haya un caso de uso de reportería tick-level.

## NEWS — retención 2 días (no se acumula)

Las noticias viven **2 días** y se borran solas (decisión 2026-06-11): TTL index
`ttl_fecha_publicacion` en `News.Headlines` (creado idempotente por
`jobs/news_ingesta.py`, `RETENCION_DIAS = 2`). El espejo `news_headlines` aplica
la misma retención: `prune_job` tras cada ingesta + el delete-orphans del sync de
red. Al crear el TTL, Mongo purga lo viejo existente (~14k docs del backlog).

## Vista MARKET en SQL (quotes + calendario)

`/api/market/quotes` y `/api/market/calendar/economic` corren dual-run (flag
**`MARKET_SQL=1`**, path Mongo intacto). Servicio: `api/services/market_sql.py`
(lee `data jsonb`; los retornos desde anchors se computan con `compute_returns`,
ÚNICA implementación que también usa el path Mongo del router; `_fix_tz` agrega
el offset `+00:00` a los campos que el `_serialize` Mongo emite con
`astimezone(UTC)` — paridad byte-a-byte). `market_calendar` v2: **PK natural
(evt_ts, country, event)** = el unique index del escritor (la v1 hkey=md5 del doc
generaba fila nueva en cada update; re-aplicar `sql/schema.sql` migra solo).
GATE: `scripts/compare_market_sql_vs_mongo.py` — correr con `MERCADO_SQL_WRITE=1`
ya prendido (si no, quotes diffea por frescura, no por bug). `/candle` y
`/profile` pegan a APIs externas — no migran.

## Fase B — sync (cuando esté la `POSTGRES_URI`)
`jobs/sync_postgres.py`: lee las colecciones fuente de Mongo y hace UPSERT por PK a
Postgres (incremental + idempotente), en orden de dependencia (dimensiones antes que
hechos). Cron fuera de rueda. Después: un diag de **reconciliación** que compara
totales/sumas Mongo vs PG por colección y avisa diferencias (no confiar a ciegas).

Dependencia Python a sumar en Fase B: `psycopg[binary]` (driver Postgres). Conexión
vía singleton, igual patrón que `core/mongo.py` (un pool, sin `close()` por llamada).
