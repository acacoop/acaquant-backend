# SQL / Postgres (Supabase) — capa relacional del núcleo de negocio

Subdoc de `docs/ARQUITECTURA.md §5`. Proveedor: **Supabase**.

> **2026-06-16 — CUTOVER COMPLETO del núcleo Aunesa.** Postgres dejó de ser un
> "espejo read-only" y pasó a ser la **FUENTE DE VERDAD (escritura+lectura)** de
> tenencias, negocio, operaciones, clientes y contrapartes. Mongo quedó SOLO para
> mercado (`Trading.*`), catálogos y dominios no migrados. **NO se recrean en SQL
> los precomputes/rollups de Mongo** — Postgres agrega EN VIVO con índices.

## Estado (qué vive dónde)
- **SQL = fuente de verdad:** `portafolio.{tenencia, assets}`, `clientes.{comitentes,
  cuentas, operadores, contrapartes, actividad_mensual, accionistas}`,
  `operaciones.{operaciones, negocio_movimientos, movimientos}`. Writers escriben SQL
  directo (`jobs.portafolio_backfill`, `jobs.sync_comitentes`, `jobs.negocio_movimientos`,
  `jobs.operaciones_informes`, `jobs.fci_bilateral`). Flags `*_SQL=1` en `.env`.
- **DROPEADAS de Mongo (2026-06-15/16):** `Valuaciones.AuM`, `Valuaciones.Assets`,
  `Clientes.Comitentes`, `CashFlow.Contrapartes`, `CashFlow.NegocioMovimientos`,
  `CashFlow.Operaciones`, `CashFlow.OpsSerieDiaria`. Tabla SQL `aum` también dropeada.
- **Sync/rollups Mongo MATADOS:** `sync_{operaciones,negocio,aum,assets,contrapartes,
  dims_clientes}` de `jobs/sync_postgres.py`, `jobs/ops_rollup.py` (+OpsSerieDiaria),
  `jobs/comercial_rollup.py` (+ComercialCache).
- **Sigue en Mongo (no migrado):** `Trading.*` (mercado), `CashFlow.{Acreencias,
  Movimientos, Productores, Accionistas, VolumenMercadoAgro, TiposOperacion}`,
  `Manager.*`, `Opciones`, `News`, `Market`. `sync_postgres` solo espeja estas dims.

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

## El esquema (`sql/schema.sql`) — v2 organizado por dominio (2026-06-18)

**Nada en `public`.** Las tablas viven en schemas de dominio; el `search_path`
(`core/postgres.py`) resuelve los nombres sin calificar — los nombres de tabla son
únicos entre schemas, no hay colisión:

| Schema | Tablas |
|---|---|
| `clientes` | cuentas, operadores, comitentes, contrapartes, accionistas, actividad_mensual |
| `operaciones` | operaciones, negocio_movimientos |
| `portafolio` | tenencia, backfill_log, assets |
| `valuaciones` | consolidado, dolar, portfolio_snapshot |
| `mercado` | curvas, bonds_master, market_snapshot, snapshots_cierre, snapshots_cierre_hist, canje_cierre, mercado_hist |
| `macro` | series_macro, rem |
| `manager` | manager_users, role_matrix, grupos |
| `home` | news_headlines, market_quotes, market_calendar |

- **Migración v1→v2 idempotente:** un bloque `DO $$ … ALTER TABLE … SET SCHEMA` al
  principio de `schema.sql` mueve cada tabla que todavía esté en `public`/`portafolio`
  (instalación vieja). En fresh install no matchea nada y se crean ya en su schema.
  `SET SCHEMA` arrastra índices y constraints. Reincorporadas al schema.sql en esta
  tanda: `portafolio.tenencia` + `portafolio.backfill_log` (se habían creado a mano
  en Supabase). `consolidado` se movió `portafolio`→`valuaciones`.
- **Dimensiones** (PK natural): `operadores`, `cuentas`, `comitentes`, `contrapartes`.
- **Hechos** (id_cuenta indexado, sin FK dura): `operaciones`, `negocio_movimientos`.
- **Por qué sin FK dura en los hechos:** la fuente Mongo tiene huérfanos (operaciones
  con `id_cuenta` que no está en Comitentes). Un FK duro los rechazaría; soft +
  indexado nos deja cargarlos Y auditarlos (`SELECT ... WHERE id_cuenta NOT IN (SELECT id_cuenta FROM comitentes)`)
  como feature de calidad de dato.

### Orden de aplicación SEGURO (sin ventana de caída)
Postgres ignora en silencio los schemas inexistentes del `search_path`, así que el
código nuevo (con `mercado,macro,…` en el path) anda igual contra el esquema viejo.
El orden que NO corta la web:
1. `git pull` + `systemctl restart api.service` (deploya el `search_path` nuevo; las
   tablas siguen en `public`, el path las resuelve por ahí).
2. Correr `sql/schema.sql` en Supabase (mueve las tablas a sus schemas; ahora el path
   las resuelve por `mercado/macro/…`). Es idempotente — se puede re-correr.
   Hacerlo al revés (mover las tablas con el código viejo aún corriendo) deja la API
   sin resolver los nombres → 500. Código primero, SQL después.

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
| `mercado_hist` | Trading.{BreakevensHistorico, ForwardsHistorico, FuturosDLR, Caucion, FitParams, FairValueResiduos} | tabla genérica de históricos DIARIOS: grano (colección, fecha, subclave curva/ticker/moneda), doc en jsonb. Claves verificadas contra cada escritor |

**Cobertura de la vista MERCADO — qué NO se espeja por sync (decisión):**
- **Snapshots LIVE** (BreakevensLive, ForwardsLive/Zscore, CaucionSnapshot,
  FuturosDLRSnapshot, OptionsSnapshot, AgroSnapshot, AgroOpcionesSnapshot,
  CedearsSnapshot, AdrSnapshot, SnapshotsSinteticos, Valuaciones.DolarSnapshot,
  DolarOficialLive): cambian por segundo — un espejo horario es una foto vieja que
  ninguna vista puede servir. Migran **junto con su lectura**, vía dual-write del
  motor (`core/pg_mirror`, mismo patrón que `market_snapshot` live).
- **Streams** (TimeSales, CedearsTimeSales, OrderBookL2): alto volumen, sin
  consumidor SQL. `Trading.PreciosAcciones` (TS del scanner): pendiente de MEDIR
  volumen antes de decidir (REGLA #2/#4).

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

### Dual-write robusto de mercado — activar + validar (2026-06-18)

Objetivo: que la escritura de mercado caiga en SQL **en vivo siempre**, con Mongo de
respaldo (si SQL cae, la mesa sigue). El mecanismo (`core/pg_mirror.py`) ya existe y es
no-op con los flags apagados. Para volverlo el camino real:

1. **Prender los flags en el `.env` del Droplet** (los leen los motores y los jobs):
   - `SNAPSHOT_SQL=1` → motores live (`engines/valores.py`, `engines/curvas.py`)
     espejan `market_snapshot` a SQL.
   - `MERCADO_SQL_WRITE=1` → jobs batch (`bcra`, `argentina_datos`, `snapshot_cierre`,
     `cierre_canje`, quotes/anchors/calendar/news) espejan series_macro/rem/cierres/home.
   Restart de los servicios afectados para que tomen el env.
2. **Validar paridad con `python -m scripts.recon_mercado_sql`** (read-only): compara
   conteos Mongo `Trading.*` ↔ SQL `mercado.*`/`macro.*`. Diferencias chicas en tablas
   live (market_snapshot) son por timing — el chequeo de frescura (`max(updated_at)` de
   cada lado) confirma que el espejo está al día. Si una tabla de masters/cierres
   diffea fuerte, correr `jobs.sync_postgres --full` (baseline) y re-validar.

> Esto es el paso previo OBLIGADO a cualquier cutover SQL-native de los motores
> (decisión "dual-write robusto" — Mongo queda de red de seguridad).

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
