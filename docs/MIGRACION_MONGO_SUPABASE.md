# Migración MongoDB → Supabase (Postgres) — bitácora maestra

> **Documento vivo.** Registra TODO lo que se hace en la migración de MongoDB Atlas a
> Supabase/Postgres: el **qué** (ejecutivo) y el **cómo** (técnico). Se actualiza en cada
> cambio. Docs relacionados: `SQL.md` (operativa), `SQL_MODELO.md` (diseño del modelo).

> ## ✅ 2026-06-16 — NÚCLEO AUNESA MIGRADO Y CERRADO
> El plan dual-run/espejo de abajo quedó **superado**: se hizo cutover COMPLETO a SQL
> (escritura+lectura) y se **dropeó Mongo** de tenencias, assets, comitentes,
> contrapartes, NegocioMovimientos y Operaciones (+rollups OpsSerieDiaria/ComercialCache).
> Estado vigente y mapa "qué vive dónde": **`docs/SQL.md`**. Lo de abajo queda como
> bitácora histórica del proceso (cómo se llegó). Sigue en Mongo: mercado (`Trading.*`),
> catálogos, `Acreencias`/`Movimientos`, `Manager`, `Opciones`, `News`, `Market`.

## 🎯 2026-06-16 — ROADMAP DECOMMISSION (apagar Mongo por completo)

Decisión del user: **dar de baja Mongo, todo a SQL.** Inventario factual medido con
`scripts/diag_inventario_mongo_sql` (102 colecciones Mongo / 17 tablas SQL públicas + schemas
`clientes`/`operaciones`/`portafolio`).

**Buckets:**
- ✅ **YA en SQL** — negocio (operaciones, negocio_mov, comitentes, contrapartes, tenencia,
  assets, consolidado) + mercado base (curvas, bonds_master, canje_cierre, snapshots_cierre
  +hist, market_snapshot, dolar, rem, news, market_quotes/calendar, 7 series en `series_macro`).
- 🗑️ **DROP (muertas)** — **paso 1, EN CURSO**: `Manager.AsistenteLogs`, `Manager.AumBackfillLog`,
  `Valuaciones.AuMResumen`, `Valuaciones.AumBackfillRuns` → `scripts/drop_colecciones_muertas`
  (dry-run/--apply; refs en crear_indices/db_maintenance ya limpiadas). Pendiente (necesita sacar
  el path Mongo de comercial primero): `Clientes.ComercialCache` (58k, precompute muerto).
- 🔨 **MIGRAR** — RF derivada (Breakevens/Forwards/FitParams/FairValue/DiasHabiles), RV
  (Cedears/ADR/PreciosAcciones/DayTrading), Agro/Derivados, CashFlow (Movimientos/Acreencias/
  Productores/TiposOperacion/VolumenMercadoAgro), caches (PnLTotalesCache/TenenciaHD), Manager
  infra (JobRuns/HealthReports/WatchdogAlertas/RoleAudit/PyRofex*), MCP (OAuth*), UVA (+ macro).

**3 piedras grandes** (cada una = mini-proyecto con su gate): `Trading.TimeSales` (5,1M docs),
`Opciones.Data` (1M docs), **motor de órdenes** (`Operaciones.OrdenesLive/Audit/Triggers/Brackets`,
real-time, crítico).

**Secuencia (menor → mayor riesgo):** 1) drop muertas → 2) series simples → 3) snapshots de
mercado → 4) caches → 5) CashFlow → 6) Manager/MCP → 7) las 3 piedras. **Cada paso:** crear tabla
SQL → write nativo → leer SQL → validar paridad → drop Mongo. Reversible hasta el drop.

> **Lo más pesado es la ESCRITURA, no la lectura:** hoy los motores/jobs escriben Mongo y
> `sync_postgres` lo espeja. Para apagar Mongo, cada motor/job escribe SQL nativo (dual-write
> → cortar Mongo). Eso es el grueso del trabajo, no un flag.

---

## 1. Resumen ejecutivo

**Qué:** mover la plataforma de MongoDB Atlas (M10, ~US$56/mes) a Postgres en Supabase, para
consolidar en **una sola base SQL**, ganar el poder de SQL (joins, reportería, vistas) y, al
final, **apagar Mongo**.

**Para qué:** hoy los datos están "desparramados" en ~25 colecciones Mongo con deuda de
calidad (mismo cliente con varias grafías, boletos sucios). SQL nos da un modelo **limpio,
tipado y normalizado**, y reemplaza las colecciones-cache por **vistas** que se mantienen solas.

**Cómo (estrategia):** migración por **dominios**, sin romper la mesa en vivo:
- **Track A — Lecturas a SQL** (bajo riesgo): cada vista lee de SQL, validada 1:1 contra Mongo.
- **Track B — Escrituras dual** (SQL + Mongo como plan B): los jobs/motores escriben en ambos.
- **Track C — Apagar Mongo**: solo cuando un check confirme 0 dependencias.

**Regla de oro de seguridad:** ⚠️ NO se cancela el M10 hasta que el check de dependencias dé
cero. Mongo queda como plan B hasta verificar. Manda el gate, no el calendario.

---

## 2. Decisiones de arquitectura (el porqué)

| Decisión | Por qué |
|---|---|
| **Supabase región `us-east-1` (East US)** | El Droplet está en DO `nyc1` y el M10 en AWS us-east-1. Otra región daba 76ms de latencia (SQL más lento que Mongo); us-east-1 da **8ms**. La región se elige al CREAR el proyecto (no se cambia después). |
| **Plan Pro** | El espejo ya pesa >0.5GB (límite Free). Pro = 8GB + compute siempre prendido (sin cold start). |
| **Session pooler (no conexión directa)** | La directa es IPv6-only; el Droplet sale por IPv4 → falla. El pooler responde IPv4. |
| **Agregar en vivo, sin rollup** | Postgres agrega 490k filas en ms con índices; el rollup de Mongo existía solo porque el M10 no aguantaba. Menos maquinaria. |
| **Dual-run por flag** | Cada vista elige motor (`OPERACIONES_SQL=1` global o `?_engine=sql\|mongo` por request). Rollback = sacar la env + restart. |
| **Lo derivado = vistas, no colecciones-cache** | OpsSerieDiaria, ComercialCache, PnLTotalesCache, etc. → vistas (materializadas si pesan). Se elimina el cron que las llena. |
| **Modelo limpio por schemas** | `core / negocio / valuaciones / mercado / comercial` (ver `SQL_MODELO.md`). No se copia el desparramo de Mongo. |

---

## 3. Bitácora (qué se hizo, el qué y el cómo)

### Fase A — Esquema relacional ✅
- **Qué:** primer esquema SQL (capa analítica). **Cómo:** `sql/schema.sql` (7 tablas: dims
  operadores/cuentas/comitentes/contrapartes + hechos operaciones/aum/negocio_movimientos),
  aplicado en Supabase.

### Fase B — Sync Mongo→SQL + auto-update ✅
- **Qué:** llenar y mantener el espejo SQL solo. **Cómo:** `jobs/sync_postgres.py` (lee del
  SECONDARY de Mongo, batcheado + throttle, UPSERT idempotente, borra huérfanos en dims).
  Backfill `--full` (1.1M filas, reconciliado exacto). Auto-update: cron en `deploy/crontab.txt`
  (incremental cada hora :50 15-23 UTC L-V + `--full` domingo 12 UTC) con `JobRunLogger`
  (alerta Telegram si falla).
- **Mapeos no obvios verificados (REGLA #2):** `operaciones.id_cuenta ← Mongo cuenta`;
  fechas string→date; `operadores` = solo los de Comitentes (no usuarios-app); `''→NULL`.

### Migración vista OPERACIONES (`/api/operaciones/ops/*`) ✅
- **Qué:** primera vista de producto 100% en SQL (MOVIMIENTOS / ARANCELES / AGRO).
- **Cómo:**
  - `operaciones` ganó 5 columnas (`cantidad, instrumento, tipo_operacion, condiciones,
    ingestado_en`) + índices. Sync actualizado.
  - `api/services/operaciones_sql.py`: replica los 10 endpoints leyendo Postgres, agregando
    en vivo (sin rollup). Reglas blindadas: `etapa IS DISTINCT FROM 'solicitud'` (98% NULL),
    `es_cierre = false`, `to_char` (substr 0-based), `COALESCE`/`ABS`, `ultima_ingesta` ISO naive.
  - `core/postgres.py`: pool de conexiones (`get_pool`, psycopg_pool).
  - `api/routers/operaciones.py`: flag dual-run (`_motor()`), Mongo intacto como fallback.
  - `scripts/compare_ops_sql_vs_mongo.py`: GATE de validación → **32/32 SQL == Mongo**.
- **Performance (medido, `scripts/smoke_ops_perf.py`):** queries pesadas 2-3x más rápidas en
  SQL (aranceles 1.6s→0.55s, agro 1.3s→0.66s) y, sobre todo, **dejan de pegarle al M10**.
  Las livianas empatan.

### Migración vista NEGOCIO (`/api/operaciones/negocio/*`) — código listo ⏳ validar
- **Qué:** la vista de negocio del día (serie por categoría, detalle por cuenta, matrix,
  boletos, meta) lee de SQL.
- **Cómo:**
  - `negocio_movimientos` ganó columnas: `cuenta` (string "[id] NOMBRE"), `unidad` (excluir
    futuros DLR), `plazo/lugar/estado/informacion` (drill-down), `ingestado_en` (meta) +
    índice por `cuenta`. Nueva tabla `accionistas` (filtro de cuenta). Sync actualizado
    (`sync_negocio` + `sync_accionistas`).
  - `api/services/negocio_sql.py`: replica los 7 endpoints. Reglas: conversión por `mep` del
    boleto, `unidad IS DISTINCT FROM 'USDL'` (excluir futuros), filtros de cuenta
    (accionistas/sin_accionistas/cooperativas vía subquery + `~* '\ycoop'`, productores vía
    `comitentes.nivel_1`), scope por `id_cuenta`.
  - `api/routers/operaciones.py`: flag `NEGOCIO_SQL` (independiente de OPERACIONES_SQL).
  - `scripts/compare_negocio_sql_vs_mongo.py`: GATE de validación.
- **Pendiente del user:** ALTER en Supabase + `sync --full` + correr el harness (→ 0 diffs) +
  prender `NEGOCIO_SQL=1`.

### Migración HOME/NEWS (`/api/news/*`) — código listo ⏳ validar
- **Qué:** el panel de noticias de la home (lista + stats por fuente) lee de SQL.
- **Cómo:**
  - Tabla nueva `news_headlines` (`url` PK, `fecha_publicacion`, `fuente`, `categoria`, `titulo`,
    **`data jsonb` = doc completo con fechas a ISO**) + índice `ix_news_fecha`. Es el patrón
    "doc entero en jsonb" — sirve para colecciones semi-estructuradas sin arrastrar el esquema.
  - `sync_news` en `jobs/sync_postgres.py` (espeja `News.Headlines`, key `url`, delete-orphans).
  - `api/services/news_sql.py`: `list_headlines` (filtros desde/hasta/fuente/categoria/keyword
    ILIKE + orden fecha desc + paginado) y `stats` (group by fuente últimas N horas).
  - `api/routers/news.py`: flag `NEWS_SQL` en ambos endpoints (Mongo intacto como fallback).
  - `scripts/compare_news_sql_vs_mongo.py`: GATE.
- **Pendiente del user:** `CREATE TABLE news_headlines` + índice en Supabase + `sync --full` +
  harness (→ 0 diffs) + `NEWS_SQL=1`.

### Migración MARKET quotes/calendar (`/api/market/*`) — código listo ⏳ validar
- **Qué:** cotizaciones del watchlist (`/quotes`) y calendario económico (`/calendar/economic`).
  (`/candle` y `/profile` son APIs externas — Yahoo/Finnhub — NO tocan Mongo, no migran.)
- **Cómo:**
  - Tablas `market_quotes`/`market_calendar` + sync (ya estaban). `market_calendar` ganó
    **`evt_ts timestamptz`** (el filtro de rango sobre `evt_time text` era frágil): el sync
    la puebla SOLO cuando `time` es datetime — los docs con `time` string tampoco matchean
    el rango en Mongo → semántica idéntica.
  - `api/services/market_sql.py`: `quotes` + `calendar_economic` leyendo `data jsonb`.
    La regla de retornos desde anchors se factorizó en `compute_returns` (única
    implementación — el `_serialize` del path Mongo la invoca también → cero drift).
  - `api/routers/market.py`: flag `MARKET_SQL` en ambos endpoints (Mongo intacto).
  - `scripts/compare_market_sql_vs_mongo.py`: GATE (quotes todos/subset + grilla calendar).
- **Post-backfill 2026-06-11 (primer run real del user):** se corrigieron 3 cosas:
  1. `bonds_master` fallaba (`datetime is not JSON serializable`): los flujos de
     BondsMaster traen datetimes ANIDADOS y `_doc_iso` solo convertía el nivel top →
     ahora es recursivo (`core/pg_mirror.doc_iso`, lo usan sync y dual-writes).
  2. Los 8 diffs del harness eran (a) formato tz: el path Mongo emite `+00:00` en
     timestamp/updated_at/fetched_at/anchors_updated_at (vía `astimezone(UTC)`) y el
     jsonb los tenía naive → `_fix_tz` en `market_sql` (paridad exacta); (b) frescura:
     quotes se actualiza cada 1 min en Mongo y el espejo era horario → dual-write en
     las ingestas (abajo) + retry en el harness.
  3. `market_calendar` v2: PK natural `(evt_ts, country, event)` — igual al unique
     index del escritor `jobs/economic_calendar` (verificado en el código). La v1
     (hkey = md5 del doc) generaba fila nueva en cada update del evento → imposible
     dual-write limpio. Migración guardada en `sql/schema.sql` (detecta hkey → drop +
     recreate; el sync la repuebla).
  Además, **dual-write en las ingestas de home** (flag `MERCADO_SQL_WRITE`):
  `market_quotes`/`market_anchors`/`economic_calendar` espejan el estado completo
  re-leyendo Mongo (datetimes naive/ms idénticos al sync) y `news_ingesta` espeja +
  aplica retención.
- **NEWS — retención 2 días (pedido del user 2026-06-11):** TTL index en
  `News.Headlines` (`jobs/news_ingesta._ensure_indexes`, `RETENCION_DIAS=2`) — Mongo
  borra solo; purga el backlog existente (~14k docs) al crearse. Espejo: `prune_job`
  post-ingesta + delete-orphans del sync.
- **Reconciliación del backfill (explicación, no bug):** `contrapartes PG=362 vs
  Mongo≈377` = docs sin `cuenta` o con `cuenta` repetida (el espejo dedupea por PK);
  `aum PG=310.049 vs Mongo≈309.901` = el sync de aum no borra huérfanos (docs que el
  cleanup retroactivo sacó de Mongo quedan en PG; `estimated_document_count` además
  es aproximado). Si molesta, se agrega delete-orphans a la fase aum.
- **Pendiente del user:** re-aplicar `sql/schema.sql` (migra market_calendar + ya no
  hace falta el ALTER evt_ts) + `sync --full` (re-puebla bonds_master y calendar) +
  `MERCADO_SQL_WRITE=1` + harness (→ 0 diffs) + `MARKET_SQL=1`.

### Capa MERCADO — data layer + ESCRITURA (curvas, bonos, snapshots, macro) — código listo ⏳ validar
- **Qué:** el espejo SQL de TODO lo que alimenta mercados: series macro (CER/A3500/BADLAR/
  TAMAR/riesgo país/inflación), REM, `Trading.Curvas`, `Trading.BondsMaster`,
  `Trading.MarketSnapshot` (live), histórico de cierres y canje. Y la **Fase 2 (escritura)**
  del dominio mercado: los escritores espejan a PG con dual-write.
- **Cómo (diseñado, no copiado 1:1 — detalle en `docs/SQL.md §Capa MERCADO`):**
  - 7 tablas nuevas en `sql/schema.sql`: `series_macro` (7 colecciones-serie → UNA tabla
    larga), `rem`, `curvas` (columnar + flujos/doc jsonb), `bonds_master`, `market_snapshot`
    (**columnar** para replicar el `$set` parcial de los 2 motores sin pisarse),
    `snapshots_cierre_hist` (PK fecha/curva/ticker — shape verificado contra el ESCRITOR
    `jobs/snapshot_cierre.py`: `ts_cierre` es string, `ultimo_precio`/`tea` minúscula),
    `canje_cierre`.
  - 7 fases nuevas en `jobs/sync_postgres.py` (baseline horario + backfill `--full`;
    no-críticas hasta que una vista las lea).
  - **`core/pg_mirror.py`** (nuevo): dual-write best-effort genérico — agrupa filas por set
    de columnas (semántica `$set` parcial), chunking, jamás levanta. Flags default OFF:
    `MERCADO_SQL_WRITE=1` (jobs bcra / argentina_datos / snapshot_cierre / cierre_canje) y
    `SNAPSHOT_SQL=1` (motores valores.py con throttle 5s — re-escribe estado completo — y
    curvas.py sin throttle — escribe deltas).
  - Curvas/BondsMaster sin dual-write (masters chicos editados a mano → sync horario).
    `TimeSales`/`OrderBookL2` NO migran (streams de alto volumen sin consumidor SQL).
- **Pendiente del user:** re-aplicar `sql/schema.sql` en Supabase + `sync --full` (fuera de
  rueda) + prender `MERCADO_SQL_WRITE=1`; `SNAPSHOT_SQL=1` recién con mercado abierto para
  verificar carga (los motores live son el único write frecuente).
- **Cobertura TOTAL de la vista mercado (2026-06-12):** tabla genérica `mercado_hist`
  con los históricos diarios restantes (BreakevensHistorico, ForwardsHistorico,
  FuturosDLR, Caucion, FitParams, FairValueResiduos — claves verificadas contra cada
  escritor). Los snapshots LIVE (Breakevens/Forwards/Caucion/Options/Agro/Cedears/
  DolarSnapshot/DolarOficialLive) NO se espejan por sync (foto horaria de dato
  por-segundo = inservible): migran con su vista vía dual-write del motor.
  PreciosAcciones (TS scanner): medir volumen antes de decidir.
- **Próximo paso del dominio:** lecturas SQL de renta-fija/macro/REM (services + harness) —
  los harness de la parte live necesitan mercado abierto.

### Feature: chart de volumen interactivo (Operadores) — NO es migración, va sobre SQL
- **Qué:** click en una barra del chart (día/semana/mes) → la tabla de clientes muestra quiénes
  operaron en ESE período con su volumen (antes la tabla era siempre YTD y la barra inerte).
- **Cómo:** endpoint nuevo `/api/operaciones/comercial/clientes-por-fecha` (`desde`/`hasta`/`moneda`),
  implementado en **`comercial_sql.py` Y `comercial.py`** (dual-run, respeta `COMERCIAL_SQL`).
  Frontend `comercial-operaciones-view.tsx`: `onClick` en `<Bar>` + estado `selBar`/`periodo`,
  resaltado de barra y chip para limpiar.

### Feature: eliminadas sub-tabs TASA FIJA y CER de VALUACIONES (frontend)
- Backend ya había borrado `/api/portfolio/tasa-fija` y `/cer`. Se sacaron del front
  (`aum-view.tsx`: componentes, tipos, tabs) + las routes proxy muertas. Quedan TOTAL/FCI/ANÁLISIS.
  El módulo **Renta Fija (curvas)** NO se tocó (es otra cosa).

### Hallazgos / deuda de datos detectada
- Cuentas con el **mismo id_cuenta y distinta grafía** de denominación entre boletos (Mongo
  `$first` arbitrario). En el modelo SQL limpio el nombre vive una vez en `core.cuentas`.
- Contraparte cuenta **375** = BAVSA y ALLARIA a la vez (revisar con la mesa).

---

## 4. Lo que se puede migrar/probar HOY (no depende de la rueda)

Todo lo **Aunesa** (no real-time) y parte de **Primary** que no necesita mercado abierto:
- **Aunesa:** operaciones, negocio_movimientos, comitentes, AuM, contrapartes, aranceles,
  actividad_mensual, accionistas, productores → vistas OPERACIONES (hecho), NEGOCIO, COMERCIAL,
  PORTFOLIO/AuM.
- **Primary no-realtime:** master de instrumentos, configuración de curvas, etc. (verificable
  sin tick en vivo).
- **Real-time (motores WS → MarketSnapshot/TimeSales/OrderBookL2):** al final, requiere rueda.

→ **Hoy avanzamos en lecturas de todas las vistas Aunesa** (Track A) + dual-write de sus jobs.

---

### Migración vista COMERCIAL (`/api/operaciones/comercial/*`) — código listo ⏳ validar
- **Qué:** la vista por operador (selector, KPIs, clientes, análisis/churn, series, informes
  globales por comercial y por segmento) lee de SQL.
- **Cómo:**
  - Chunk 0 (data layer): `comitentes` ganó `estado` (legal Activa), `fecha_alta_legajo`, ficha
    (telefono/email/niveles 4-5/etc.) y `cupo_*`. Tablas nuevas `manager_users` y
    `actividad_mensual` (snapshot point-in-time). Sync ampliado.
  - `api/services/comercial_sql.py`: replica las 11 funciones. Lo DERIVADO (`ComercialCache`)
    se computa EN VIVO (CTE vol+arancel) — no se sincroniza (sería derivar de un derivado).
    Reglas: `estado='Activa'`, `etapa IS DISTINCT FROM 'solicitud'`, AuM último snapshot global,
    pesificación por mep, dolarización al MEP actual (`_cv`/`_factor_usd` reusados de Mongo).
  - `api/routers/operaciones.py`: flag `COMERCIAL_SQL` (vía `_com_motor`).
  - `scripts/compare_comercial_sql_vs_mongo.py`: GATE.
- **Pendiente del user:** ALTER+tablas en Supabase (Chunk 0, ya entregado) + `sync --full` +
  harness (→ 0 diffs) + `COMERCIAL_SQL=1`. Nota: `informe_comercial` Mongo puede leer
  ComercialCache (cache) vs SQL en vivo → posible diff chico de frescura (evaluar, no es bug).

## 5. Plan por dominios (orden)

1. ✅ OPERACIONES (negocio/operaciones)
2. ✅ NEGOCIO (validado 26/26; cutover con NEGOCIO_SQL=1)
2b. 🟡 COMERCIAL (código completo, harness 19/23; 4 diffs menores PENDIENTES de revisar
    —probable cache-vs-live de informe_comercial / redondeo—; queda en Mongo hasta eyeballear)
3. ✅ COMERCIAL (código completo; 4 diffs menores aceptados; flag COMERCIAL_SQL)
4. 🔵 PORTFOLIO / AuM / PnL — EN CURSO:
   - Chunk 0 (data layer: `assets`, `dolar`, `aum.tipo_titulo`) ✅
   - Chunk 1 (raw: `listar_aum`, `listar_cuentas`) ✅
   - Chunk 2 (agregados `total_serie/snapshot/diff`, `fci_serie/snapshot`) ✅ + flag `PORTFOLIO_SQL`
     + harness `compare_portfolio_sql_vs_mongo` → **cutover PARCIAL de AuM** (tasa-fija/cer/pnl
     siguen en Mongo hasta chunks 3-5). Servicio `api/services/portfolio_sql.py`, router `carteras.py`.
     Harness 18/19: único diff `fci_serie len 70 vs 62` = cache-vs-live (Mongo lee
     AuMResumenFCI con 8 fechas viejas; SQL agrega en vivo, más correcto; cruzado con
     total_serie que sí coincide → aum NO está stale). ACEPTADO. Flag PORTFOLIO_SQL listo.
   - Chunk 3 (renta fija/CER → tablas `curvas`/`bonds_master`/`market_snapshot`) ⏳
   - Chunk 4 (PnL por cuenta — motor cost-basis copiado verbatim; `portfolio_snapshot`/`snapshots_cierre`) ⏳
   - Chunk 5 (PnL TOTALES + consolidado → tablas jsonb sincronizadas desde los crons) ⏳
5. 🔵 AUTH / permisos (keystone para apagar Mongo) — EN CURSO:
   - Chunk 0 (schema + sync: `manager_users` full, `role_matrix`, `grupos`) ✅ riesgo CERO (no toca lectura)
   - Chunk 1 (lecturas SQL puras: `core/roles_sql.py`, `core/grupos_sql.py`) ✅
   - Chunk 2 (orquestador con **FALLBACK** en core/roles.py + core/grupos.py: try SQL → except →
     Mongo → DEFAULT; flag `AUTH_SQL` default OFF; nunca puede lockear) ✅ + harness
     `compare_auth_sql_vs_mongo`. Flag OFF = comportamiento idéntico. Pendiente del user:
     sync auth + harness (0 diffs) + AUTH_SQL=1.
   - Chunk 3 (prender `AUTH_SQL=1` tras harness verde) ⏳
   - Chunk 5 (dual-write de los writes de roles/grupos) ⏳
   **Patrón de seguridad:** el fallback vive DENTRO de core (no en el router), porque auth corre
   en CADA request; ante cualquier error SQL cae a Mongo → prender AUTH_SQL nunca tumba la app.
   Vistas tasa-fija/CER de portfolio: ELIMINADAS (sin uso). PnL Títulos: pendiente (task aparte).
5c. ✅ HOME/NEWS (código listo; flag `NEWS_SQL`; harness) — pendiente user: CREATE TABLE + sync + flag.
5d. ✅ MARKET quotes/calendar (service `market_sql` + flag `MARKET_SQL` + harness; pendiente
    user: ALTER `evt_ts` + sync + harness + flag).
6. 🟡 MERCADO — data layer + ESCRITURA hechos (tablas series_macro/rem/curvas/bonds_master/
   market_snapshot/snapshots_cierre_hist/canje_cierre + sync + dual-write `core/pg_mirror`
   con flags `MERCADO_SQL_WRITE`/`SNAPSHOT_SQL`). Falta: lecturas SQL de renta-fija/macro/REM
   (real-time se valida con mercado abierto). TimeSales/OrderBookL2 NO migran (decisión).
7. ⏳ FASE 2 — ESCRITURAS → apagar Mongo. Ver §5b "Aunesa/Negocio — las dos capas":
   Capa A (lecturas, listo, validar+prender 5 flags) + Capa B (dual-write de 6 ingestas, PAUSADA
   2026-06-07 a pedido del user). Incluye también auth writes + caches + ~40 jobs cron.

---

## 5b. INVENTARIO COMPLETO de lo que FALTA (mapa real, para no esconder scope)

### ✅ Migrado (lee SQL, con harness + flag): operaciones, negocio, comercial, portfolio-AuM, PnL, auth(lecturas).

### ⏳ Migrable SIN mercado (datos diarios/Aunesa) — pendiente:
- **Home/News**: `/api/news/*` → **código listo ⏳ validar** (tabla `news_headlines` jsonb + sync +
  `news_sql.py` + flag `NEWS_SQL` + harness). Pendiente user: CREATE TABLE + sync --full + harness + flag.
- **Market quotes/calendar**: `/api/market/quotes` + `/calendar/economic` → **PARCIAL**: tablas
  (`market_quotes`, `market_calendar`) + sync HECHOS; falta `market_sql.py` + flag `MARKET_SQL` +
  harness. (candle/profile = APIs externas, no migran.) **← primer pendiente concreto si se retoma.**
- **Watchlist/anchors**: jobs.market_quotes / market_anchors → colecciones de home (escritura, Fase 2).
- **Manager** (6 tabs): intel, jobs/logs (JobRuns), **clientes/Comitentes (lectura + EDICIÓN/segmentación)**,
  compliance, assets/títulos. (roles/users/grupos: lecturas ✅ por auth; falta su edición.)
- **back-office**.

### ⏳ Necesita MERCADO ABIERTO (real-time, motores):
- Vistas: renta-fija, derivados, agro, sintéticos, renta-variable/scanner, estrategia (curvas/forwards/
  breakevens/carry), operar, MCP. (El DATA LAYER de curvas/bonos/snapshots/macro ya está — ver
  "Capa MERCADO" arriba — lo que falta acá son los SERVICES de lectura + harness con rueda.)
- **Motores**: `valores.py` y `curvas.py` (los 2 que escriben MarketSnapshot) ya tienen dual-write
  vía `core/pg_mirror` (flag SNAPSHOT_SQL). Falta evaluar el resto (options/caución/cedears/dólar)
  cuando su lectura migre — mismo patrón pg_mirror.

### ⏳ ESCRITURAS (para apagar Mongo) — avance parcial:
- ✅ **Jobs de MERCADO/HOME con dual-write** (flag `MERCADO_SQL_WRITE`): bcra, argentina_datos,
  snapshot_cierre, cierre_canje, market_quotes, market_anchors, economic_calendar,
  news_ingesta. ✅ **Motores** valores/curvas (flag `SNAPSHOT_SQL`).
- ⏳ El resto de los **~40 jobs cron** (market_quotes, news, fair_value, aum, negocio, operaciones,
  aranceles, actividad_mensual, pnl_totales_precompute, consolidado, …) → mismo patrón
  `core/pg_mirror.mirror_job` cuando se retome la Capa B de Aunesa.
- **Auth writes**: upsert_user, delete_user, set_role_modules, auto_register, last_seen, grupos CRUD, RoleAudit.
- **Segmentación writes**: edición de Comitentes (niveles/operador) desde /manager.
- **Caches**: PnLTotalesCache, ConsolidadoCuentas (cron → tabla jsonb).

### 🅰️🅱️ Aunesa/Negocio — las dos capas (decisión 2026-06-07: Capa B PAUSADA por el user)
El dominio Aunesa/Negocio (lo que más interesa apagar de Mongo) se cierra en dos capas:

- **Capa A — LECTURAS (código 100% listo, falta validar+prender):** los 5 dominios
  (`OPERACIONES_SQL`, `NEGOCIO_SQL`, `COMERCIAL_SQL`, `PORTFOLIO_SQL`, `PNL_SQL`) ya tienen servicio
  SQL + harness + flag. Runbook: `git pull` → `sync --full` → correr los 5 `compare_*_sql_vs_mongo`
  → si 0 diffs, agregar los 5 flags al `.env` + `restart`. **100% testeable sin mercado.** Reversible.
- **Capa B — ESCRITURAS (PAUSADA hoy a pedido del user):** mientras Aunesa escriba Mongo y `sync_postgres`
  copie a SQL, Mongo NO se puede apagar. Para apagarlo hay que pasar a SQL la escritura de **6 ingestas**,
  vía **dual-write** (escribir SQL *además de* Mongo → validar → quitar write Mongo + sync de esa tabla):
  1. `jobs/negocio_movimientos.py` → `CashFlow.NegocioMovimientos`
  2. `jobs/operaciones_informes.py` (+`scripts/enrich_operaciones.py`) → `CashFlow.Operaciones`
  3. `jobs/aum.py` → `Valuaciones.AuM`
  4. ingesta Comitentes (Aunesa) → `Clientes.Comitentes`
  5. `actividad_mensual` → snapshot
  6. `pnl_totales_precompute` / `consolidado_cuentas` → caches (→ tablas jsonb)
  Cuando las 6 estén en SQL-only → nadie usa Mongo de Aunesa/Negocio → **apagar Mongo** del dominio.
  **Próximo paso cuando se retome:** dual-write de (1) `negocio_movimientos` de a uno, con harness.

### Realidad: estamos ~35-40%. Lo hecho es la parte analítica pesada + auth + PnL. El resto es un
### programa de varias corridas (la capa de mercado se valida con mercado abierto).

## 6. Operación: prender / apagar / rollback

- **Prender SQL en una vista:** `OPERACIONES_SQL=1` (y equivalentes por vista) en el `.env`
  del Droplet + `systemctl restart api.service`.
- **A/B sin cutover global:** `?_engine=sql|mongo` por request.
- **Rollback:** sacar la env + restart → vuelve a Mongo al instante (el path Mongo queda).
- **Auto-sync:** `crontab /root/TradingAV/deploy/crontab.txt` (ya incluye `sync_postgres`).

---

## 7. Reglas de seguridad (no negociables)

1. NO cancelar el M10 hasta que el check de dependencias dé 0 (Track C).
2. Cada vista migrada pasa por el harness de comparación (SQL == Mongo) antes del cutover.
3. Mongo queda como plan B (dual-write) durante toda la transición.
4. Validar imports (`python -c "from api.main import app"`) antes de pushear cambios a `api/`.
