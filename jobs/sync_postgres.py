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
    """'' → None; resto str | None."""
    if v is None:
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


def sync_dims_clientes(mdb, conn, dry) -> tuple[int, int, int]:
    """operadores + cuentas + comitentes salen TODOS de Clientes.Comitentes (1 sola lectura).

    operadores = SOLO los operador_email que aparecen en Comitentes (= los que realmente
    manejan cartera). NO se mezcla con Manager.Users (esos son usuarios de la app, otra
    entidad). estado_comercial es DERIVADO (comercial.py) → NULL en la capa SQL.
    Borra huérfanos en orden FK-safe: comitentes (hijo) → cuentas / operadores (padres)."""
    proj = {
        "id_cuenta": 1, "denominacion": 1, "operador_email": 1, "operador_nombre": 1,
        "tipo_doc": 1, "nro_doc": 1, "nivel_1": 1, "nivel_2": 1, "nivel_3": 1,
        # Campos de la vista COMERCIAL:
        "estado": 1, "fecha_alta_legajo": 1, "telefono": 1, "email": 1, "nivel_4": 1,
        "nivel_5": 1, "primer_contacto_comercial": 1, "riesgo_la_ft": 1, "division": 1,
        "adc": 1, "dma": 1, "cupo": 1,
    }
    operadores: dict[str, str | None] = {}
    cuentas, comitentes = [], []
    for d in mdb["Clientes"]["Comitentes"].find({}, proj):
        idc = _s(d.get("id_cuenta"))
        if not idc:
            continue
        em = _s(d.get("operador_email"))
        if em:
            operadores[em] = _s(d.get("operador_nombre")) or operadores.get(em)
        cuentas.append((idc, _s(d.get("denominacion"))))
        cupo = d.get("cupo") or {}
        comitentes.append((
            idc, em, _s(d.get("tipo_doc")), _s(d.get("nro_doc")),
            _s(d.get("nivel_1")), _s(d.get("nivel_2")), _s(d.get("nivel_3")), None,
            _s(d.get("estado")), _d(d.get("fecha_alta_legajo")), _s(d.get("telefono")),
            _s(d.get("email")), _s(d.get("nivel_4")), _s(d.get("nivel_5")),
            _s(d.get("primer_contacto_comercial")), _s(d.get("riesgo_la_ft")),
            _s(d.get("division")), _s(d.get("adc")), _s(d.get("dma")),
            cupo.get("transaccional_ars"), cupo.get("usado_ars"),
        ))
    cuentas, comitentes = _dedup(cuentas, [0]), _dedup(comitentes, [0])

    # Padres antes que el hijo en el UPSERT (FK), hijo antes que padres en el DELETE.
    n_op = _upsert(conn, "operadores", ["email", "nombre"], ["email"],
                   list(operadores.items()), dry)
    n_cu = _upsert(conn, "cuentas", ["id_cuenta", "denominacion"], ["id_cuenta"], cuentas, dry)
    n_co = _upsert(
        conn, "comitentes",
        ["id_cuenta", "operador_email", "tipo_doc", "nro_doc",
         "nivel_1", "nivel_2", "nivel_3", "estado_comercial",
         "estado", "fecha_alta_legajo", "telefono", "email", "nivel_4", "nivel_5",
         "primer_contacto_comercial", "riesgo_la_ft", "division", "adc", "dma",
         "cupo_transaccional_ars", "cupo_usado_ars"],
        ["id_cuenta"], comitentes, dry,
    )
    ids = {r[0] for r in comitentes}
    _delete_not_in(conn, "comitentes", "id_cuenta", ids, dry)
    _delete_not_in(conn, "cuentas", "id_cuenta", ids, dry)
    _delete_not_in(conn, "operadores", "email", operadores.keys(), dry)
    return n_op, n_cu, n_co


def sync_contrapartes(mdb, conn, dry) -> int:
    """contrapartes: en Mongo el id de cuenta se llama 'cuenta'."""
    rows = []
    for d in mdb["CashFlow"]["Contrapartes"].find(
        {}, {"cuenta": 1, "contraparte": 1, "segmento": 1}
    ):
        idc = _s(d.get("cuenta"))
        if not idc:
            continue
        rows.append((idc, _s(d.get("contraparte")), _s(d.get("segmento"))))
    rows = _dedup(rows, [0])
    n = _upsert(conn, "contrapartes", ["id_cuenta", "contraparte", "segmento"],
                ["id_cuenta"], rows, dry)
    _delete_not_in(conn, "contrapartes", "id_cuenta", {r[0] for r in rows}, dry)
    return n


# ── HECHOS (batcheados + throttle; incremental por campo de ingesta) ──────────
def sync_operaciones(mdb, conn, dry, desde: datetime | None) -> tuple[int, int]:
    """CashFlow.Operaciones. OJO: el id de cuenta es 'cuenta', NO 'id_cuenta'.
    Se saltean docs sin boleto (PK natural) y se reporta el conteo."""
    q = {"ingestado_en": {"$gte": desde}} if desde else {}
    proj = {
        "boleto": 1, "concertacion": 1, "cuenta": 1, "denominacion": 1, "moneda": 1,
        "mercado": 1, "operacion": 1, "segmento": 1, "nivel_3": 1, "commodity": 1,
        "es_cierre": 1, "etapa": 1, "bruto": 1, "arancel": 1,
        # Campos de la vista OPERACIONES (migración a SQL):
        "cantidad": 1, "instrumento": 1, "tipo_operacion": 1, "condiciones": 1,
        "ingestado_en": 1,
    }
    cols = ["boleto", "concertacion", "id_cuenta", "denominacion", "moneda", "mercado",
            "operacion", "segmento", "nivel_3", "commodity", "es_cierre", "etapa",
            "bruto", "arancel",
            "cantidad", "instrumento", "tipo_operacion", "condiciones", "ingestado_en"]
    total, sin_boleto = 0, 0
    cur = mdb["CashFlow"]["Operaciones"].find(q, proj, batch_size=BATCH)
    for batch in _iter_batches(cur):
        rows = []
        for d in batch:
            bol = _s(d.get("boleto"))
            if not bol:
                sin_boleto += 1
                continue
            rows.append((
                bol, _d(d.get("concertacion")), _s(d.get("cuenta")),
                _s(d.get("denominacion")), _s(d.get("moneda")), _s(d.get("mercado")),
                _s(d.get("operacion")), _s(d.get("segmento")), _s(d.get("nivel_3")),
                _s(d.get("commodity")), d.get("es_cierre"), _s(d.get("etapa")),
                d.get("bruto"), d.get("arancel"),
                d.get("cantidad"), _s(d.get("instrumento")), _s(d.get("tipo_operacion")),
                _s(d.get("condiciones")), d.get("ingestado_en"),
            ))
        total += _upsert(conn, "operaciones", cols, ["boleto"], _dedup(rows, [0]), dry)
        time.sleep(THROTTLE)
    return total, sin_boleto


def sync_aum(mdb, conn, dry, desde: datetime | None) -> int:
    """Valuaciones.AuM. Grano (fecha_snapshot, id_cuenta, unidad). Incremental por timestamp."""
    q = {"timestamp": {"$gte": desde}} if desde else {}
    proj = {
        "fecha_snapshot": 1, "id_cuenta": 1, "unidad": 1, "cuenta": 1,
        "cantidad": 1, "precio": 1, "valuacion": 1, "tipoTitulo": 1,
    }
    cols = ["fecha_snapshot", "id_cuenta", "unidad", "cuenta",
            "cantidad", "precio", "valuacion", "tipo_titulo"]
    total = 0
    cur = mdb["Valuaciones"]["AuM"].find(q, proj, batch_size=BATCH)
    for batch in _iter_batches(cur):
        rows = []
        for d in batch:
            f, idc, u = _d(d.get("fecha_snapshot")), _s(d.get("id_cuenta")), _s(d.get("unidad"))
            if not (f and idc and u):
                continue
            rows.append((f, idc, u, _s(d.get("cuenta")),
                         d.get("cantidad"), d.get("precio"), d.get("valuacion"),
                         _s(d.get("tipoTitulo"))))
        total += _upsert(conn, "aum", cols, ["fecha_snapshot", "id_cuenta", "unidad"],
                         _dedup(rows, [0, 1, 2]), dry)
        time.sleep(THROTTLE)
    return total


def sync_negocio(mdb, conn, dry, desde: datetime | None) -> int:
    """CashFlow.NegocioMovimientos. Grano (fecha, comprobante). Incremental por ingestado_en."""
    q = {"ingestado_en": {"$gte": desde}} if desde else {}
    proj = {
        "fecha": 1, "comprobante": 1, "id_cuenta": 1, "categoria": 1, "op": 1,
        "ticker": 1, "cantidad": 1, "precio": 1, "importe": 1, "moneda": 1, "mep": 1,
        # Campos de la vista NEGOCIO:
        "cuenta": 1, "unidad": 1, "plazo": 1, "lugar": 1, "estado": 1, "informacion": 1,
        "ingestado_en": 1,
    }
    cols = ["fecha", "comprobante", "id_cuenta", "categoria", "op", "ticker",
            "cantidad", "precio", "importe", "moneda", "mep",
            "cuenta", "unidad", "plazo", "lugar", "estado", "informacion", "ingestado_en"]
    total = 0
    cur = mdb["CashFlow"]["NegocioMovimientos"].find(q, proj, batch_size=BATCH)
    for batch in _iter_batches(cur):
        rows = []
        for d in batch:
            f, comp = _d(d.get("fecha")), _s(d.get("comprobante"))
            if not (f and comp):
                continue
            rows.append((f, comp, _s(d.get("id_cuenta")), _s(d.get("categoria")),
                         _s(d.get("op")), _s(d.get("ticker")), d.get("cantidad"),
                         d.get("precio"), d.get("importe"), _s(d.get("moneda")), d.get("mep"),
                         _s(d.get("cuenta")), _s(d.get("unidad")), _s(d.get("plazo")),
                         _s(d.get("lugar")), _s(d.get("estado")), _s(d.get("informacion")),
                         d.get("ingestado_en")))
        total += _upsert(conn, "negocio_movimientos", cols, ["fecha", "comprobante"],
                         _dedup(rows, [0, 1]), dry)
        time.sleep(THROTTLE)
    return total


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


def sync_manager_users(mdb, conn, dry) -> int:
    """Manager.Users → tabla manager_users (solo email, lowercased). Flag de huérfanas en COMERCIAL."""
    rows = []
    for d in mdb["Manager"]["Users"].find({}, {"email": 1}):
        em = _s(d.get("email"))
        if em:
            rows.append((em.lower(),))
    rows = _dedup(rows, [0])
    n = _upsert(conn, "manager_users", ["email"], ["email"], rows, dry)
    _delete_not_in(conn, "manager_users", "email", {r[0] for r in rows}, dry)
    return n


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


def sync_assets(mdb, conn, dry) -> int:
    """Valuaciones.Assets (UPPERCASE) → tabla assets (lowercase). Join por `unidad` con aum.
    Master de instrumentos para carteras/FCI/renta fija/PnL. Chica (~1630), completa."""
    cols = ["unidad", "cartera", "clase_activo", "emisor", "ticker", "instrumento",
            "calificacion", "cafci"]
    rows = []
    for d in mdb["Valuaciones"]["Assets"].find({}, {
        "_id": 0, "unidad": 1, "CARTERA": 1, "CLASE_ACTIVO": 1, "EMISOR": 1, "TICKER": 1,
        "INSTRUMENTO": 1, "CALIFICACION": 1, "CAFCI": 1,
    }):
        u = _s(d.get("unidad"))
        if not u:
            continue
        rows.append((u, _s(d.get("CARTERA")), _s(d.get("CLASE_ACTIVO")), _s(d.get("EMISOR")),
                     _s(d.get("TICKER")), _s(d.get("INSTRUMENTO")), _s(d.get("CALIFICACION")),
                     _s(d.get("CAFCI"))))
    rows = _dedup(rows, [0])
    n = _upsert(conn, "assets", cols, ["unidad"], rows, dry)
    _delete_not_in(conn, "assets", "unidad", {r[0] for r in rows}, dry)
    return n


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


# ── reconciliación (no confiar a ciegas) ─────────────────────────────────────
def reconciliar(mdb, conn):
    pares = [
        ("operadores", None, None),
        ("cuentas", None, None),
        ("comitentes", "Clientes", "Comitentes"),
        ("contrapartes", "CashFlow", "Contrapartes"),
        ("operaciones", "CashFlow", "Operaciones"),
        ("aum", "Valuaciones", "AuM"),
        ("negocio_movimientos", "CashFlow", "NegocioMovimientos"),
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

    mdb = get_mongo_client_read()
    with connect() as conn:
        n_op, n_cu, n_co = sync_dims_clientes(mdb, conn, dry)
        n_cp = sync_contrapartes(mdb, conn, dry)
        n_ac = sync_accionistas(mdb, conn, dry)
        n_mu = sync_manager_users(mdb, conn, dry)
        n_am = sync_actividad_mensual(mdb, conn, dry)
        n_as = sync_assets(mdb, conn, dry)
        n_dl = sync_dolar(mdb, conn, dry, desde)
        print(f"  dimensiones: operadores={n_op}  cuentas={n_cu}  comitentes={n_co}  "
              f"contrapartes={n_cp}  accionistas={n_ac}  manager_users={n_mu}  "
              f"actividad_mensual={n_am}  assets={n_as}  dolar={n_dl}")

        n_ops, sin_bol = sync_operaciones(mdb, conn, dry, desde)
        n_aum = sync_aum(mdb, conn, dry, desde)
        n_nm = sync_negocio(mdb, conn, dry, desde)
        print(f"  hechos: operaciones={n_ops:,} (sin boleto, salteadas={sin_bol:,})  "
              f"aum={n_aum:,}  negocio={n_nm:,}")

        if not dry:
            reconciliar(mdb, conn)
    print("\nOK." if not dry else "\nDRY-RUN OK (nada escrito).")
    return {"operadores": n_op, "cuentas": n_cu, "comitentes": n_co, "contrapartes": n_cp,
            "accionistas": n_ac, "manager_users": n_mu, "actividad_mensual": n_am,
            "assets": n_as, "dolar": n_dl, "operaciones": n_ops, "aum": n_aum,
            "negocio": n_nm, "sin_boleto": sin_bol}


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
