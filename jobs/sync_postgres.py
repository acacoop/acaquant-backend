"""jobs/sync_postgres.py — sync Mongo → Postgres (Supabase). Fase B.

Vuelca el núcleo relacional de negocio (Mongo) a la capa analítica de Postgres
(sql/schema.sql). Es un ESPEJO de solo-lectura: si Postgres se cae, la mesa (Mongo)
sigue intacta. Ver docs/SQL.md y docs/ARQUITECTURA.md §5.

Seguridad (REGLA #4 — no escanear prod a ciegas):
  * Lee del SECONDARY (get_mongo_client_read, SECONDARY_PREFERRED) → no compite con
    los motores que escriben al primary.
  * Batcheado (BATCH docs) + throttle (THROTTLE s) entre upserts.
  * Idempotente: UPSERT por PK. Re-correrlo no duplica.
  * Dedup intra-lote por PK (no asume unicidad de claves compuestas en Mongo).

Modos:
    python -m jobs.sync_postgres              # incremental: dims completas (chicas) +
                                              #   hechos de los últimos DEFAULT_DIAS días
    python -m jobs.sync_postgres --full       # backfill completo (correr 1 vez, fuera de rueda)
    python -m jobs.sync_postgres --days 30    # hechos de los últimos 30 días
    python -m jobs.sync_postgres --dry-run    # cuenta lo que leería, NO escribe en PG

Las DIMENSIONES (operadores, cuentas, comitentes, contrapartes) son chicas → siempre
completas. Los HECHOS (operaciones, aum, negocio_movimientos) filtran por su campo de
ingesta para el incremental (retro-boletos: re-procesa la ventana entera).
"""
from __future__ import annotations

import argparse
import time
from datetime import UTC, date, datetime, timedelta

from core.mongo import get_mongo_client_read
from core.postgres import connect

BATCH = 5000          # docs por upsert
THROTTLE = 0.15       # segundos de pausa entre upserts (no starvar la DB)
DEFAULT_DIAS = 7      # ventana del incremental para los hechos (cubre retro-boletos)

# Fases cuyo fallo SÍ dispara alerta (datos que consumen las vistas). Las demás
# (news/quotes/calendar = mirror de Market, aún sin consumidores core) se loguean
# pero no alertan → evitan spam mientras se termina de cablear ese feature.
_FASES_CRITICAS = frozenset({
    "dims_clientes", "contrapartes", "accionistas", "manager_users", "role_matrix",
    "grupos", "actividad_mensual", "assets", "dolar", "operaciones", "aum", "negocio",
})


# ── helpers de tipado ────────────────────────────────────────────────────────
def _d(v):
    """str 'YYYY-MM-DD' | datetime → date. None/ inválido → None."""
    if not v:
        return None
    if isinstance(v, datetime):
        return v.date()
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _s(v):
    """'' / NaN → None; resto str | None. (NaN aparece en campos sucios como tipoTitulo)."""
    if v is None:
        return None
    if isinstance(v, float) and v != v:  # NaN
        return None
    v = str(v)
    return v if v != "" else None


def _dedup(rows, key_idx):
    """Dedup por PK dentro del lote, queda la última (UPSERT no acepta misma PK 2x)."""
    seen = {}
    for r in rows:
        seen[tuple(r[i] for i in key_idx)] = r
    return list(seen.values())


def _iter_batches(cursor, size=BATCH):
    batch = []
    for doc in cursor:
        batch.append(doc)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


# ── upsert genérico ──────────────────────────────────────────────────────────
def _upsert(conn, table, cols, conflict_cols, rows, dry):
    if not rows:
        return 0
    if dry:
        return len(rows)
    ph = "(" + ",".join(["%s"] * len(cols)) + ")"
    sets = ",".join(f"{c}=EXCLUDED.{c}" for c in cols if c not in conflict_cols)
    # Si todas las columnas son la PK (ej. accionistas) no hay nada que actualizar → DO NOTHING.
    on_conflict = (f"DO UPDATE SET {sets}" if sets else "DO NOTHING")
    sql = (
        f'INSERT INTO {table} ({",".join(cols)}) VALUES {ph} '
        f'ON CONFLICT ({",".join(conflict_cols)}) {on_conflict}'
    )
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()
    return len(rows)


# ── DIMENSIONES (siempre completas, son chicas) ──────────────────────────────
def _delete_not_in(conn, table, pk_col, keep, dry) -> int:
    """Borra de `table` las filas cuya PK no está en `keep` (limpieza de huérfanos: el
    UPSERT nunca borra). PG es un espejo descartable → bajo riesgo. El caller respeta el
    orden de FK (hijos antes que padres)."""
    if dry:
        return 0
    keep = set(keep)
    with conn.cursor() as cur:
        cur.execute(f"SELECT {pk_col} FROM {table}")
        muertas = [r[0] for r in cur.fetchall() if r[0] not in keep]
        if muertas:
            cur.execute(f"DELETE FROM {table} WHERE {pk_col} = ANY(%s)", (muertas,))
    conn.commit()
    return len(muertas)


# sync_dims_clientes ELIMINADO: clientes.{comitentes,cuentas,operadores} son la fuente
# de verdad ahora — los escribe jobs/sync_comitentes (Aunesa→SQL directo) + el panel
# Manager → Clientes. Sincronizar Mongo→SQL acá pisaría la segmentación de la mesa.


# sync_contrapartes ELIMINADO: clientes.contrapartes es la fuente de verdad ahora
# (editor del panel + reconciliador escriben SQL directo). Mongo CashFlow.Contrapartes
# deprecado. Sincronizar Mongo→SQL acá pisaría las ediciones del panel.


# ── HECHOS (batcheados + throttle; incremental por campo de ingesta) ──────────
# sync_operaciones ELIMINADO (migración Operaciones→SQL): la tabla
# operaciones.operaciones la escriben directo jobs/operaciones_informes.py y
# jobs/fci_bilateral.py. Ya no se copia desde Mongo CashFlow.Operaciones.


# sync_aum ELIMINADO (migración AuM→SQL): pnl_sql/comercial_sql leen portafolio.tenencia
# (writer diario portafolio_backfill), ya no la tabla 'aum'. Mongo Valuaciones.AuM + la
# tabla SQL 'aum' quedan deprecados → se dropean (scripts.drop_mongo_aum_deprecado).


# sync_negocio ELIMINADO (migración NegocioMovimientos→SQL): la tabla
# operaciones.negocio_movimientos la escribe directo jobs/negocio_movimientos.py.
# Ya no se copia desde Mongo CashFlow.NegocioMovimientos (colección eliminada).


def sync_accionistas(mdb, conn, dry) -> int:
    """CashFlow.Accionistas → tabla accionistas (solo el string `cuenta`). Para el filtro
    de cuenta (accionistas/sin_accionistas/cooperativas) de NEGOCIO y portfolio."""
    rows = []
    for d in mdb["CashFlow"]["Accionistas"].find({}, {"cuenta": 1}):
        c = _s(d.get("cuenta"))
        if c:
            rows.append((c,))
    rows = _dedup(rows, [0])
    n = _upsert(conn, "accionistas", ["cuenta"], ["cuenta"], rows, dry)
    _delete_not_in(conn, "accionistas", "cuenta", {r[0] for r in rows}, dry)
    return n


# sync_movimientos ELIMINADO (cutover Movimientos→SQL 2026-06-24): jobs/cashflow.py escribe
# operaciones.movimientos SQL-native (write_native) y api/services/cashflow_sql.py lo lee de
# SQL. CashFlow.Movimientos (Mongo) dropeada → ya no hay de dónde sincronizar.


# sync_acreencias ELIMINADO (cutover Acreencias→SQL 2026-06-23): jobs/acreencias.py
# escribe operaciones.acreencias SQL-native (replace_native, swap atómico) y
# api/services/cashflow_sql.py lo lee de SQL. CashFlow.Acreencias (Mongo) dropeada
# → ya no hay de dónde sincronizar.


def sync_tipos_operacion(mdb, conn, dry) -> int:
    """CashFlow.TiposOperacion → operaciones.tipos_operacion (catálogo chico, mapeo
    tipo_operacion→mercado/operacion). Espejo para tenerlo en SQL; el enrich de los
    writers SIGUE leyendo Mongo (corre en el proceso del job). PK = tipo_operacion."""
    rows = []
    for d in mdb["CashFlow"]["TiposOperacion"].find({}, {"_id": 0}):
        t = _s(d.get("tipo_operacion"))
        if t:
            rows.append((t, _jsonb(d)))
    rows = _dedup(rows, [0])
    n = _upsert(conn, "tipos_operacion", ["tipo_operacion", "data"], ["tipo_operacion"], rows, dry)
    _delete_not_in(conn, "tipos_operacion", "tipo_operacion", {r[0] for r in rows}, dry)
    return n


def sync_volumen_mercado_agro(mdb, conn, dry) -> int:
    """CashFlow.VolumenMercadoAgro → mercado.volumen_mercado_agro (denominador del share
    AGRO, carga MANUAL). Passthrough: periodo/commodity materializados + data jsonb.
    PK = (periodo, commodity)."""
    cols = ["periodo", "commodity", "toneladas", "data"]
    rows = []
    for d in mdb["CashFlow"]["VolumenMercadoAgro"].find({}, {"_id": 0}):
        per, comm = _s(d.get("periodo")), _s(d.get("commodity"))
        if per and comm:
            rows.append((per, comm, d.get("toneladas"), _jsonb(d)))
    rows = _dedup(rows, [0, 1])
    # Solo upsert (sin _delete_not_in): la PK es compuesta (periodo, commodity) y el
    # helper borra por una sola columna → no aplica. Tabla chica/manual, no se limpian
    # huérfanos (despreciable).
    return _upsert(conn, "volumen_mercado_agro", cols, ["periodo", "commodity"], rows, dry)


# ── MOTOR DE ÓRDENES (read-side SQL — BASELINE; el write-side dual-escribe live) ──
# Las 8 tablas operaciones.{ordenes_live, ordenes_audit, motor_heartbeat, operativas_mep,
# brackets_live, triggers_mep, ordenes_idempotency, accounts_descubiertas} las dual-escribe
# el motor/services en vivo (flag ORDENES_SQL_WRITE). Acá está el BASELINE: alinea SQL a
# Mongo por si el dual-write no corrió aún (fresh install) o se rompió. Passthrough:
# columnas clave (para filtrar) + `data` jsonb (doc completo via _jsonb). Best-effort:
# cada colección puede no existir todavía → el caller (_t) aísla el fallo.

def sync_ordenes_live(mdb, conn, dry) -> int:
    """Operaciones.OrdenesLive → operaciones.ordenes_live. Doc del ciclo de vida de
    cada orden (upsert por cl_ord_id, last-write-wins en Mongo). `estado` materializa
    `status`; `updated_at` es aware (datetime.now(UTC)). data jsonb = doc completo."""
    cols = ["cl_ord_id", "account", "ticker", "estado", "updated_at", "data"]
    rows = []
    for d in mdb["Operaciones"]["OrdenesLive"].find({}, {"_id": 0}):
        cid = _s(d.get("cl_ord_id"))
        if not cid:
            continue
        rows.append((cid, _s(d.get("account")), _s(d.get("ticker")), _s(d.get("status")),
                     d.get("updated_at"), _jsonb(d)))
    rows = _dedup(rows, [0])
    # SIN _delete_not_in: OrdenesLive crece y NO se limpia en Mongo (queda histórico
    # del día); borrar acá perdería órdenes válidas. El read filtra por created_at.
    return _upsert(conn, "ordenes_live", cols, ["cl_ord_id"], rows, dry)


def sync_ordenes_audit(mdb, conn, dry, desde: datetime | None) -> int:
    """Operaciones.OrdenesAudit → operaciones.ordenes_audit (APPEND-ONLY, PK surrogate
    IDENTITY). Cada evento (SEND_REQUEST / EXECUTION_REPORT / etc.) es una fila nueva.

    Sin PK natural → no se puede UPSERT. El BASELINE usa la ventana incremental por `ts`
    y, para no duplicar en re-corridas, BORRA primero las filas del rango [desde, now] y
    re-inserta (idempotente sobre la ventana; las filas fuera de la ventana no se tocan).
    En --full (desde=None) re-truncar sería destructivo si el dual-write ya cargó eventos
    nuevos → en ese caso se omite el borrado y se confía en que la tabla esté vacía
    (fresh install) o se corra el baseline una sola vez. Batcheado + throttle."""
    cols = ["ts", "kind", "cl_ord_id", "account", "actor_email", "data"]
    q = {"ts": {"$gte": desde}} if desde else {}
    if not dry and desde is not None:
        # Idempotencia de la ventana: limpia [desde, ∞) antes de re-insertar.
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ordenes_audit WHERE ts >= %s", (desde,))
        conn.commit()
    total = 0
    cur = mdb["Operaciones"]["OrdenesAudit"].find(q, {"_id": 0}, batch_size=BATCH)
    for batch in _iter_batches(cur):
        rows = []
        for d in batch:
            doc = {k: v for k, v in d.items()
                   if k not in ("ts", "kind", "cl_ord_id", "account", "actor_email")}
            rows.append((d.get("ts"), _s(d.get("kind")), _s(d.get("cl_ord_id")),
                         _s(d.get("account")), _s(d.get("actor_email")), _jsonb(doc)))
        if rows and not dry:
            ph = "(" + ",".join(["%s"] * len(cols)) + ")"
            with conn.cursor() as c:
                c.executemany(
                    f'INSERT INTO ordenes_audit ({",".join(cols)}) VALUES {ph}', rows)
            conn.commit()
        total += len(rows)
        time.sleep(THROTTLE)
    return total


def sync_motor_heartbeat(mdb, conn, dry) -> int:
    """Operaciones.MotorOrdenesHeartbeat (_id='singleton') → operaciones.motor_heartbeat
    (PK id='current'). Lo escribe el loop del motor cada 30s (updated_at aware + account).
    Frescura para /manager → DIAG. data jsonb = doc completo."""
    cols = ["id", "updated_at", "data"]
    rows = []
    for d in mdb["Operaciones"]["MotorOrdenesHeartbeat"].find({}):
        doc = {k: v for k, v in d.items() if k != "_id"}
        rows.append(("current", d.get("updated_at"), _jsonb(doc)))
    rows = _dedup(rows, [0])
    return _upsert(conn, "motor_heartbeat", cols, ["id"], rows, dry)


def sync_operativas_mep(mdb, conn, dry) -> int:
    """Operaciones.OperativasMep → operaciones.operativas_mep (wrapper de la operativa
    Dólar MEP). PK = operativa_id (str). `ts` = created_at (aware). data jsonb = doc
    completo (incluye buy/sell.cl_ord_id, nominales, mep_inicial, etc.)."""
    cols = ["id", "account", "rueda", "ts", "data"]
    rows = []
    for d in mdb["Operaciones"]["OperativasMep"].find({}, {"_id": 0}):
        oid = _s(d.get("operativa_id"))
        if not oid:
            continue
        rows.append((oid, _s(d.get("account")), _s(d.get("rueda")),
                     d.get("created_at"), _jsonb(d)))
    rows = _dedup(rows, [0])
    return _upsert(conn, "operativas_mep", cols, ["id"], rows, dry)


def sync_brackets_live(mdb, conn, dry) -> int:
    """Operaciones.BracketsLive → operaciones.brackets_live (entrada LIMIT + salida auto).
    OJO (no inferible): la PK natural en Mongo es `entry_cl_ord_id` (unique) → mapea a la
    columna `cl_ord_id` de la tabla. `estado` materializa `status`. data jsonb = doc
    completo (price_entry/price_exit, exit_cl_ord_id, etc.)."""
    cols = ["cl_ord_id", "account", "estado", "data"]
    rows = []
    for d in mdb["Operaciones"]["BracketsLive"].find({}, {"_id": 0}):
        cid = _s(d.get("entry_cl_ord_id"))
        if not cid:
            continue
        rows.append((cid, _s(d.get("account")), _s(d.get("status")), _jsonb(d)))
    rows = _dedup(rows, [0])
    return _upsert(conn, "brackets_live", cols, ["cl_ord_id"], rows, dry)


def sync_triggers_mep(mdb, conn, dry) -> int:
    """Operaciones.TriggersMep → operaciones.triggers_mep. Colección del scanner de
    triggers MEP (puede no existir / estar vacía — el scanner está inactivo hoy). PK = id
    (str(_id) Mongo). Passthrough defensivo; data jsonb = doc completo."""
    cols = ["id", "account", "data"]
    rows = []
    for d in mdb["Operaciones"]["TriggersMep"].find({}):
        doc = {k: v for k, v in d.items() if k != "_id"}
        rows.append((str(d.get("_id")), _s(d.get("account")), _jsonb(doc)))
    rows = _dedup(rows, [0])
    return _upsert(conn, "triggers_mep", cols, ["id"], rows, dry)


def sync_ordenes_idempotency(mdb, conn, dry) -> int:
    """Operaciones.OrdenesIdempotency → operaciones.ordenes_idempotency (claves anti
    doble-orden, TTL 1 día en Mongo). PK = clave (← campo `key`). `ts` = created_at.
    data jsonb = doc completo (status/result/finished_at)."""
    cols = ["clave", "ts", "data"]
    rows = []
    for d in mdb["Operaciones"]["OrdenesIdempotency"].find({}, {"_id": 0}):
        k = _s(d.get("key"))
        if not k:
            continue
        rows.append((k, d.get("created_at"), _jsonb(d)))
    rows = _dedup(rows, [0])
    return _upsert(conn, "ordenes_idempotency", cols, ["clave"], rows, dry)


def sync_accounts_descubiertas(mdb, conn, dry) -> int:
    """Operaciones.AccountsDescubiertas → operaciones.accounts_descubiertas (cuentas
    autorizadas del master, las pobla jobs.descubrir_cuentas). PK = account_id. `data`
    jsonb trae `last_snapshot` (ars/usd/n_pos) — el read service lo lee de ahí."""
    cols = ["account_id", "activa", "last_discovered_at", "data"]
    rows = []
    for d in mdb["Operaciones"]["AccountsDescubiertas"].find({}, {"_id": 0}):
        acc = _s(d.get("account_id"))
        if not acc:
            continue
        rows.append((acc, bool(d.get("activa")), d.get("last_discovered_at"), _jsonb(d)))
    rows = _dedup(rows, [0])
    n = _upsert(conn, "accounts_descubiertas", cols, ["account_id"], rows, dry)
    _delete_not_in(conn, "accounts_descubiertas", "account_id", {r[0] for r in rows}, dry)
    return n


def sync_manager_users(mdb, conn, dry) -> int:
    """Manager.Users → manager_users (email lowercased + role/enabled/etc para AUTH SQL)."""
    cols = ["email", "role", "enabled", "auto_registered", "notes",
            "last_seen_at", "created_at", "updated_at"]
    rows = []
    for d in mdb["Manager"]["Users"].find({}, {
        "email": 1, "role": 1, "enabled": 1, "auto_registered": 1, "notes": 1,
        "last_seen_at": 1, "created_at": 1, "updated_at": 1,
    }):
        em = _s(d.get("email"))
        if not em:
            continue
        rows.append((em.lower(), _s(d.get("role")), d.get("enabled"),
                     d.get("auto_registered"), _s(d.get("notes")), d.get("last_seen_at"),
                     d.get("created_at"), d.get("updated_at")))
    rows = _dedup(rows, [0])
    n = _upsert(conn, "manager_users", cols, ["email"], rows, dry)
    _delete_not_in(conn, "manager_users", "email", {r[0] for r in rows}, dry)
    return n


def sync_role_matrix(mdb, conn, dry) -> int:
    """Manager.RoleMatrix (1 doc/rol, array modules) → role_matrix (filas role,module)."""
    rows = []
    for d in mdb["Manager"]["RoleMatrix"].find({}, {"role": 1, "modules": 1}):
        role = _s(d.get("role"))
        if not role:
            continue
        for m in (d.get("modules") or []):
            ms = _s(m)
            if ms:
                rows.append((role, ms))
    rows = _dedup(rows, [0, 1])
    n = _upsert(conn, "role_matrix", ["role", "module"], ["role", "module"], rows, dry)
    if not dry:  # borra (role,module) que ya no están (PK compuesta → delete propio)
        keep = [f"{r}|{m}" for r, m in rows] or [""]
        with conn.cursor() as cur:
            cur.execute("DELETE FROM role_matrix WHERE role || '|' || module <> ALL(%s)", (keep,))
        conn.commit()
    return n


def sync_grupos(mdb, conn, dry) -> int:
    """Manager.Grupos → grupos (emails lowercased, id_cuentas como text[]). Scope de cuentas."""
    cols = ["id", "nombre", "emails", "id_cuentas", "creado_por", "creado_at", "updated_at"]
    rows = []
    for d in mdb["Manager"]["Grupos"].find({}):
        emails = [str(e).strip().lower() for e in (d.get("emails") or []) if e]
        idc = [str(c) for c in (d.get("id_cuentas") or []) if c is not None]
        rows.append((str(d.get("_id")), _s(d.get("nombre")), emails, idc,
                     _s(d.get("creado_por")), d.get("creado_at"), d.get("updated_at")))
    rows = _dedup(rows, [0])
    n = _upsert(conn, "grupos", cols, ["id"], rows, dry)
    _delete_not_in(conn, "grupos", "id", {r[0] for r in rows}, dry)
    return n


def sync_job_runs(mdb, conn, dry, desde: datetime | None) -> int:
    """Manager.JobRuns → manager.job_runs (historial de corridas). PK = str(_id) Mongo.
    Columnas materializadas (tipo, started_at, finished_at, status) + doc en jsonb.
    Incremental por started_at (datetime). Batcheado + throttle (puede crecer). El
    dual-write de core/job_runs.py (MANAGER_SQL_WRITE) lo mantiene fresco entre syncs."""
    q = {"started_at": {"$gte": desde}} if desde else {}
    total = 0
    cur = mdb["Manager"]["JobRuns"].find(q, batch_size=BATCH)
    for batch in _iter_batches(cur):
        rows = []
        for d in batch:
            rid = str(d.get("_id"))
            doc = {k: v for k, v in d.items() if k != "_id"}
            rows.append((rid, _s(d.get("tipo")), d.get("started_at"),
                         d.get("finished_at"), _s(d.get("status")), _jsonb(doc)))
        total += _upsert(conn, "job_runs",
                         ["run_id", "tipo", "started_at", "finished_at", "status", "data"],
                         ["run_id"], _dedup(rows, [0]), dry)
        time.sleep(THROTTLE)
    return total


def sync_role_audit(mdb, conn, dry) -> int:
    """Manager.RoleAudit → manager.role_audit (append-only, chico). PK = str(_id) Mongo.
    Columnas materializadas (ts, actor, action, target) + doc completo en jsonb. Solo
    upsert (append-only, no se borran eventos históricos)."""
    rows = []
    for d in mdb["Manager"]["RoleAudit"].find({}):
        rid = str(d.get("_id"))
        doc = {k: v for k, v in d.items() if k != "_id"}
        rows.append((rid, d.get("ts"), _s(d.get("actor")), _s(d.get("action")),
                     _s(d.get("target")), _jsonb(doc)))
    rows = _dedup(rows, [0])
    return _upsert(conn, "role_audit",
                   ["audit_id", "ts", "actor", "action", "target", "data"],
                   ["audit_id"], rows, dry)


def sync_actividad_mensual(mdb, conn, dry) -> int:
    """Clientes.ActividadMensual → tabla actividad_mensual (snapshot point-in-time, se espeja
    tal cual; operador/segmento están CONGELADOS al correr el job — NO recomputar en vivo)."""
    cols = ["year_month", "id_cuenta", "operador_email", "operador_nombre", "nivel_1",
            "n_ops", "volumen_ars"]
    rows = []
    for d in mdb["Clientes"]["ActividadMensual"].find({}, {
        "year_month": 1, "id_cuenta": 1, "operador_email": 1, "operador_nombre": 1,
        "nivel_1": 1, "n_ops": 1, "volumen_ars": 1,
    }):
        ym, idc = _s(d.get("year_month")), _s(d.get("id_cuenta"))
        if not (ym and idc):
            continue
        rows.append((ym, idc, _s(d.get("operador_email")), _s(d.get("operador_nombre")),
                     _s(d.get("nivel_1")), d.get("n_ops"), d.get("volumen_ars")))
    rows = _dedup(rows, [0, 1])
    # Snapshot point-in-time: solo upsert (no se borran meses históricos).
    return _upsert(conn, "actividad_mensual", cols, ["year_month", "id_cuenta"], rows, dry)


# sync_assets ELIMINADO (migración assets→SQL): portafolio.assets es la fuente de
# verdad ahora (panel Manager escribe ahí, writer diario auto-da-de-alta). Sincronizar
# Mongo→SQL acá pisaría las ediciones del panel. Mongo Valuaciones.Assets deprecado.


def sync_dolar(mdb, conn, dry, desde: datetime | None) -> int:
    """Valuaciones.Dolar (timestamp, mep) → tabla dolar. Para get_mep_for_date (MEP histórico).
    Incremental por timestamp."""
    q = {"timestamp": {"$gte": desde}} if desde else {}
    rows = []
    for d in mdb["Valuaciones"]["Dolar"].find(q, {"_id": 0, "timestamp": 1, "mep": 1}):
        ts = d.get("timestamp")
        if ts is None:
            continue
        rows.append((ts, d.get("mep")))
    return _upsert(conn, "dolar", ["timestamp", "mep"], ["timestamp"], _dedup(rows, [0]), dry)


def sync_portfolio_snapshot(mdb, conn, dry) -> int:
    """Trading.PortfolioSnapshot (precio live por ticker) → portfolio_snapshot. PnL no-realizado."""
    cols = ["ticker", "last_price", "closing_price"]
    rows = []
    for d in mdb["Trading"]["PortfolioSnapshot"].find(
        {}, {"_id": 0, "ticker": 1, "last_price": 1, "closing_price": 1}
    ):
        t = _s(d.get("ticker"))
        if t:
            rows.append((t, d.get("last_price"), d.get("closing_price")))
    rows = _dedup(rows, [0])
    n = _upsert(conn, "portfolio_snapshot", cols, ["ticker"], rows, dry)
    _delete_not_in(conn, "portfolio_snapshot", "ticker", {r[0] for r in rows}, dry)
    return n


def sync_pnl_totales(mdb, conn, dry) -> int:
    """Valuaciones.PnLTotalesCache → valuaciones.pnl_totales_cache (cache del PnL de TODAS
    las cuentas, vista TOTALES /pnl-todas). BASELINE: el cron jobs.pnl_totales_precompute
    lo dual-escribe en vivo cada 30min (swap atómico) — esto solo lo re-alinea si el cron
    no corrió aún (fresh install). PK = id_cuenta; `rows`/`totales` jsonb (rows trae el
    detalle de boletos). `computed_at` es aware (datetime.now(UTC)) → timestamptz."""
    cols = ["id_cuenta", "cuenta", "rows", "totales", "computed_at"]
    rows = []
    for d in mdb["Valuaciones"]["PnLTotalesCache"].find({}, {"_id": 0}):
        idc = _s(d.get("id_cuenta"))
        if not idc:
            continue
        rows.append((idc, _s(d.get("cuenta")), _jsonb(d.get("rows") or []),
                     _jsonb(d.get("totales") or {}), d.get("computed_at")))
    rows = _dedup(rows, [0])
    n = _upsert(conn, "pnl_totales_cache", cols, ["id_cuenta"], rows, dry)
    _delete_not_in(conn, "pnl_totales_cache", "id_cuenta", {r[0] for r in rows}, dry)
    return n


def _doc_iso(d):
    """Doc con todos los datetime → ISO, RECURSIVO (datetimes anidados — ej. flujos
    de BondsMaster — rompen json.dumps si solo se convierte el nivel top)."""
    from core.pg_mirror import doc_iso
    return doc_iso(d)


def _jsonb(v):
    """Jsonb BLINDADO para columnas jsonb: pasa por _doc_iso (datetime→ISO) y
    cualquier tipo no-JSON residual (Decimal128, ObjectId anidado, date) cae a
    str — un campo raro en UN doc no puede volver a tumbar la fase entera
    (incidente bonds_master 2026-06-12: flujos con datetime iban a Jsonb crudos)."""
    import json
    from functools import partial

    from psycopg.types.json import Jsonb
    return Jsonb(_doc_iso(v), dumps=partial(json.dumps, default=str))


def sync_quotes(mdb, conn, dry) -> int:
    """Market.Quotes → market_quotes (key symbol). data jsonb = doc completo (anchors incluidos)."""
    cols = ["symbol", "grupo", "data"]
    rows = []
    for d in mdb["Market"]["Quotes"].find({}, {"_id": 0}):
        sym = _s(d.get("symbol"))
        if not sym:
            continue
        rows.append((sym, _s(d.get("grupo")), _jsonb(d)))
    rows = _dedup(rows, [0])
    n = _upsert(conn, "market_quotes", cols, ["symbol"], rows, dry)
    _delete_not_in(conn, "market_quotes", "symbol", {r[0] for r in rows}, dry)
    return n


def sync_calendar(mdb, conn, dry) -> int:
    """Market.EconomicCalendar → market_calendar. PK NATURAL (evt_ts, country, event)
    = el unique index del escritor (jobs/economic_calendar upsertea por time/country/
    event) → habilita dual-write limpio (la v1 con hkey=md5 del doc generaba una fila
    nueva en cada update). Docs cuyo `time` no es datetime se saltean — tampoco
    matchean el filtro de rango del endpoint en Mongo (misma semántica)."""
    cols = ["evt_ts", "country", "event", "impact", "data"]
    rows = []
    for d in mdb["Market"]["EconomicCalendar"].find({}, {"_id": 0}):
        evt = d.get("time")
        if not isinstance(evt, datetime):
            continue
        # Mongo devuelve naive-UTC → se clava UTC para que el cast no corra la hora.
        evt_ts = evt.replace(tzinfo=UTC) if evt.tzinfo is None else evt
        rows.append((evt_ts, d.get("country") or "", d.get("event") or "",
                     int(d.get("impact") or 0), _jsonb(d)))
    rows = _dedup(rows, [0, 1, 2])
    n = _upsert(conn, "market_calendar", cols, ["evt_ts", "country", "event"], rows, dry)
    if not dry:  # delete de huérfanos con PK compuesta (unnest de arrays paralelos,
        # comparación por TIPO — nada de matchear timestamps como strings)
        ts_k = [r[0] for r in rows]
        co_k = [r[1] for r in rows]
        ev_k = [r[2] for r in rows]
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM market_calendar mc WHERE NOT EXISTS ("
                "  SELECT 1 FROM unnest(%s::timestamptz[], %s::text[], %s::text[]) AS k(ts, c, e)"
                "  WHERE k.ts = mc.evt_ts AND k.c = mc.country AND k.e = mc.event)",
                (ts_k, co_k, ev_k),
            )
        conn.commit()
    return n


# sync_snapshots_cierre ELIMINADO (cutover SnapshotsCierre→SQL 2026-06-24): jobs/snapshot_cierre.py
# escribe mercado.snapshots_cierre SQL-native (último precio por ticker, fallback del PnL) y
# api/services/pnl_sql.py / pnl.py lo leen de SQL. Trading.SnapshotsCierre (Mongo) dropeada → ya no
# hay de dónde sincronizar.


def sync_futuros_dlr_snapshot(mdb, conn, dry) -> int:
    """Trading.FuturosDLRSnapshot → futuros_dlr_snapshot (BASELINE; el motor lo refresca
    live bajo SNAPSHOT_SQL). Passthrough jsonb."""
    rows = []
    for d in mdb["Trading"]["FuturosDLRSnapshot"].find({}, {"_id": 0}):
        t = _s(d.get("ticker"))
        if t:
            rows.append((t, _s(d.get("vencimiento")), _jsonb(d)))
    rows = _dedup(rows, [0])
    n = _upsert(conn, "futuros_dlr_snapshot", ["ticker", "vencimiento", "data"], ["ticker"], rows, dry)
    _delete_not_in(conn, "futuros_dlr_snapshot", "ticker", {r[0] for r in rows}, dry)
    return n


def sync_caucion_snapshot(mdb, conn, dry) -> int:
    """Trading.CaucionSnapshot → caucion_snapshot (BASELINE; el motor lo refresca live)."""
    rows = []
    for d in mdb["Trading"]["CaucionSnapshot"].find({}, {"_id": 0}):
        m = _s(d.get("moneda"))
        if m:
            rows.append((m, _jsonb(d)))
    rows = _dedup(rows, [0])
    n = _upsert(conn, "caucion_snapshot", ["moneda", "data"], ["moneda"], rows, dry)
    _delete_not_in(conn, "caucion_snapshot", "moneda", {r[0] for r in rows}, dry)
    return n


def sync_agro_snapshot(mdb, conn, dry) -> int:
    """Trading.AgroSnapshot → agro_snapshot (BASELINE; el motor lo refresca live bajo
    SNAPSHOT_SQL). Passthrough jsonb; `commodity` columna para filtrar. _delete_not_in
    limpia tickers stale (vencidos/variantes) que el motor borra en Mongo pero el mirror
    live —solo upsert— no quita."""
    rows = []
    for d in mdb["Trading"]["AgroSnapshot"].find({}, {"_id": 0}):
        t = _s(d.get("ticker"))
        if t:
            rows.append((t, _s(d.get("commodity")), _jsonb(d)))
    rows = _dedup(rows, [0])
    n = _upsert(conn, "agro_snapshot", ["ticker", "commodity", "data"], ["ticker"], rows, dry)
    _delete_not_in(conn, "agro_snapshot", "ticker", {r[0] for r in rows}, dry)
    return n


def sync_agro_opciones_snapshot(mdb, conn, dry) -> int:
    """Trading.AgroOpcionesSnapshot → agro_opciones_snapshot (BASELINE; el motor lo
    refresca live bajo SNAPSHOT_SQL). Passthrough jsonb; `commodity` columna."""
    rows = []
    for d in mdb["Trading"]["AgroOpcionesSnapshot"].find({}, {"_id": 0}):
        t = _s(d.get("ticker"))
        if t:
            rows.append((t, _s(d.get("commodity")), _jsonb(d)))
    rows = _dedup(rows, [0])
    n = _upsert(conn, "agro_opciones_snapshot",
                ["ticker", "commodity", "data"], ["ticker"], rows, dry)
    _delete_not_in(conn, "agro_opciones_snapshot", "ticker", {r[0] for r in rows}, dry)
    return n


def sync_agro_pizarra(mdb, conn, dry) -> int:
    """Derivados.AgroPizarra → agro_pizarra (carga MANUAL; el service la dual-escribe en
    write_native). Baseline para arrancar el espejo. PK = commodity (Mongo _id); `commodity`
    queda también dentro de `data` (igual que el dual-write del service)."""
    rows = []
    for d in mdb["Derivados"]["AgroPizarra"].find({}):
        c = _s(d.get("_id"))
        if c:
            data = {"commodity": c, **{k: v for k, v in d.items() if k != "_id"}}
            rows.append((c, _jsonb(data)))
    rows = _dedup(rows, [0])
    n = _upsert(conn, "agro_pizarra", ["commodity", "data"], ["commodity"], rows, dry)
    _delete_not_in(conn, "agro_pizarra", "commodity", {r[0] for r in rows}, dry)
    return n


def sync_camara_cereales(mdb, conn, dry) -> int:
    """Derivados.CamaraCereales → camara_cereales (carga MANUAL; el service la dual-escribe
    en write_native). Baseline. PK = cereal (Mongo _id); `cereal` queda dentro de `data`."""
    rows = []
    for d in mdb["Derivados"]["CamaraCereales"].find({}):
        c = _s(d.get("_id"))
        if c:
            data = {"cereal": c, **{k: v for k, v in d.items() if k != "_id"}}
            rows.append((c, _jsonb(data)))
    rows = _dedup(rows, [0])
    n = _upsert(conn, "camara_cereales", ["cereal", "data"], ["cereal"], rows, dry)
    _delete_not_in(conn, "camara_cereales", "cereal", {r[0] for r in rows}, dry)
    return n


def sync_forwards_zscore(mdb, conn, dry) -> int:
    """Trading.ForwardsZscore → forwards_zscore (BASELINE; el job lo refresca diario)."""
    rows = []
    for d in mdb["Trading"]["ForwardsZscore"].find({}, {"_id": 0}):
        c = _s(d.get("curva"))
        if c:
            rows.append((c, _jsonb(d)))
    rows = _dedup(rows, [0])
    n = _upsert(conn, "forwards_zscore", ["curva", "data"], ["curva"], rows, dry)
    _delete_not_in(conn, "forwards_zscore", "curva", {r[0] for r in rows}, dry)
    return n


def sync_options_metadata(mdb, conn, dry) -> int:
    """Opciones.Metadata → options_metadata (config + vr_ggal). 2 docs, completa. La tasa
    además se dual-writea inmediata al editarla (update_opciones_tasa); acá es el baseline."""
    rows = []
    for d in mdb["Opciones"]["Metadata"].find({"type": {"$in": ["config", "vr_ggal"]}},
                                              {"_id": 0}):
        t = _s(d.get("type"))
        if t:
            rows.append((t, _jsonb(d)))
    rows = _dedup(rows, [0])
    n = _upsert(conn, "options_metadata", ["type", "data"], ["type"], rows, dry)
    _delete_not_in(conn, "options_metadata", "type", {r[0] for r in rows}, dry)
    return n


def sync_options_vr(mdb, conn, dry) -> int:
    """Opciones.VR-GGal → options_vr (serie diaria GGAL local/ADR, ~40 ruedas). Completa.
    Saltea el doc SUMMARY_METRICS (no tiene `Date`). PK = fecha (date)."""
    rows = []
    for d in mdb["Opciones"]["VR-GGal"].find({"type": {"$ne": "SUMMARY_METRICS"}}, {"_id": 0}):
        f = _d(d.get("Date"))
        if f:
            rows.append((f, _jsonb(d)))
    rows = _dedup(rows, [0])
    n = _upsert(conn, "options_vr", ["fecha", "data"], ["fecha"], rows, dry)
    _delete_not_in(conn, "options_vr", "fecha", {r[0] for r in rows}, dry)
    return n


# sync_options_data_hist ELIMINADO (cutover DataHistorica→SQL 2026-06-24): jobs/options_rollup.py
# escribe mercado.options_data_hist SQL-native (write_native) y api/services/opciones_sql.py lo lee
# de SQL. Opciones.DataHistorica (Mongo) dropeada → ya no hay de dónde sincronizar.


# ── CAPA MERCADO (espejo Trading.* — ver docs/SQL.md §Mercado) ───────────────
# Colecciones-serie {fecha:'YYYY-MM-DD', valor} → tabla larga series_macro.
# `serie` = nombre de la colección Mongo (misma clave usa el dual-write).
_SERIES_MACRO = ("CER", "DOLAR", "BADLAR", "TAMAR",
                 "RiesgoPais", "InflacionMensual", "InflacionInteranual")


def sync_series_macro(mdb, conn, dry, desde: datetime | None) -> int:
    """Trading.{CER,DOLAR,...} → series_macro. Incremental por `fecha` (string ISO
    → comparación lexicográfica; incluye el CER forward, fecha > hoy)."""
    f_desde = desde.date().isoformat() if desde else None
    total = 0
    for serie in _SERIES_MACRO:
        q = {"fecha": {"$gte": f_desde}} if f_desde else {}
        rows = []
        # 7 finds sobre 7 COLECCIONES distintas (no N+1 sobre la misma).
        for d in mdb["Trading"][serie].find(q, {"_id": 0, "fecha": 1, "valor": 1}):  # perf-ok: PERF002
            f = _d(d.get("fecha"))
            if f is None:
                continue
            rows.append((serie, f, d.get("valor")))
        total += _upsert(conn, "series_macro", ["serie", "fecha", "valor"],
                         ["serie", "fecha"], _dedup(rows, [0, 1]), dry)
        time.sleep(THROTTLE)
    return total


def sync_rem(mdb, conn, dry) -> int:
    """Trading.REM → rem. Chica (consenso IPC por informe/período), completa.
    Solo upsert — los informes históricos no se borran (snapshot acumulativo)."""
    cols = ["informe", "periodo", "periodo_tipo", "fecha_informe", "mediana", "promedio",
            "desvio", "minimo", "maximo", "p10", "p25", "p75", "p90", "participantes",
            "updated_at"]
    rows = []
    for d in mdb["Trading"]["REM"].find({}, {"_id": 0}):
        inf, per, pt = _s(d.get("informe")), _s(d.get("periodo")), _s(d.get("periodo_tipo"))
        if not (inf and per and pt):
            continue
        rows.append((inf, per, pt, _d(d.get("fecha_informe")), d.get("mediana"),
                     d.get("promedio"), d.get("desvio"), d.get("minimo"), d.get("maximo"),
                     d.get("p10"), d.get("p25"), d.get("p75"), d.get("p90"),
                     d.get("participantes"), d.get("updated_at")))
    return _upsert(conn, "rem", cols, ["informe", "periodo", "periodo_tipo"],
                   _dedup(rows, [0, 1, 2]), dry)


def sync_curvas(mdb, conn, dry) -> tuple[int, int]:
    """Trading.Curvas → curvas. Chica (~cientos), completa + delete de huérfanos
    (cleanup_curvas borra vencidos en Mongo; el UPSERT no borra). Lo consultable
    va columnar; flujos + doc completo en jsonb. Saltea docs sin ticker_corto
    (PK del upsert de ons.sync_ons_to_curvas) y los cuenta."""
    cols = ["ticker_corto", "ticker", "curva", "tipo", "moneda_flujo", "valor_nominal",
            "fecha_emision", "fecha_vencimiento", "cupon_anual", "cer_emision",
            "flujo_vencimiento", "emisor", "sector", "flujos", "data"]
    rows, sin_corto = [], 0
    for d in mdb["Trading"]["Curvas"].find({}, {"_id": 0}):
        tc = _s(d.get("ticker_corto"))
        if not tc:
            sin_corto += 1
            continue
        rows.append((
            tc, _s(d.get("ticker")), _s(d.get("curva")), _s(d.get("tipo")),
            _s(d.get("moneda_flujo")), d.get("valor_nominal"), _d(d.get("fecha_emision")),
            _d(d.get("fecha_vencimiento")), d.get("cupon_anual"), d.get("cer_emision"),
            d.get("flujo_vencimiento"), _s(d.get("emisor")), _s(d.get("sector")),
            _jsonb(d.get("flujos") or []), _jsonb(d),
        ))
    rows = _dedup(rows, [0])
    n = _upsert(conn, "curvas", cols, ["ticker_corto"], rows, dry)
    _delete_not_in(conn, "curvas", "ticker_corto", {r[0] for r in rows}, dry)
    return n, sin_corto


def sync_market_snapshot(mdb, conn, dry) -> int:
    """Trading.MarketSnapshot → market_snapshot (columnar, 1 fila/ticker). Baseline
    horario; la frescura intradía la da el dual-write de los motores (SNAPSHOT_SQL,
    core/pg_mirror). Completa + delete de huérfanos."""
    cols = ["ticker", "book", "last_price", "open_price", "high_price", "low_price",
            "closing_price", "vwap", "total_nominals", "updated_at",
            "tea", "tem", "duration", "mod_duration", "convexity", "paridad"]
    rows = []
    for d in mdb["Trading"]["MarketSnapshot"].find({}, {"_id": 0}):
        t = _s(d.get("ticker"))
        if not t:
            continue
        m = d.get("metrics") or {}
        rows.append((t, _jsonb(d.get("book") or {}), m.get("last_price"), m.get("open_price"),
                     m.get("high_price"), m.get("low_price"), m.get("closing_price"),
                     m.get("vwap"), m.get("total_nominals"), d.get("updated_at"),
                     m.get("TEA"), m.get("TEM"), m.get("duration"), m.get("mod_duration"),
                     m.get("convexity"), m.get("paridad")))
    rows = _dedup(rows, [0])
    n = _upsert(conn, "market_snapshot", cols, ["ticker"], rows, dry)
    _delete_not_in(conn, "market_snapshot", "ticker", {r[0] for r in rows}, dry)
    return n


# sync_snapshots_cierre_hist ELIMINADO (cutover SnapshotsCierre→SQL 2026-06-24):
# jobs/snapshot_cierre.py escribe mercado.snapshots_cierre_hist SQL-native (write_native,
# histórico por fecha+curva+ticker) y lo leen api/services/{renta_fija_sql,renta_fija,analitica,
# carry_trade}.py + jobs/fair_value.py. Trading.SnapshotsCierre (Mongo) dropeada → sin fuente.


# Históricos diarios de la vista mercado → tabla genérica mercado_hist.
# (colección Mongo, campo fecha, campos subclave) — claves VERIFICADAS contra el
# upsert de cada escritor (engines/breakevens, forwards, futuros_dlr, caucion;
# jobs/fair_value). Los snapshots LIVE no se espejan acá (ver sql/schema.sql).
_MERCADO_HIST = [
    ("BreakevensHistorico", "fecha",     []),                   # 1 doc/día (pares)
    ("ForwardsHistorico",   "fecha",     ["curva"]),            # tasas + matrix
    ("FuturosDLR",          "fecha",     ["ticker"]),           # cierre + TNA implícita
    ("Caucion",             "fecha",     ["moneda"]),           # cierre TNA caución
    ("FitParams",           "ts_cierre", ["curva"]),            # Nelson-Siegel betas
    ("FairValueResiduos",   "ts_cierre", ["curva", "ticker"]),  # residuos fair value
]


def sync_mercado_hist(mdb, conn, dry, desde: datetime | None) -> int:
    """Históricos diarios de mercado → mercado_hist (grano colección/fecha/subclave,
    doc completo en jsonb). Incremental por el campo fecha de cada colección
    (string ISO → comparación lexicográfica)."""
    f_desde = desde.date().isoformat() if desde else None
    total = 0
    for coleccion, campo_fecha, subclaves in _MERCADO_HIST:
        q = {campo_fecha: {"$gte": f_desde}} if f_desde else {}
        rows = []
        for d in mdb["Trading"][coleccion].find(q, {"_id": 0}):  # perf-ok: PERF002
            f = _d(d.get(campo_fecha))
            if f is None:
                continue
            k = "|".join(str(d.get(s) or "") for s in subclaves)
            rows.append((coleccion, f, k, _jsonb(d)))
        total += _upsert(conn, "mercado_hist", ["coleccion", "fecha", "k", "data"],
                         ["coleccion", "fecha", "k"], _dedup(rows, [0, 1, 2]), dry)
        time.sleep(THROTTLE)
    return total


# sync_canje_cierre ELIMINADO (cutover CanjeCierre→SQL 2026-06-24): jobs/cierre_canje.py
# escribe mercado.canje_cierre SQL-native (write_native) y api/services/canje.py lo lee de SQL.
# Trading.CanjeCierre (Mongo) dropeada → ya no hay de dónde sincronizar.


# ── RENTA VARIABLE — Scanner CEDEARs (espejo Trading.* — ver docs/SQL.md) ─────
def sync_cedears(mdb, conn, dry) -> int:
    """Trading.Cedears → cedears. Master chico (~73), completo + delete de huérfanos.
    Columnas materializadas (ticker/ticker_corto/underlying/activo) + doc en jsonb
    (el scanner usa nombre/ratio/sector/industria/region/pais)."""
    rows = []
    for d in mdb["Trading"]["Cedears"].find({}, {"_id": 0}):
        t = _s(d.get("ticker"))
        if not t:
            continue
        rows.append((t, _s(d.get("ticker_corto")), _s(d.get("underlying")),
                     bool(d.get("activo")), _jsonb(d)))
    rows = _dedup(rows, [0])
    n = _upsert(conn, "cedears", ["ticker", "ticker_corto", "underlying", "activo", "data"],
                ["ticker"], rows, dry)
    _delete_not_in(conn, "cedears", "ticker", {r[0] for r in rows}, dry)
    return n


def sync_cedears_snapshot(mdb, conn, dry) -> int:
    """Trading.CedearsSnapshot → cedears_snapshot (BASELINE; el motor lo refresca live
    bajo SNAPSHOT_SQL). Passthrough jsonb, 1 doc por ticker BYMA."""
    rows = []
    for d in mdb["Trading"]["CedearsSnapshot"].find({}, {"_id": 0}):
        t = _s(d.get("ticker"))
        if t:
            rows.append((t, _jsonb(d), d.get("updated_at")))
    rows = _dedup(rows, [0])
    n = _upsert(conn, "cedears_snapshot", ["ticker", "data", "updated_at"], ["ticker"], rows, dry)
    _delete_not_in(conn, "cedears_snapshot", "ticker", {r[0] for r in rows}, dry)
    return n


# sync_adr_snapshot ELIMINADO en el cutover AdrSnapshot→SQL (2026-06-24):
# jobs/adr_live escribe mercado.adr_snapshot SQL-native (write_native). Lectores en SQL
# (scanner_sql). Trading.AdrSnapshot dropeada.


# sync_precios_acciones ELIMINADO en el cutover PreciosAcciones→SQL (2026-06-24):
# jobs/precios_acciones_daily escribe mercado.precios_acciones SQL-native (write_native),
# lectores (scanner_sql, quant/pivot_points) leen SQL. Trading.PreciosAcciones dropeada.


def sync_day_trading_stats(mdb, conn, dry, desde: datetime | None) -> int:
    """Trading.DayTradingStats → day_trading_stats (passthrough jsonb). Incremental
    por `fecha` (string ISO → comparación lexicográfica). Upsert por (fecha, ticker)."""
    f_desde = desde.date().isoformat() if desde else None
    q = {"fecha": {"$gte": f_desde}} if f_desde else {}
    rows = []
    for d in mdb["Trading"]["DayTradingStats"].find(q, {"_id": 0}):
        f, tk = _d(d.get("fecha")), _s(d.get("ticker"))
        if not (f and tk):
            continue
        rows.append((f, tk, _jsonb(d)))
    return _upsert(conn, "day_trading_stats", ["fecha", "ticker", "data"],
                   ["fecha", "ticker"], _dedup(rows, [0, 1]), dry)


# ── reconciliación (no confiar a ciegas) ─────────────────────────────────────
def reconciliar(mdb, conn):
    pares = [
        ("operadores", None, None),
        ("cuentas", None, None),
    ]
    print("\n── Reconciliación (filas PG vs docs Mongo) ──")
    with conn.cursor() as cur:
        for tabla, db, col in pares:
            cur.execute(f"SELECT count(*) FROM {tabla}")
            pg = cur.fetchone()[0]
            if db:
                mg = mdb[db][col].estimated_document_count()
                flag = "  ⚠ dif" if abs(pg - mg) > 0 else ""
                print(f"  {tabla:22} PG={pg:>9,}  Mongo≈{mg:>9,}{flag}")
            else:
                print(f"  {tabla:22} PG={pg:>9,}")


def run(full: bool = False, days: int = DEFAULT_DIAS, dry: bool = False) -> dict:
    """Corre el sync y devuelve los conteos. Lo invoca main() (CLI) y el cron (vía JobRunLogger)."""
    desde = None if full else (datetime.now(UTC) - timedelta(days=days))
    modo = "FULL" if full else f"incremental (últimos {days}d)"
    print(f"sync_postgres — modo {modo}{'  [DRY-RUN]' if dry else ''}")

    tempos: dict[str, float] = {}
    fallos: list[tuple[str, str]] = []

    mdb = get_mongo_client_read()
    with connect() as conn:
        def _t(label, fn, default=0):
            """Cronometra y AÍSLA cada fase: si falla, rollback (limpia la transacción
            abortada del pool), registra el fallo y sigue con las demás. Así una tabla
            rota (ej. market_quotes inexistente) ya NO tumba todo el sync — operaciones
            SIEMPRE intenta correr. Al final se re-lanza un resumen para que el alert
            avise QUÉ falló, pero lo que sí pudo sincronizar ya quedó commiteado."""
            t0 = time.perf_counter()
            try:
                r = fn()
            except Exception as e:
                conn.rollback()
                msg = str(e).splitlines()[0][:200]
                fallos.append((label, f"{type(e).__name__}: {msg}"))
                print(f"  ⚠ {label} FALLÓ → se saltea: {type(e).__name__}: {msg}")
                r = default
            tempos[label] = time.perf_counter() - t0
            return r

        n_ac = _t("accionistas", lambda: sync_accionistas(mdb, conn, dry))
        # movimientos y acreencias ya NO se sincronizan: las escriben jobs/cashflow.py y
        # jobs/acreencias.py SQL-native (cutover 2026-06-23/24, colecciones Mongo dropeadas).
        n_to = _t("tipos_operacion", lambda: sync_tipos_operacion(mdb, conn, dry))
        n_vma = _t("volumen_mercado_agro", lambda: sync_volumen_mercado_agro(mdb, conn, dry))

        # Motor de órdenes — read-side SQL (BASELINE; el write-side dual-escribe live).
        n_ol = _t("ordenes_live", lambda: sync_ordenes_live(mdb, conn, dry))
        n_oa = _t("ordenes_audit", lambda: sync_ordenes_audit(mdb, conn, dry, desde))
        n_hb = _t("motor_heartbeat", lambda: sync_motor_heartbeat(mdb, conn, dry))
        n_op = _t("operativas_mep", lambda: sync_operativas_mep(mdb, conn, dry))
        n_bk = _t("brackets_live", lambda: sync_brackets_live(mdb, conn, dry))
        n_tr = _t("triggers_mep", lambda: sync_triggers_mep(mdb, conn, dry))
        n_id = _t("ordenes_idempotency", lambda: sync_ordenes_idempotency(mdb, conn, dry))
        n_acd = _t("accounts_descubiertas", lambda: sync_accounts_descubiertas(mdb, conn, dry))
        print(f"  motor órdenes (baseline): ordenes_live={n_ol}  ordenes_audit={n_oa:,}  "
              f"motor_heartbeat={n_hb}  operativas_mep={n_op}  brackets_live={n_bk}  "
              f"triggers_mep={n_tr}  ordenes_idempotency={n_id}  accounts_descubiertas={n_acd}")

        n_mu = _t("manager_users", lambda: sync_manager_users(mdb, conn, dry))
        n_rm = _t("role_matrix", lambda: sync_role_matrix(mdb, conn, dry))
        n_gr = _t("grupos", lambda: sync_grupos(mdb, conn, dry))
        n_jr = _t("job_runs", lambda: sync_job_runs(mdb, conn, dry, desde))
        n_ra = _t("role_audit", lambda: sync_role_audit(mdb, conn, dry))
        n_am = _t("actividad_mensual", lambda: sync_actividad_mensual(mdb, conn, dry))
        n_dl = _t("dolar", lambda: sync_dolar(mdb, conn, dry, desde))
        n_ps = _t("portfolio_snapshot", lambda: sync_portfolio_snapshot(mdb, conn, dry))
        n_pt = _t("pnl_totales", lambda: sync_pnl_totales(mdb, conn, dry))
        # snapshots_cierre (último por ticker) ya NO se sincroniza: lo escribe SQL-native
        # jobs/snapshot_cierre.py (cutover SnapshotsCierre→SQL 2026-06-24).
        n_qt = _t("quotes", lambda: sync_quotes(mdb, conn, dry))
        n_cal = _t("calendar", lambda: sync_calendar(mdb, conn, dry))
        print(f"  quotes={n_qt}  calendar={n_cal}  (news → SQL-native, ya sin espejo)")

        # Capa MERCADO (espejo Trading.* — no-crítica hasta que una vista la lea).
        n_sm = _t("series_macro", lambda: sync_series_macro(mdb, conn, dry, desde))
        n_rem = _t("rem", lambda: sync_rem(mdb, conn, dry))
        n_cv, sin_corto = _t("curvas", lambda: sync_curvas(mdb, conn, dry), (0, 0))
        n_ms = _t("market_snapshot", lambda: sync_market_snapshot(mdb, conn, dry))
        # snapshots_cierre_hist (histórico) ya NO se sincroniza: lo escribe SQL-native
        # jobs/snapshot_cierre.py (cutover SnapshotsCierre→SQL 2026-06-24).
        n_mh = _t("mercado_hist", lambda: sync_mercado_hist(mdb, conn, dry, desde))
        n_fd = _t("futuros_dlr_snapshot", lambda: sync_futuros_dlr_snapshot(mdb, conn, dry))
        n_ca = _t("caucion_snapshot", lambda: sync_caucion_snapshot(mdb, conn, dry))
        n_fz = _t("forwards_zscore", lambda: sync_forwards_zscore(mdb, conn, dry))

        # Renta variable — Scanner CEDEARs (baseline; motor/jobs refrescan live).
        n_ced = _t("cedears", lambda: sync_cedears(mdb, conn, dry))
        n_csn = _t("cedears_snapshot", lambda: sync_cedears_snapshot(mdb, conn, dry))
        # adr_snapshot + precios_acciones: SQL-native (adr_live / precios_acciones_daily
        # escriben SQL directo) — syncs eliminados en los cutovers →SQL (2026-06-24).
        n_dts = _t("day_trading_stats", lambda: sync_day_trading_stats(mdb, conn, dry, desde))
        print(f"  scanner: cedears={n_ced}  cedears_snapshot={n_csn}  "
              f"day_trading_stats={n_dts}")
        n_as = _t("agro_snapshot", lambda: sync_agro_snapshot(mdb, conn, dry))
        n_ao = _t("agro_opciones_snapshot", lambda: sync_agro_opciones_snapshot(mdb, conn, dry))
        n_ap = _t("agro_pizarra", lambda: sync_agro_pizarra(mdb, conn, dry))
        n_cc = _t("camara_cereales", lambda: sync_camara_cereales(mdb, conn, dry))
        # Opciones — charts (baseline diario). Data (ticks) NO acá: la dual-writea el motor.
        # data_hist tampoco: options_rollup escribe SQL-native (cutover DataHistorica→SQL 2026-06-24).
        n_om = _t("options_metadata", lambda: sync_options_metadata(mdb, conn, dry))
        n_ov = _t("options_vr", lambda: sync_options_vr(mdb, conn, dry))
        print(f"  mercado: series_macro={n_sm:,}  rem={n_rem}  curvas={n_cv} "
              f"(sin ticker_corto, salteadas={sin_corto})  "
              f"market_snapshot={n_ms}  mercado_hist={n_mh:,}")
        print(f"  derivados live (baseline): futuros_dlr={n_fd}  caucion={n_ca}  forwards_zscore={n_fz}")
        print(f"  agro (baseline): agro_snapshot={n_as}  agro_opciones={n_ao}  "
              f"agro_pizarra={n_ap}  camara_cereales={n_cc}")
        print(f"  opciones (baseline): metadata={n_om}  vr={n_ov}")
        print(f"  dimensiones: accionistas={n_ac}  manager_users={n_mu}  "
              f"role_matrix={n_rm}  grupos={n_gr}  actividad_mensual={n_am}  "
              f"dolar={n_dl}  portfolio_snapshot={n_ps}  pnl_totales={n_pt}")
        print(f"  manager infra: job_runs={n_jr:,}  role_audit={n_ra}")
        print(f"  cashflow (baseline): tipos_operacion={n_to}  volumen_mercado_agro={n_vma}")

        # operaciones, negocio_movimientos y movimientos ya NO se sincronizan: los escriben
        # SQL directo jobs/operaciones_informes.py, jobs/fci_bilateral.py,
        # jobs/negocio_movimientos.py y jobs/cashflow.py (migración Operaciones/NegocioMov/
        # Movimientos → SQL).

        if not dry:
            _t("reconciliar", lambda: reconciliar(mdb, conn))

    # Breakdown de tiempos (desc) — para saber QUÉ optimizar, no adivinar.
    print("\n── Tiempos por fase (seg, desc) ──")
    for label, seg in sorted(tempos.items(), key=lambda kv: kv[1], reverse=True):
        print(f"  {label:<22}{seg:>8.2f}")
    print(f"  {'─' * 30}\n  {'TOTAL':<22}{sum(tempos.values()):>8.2f}")

    if fallos:
        print(f"\n⚠ {len(fallos)} fase(s) FALLARON (se saltearon; el resto SÍ sincronizó):")
        for label, err in fallos:
            print(f"  - {label}: {err}")
    print("\nOK." if not dry else "\nDRY-RUN OK (nada escrito).")
    stats = {"accionistas": n_ac, "manager_users": n_mu, "role_matrix": n_rm, "grupos": n_gr,
             "job_runs": n_jr, "role_audit": n_ra,
             "actividad_mensual": n_am, "dolar": n_dl,
             "tipos_operacion": n_to,
             "volumen_mercado_agro": n_vma,
             "portfolio_snapshot": n_ps, "pnl_totales": n_pt,
             "quotes": n_qt, "calendar": n_cal,
             "series_macro": n_sm, "rem": n_rem, "curvas": n_cv,
             "curvas_sin_ticker_corto": sin_corto,
             "market_snapshot": n_ms,
             "mercado_hist": n_mh,
             "cedears": n_ced, "cedears_snapshot": n_csn,
             "day_trading_stats": n_dts,
             "agro_snapshot": n_as, "agro_opciones_snapshot": n_ao,
             "agro_pizarra": n_ap, "camara_cereales": n_cc,
             "options_metadata": n_om, "options_vr": n_ov,
             "ordenes_live": n_ol, "ordenes_audit": n_oa, "motor_heartbeat": n_hb,
             "operativas_mep": n_op, "brackets_live": n_bk, "triggers_mep": n_tr,
             "ordenes_idempotency": n_id, "accounts_descubiertas": n_acd,
             "fases_fallidas": len(fallos)}
    # Re-lanza SOLO si falló una fase crítica (las vistas la consumen). El mirror de
    # Market que falle no alerta. Lo que sí sincronizó ya quedó commiteado por fase.
    criticas = [lbl for lbl, _ in fallos if lbl in _FASES_CRITICAS]
    if criticas and not dry:
        raise RuntimeError(f"sync_postgres: fallaron fases críticas: {', '.join(criticas)} "
                           f"({len(fallos)} fallos en total — ver log)")
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="backfill completo (sin filtro de fecha)")
    ap.add_argument("--days", type=int, default=DEFAULT_DIAS, help="ventana incremental (hechos)")
    ap.add_argument("--dry-run", action="store_true", help="cuenta, NO escribe en PG")
    args = ap.parse_args()

    if args.dry_run:
        run(full=args.full, days=args.days, dry=True)
        return 0
    # Cron / corrida real: envuelta en JobRunLogger (alerta Telegram si falla).
    from core.job_runs import JobRunLogger
    with JobRunLogger("sync_postgres") as jr:
        stats = run(full=args.full, days=args.days, dry=False)
        for k, v in stats.items():
            jr.set_stat(k, v)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
