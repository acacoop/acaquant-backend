# SQL / Postgres (Supabase) — la base de datos de TradingAV

Subdoc de `docs/ARQUITECTURA.md §5`. Proveedor: **Supabase** (Postgres managed).

> **2026-06-29 — DECOMISO COMPLETO DE MONGO.** Postgres/Supabase es la **ÚNICA**
> base de datos del sistema. Ya NO hay dual-run, ni flags de engine, ni "espejo
> read-only", ni migración en curso: la migración Mongo→Postgres **terminó**.
> Todos los motores, jobs y services leen y escriben SQL nativo.
> El cliente Mongo, `api/db.py`, `core/mongo*.py` y el tooling Mongo fueron
> borrados del repo. Registro del decomiso: `docs/HANDOFF_DECOMISO_MONGO.md`.

---

## 1. El modelo en una página

- **Una sola base** (Supabase Postgres), organizada en **10 schemas de dominio**.
  Nada vive en `public`; el `search_path` (definido en `core/postgres.py`) resuelve
  los nombres sin calificar — los nombres de tabla son únicos entre schemas, no hay
  colisión.
- **Conexión:** singleton `core.postgres.get_pool()` (un pool, sin `close()` por
  llamada — mismo contrato que tenía el cliente Mongo).
- **Escritura nativa:** los motores y jobs escriben SQL directo vía los helpers de
  `core/pg_mirror.py` (ver §3) o SQL crudo en sus propios services. No hay capa de
  "espejo" intermedia.
- **Lectura:** la lógica de cada dominio vive en `api/services/<dominio>_sql.py`
  (puros, leen del pool). Los routers son thin HTTP wrappers.
- **Tipos reales:** fechas `date`/`timestamptz`, plata `numeric`, flags `boolean`.
  Los documentos anidados que no vale la pena descomponer en columnas viven en
  columnas `jsonb` (`data`, `flujos`, `book`, `rows`, etc.).

---

## 2. El esquema — `sql/schema.sql` (10 schemas por dominio)

`sql/schema.sql` es la fuente de verdad del modelo. (Nota: `schema.sql` no siempre
está 100% aplicado en la DB real — es el espejo del diseño; al agregar/cambiar una
tabla, aplicar el `CREATE TABLE IF NOT EXISTS` correspondiente en Supabase.)

| Schema | Tablas |
|---|---|
| `mercado` | curvas, market_snapshot, snapshots_cierre, snapshots_cierre_hist, canje_cierre, timesales, dias_habiles, forwards_zscore, fit_params, fair_value_residuos, ons_ignoradas, futuros_dlr_snapshot, caucion_snapshot, options_data, options_data_hist, options_snapshot, options_metadata, options_vr, cedears, cedears_snapshot, adr_snapshot, precios_acciones, cedears_time_sales, day_trading_stats, agro_snapshot, agro_opciones_snapshot, agro_pizarra, camara_cereales, volumen_mercado_agro, snapshots_sinteticos, mercado_hist, rubros, adhoc_subscriptions |
| `macro` | series_macro, uva, rem |
| `valuaciones` | consolidado, pnl_totales_cache, portfolio_snapshot, dolar, dolar_snapshot, dolar_oficial_live |
| `portafolio` | tenencia, assets, backfill_log |
| `operaciones` | operaciones, negocio_movimientos, acreencias, movimientos, tipos_operacion, ordenes_live, ordenes_audit, ordenes_idempotency, triggers_mep, brackets_live, operativas_mep, motor_heartbeat, accounts_descubiertas |
| `clientes` | comitentes, cuentas, contrapartes, accionistas, actividad_mensual, operadores, objetivos_comerciales |
| `manager` | manager_users, role_matrix, role_audit, grupos, job_runs, pyrofex_instruments, pyrofex_discovery |
| `home` | market_quotes, news_headlines |

**Diseño:**
- **Dimensiones** (PK natural): `clientes.{comitentes, cuentas, operadores, contrapartes}`.
- **Hechos** (`id_cuenta` indexado, **sin FK dura**): `operaciones.{operaciones, negocio_movimientos}`.
  La fuente histórica traía huérfanos (hechos con `id_cuenta` que no está en
  comitentes); un FK duro los rechazaría. Soft + indexado deja cargarlos Y
  auditarlos (`SELECT … WHERE id_cuenta NOT IN (SELECT id_cuenta FROM comitentes)`)
  como feature de calidad de dato.
- **Snapshots live** (market_snapshot, *_snapshot, dolar_oficial_live, etc.): estado
  "último por clave", lo escriben los motores en cada update.
- **Históricos / streams**: `mercado_hist` (genérica de históricos diarios por
  colección+fecha+subclave en jsonb), `timesales`/`cedears_time_sales` (tape
  intradía, retención corta), `*_hist`/`snapshots_cierre_hist` (cierres diarios).

---

## 3. Convenciones de escritura (`core/pg_mirror.py`)

`core/pg_mirror.py` concentra los helpers de escritura native-SQL que usan los
motores y jobs. Best-effort: nunca levanta una excepción que tumbe al motor/job.

| Helper | Uso |
|---|---|
| `write_native(table, key_cols, rows)` | UPSERT por PK — el patrón general de los writers (curvas, snapshots, masters). El SQL es la única escritura (sin gate de flag). |
| `append_native(table, rows)` | INSERT append-only (streams/auditorías: `timesales`, `ordenes_audit`, `options_data`). |
| `write_hist(coleccion, fecha_str, k, doc)` | Históricos diarios → `mercado.mercado_hist` (grano colección+fecha+subclave, doc completo en jsonb). |
| `replace_native(table, rows)` | Swap atómico (TRUNCATE/replace) para caches recomputados de punta a punta. |
| `merge_jsonb_native(table, key_cols, key_vals, patch)` | Merge parcial sobre una columna `jsonb`. |
| `read_native_doc(table, key_cols, key_vals)` | Lectura del doc jsonb por clave. |
| `prune_native(table, col, days)` | Retención (borra filas más viejas que `days`). |
| `doc_iso(v)` | Conversión **recursiva** datetime→ISO antes de guardar en `jsonb` (los datetimes anidados, ej. flujos, rompen `json.dumps` si solo se convierte el nivel top). Usar SIEMPRE al armar el doc jsonb. |

**Patrón columnar de `market_snapshot` (no inferible):** dos motores escriben la
misma fila por ticker — `engines/valores.py` (book/precios, ~1s) y `engines/curvas.py`
(analíticos TEA/TEM/duration/paridad, ~5s). Cada uno upsertea **solo sus columnas**.
Por eso la tabla es columnar y no un único `jsonb` compartido: un merge shallow de
jsonb pisaría al otro motor.

**Lectura por dominio:** `api/services/<dominio>_sql.py` (puros, pool `get_pool`):
`operaciones_sql`, `pnl_sql`, `valuaciones_sql`, `portfolio_sql`, `renta_fija_sql`,
`scanner_sql`, `opciones_sql`, `agro_sql`, `macro_sql`, `rem_sql`, `market_sql`,
`mercado_hist_sql`, `cashflow_sql`, `comercial_sql`, `control_comercial_sql`,
`manager_infra_sql`, `ordenes_sql`, `operativa_mep_sql`, `news_sql`, `assets_sql`,
`import_tenencia_sql`.

---

## 4. Notas de modelo por dominio (referencia)

### MERCADO (curvas, bonos, snapshots, opciones, RV, agro, macro)
- `mercado.curvas` — PK **`ticker`** (`AL30`; se llamaba `ticker_corto` hasta el
  renombre del 2026-08-15) + **`instrumento`** = el símbolo de mercado
  `MERV - XMEV - AL30 - 24hs` (era la columna `ticker`). El eje bono/letra
  (`tipo_instrumento`) se ELIMINÓ el 2026-08-15: estaba vacío en los 221 bonos.
  El blob `data` conserva las claves VIEJAS a propósito.
  Columnas consultables tipadas
  (curva/tipo/moneda/fechas/cupón/etc.) + `flujos` jsonb (cashflows del bono) +
  `data` jsonb (doc completo). Las ONs se consolidaron acá como `curva = on_<sector>`
  (la vieja `bonds_master` fue eliminada — UNA sola base de bonos).
- `macro.series_macro` — tabla larga: las 7 series macro (CER/DOLAR/BADLAR/TAMAR/
  RiesgoPais/Inflación Mensual/Interanual) colapsan en una tabla con `serie` =
  nombre de la serie + `{fecha, valor}`.
- `mercado.mercado_hist` — genérica de históricos diarios (breakevens/forwards/
  futuros DLR/caución/fit params/fair value residuos): grano (colección, fecha,
  subclave) + doc jsonb.
- **Renta variable** (cutover histórico 2026-06-24): `mercado.{cedears, cedears_snapshot,
  adr_snapshot, precios_acciones, cedears_time_sales, day_trading_stats}`. Los motores
  `motor_cedears`/`adr_live` y el job `precios_acciones_daily` escriben SQL-native;
  scanner/day_trading/pivot_points leen SQL.
- **Opciones** (GGAL): `mercado.options_{data, data_hist, snapshot, metadata, vr}`.

### OPERACIONES / NEGOCIO
- `operaciones.operaciones` — fuente de la vista MOVIMIENTOS (`/api/operaciones/ops/*`)
  + Contrapartes (`/operaciones/flujo`). Se agrega **EN VIVO** con `GROUP BY` + índices
  (no hay rollup precomputado). Origen: `jobs.operaciones_informes` (API Aunesa) que
  normaliza+enriquece inline (`moneda`/`mercado`/`operacion`/`nivel_3`/`segmento`/
  `es_cierre`/`commodity`/`mep`). `jobs.fci_bilateral` escribe el FCI bilateral (campo
  `etapa`) por boleto sin pisar el resto.
  - **Arancel vs bruto NO comparten filtro de cierre**: para `bruto` se excluye
    `es_cierre=true`; para `arancel` se INCLUYEN los cierres (el arancel de caución
    vive SOLO en el cierre). `etapa IS DISTINCT FROM 'solicitud'` siempre.
  - `id_cuenta` es la columna de cruce (la fuente histórica la traía como `cuenta`).
  - El motor de PnL NO usa esta tabla (el cost-basis sale de `negocio_movimientos`);
    acá viven volumen/arancel comercial.
- `operaciones.negocio_movimientos` — cost-basis del PnL + vista `/operaciones/negocio`.
  Lo escribe `jobs.negocio_movimientos` (idempotente por boleto, campo `etapa`).

### MOTOR DE ÓRDENES (transaccional, en vivo)
8 tablas en `operaciones`: `ordenes_live` (PK `cl_ord_id`, columnas account/ticker/
estado + `data` jsonb), `ordenes_audit` (append-only, cada ER/evento = fila),
`motor_heartbeat` (frescura → DIAG), `operativas_mep` (wrapper Dólar MEP),
`brackets_live` (PK natural `entry_cl_ord_id`), `triggers_mep`, `ordenes_idempotency`
(claves anti doble-orden), `accounts_descubiertas`. Lectura: `ordenes_sql.py`. El
merge con el broker (pyRofex `get_all_orders_status`) sigue siendo la verdad
real-time del listado del día (se reusa el builder puro `_broker_report_to_local`);
SQL aporta el doc local. Fechas en `data` jsonb son ISO → el read las rehidrata a
datetime aware UTC.

### VALUACIONES (caches precalculados por cron)
Las dos vistas pesadas de Portfolio leen un cache precalculado (recorrer ~880 cuentas
en una request = 502):
- `/valuaciones/consolidado` → `valuaciones.consolidado` (columnar: 1 fila/cuenta con
  valor/base100/PnL/TEM/TEA). Writer: `jobs/consolidado_cuentas.py` (swap por
  TRUNCATE+INSERT). Service: `valuaciones_sql.valuacion_consolidada`.
- `/api/portfolio/pnl-todas` → `valuaciones.pnl_totales_cache` (passthrough: PK
  `id_cuenta`, `rows`/`totales` jsonb — el detalle de boletos por ticker es demasiado
  anidado para columnar). Writer: `jobs/pnl_totales_precompute.py` (`replace_native`).
  Service: `pnl_sql.pnl_todas_cuentas_sql`. El filtro de tipo de cuenta
  (accionistas/productores) se hace en Python contra los sets SQL (`_cuentas_filter`).

### CLIENTES / COMERCIAL
- `clientes.comitentes` (QUIÉN: operador + `nivel_1`), `clientes.cuentas`,
  `clientes.operadores` (= solo los `operador_email` que aparecen en comitentes — los
  que manejan cartera; NO se mezclan con `manager.manager_users`, que son usuarios de
  la app), `clientes.contrapartes`, `clientes.accionistas`, `clientes.actividad_mensual`,
  `clientes.objetivos_comerciales`.
- `comitentes.estado_comercial` es DERIVADO (`comercial.py`) → se calcula en vivo, no
  se persiste. El Tablero Comercial agrega EN VIVO con índices (no hay rollup-cache).

### MANAGER (plataforma)
`manager.{manager_users, role_matrix, role_audit, grupos, job_runs,
pyrofex_instruments, pyrofex_discovery}`. `job_runs` (historial de
corridas) y `role_audit` (auditoría append-only de cambios de rol/usuario) alimentan
el panel Manager (`/jobs/history`, `/roles/audit`) y la frescura del Diagnóstico, vía
`manager_infra_sql.py`. PKs `run_id` / `audit_id`; timestamps `timestamptz` (los
writers usan `datetime.now(UTC)` aware → el cast no corre la hora).

### HOME
- `home.{market_quotes, news_headlines}` — watchlist HOME y headlines (retención 2 días
  vía `prune_native`). Services `market_sql.py` / `news_sql.py`.
  (`market_calendar` se eliminó el 2026-08-03 junto con el calendario económico: FMP
  dejó de servir el endpoint en el plan contratado y la tabla nunca tuvo datos.)

---

## 5. Reglas de traducción / convenciones de dato (referencia del modelo)
Sutilezas del modelo que importan al escribir/leer (heredadas del diseño original):
- **NULL vs `''`**: las dimensiones usan `NULL` para "sin valor"; los labels de UI
  resuelven a `'(sin)'` donde corresponde.
- **`es_cierre`** materializado (bool) separa volumen de arancel sin regex.
- **`etapa IS DISTINCT FROM 'solicitud'`** filtra solicitudes (la liquidación CL ya
  cuenta); la mayoría de las filas tienen `etapa` NULL.
- **Fechas** (`concertacion`, `fecha`, `fecha_snapshot`, `vencimiento`) son `date`
  tipadas; los datetimes con hora van a `timestamptz` aware (UTC).
- **Operaciones sin `boleto`** se saltean (es la PK natural del upsert).
- **`jsonb data`**: doc completo / sub-estructuras anidadas. Convertir datetimes con
  `pg_mirror.doc_iso` (recursivo) antes de serializar.

---

## 6. Cómo aplicar / extender el esquema (Supabase)
1. Proyecto en supabase.com → Postgres managed.
2. SQL Editor → pegar `sql/schema.sql` → Run (o `psql < sql/schema.sql`). Los
   `CREATE … IF NOT EXISTS` son idempotentes → se puede re-correr.
3. Connection string (Settings → Database) en el `.env` del Droplet como
   `POSTGRES_URI`. NO commitear el valor.

Agregar un instrumento de renta fija: doc en `mercado.curvas` (PK `ticker`) +
fila en `portafolio.assets` con el MISMO `ticker` (sin el segundo no aparece en
AuM/Portfolios — ver `docs/ARQUITECTURA.md` y la sección de fórmulas no inferibles en
el `CLAUDE.md` raíz).
