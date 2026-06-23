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
- **Sigue en Mongo (no migrado):** `Trading.*` (mercado), `CashFlow.{Accionistas,
  Productores}`, `Manager.*`, `Opciones`, `News`, `Market`. `sync_postgres` solo
  espeja estas dims.
- **CashFlow dual-run (lectura SQL bajo flag, Mongo intacto, 2026-06-23):**
  `CashFlow.Movimientos` → `operaciones.movimientos` (flag **`MOVIMIENTOS_SQL`**,
  vista FLUJOS `/api/operaciones/flujos`); `CashFlow.Acreencias` →
  `operaciones.acreencias` (flag **`ACREENCIAS_SQL`**, back-office `/acreencias/*` +
  comercial `/cobros-futuros`); `CashFlow.VolumenMercadoAgro` →
  `mercado.volumen_mercado_agro` (flag **`VOLUMEN_AGRO_SQL`**, denominador del share
  AGRO en `/ops/agro`). `CashFlow.TiposOperacion` → `operaciones.tipos_operacion`
  (catálogo: espejado por sync para tenerlo, SIN lector SQL — el enrich de los
  writers sigue leyendo Mongo, corre en el proceso del job, no en una vista con flag).
  Dual-write en vivo: `jobs/cashflow.py` (movimientos), `jobs/acreencias.py`
  (acreencias, swap atómico). `CashFlow.Productores` es **HUÉRFANA** (nadie la lee en
  el código) → candidata a drop, NO se migró. Servicio de lectura:
  `api/services/cashflow_sql.py`.
- **PARTNER API dual-run (app SEPARADA, schema `partner`, 2026-06-23):** la base
  Mongo `ACAPortfolio` del servicio externo (`partner_api/`) se migró al mismo patrón.
  `ACAPortfolio.Cartera` → **`partner.cartera`** (columnas materializadas, shape fijo);
  `ACAPortfolio.ApiUsers` → **`partner.api_users`** (con `password_hash`). Lectura por
  flag **`PARTNER_SQL`** (default Mongo) en `partner_api/store.py`; escritura por flag
  **`PARTNER_SQL_WRITE`** (dual-write best-effort) en `jobs/partner_export.py` (Cartera)
  y `scripts/partner_user.py` (usuarios). Baseline: `scripts/partner_sql_baseline.py`.
  Conexión PROPIA `partner_api/pg.py` (no `core.postgres`; siempre califica
  `partner.<tabla>`, fuera del search_path de la mesa). NO entra en `jobs/sync_postgres.py`
  (dominio separado, otra base, dato de un tercero). Ver `docs/PARTNER_API.md`.

## MOTOR DE ÓRDENES — read-side dual-run (2026-06-23, dominio TRANSACCIONAL)

⚠️ **Dominio en vivo, crítico.** La base Mongo `Operaciones` (motor de órdenes) se migró
al patrón dual-run. **El write-side (motor + services de envío/cancel) sigue escribiendo
Mongo** y dual-escribe SQL **best-effort** bajo flag `ORDENES_SQL_WRITE` (try/except,
DESPUÉS del write a Mongo → un fallo de SQL NUNCA bloquea ni afecta la orden real al
broker). **La lectura** corre dual-run bajo flag `ORDENES_SQL` (default Mongo, override por
request `?_engine=sql|mongo`); el path Mongo queda intacto → rollback = sacar el flag.

8 tablas en el schema `operaciones` (ver `sql/schema.sql`):

| Mongo (`Operaciones.*`) | Tabla SQL | PK | Notas |
|---|---|---|---|
| `OrdenesLive` | `ordenes_live` | `cl_ord_id` | columnas account/ticker/estado(=`status`)/updated_at + `data` jsonb (doc completo) |
| `OrdenesAudit` | `ordenes_audit` | `id` IDENTITY | APPEND-ONLY (cada ER/evento = fila); `data` = ws_cl_ord_id + payload |
| `MotorOrdenesHeartbeat` (`_id='singleton'`) | `motor_heartbeat` | `id`='current' | frescura para /manager → DIAG |
| `OperativasMep` | `operativas_mep` | `id`=`operativa_id` | wrapper Dólar MEP; `ts`=created_at |
| `BracketsLive` | `brackets_live` | `cl_ord_id`=`entry_cl_ord_id` | **PK natural Mongo es `entry_cl_ord_id`** (no inferible) |
| `TriggersMep` | `triggers_mep` | `id`=str(_id) | scanner inactivo hoy; sync defensivo |
| `OrdenesIdempotency` | `ordenes_idempotency` | `clave`=`key` | claves anti doble-orden (TTL 1d en Mongo) |
| `AccountsDescubiertas` | `accounts_descubiertas` | `account_id` | `data.last_snapshot` trae ars/usd/n_pos |

**LECTURAS migradas** (`api/services/ordenes_sql.py`, selector en los routers):
- `GET /api/ordenes/dia` (`ordenes.list_orders_dia`) — el doc LOCAL sale de SQL
  `ordenes_live`; **el merge con el broker (pyRofex `get_all_orders_status`) es la verdad
  real-time y queda IDÉNTICO** (se reusa el builder puro `_broker_report_to_local`).
- `GET /api/ordenes/{cl_ord_id}` (`ordenes.get_order_status`, find_one) y el chequeo de
  scope del `DELETE /api/ordenes/{id}` (lectura de cuenta).
- `GET /api/risk/account/listado` (`risk.listado_cuentas`) — mismo shape, mismo join de
  nombres (`risk._nombres_por_id_cuenta`), mismo orden.

**Reglas de traducción (no inferibles):**
- `ordenes_live` filtra el día por `data->>'created_at'` (ISO en jsonb), **NO** por la
  columna `updated_at` (que se mueve con cada ER y arrastraría órdenes de días previos).
- Fechas en `data` jsonb son ISO → el read las rehidrata a datetime aware UTC (el merge y
  el sort por `created_at` las necesitan tipadas).
- `brackets_live`/`motor_heartbeat`/`operativas_mep`/`idempotency` mapean nombres de PK
  (`entry_cl_ord_id`→`cl_ord_id`, `_id`→`id='current'`, `operativa_id`→`id`, `key`→`clave`).

**NO migradas** (siguen Mongo-only, justificación):
- Operativa MEP listado/detalle/serie (`operativa_mep.listar_operativas_dia`,
  `obtener_detalle_operativa`, `serie_mep_minuto`) y brackets `/operar/brackets/dia`
  (`core.brackets.list_dia`): leen `OrdenesLive`+`OrdenesAudit`+`OperativasMep` con joins
  vivos; el read-side de esta tanda cubre la orden individual y el listado del día, que es
  lo que consume el Dashboard de Operar. Se pueden migrar después con el mismo patrón.
- **Heartbeat freshness** (/manager → DIAG): lo lee `api/services/diagnostico.py` de forma
  GENÉRICA por `db/coll/field` declarado en `diagnostico_registry.py` (no un lector con
  flag). Migrarlo tocaría el motor de diagnóstico → fuera del read-side; queda Mongo.

**Baseline**: `jobs/sync_postgres.py` (8 funciones `sync_*`, no-críticas → no alertan si
fallan; las colecciones pueden no existir aún). `ordenes_audit` es append-only: el
incremental borra la ventana `ts >= desde` y re-inserta (idempotente sobre la ventana).
Estado del flag: `python -m scripts.estado_sql` (`ORDENES_SQL` lectura, `ORDENES_SQL_WRITE`
dual-write).

> **GATE antes de cutover** (igual que operaciones): comparar SQL↔Mongo para
> `/ordenes/dia` (mismo merge de broker → comparar solo el set LOCAL), `/ordenes/{id}` y
> `/risk/listado`, exigir paridad. NO migrar la lectura a ciegas — el dual-write es
> best-effort y la tabla puede ir atrás del motor hasta que el baseline corra.

## VALUACIONES — caches precalculados (dual-run, lectura SQL bajo flag)

Las dos vistas pesadas de Portfolio leen un cache precalculado por cron (recorrer
~880 cuentas en una request HTTP = 502). Ambas corren dual-run Mongo↔SQL con el
path Mongo intacto; el cron dual-escribe Mongo+SQL en cada corrida:

- **`/valuaciones/consolidado`** (TOTALES > POR CUENTA): `Valuaciones.ConsolidadoCuentas`
  → `valuaciones.consolidado` (columnar: 1 fila/cuenta con valor/base100/PnL/TEM/TEA).
  Flag **`VALUACIONES_SQL`**. Writer: `jobs/consolidado_cuentas.py` (swap por TRUNCATE+INSERT).
  Service SQL: `valuaciones_sql.valuacion_consolidada`.
- **`/api/portfolio/pnl-todas`** (TOTALES, PnL por (cuenta, ticker) de toda la mesa):
  `Valuaciones.PnLTotalesCache` → `valuaciones.pnl_totales_cache` (**passthrough jsonb**:
  PK `id_cuenta`, `cuenta` materializado, `rows`/`totales` jsonb — `rows` trae el detalle
  de boletos por ticker, demasiado anidado para columnar). Flag **`PNL_TOTALES_SQL`**
  (override `?_engine=sql|mongo`). Writer: `jobs/pnl_totales_precompute.py` (dual-write SQL
  vía `pg_mirror.replace_native`, swap atómico; `pnl_todas_cuentas_compute_sql` ya leía
  TODO de SQL — boletos/posición/precios — para el cálculo). Service SQL de lectura:
  `pnl_sql.pnl_todas_cuentas_sql`. **TZ:** `computed_at` = `datetime.now(UTC)` aware →
  `timestamptz`. El filtro de tipo de cuenta (accionistas/productores) se hace EN PYTHON
  contra los sets SQL (`_cuentas_filter`), NO contra `Valuaciones.AuM` (eliminada) — el
  path Mongo viejo (`pnl.pnl_todas_cuentas`) todavía cruza AuM y queda roto para
  `filtro_cuenta != 'todas'`; el path SQL es el correcto. Sync baseline: `sync_pnl_totales`
  en `jobs/sync_postgres.py`.

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

### MANAGER infra — JobRuns + RoleAudit (dual-run 2026-06-23)

Las lecturas de INFRAESTRUCTURA del panel Manager corren dual-run Mongo↔SQL:
- **Tablas:** `manager.job_runs` (Manager.JobRuns — historial de corridas) y `manager.role_audit`
  (Manager.RoleAudit — auditoría append-only de cambios de rol/usuario). Passthrough `data jsonb`
  (doc completo vía `doc_iso`) + columnas materializadas para filtrar/ordenar: job_runs
  `(run_id PK, tipo, started_at, finished_at, status)`; role_audit `(audit_id PK, ts, actor, action,
  target)`. PK = `str(_id)` de Mongo.
- **TZ (crítico):** los writers usan `datetime.now(UTC)` **aware** → columnas **timestamptz**
  (started_at/finished_at/ts) — el cast no corre la hora.
- **Servicio:** `api/services/manager_infra_sql.py` (puro, pool `get_pool`). Replica el shape exacto
  del path Mongo: `/jobs/history` y `/jobs/history/stats` reformatean ts a AR igual que `jobs.py`;
  `/roles/audit` devuelve el doc tal cual (ts ISO del jsonb, como `core.roles.list_audit`).
- **Lecturas migradas:** `GET /api/manager/jobs/history`, `/jobs/history/stats`, `/roles/audit`, y la
  frescura por `run_tipo` del Diagnóstico (`api/services/diagnostico._leer_frescura`, con fallback a
  Mongo si SQL falla).
- **Flag lectura:** `MANAGER_SQL=1` (override `?_engine=sql|mongo`). Default Mongo → path Mongo intacto.
- **Flag escritura (dual-write):** `MANAGER_SQL_WRITE=1` → `core/job_runs.py` (JobRunLogger, tras el
  insert Mongo) y `core/roles.py::_audit_insert` (las 3 mutaciones de rol/usuario) espejan a SQL.
  Best-effort try/except: si SQL falla NO tumba el job/mutación (Mongo es la fuente de verdad; el sync
  alinea). **Sin este flag, lo recién escrito no aparece en SQL hasta el próximo `sync_postgres`.**
- **Sync baseline:** `sync_job_runs` (incremental por `started_at`, batcheado) + `sync_role_audit`
  (full, chica) en `jobs/sync_postgres.py`.
- **NO migradas (decisión):** `Manager.HealthReports` y `Manager.WatchdogAlertas` — son estado
  INTERNO job-a-job (informe de salud lee su propio informe previo; el watchdog su cooldown por `_id`).
  Ninguna vista/web las lee → migrarlas no aporta. Candidatas a quedarse en Mongo (TTL propio).

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
| `manager` | manager_users, role_matrix, grupos, job_runs, role_audit |
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
| `options_snapshot` | Opciones.OptionsSnapshot | grid LIVE de la chain (dual-write motor, SNAPSHOT_SQL). Solo vencimiento vigente |
| `options_metadata` | Opciones.Metadata (config + vr_ggal) | PK `type`, doc jsonb. Tasa risk-free + VR. Baseline por sync; tasa dual-write inmediata al editar |
| `options_data_hist` | Opciones.DataHistorica | rollup DIARIO de griegas por contrato, PK (fecha, symbol). Baseline por sync |
| `options_vr` | Opciones.VR-GGal | serie diaria GGAL local/ADR, PK `fecha`. Baseline por sync (saltea SUMMARY_METRICS) |
| `options_data` | Opciones.Data | ticks intradía (append-only, sin PK). Dual-write motor (SNAPSHOT_SQL). Solo vencimiento vigente: purga el motor + `jobs/archive_options_data` (ts < hoy ART, cron 20:50 UTC) |

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
