"""Manager sub-router — edición de `clientes.comitentes` (segmentación comercial, SQL).

Tab `/manager → CLIENTES`. Editor del master de clientes del Tablero Comercial
(ver docs/TABLERO_COMERCIAL.md). Los campos de Aunesa (id_cuenta, denominacion,
operador, etc.) son READ-ONLY; se editan SOLO los 13 campos manuales de
segmentación. **Fuente única SQL** (Mongo Clientes.Comitentes deprecado).

Modelo SQL: `clientes.comitentes` (campos del comitente) + `clientes.cuentas`
(denominacion) + `clientes.operadores` (nombre). `cupo` (subdoc en Mongo) → columnas
`cupo_*`. La respuesta reconstruye el subdoc `cupo` para no cambiar el contrato del front.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.services.sin_operador import cuentas_sin_operador
from core.postgres import get_pool

router = APIRouter()
bulk_router = APIRouter()

# Campos manuales editables (segmentación comercial). nivel_1..5 = árbol.
_EDITABLE_FIELDS: tuple[str, ...] = (
    "nivel_1", "nivel_2", "nivel_3", "nivel_4", "nivel_5",
    "primer_contacto_comercial", "riesgo_la_ft", "division",
    "adc", "dma", "observaciones", "sucursal", "referido",
)
# Operador (de Aunesa): solo se corrige por bulk/patch (no en _EDITABLE_FIELDS).
_BULK_OPERADOR_FIELDS: tuple[str, ...] = ("operador_email", "operador_nombre")
# Segmentos en MAYÚSCULAS (evita duplicados por capitalización).
_UPPERCASE_FIELDS: frozenset[str] = frozenset({"nivel_1", "nivel_2", "nivel_3", "nivel_4", "nivel_5"})
# Campos de Aunesa read-only (contexto) — columnas de comitentes.
_READONLY_COLS = ("tipo", "estado", "tipo_titular", "clase", "tipo_cliente",
                  "perfil_inversion", "provincia")
_CUPO_COLS = ("cupo_transaccional_ars", "cupo_usado_ars", "cupo_utilizacion_pct",
              "cupo_cargado_en", "cupo_fuente")


def _normalize_value(field: str, value: str) -> str:
    return value.upper() if field in _UPPERCASE_FIELDS else value


def _q(sql: str, params=None) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


def _row_to_cliente(r: dict) -> dict:
    """Fila SQL → doc cliente con el subdoc `cupo` reconstruido + timestamps ISO."""
    cupo = {
        "transaccional_ars": r.get("cupo_transaccional_ars"),
        "usado_ars": r.get("cupo_usado_ars"),
        "utilizacion_pct": r.get("cupo_utilizacion_pct"),
        "cargado_en": r["cupo_cargado_en"].isoformat() if r.get("cupo_cargado_en") else None,
        "fuente": r.get("cupo_fuente"),
    }
    at = r.get("actualizado_at")
    out = {
        "id_cuenta": r.get("id_cuenta"), "denominacion": r.get("denominacion"),
        "operador_email": r.get("operador_email"), "operador_nombre": r.get("operador_nombre"),
        **{f: r.get(f) for f in _EDITABLE_FIELDS},
        **{f: r.get(f) for f in _READONLY_COLS},
        "cupo": cupo,
        "actualizado_por": r.get("actualizado_por"),
        "actualizado_at": at.isoformat() if isinstance(at, datetime) else at,
    }
    return out


# Columnas del SELECT base (comitentes + denominacion de cuentas + nombre de operadores).
_SEL = (
    "c.id_cuenta, u.denominacion, c.operador_email, o.nombre AS operador_nombre, "
    + ", ".join(f"c.{f}" for f in (*_EDITABLE_FIELDS, *_READONLY_COLS, *_CUPO_COLS))
    + ", c.actualizado_por, c.actualizado_at "
    "FROM comitentes c LEFT JOIN cuentas u ON u.id_cuenta = c.id_cuenta "
    "LEFT JOIN operadores o ON o.email = c.operador_email"
)


@router.get("/clientes")
def list_clientes(
    operador:    str | None = Query(None, description="Filtrar por operador_email exacto"),
    nivel_1:     str | None = Query(None, description="Filtrar por nivel_1 exacto"),
    campo_vacio: str | None = Query(None, description="Solo los que tienen ese campo manual vacío/null"),
    q:           str | None = Query(None, description="Búsqueda en id_cuenta o denominación"),
) -> dict:
    """Lista clientes desde SQL. Sin filtros: todo el master."""
    where: list[str] = []
    p: dict = {}
    if operador == "__vacio__":
        where.append("(c.operador_email IS NULL OR c.operador_email !~ '\\S')")
    elif operador:
        where.append("c.operador_email = %(op)s")
        p["op"] = operador
    if nivel_1:
        where.append("c.nivel_1 = %(n1)s")
        p["n1"] = nivel_1
    if campo_vacio and campo_vacio in _EDITABLE_FIELDS:
        where.append(f"(c.{campo_vacio} IS NULL OR c.{campo_vacio} = '')")
    if q and q.strip():
        where.append("(c.id_cuenta ILIKE %(q)s OR u.denominacion ILIKE %(q)s)")
        p["q"] = f"%{q.strip()}%"
    w = f"WHERE {' AND '.join(where)}" if where else ""
    rows = _q(f"SELECT {_SEL} {w} ORDER BY u.denominacion NULLS LAST LIMIT 5000", p)
    clientes = [_row_to_cliente(r) for r in rows]
    return {"clientes": clientes, "n": len(clientes)}


@router.get("/clientes/values")
def get_clientes_values() -> dict:
    """Valores únicos por campo manual (datalists) + operadores + combos de niveles."""
    values: dict[str, list[str]] = {}
    for f in _EDITABLE_FIELDS:
        rows = _q(f"SELECT DISTINCT {f} AS v FROM comitentes "
                  f"WHERE {f} IS NOT NULL AND {f} <> ''")
        values[f] = sorted(str(r["v"]) for r in rows)
    operadores = [
        {"email": r["operador_email"], "nombre": r["nombre"] or r["operador_email"]}
        for r in _q("SELECT c.operador_email, o.nombre FROM comitentes c "
                    "LEFT JOIN operadores o ON o.email = c.operador_email "
                    "WHERE c.operador_email IS NOT NULL AND c.operador_email <> '' "
                    "GROUP BY c.operador_email, o.nombre ORDER BY o.nombre NULLS LAST")
    ]
    nf = ("nivel_1", "nivel_2", "nivel_3", "nivel_4", "nivel_5")
    niveles = [
        {n: (r[n] or "") for n in nf}
        for r in _q(f"SELECT DISTINCT {', '.join(nf)} FROM comitentes")
    ]
    niveles = [c for c in niveles if any(c.values())]
    return {"values": values, "operadores": operadores, "niveles": niveles}


@router.get("/clientes/sin-operador")
def clientes_sin_operador() -> dict:
    """Cuentas con volumen que caen en '(sin operador)' del ranking comercial."""
    return cuentas_sin_operador()


def _ensure_operador(cur, email: str | None, nombre: str | None) -> None:
    """Upsert del operador (FK destino) antes de asignarlo a un comitente."""
    if email:
        cur.execute("INSERT INTO operadores (email, nombre) VALUES (%s, %s) "
                    "ON CONFLICT (email) DO UPDATE SET nombre = COALESCE(EXCLUDED.nombre, operadores.nombre)",
                    (email, nombre))


def _one_cliente(idc: str) -> dict | None:
    rows = _q(f"SELECT {_SEL} WHERE c.id_cuenta = %(idc)s", {"idc": idc})
    return _row_to_cliente(rows[0]) if rows else None


class _ClientePatch(BaseModel):
    id_cuenta:                 str = Field(..., min_length=1, max_length=64)
    operador_email:            str | None = Field(None, max_length=256)
    operador_nombre:           str | None = Field(None, max_length=256)
    nivel_1:                   str | None = Field(None, max_length=128)
    nivel_2:                   str | None = Field(None, max_length=128)
    nivel_3:                   str | None = Field(None, max_length=128)
    nivel_4:                   str | None = Field(None, max_length=128)
    nivel_5:                   str | None = Field(None, max_length=128)
    primer_contacto_comercial: str | None = Field(None, max_length=256)
    riesgo_la_ft:              str | None = Field(None, max_length=128)
    division:                  str | None = Field(None, max_length=128)
    adc:                       str | None = Field(None, max_length=128)
    dma:                       str | None = Field(None, max_length=128)
    observaciones:             str | None = Field(None, max_length=2000)
    sucursal:                  str | None = Field(None, max_length=128)
    referido:                  str | None = Field(None, max_length=256)


@router.patch("/clientes")
def patch_cliente(req: _ClientePatch = Body(...), actor: str = Depends(get_user_email)):
    """Update parcial de campos manuales + operador. id_cuenta en el body. No crea cuentas."""
    payload = req.model_dump(exclude_none=True)
    id_cuenta = payload.pop("id_cuenta")
    op_email = payload.pop("operador_email", None)
    op_nombre = payload.pop("operador_nombre", None)
    sets = {k: _normalize_value(k, v) for k, v in payload.items() if k in _EDITABLE_FIELDS}
    if not sets and op_email is None and op_nombre is None:
        raise HTTPException(400, "body sin campos editables — pasá al menos uno de "
                                 + ", ".join((*_EDITABLE_FIELDS, *_BULK_OPERADOR_FIELDS)))
    sets["actualizado_por"] = actor
    sets["actualizado_at"] = datetime.now(UTC)
    if op_email is not None:
        sets["operador_email"] = op_email
    cols = ", ".join(f"{k} = %({k})s" for k in sets)
    with get_pool().connection() as conn, conn.cursor() as cur:
        if op_email is not None:
            _ensure_operador(cur, op_email, op_nombre)
        cur.execute(f"UPDATE comitentes SET {cols} WHERE id_cuenta = %(idc)s",
                    {**sets, "idc": id_cuenta})
        matched = cur.rowcount
        # operador_nombre sin email → actualiza el nombre del operador actual del comitente.
        if op_nombre is not None and op_email is None:
            cur.execute("UPDATE operadores SET nombre = %s WHERE email = "
                        "(SELECT operador_email FROM comitentes WHERE id_cuenta = %s)",
                        (op_nombre, id_cuenta))
        conn.commit()
    if matched == 0:
        raise HTTPException(404, f"id_cuenta no encontrada en comitentes: {id_cuenta!r}")
    return _one_cliente(id_cuenta) or {}


class _BulkReq(BaseModel):
    rows: list[dict] = Field(..., max_length=20000)


@bulk_router.post("/clientes/bulk")
def bulk_clientes(req: _BulkReq, actor: str = Depends(get_user_email)):
    """Carga masiva: update por id_cuenta de SOLO los campos manuales + operador. No crea cuentas."""
    now = datetime.now(UTC)
    bulk_cols = set(_EDITABLE_FIELDS)
    ids: list[str] = []
    sin_id = sin_campos = 0
    actualizadas = matched = 0
    with get_pool().connection() as conn, conn.cursor() as cur:
        for row in req.rows:
            id_cuenta = str(row.get("id_cuenta") or "").strip()
            if not id_cuenta:
                sin_id += 1
                continue
            sets: dict = {}
            for k, v in row.items():
                if k in bulk_cols:
                    val = ("" if v is None else str(v)).strip()
                    if val != "":
                        sets[k] = _normalize_value(k, val)
            op_email = str(row.get("operador_email") or "").strip() or None
            op_nombre = str(row.get("operador_nombre") or "").strip() or None
            if not sets and not op_email and not op_nombre:
                sin_campos += 1
                continue
            sets["actualizado_por"] = actor
            sets["actualizado_at"] = now
            if op_email:
                _ensure_operador(cur, op_email, op_nombre)
                sets["operador_email"] = op_email
            cols = ", ".join(f"{k} = %({k})s" for k in sets)
            cur.execute(f"UPDATE comitentes SET {cols} WHERE id_cuenta = %(idc)s",
                        {**sets, "idc": id_cuenta})
            matched += cur.rowcount
            actualizadas += cur.rowcount
            ids.append(id_cuenta)
        conn.commit()
    if not ids:
        raise HTTPException(400, "no hay filas válidas (falta id_cuenta o columnas con datos)")
    existentes = {r["id_cuenta"] for r in
                  _q("SELECT id_cuenta FROM comitentes WHERE id_cuenta = ANY(%(ids)s)", {"ids": ids})}
    no_encontradas = sorted(set(ids) - existentes)
    return {"actualizadas": actualizadas, "matched": matched, "filas_validas": len(ids),
            "sin_id": sin_id, "sin_campos": sin_campos, "n_no_encontradas": len(no_encontradas),
            "no_encontradas": no_encontradas[:50]}


def _parse_num(v) -> float | None:
    """Tolera number, '1.234.567,89' (AR), '-' (=0) y vacío. None si no parseable."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    s_no_currency = s.replace("$", "").replace("ARS", "").strip()
    if s_no_currency in ("-", "−"):
        return 0.0
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


class _BulkFondeoReq(BaseModel):
    rows: list[dict] = Field(..., max_length=20000)
    fuente: str | None = Field(None, max_length=128)


@bulk_router.post("/clientes/bulk-fondeo")
def bulk_clientes_fondeo(req: _BulkFondeoReq, actor: str = Depends(get_user_email)):
    """Carga masiva del cupo de fondeo del custodio (ARS). No crea cuentas; toca solo
    las filas del payload; idempotente. Re-clasifica nivel_3 de las tocadas al final."""
    now = datetime.now(UTC)
    fuente = (req.fuente or "manager_bulk").strip()[:128]
    ids: list[str] = []
    sin_id = sin_campos = sin_numeros = 0
    actualizadas = matched = 0
    with get_pool().connection() as conn, conn.cursor() as cur:
        for row in req.rows:
            id_cuenta = str(row.get("id_cuenta") or "").strip()
            if not id_cuenta:
                sin_id += 1
                continue
            raw_trans, raw_usado = row.get("cupo_transaccional"), row.get("cupo_usado")
            trans, usado = _parse_num(raw_trans), _parse_num(raw_usado)
            if raw_trans in (None, "") and raw_usado in (None, ""):
                sin_campos += 1
                continue
            if (raw_trans not in (None, "") and trans is None) or (
                    raw_usado not in (None, "") and usado is None):
                sin_numeros += 1
                continue
            sets: dict = {"cupo_cargado_en": now, "cupo_fuente": fuente,
                          "actualizado_por": actor, "actualizado_at": now}
            if trans is not None:
                sets["cupo_transaccional_ars"] = trans
            if usado is not None:
                sets["cupo_usado_ars"] = usado
            sets["cupo_utilizacion_pct"] = (round(usado / trans * 100, 2)
                                            if (trans and usado is not None and trans > 0) else None)
            cols = ", ".join(f"{k} = %({k})s" for k in sets)
            cur.execute(f"UPDATE comitentes SET {cols} WHERE id_cuenta = %(idc)s",
                        {**sets, "idc": id_cuenta})
            matched += cur.rowcount
            actualizadas += cur.rowcount
            ids.append(id_cuenta)
        conn.commit()
    if not ids:
        raise HTTPException(400, "no hay filas válidas (falta id_cuenta o ambos cupos vacíos/no numéricos)")
    existentes = {r["id_cuenta"] for r in
                  _q("SELECT id_cuenta FROM comitentes WHERE id_cuenta = ANY(%(ids)s)", {"ids": ids})}
    no_encontradas = sorted(set(ids) - existentes)
    n_reclasificadas = 0
    try:
        n_reclasificadas = _reclasificar_nivel_3(list(existentes), actor)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Re-clasificación post-bulk falló: %s", e)
    return {"actualizadas": actualizadas, "matched": matched, "filas_validas": len(ids),
            "sin_id": sin_id, "sin_campos": sin_campos, "sin_numeros": sin_numeros,
            "n_no_encontradas": len(no_encontradas), "no_encontradas": no_encontradas[:50],
            "n_reclasificadas": n_reclasificadas}


def _reclasificar_nivel_3(ids_cuenta: list[str], actor: str) -> int:
    """Re-clasifica nivel_3 de un subset (post bulk-fondeo). Lee tipo_cliente + cupo,
    pide MEP/UVA, aplica clasificar_nivel_3 y persiste los que cambian (SQL)."""
    if not ids_cuenta:
        return 0
    from api.services.macro import get_ultimo_mep, get_ultimo_uva
    from api.services.segmentacion import cargar_ids_contrapartes, clasificar_nivel_3
    mep = float(get_ultimo_mep().get("mep") or 0) or None
    uva = get_ultimo_uva()
    contrapartes = cargar_ids_contrapartes()
    rows = _q("SELECT id_cuenta, tipo_cliente, nivel_3, cupo_transaccional_ars "
              "FROM comitentes WHERE id_cuenta = ANY(%(ids)s)", {"ids": ids_cuenta})
    now = datetime.now(UTC)
    n = 0
    with get_pool().connection() as conn, conn.cursor() as cur:
        for r in rows:
            cupo_ars = r.get("cupo_transaccional_ars")
            nuevo = clasificar_nivel_3(
                r.get("tipo_cliente"), float(cupo_ars) if cupo_ars is not None else None,
                mep=mep, uva=uva,
                es_contraparte=str(r.get("id_cuenta") or "").strip() in contrapartes)
            if nuevo != r.get("nivel_3"):
                cur.execute("UPDATE comitentes SET nivel_3=%s, actualizado_at=%s, "
                            "actualizado_por=%s WHERE id_cuenta=%s",
                            (nuevo, now, actor, r["id_cuenta"]))
                n += cur.rowcount
        conn.commit()
    return n


class _RecalcReq(BaseModel):
    apply: bool = False


@bulk_router.post("/clientes/recalcular-niveles")
def recalcular_niveles(req: _RecalcReq, actor: str = Depends(get_user_email)):
    """Recalcula `nivel_3` de TODAS las comitentes activas. NO destructivo (solo SETea
    cuando hay label calculado; nunca borra). `apply=False` → preview."""
    from api.services.macro import get_ultimo_mep, get_ultimo_uva
    from api.services.segmentacion import cargar_ids_contrapartes, clasificar_nivel_3
    mep = float(get_ultimo_mep().get("mep") or 0) or None
    uva = get_ultimo_uva()
    contrapartes = cargar_ids_contrapartes()
    rows = _q("SELECT id_cuenta, tipo_cliente, nivel_3, cupo_transaccional_ars "
              "FROM comitentes WHERE estado = 'Activa'")
    now = datetime.now(UTC)
    dist: dict[str, int] = {}
    cambios: list[dict] = []
    pend: list[tuple] = []
    n = 0
    for r in rows:
        n += 1
        idc = str(r.get("id_cuenta") or "").strip()
        if not idc:
            continue
        cupo = r.get("cupo_transaccional_ars")
        nuevo = clasificar_nivel_3(r.get("tipo_cliente"),
                                   float(cupo) if cupo is not None else None,
                                   mep=mep, uva=uva, es_contraparte=idc in contrapartes)
        dist[nuevo or "(sin clasificar)"] = dist.get(nuevo or "(sin clasificar)", 0) + 1
        if nuevo is not None and nuevo != r.get("nivel_3"):
            cambios.append({"id_cuenta": idc, "de": r.get("nivel_3"), "a": nuevo})
            pend.append((nuevo, now, f"recalcular:{actor}", idc))
    modificadas = 0
    if req.apply and pend:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.executemany("UPDATE comitentes SET nivel_3=%s, actualizado_at=%s, "
                            "actualizado_por=%s WHERE id_cuenta=%s", pend)
            modificadas = cur.rowcount if cur.rowcount and cur.rowcount > 0 else len(pend)
            conn.commit()
    return {"evaluadas": n, "cambios": len(cambios), "modificadas": modificadas,
            "aplicado": req.apply, "distribucion": dist, "ejemplos": cambios[:30],
            "mep": mep, "uva": uva, "sin_mep": mep is None, "sin_uva": uva is None}
