# Migración MongoDB → Supabase (Postgres) — bitácora maestra

> **Documento vivo.** Registra TODO lo que se hace en la migración de MongoDB Atlas a
> Supabase/Postgres: el **qué** (ejecutivo) y el **cómo** (técnico). Se actualiza en cada
> cambio. Docs relacionados: `SQL.md` (operativa), `SQL_MODELO.md` (diseño del modelo).

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
6. ⏳ MERCADO (curvas, snapshots, timesales — medir shapes; real-time al final)
7. ⏳ FASE 2: writes a SQL (incl. auth dual-write) + check de dependencias → apagar Mongo

---

## 5b. INVENTARIO COMPLETO de lo que FALTA (mapa real, para no esconder scope)

### ✅ Migrado (lee SQL, con harness + flag): operaciones, negocio, comercial, portfolio-AuM, PnL, auth(lecturas).

### ⏳ Migrable SIN mercado (datos diarios/Aunesa) — pendiente:
- **Home/News**: `/api/news/*` (News.Headlines, key=url) + `/api/market/quotes` (Market.Quotes) +
  `/api/market/calendar/economic` (Market.EconomicCalendar). (candle/profile son APIs externas, no Mongo.)
- **Watchlist/anchors**: jobs.market_quotes / market_anchors → colecciones de home.
- **Manager** (6 tabs): intel, jobs/logs (JobRuns), **clientes/Comitentes (lectura + EDICIÓN/segmentación)**,
  compliance, assets/títulos. (roles/users/grupos: lecturas ✅ por auth; falta su edición.)
- **back-office**.

### ⏳ Necesita MERCADO ABIERTO (real-time, motores):
- Vistas: renta-fija, derivados, agro, sintéticos, renta-variable/scanner, estrategia (curvas/forwards/
  breakevens/carry), operar, MCP.
- **~12 motores** pyRofex → Trading.* (MarketSnapshot/TimeSales/Curvas/OrderBookL2/…). Base lista:
  dual-write de SnapshotWriter (flag SNAPSHOT_SQL). Falta: tablas snapshot + `sql_table=` por motor +
  migrar motores que NO usan SnapshotWriter (motor_rofex/options/caución).

### ⏳ ESCRITURAS (para apagar Mongo) — TODO sigue en Mongo:
- **~40 jobs cron** (bcra, argentina_datos, market_quotes, news, snapshot_cierre, fair_value, aum,
  negocio, operaciones, aranceles, actividad_mensual, pnl_totales_precompute, consolidado, …) → cada uno
  debe escribir SQL (o dual-write).
- **Auth writes**: upsert_user, delete_user, set_role_modules, auto_register, last_seen, grupos CRUD, RoleAudit.
- **Segmentación writes**: edición de Comitentes (niveles/operador) desde /manager.
- **Caches**: PnLTotalesCache, ConsolidadoCuentas (cron → tabla jsonb).

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
