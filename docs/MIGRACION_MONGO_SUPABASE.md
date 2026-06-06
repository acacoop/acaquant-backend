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

## 5. Plan por dominios (orden)

1. ✅ OPERACIONES (negocio/operaciones)
2. ⏳ NEGOCIO (negocio/movimientos — `NegocioMovimientos`, ya espejado)
3. ⏳ COMERCIAL / operadores (1053 líneas; +tablas `actividad_mensual`; derivados→vistas)
4. ⏳ PORTFOLIO / AuM / PnL (fórmulas AuM; PnLTotalesCache/Consolidado → vistas materializadas)
5. ⏳ MERCADO (curvas, snapshots, timesales — medir shapes; real-time al final)
6. ⏳ Dual-write de los jobs/motores + check de dependencias → apagar Mongo

---

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
