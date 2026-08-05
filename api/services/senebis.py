"""api/services/senebis.py — SENEBIS (vista BACK OFFICE → SENEBIS).

Órdenes que los TRADERS cargan para que el BACK OFFICE las procese en el
sistema externo. Flujo mínimo a propósito:

    trader crea la orden ('pendiente') → aparece en la vista → el back office
    la carga afuera y la marca 'completada'. Dos estados, nada más.

Derivados / defaults (una sola fuente de verdad, calculados acá):
    monto        = vn × px / 100          (px viene cada 100 VN)
    concertacion = HOY (ART) si no viene
    cp           = '255' si no viene
    plazo ↔ liquidacion se infieren entre sí (core.calendario):
        CI → liquidacion = concertacion; 24 → liquidacion = próximo hábil.
        Con liquidacion y sin plazo: mismo día → CI, si no → 24.

Presencia: el front hace heartbeat (o simplemente pollea la lista, que ya
marca presencia) → `operaciones.senebis_presencia` guarda el último visto_at
por email; conectado = visto en los últimos PRESENCIA_TTL_S segundos. Así el
equipo ve quién está en la vista y no se pisan al completar órdenes.

Export: `export_xlsx()` arma el .xlsx EXACTO que el back office carga en el
otro sistema: ID · OPERACION · INSTRUMENTO · PLAZO · PRECIO · CANTIDAD ·
CONTRAPARTE · COMITENTE · CARTERA PROPIA · MERCADO. El ID es el de la tabla:
secuencia GLOBAL que arranca donde la fijemos (scripts/senebis_set_id.py) y
nunca se resetea. COMITENTE/CARTERA PROPIA salen numéricos cuando el valor es
un número (el sistema destino los espera así). openpyxl con import lazy para
no tumbar la API si la lib no está instalada todavía (REGLA #1).

Permisos: módulo `back-office` (gate en api/main.py) para TODO — traders y
back office lo tienen en la matriz. Trazabilidad: cada cambio inserta un
evento en `operaciones.senebis_audit` con before/after. Servicio puro
(sin FastAPI).
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from io import BytesIO
from typing import Any
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from api.services._sql import _f, _q
from core.calendario import proximo_habil
from core.postgres import get_pool

ESTADOS = ("pendiente", "completada")
PLAZOS = ("CI", "24")
PRESENCIA_TTL_S = 90  # visto hace ≤90s = conectado (el front pollea cada ~30s)
_TZ_AR = ZoneInfo("America/Argentina/Buenos_Aires")

# Campos editables de una orden (el resto es derivado o metadata de auditoría).
_CAMPOS_OP = (
    "operacion", "concertacion", "liquidacion", "plazo", "especie",
    "vn", "px", "monto", "cp", "cc", "contraparte", "nro_contraparte",
    "mercado", "cargan_ellos", "tipo",
)

# Columnas del Excel que el back office carga en el otro sistema (imagen de
# referencia 2026-08-05): el ID va primero y es la secuencia global de la tabla.
_COLUMNAS_XLSX = (
    ("ID", "id"),
    ("OPERACION", "operacion"),
    ("INSTRUMENTO", "especie"),
    ("PLAZO", "plazo"),
    ("PRECIO", "px"),
    ("CANTIDAD", "vn"),
    ("CONTRAPARTE", "contraparte"),
    ("COMITENTE", "cc"),
    ("CARTERA PROPIA", "cp"),
    ("MERCADO", "mercado"),
)
# Columnas que el sistema destino espera como NÚMERO cuando el valor lo es.
_COLS_NUMERICAS_SI_SE_PUEDE = {"cc", "cp"}


def _exec(sql: str, params: dict) -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        n = cur.rowcount
        conn.commit()
        return n


def _audit(actor: str, action: str, target: str, data: dict | None = None) -> None:
    """Evento de auditoría — nunca rompe la operación principal."""
    try:
        _exec(
            "INSERT INTO operaciones.senebis_audit (ts, actor, action, target, data) "
            "VALUES (%(ts)s, %(actor)s, %(action)s, %(target)s, %(data)s)",
            {"ts": datetime.now(UTC), "actor": actor or None, "action": action,
             "target": target, "data": Jsonb(_jsonable(data or {}))},
        )
    except Exception:  # el audit no bloquea la escritura real
        import logging
        logging.getLogger(__name__).exception("senebis: audit insert falló")


def _jsonable(v: Any) -> Any:
    """dict/list con date/datetime/Decimal → tipos serializables."""
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if hasattr(v, "is_finite"):  # Decimal
        return float(v)
    return v


# ─────────────────────────────────────────────────────────────
# Presencia (quién tiene la vista abierta)
# ─────────────────────────────────────────────────────────────

def marcar_presencia(email: str) -> None:
    e = (email or "").lower().strip()
    if not e:
        return
    _exec(
        "INSERT INTO operaciones.senebis_presencia (email, visto_at) "
        "VALUES (%(e)s, %(at)s) "
        "ON CONFLICT (email) DO UPDATE SET visto_at = EXCLUDED.visto_at",
        {"e": e, "at": datetime.now(UTC)},
    )


def conectados() -> list[dict]:
    """Quiénes vieron la vista en los últimos PRESENCIA_TTL_S segundos."""
    rows = _q(
        "SELECT email, visto_at FROM operaciones.senebis_presencia "
        "WHERE visto_at >= now() - make_interval(secs => %(ttl)s) ORDER BY email",
        {"ttl": PRESENCIA_TTL_S},
    )
    return [{"email": r["email"], "visto_at": r["visto_at"].isoformat()} for r in rows]


# ─────────────────────────────────────────────────────────────
# Órdenes (CRUD + estado)
# ─────────────────────────────────────────────────────────────

def _fila_op(r: dict) -> dict:
    return {
        "id": r["id"],
        "operacion": r["operacion"],
        "concertacion": r["concertacion"].isoformat() if r["concertacion"] else None,
        "liquidacion": r["liquidacion"].isoformat() if r["liquidacion"] else None,
        "plazo": r["plazo"],
        "especie": r["especie"],
        "vn": _f(r["vn"]), "px": _f(r["px"]), "monto": _f(r["monto"]),
        "cp": r["cp"], "cc": r["cc"],
        "contraparte": r["contraparte"], "nro_contraparte": r["nro_contraparte"],
        "mercado": r["mercado"],
        "cargan_ellos": r["cargan_ellos"], "tipo": r["tipo"],
        "estado": r["estado"],
        "completada_por": r["completada_por"],
        "completada_at": r["completada_at"].isoformat() if r["completada_at"] else None,
        "creado_por": r["creado_por"],
        "creado_at": r["creado_at"].isoformat() if r["creado_at"] else None,
        "actualizado_por": r["actualizado_por"],
    }


def listar_ops(desde: str | None = None, hasta: str | None = None,
               estado: str | None = None, especie: str | None = None,
               email: str = "") -> dict:
    """Órdenes del período (default: todas), más nuevas primero, + presencia.

    Cada lectura marca presencia del caller — pollear la lista ES el heartbeat:
    el front no necesita un ping aparte mientras la vista esté abierta."""
    if email:
        marcar_presencia(email)
    conds, params = [], {}
    if desde:
        conds.append("concertacion >= %(desde)s")
        params["desde"] = desde
    if hasta:
        conds.append("concertacion <= %(hasta)s")
        params["hasta"] = hasta
    if estado:
        if estado not in ESTADOS:
            raise ValueError(f"estado {estado!r} inválido: {' | '.join(ESTADOS)}")
        conds.append("estado = %(estado)s")
        params["estado"] = estado
    if especie:
        conds.append("especie ILIKE %(especie)s")
        params["especie"] = f"%{especie.strip()}%"
    where = f"WHERE {' AND '.join(conds)}" if conds else ""
    rows = _q(
        f"SELECT * FROM operaciones.senebis {where} ORDER BY concertacion DESC, id DESC",
        params,
    )
    pendientes = sum(1 for r in rows if r["estado"] == "pendiente")
    return {
        "total": len(rows),
        "pendientes": pendientes,
        "ordenes": [_fila_op(r) for r in rows],
        "conectados": conectados(),
    }


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    return float(v)


def _derivar_monto(p: dict) -> float | None:
    """monto = vn × px / 100 (px viene cada 100 VN). Si viene `monto` explícito
    en el payload, se respeta (override manual)."""
    manual = _num(p.get("monto"))
    if manual is not None:
        return manual
    vn, px = _num(p.get("vn")), _num(p.get("px"))
    if vn is not None and px is not None:
        return vn * px / 100
    return None


def _norm_plazo(v: Any) -> str | None:
    """'ci'/'CI' → 'CI'; '24'/'24hs'/'24HS' → '24'. Otro valor → ValueError."""
    s = str(v or "").strip().upper().replace("HS", "").replace(" ", "")
    if not s:
        return None
    if s == "CI":
        return "CI"
    if s == "24":
        return "24"
    raise ValueError(f"plazo {v!r} inválido: CI | 24")


def _completar_fechas(p: dict) -> dict:
    """Defaults + inferencia plazo ↔ liquidacion. Devuelve
    {concertacion, liquidacion, plazo} listos para persistir.

    - concertacion: default HOY (ART).
    - plazo sin liquidacion: CI → liq = concertacion; 24 → próximo hábil.
    - liquidacion sin plazo: mismo día → CI; posterior → 24.
    - ninguno: CI liquidando hoy (el caso más común de la mesa; editable).
    """
    conc = p.get("concertacion") or datetime.now(_TZ_AR).date().isoformat()
    conc_d = date.fromisoformat(str(conc))
    plazo = _norm_plazo(p.get("plazo"))
    liq = p.get("liquidacion") or None
    if liq:
        liq_d = date.fromisoformat(str(liq))
        if liq_d < conc_d:
            raise ValueError("'liquidacion' no puede ser anterior a 'concertacion'")
        if plazo is None:
            plazo = "CI" if liq_d == conc_d else "24"
    else:
        if plazo is None:
            plazo = "CI"
        liq_d = conc_d if plazo == "CI" else proximo_habil(conc_d)
    return {"concertacion": conc_d.isoformat(), "liquidacion": liq_d.isoformat(),
            "plazo": plazo}


def _validar(p: dict) -> None:
    op = (p.get("operacion") or "").strip().upper()
    if op not in ("COMPRA", "VENTA"):
        raise ValueError("'operacion' tiene que ser COMPRA o VENTA")
    if not (p.get("especie") or "").strip():
        raise ValueError("falta 'especie'")


def _row_de_payload(p: dict, actor: str) -> dict:
    fechas = _completar_fechas(p)
    return {
        "operacion": (p["operacion"] or "").strip().upper(),
        **fechas,
        "especie": (p.get("especie") or "").strip().upper(),
        "vn": _num(p.get("vn")), "px": _num(p.get("px")),
        "monto": _derivar_monto(p),
        "cp": (str(p.get("cp") or "").strip()) or "255",   # default cuenta propia 255
        "cc": (str(p.get("cc") or "").strip()) or None,
        "contraparte": (p.get("contraparte") or "").strip() or None,
        "nro_contraparte": (str(p.get("nro_contraparte") or "").strip()) or None,
        "mercado": (p.get("mercado") or "").strip().upper() or None,
        "cargan_ellos": (p.get("cargan_ellos") or "").strip() or None,
        "tipo": (p.get("tipo") or "").strip() or None,
        "por": (actor or "").lower() or None, "at": datetime.now(UTC),
    }


def crear_op(payload: dict, actor: str) -> dict:
    p = {k: payload.get(k) for k in _CAMPOS_OP}
    _validar(p)
    row = _row_de_payload(p, actor)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO operaciones.senebis "
            "(operacion, concertacion, liquidacion, plazo, especie, vn, px, monto, "
            " cp, cc, contraparte, nro_contraparte, mercado, cargan_ellos, tipo, "
            " estado, creado_por, creado_at, actualizado_por, actualizado_at) "
            "VALUES (%(operacion)s, %(concertacion)s, %(liquidacion)s, %(plazo)s, "
            " %(especie)s, %(vn)s, %(px)s, %(monto)s, %(cp)s, %(cc)s, %(contraparte)s, "
            " %(nro_contraparte)s, %(mercado)s, %(cargan_ellos)s, %(tipo)s, "
            " 'pendiente', %(por)s, %(at)s, %(por)s, %(at)s) "
            "RETURNING id",
            row,
        )
        op_id = cur.fetchone()[0]
        conn.commit()
    _audit(actor, "create_op", str(op_id), {"after": row})
    return _get_op(op_id)


def _get_op(op_id: int) -> dict:
    rows = _q("SELECT * FROM operaciones.senebis WHERE id = %(id)s", {"id": op_id})
    if not rows:
        raise ValueError(f"orden {op_id} no existe")
    return _fila_op(rows[0])


def editar_op(op_id: int, payload: dict, actor: str) -> dict:
    before = _get_op(op_id)
    # Merge: lo que no viene en el payload se mantiene; el monto se recalcula
    # salvo que venga un override manual REAL (distinto del monto ya guardado —
    # si el front solo echoa la fila, se re-deriva con los vn/px nuevos).
    p = {k: (payload[k] if k in payload else before.get(k)) for k in _CAMPOS_OP}
    if _num(p.get("monto")) == before.get("monto"):
        p["monto"] = None
    _validar(p)
    row = {"id": op_id, **_row_de_payload(p, actor)}
    _exec(
        "UPDATE operaciones.senebis SET operacion=%(operacion)s, "
        "concertacion=%(concertacion)s, liquidacion=%(liquidacion)s, "
        "plazo=%(plazo)s, especie=%(especie)s, vn=%(vn)s, px=%(px)s, "
        "monto=%(monto)s, cp=%(cp)s, cc=%(cc)s, contraparte=%(contraparte)s, "
        "nro_contraparte=%(nro_contraparte)s, mercado=%(mercado)s, "
        "cargan_ellos=%(cargan_ellos)s, tipo=%(tipo)s, "
        "actualizado_por=%(por)s, actualizado_at=%(at)s WHERE id=%(id)s",
        row,
    )
    after = _get_op(op_id)
    _audit(actor, "update_op", str(op_id), {"before": before, "after": after})
    return after


def borrar_op(op_id: int, actor: str) -> dict:
    before = _get_op(op_id)
    n = _exec("DELETE FROM operaciones.senebis WHERE id = %(id)s", {"id": op_id})
    _audit(actor, "delete_op", str(op_id), {"before": before})
    return {"borrado": n}


def set_estado(op_id: int, estado: str, actor: str) -> dict:
    """Marca 'completada' (con quién y cuándo) o vuelve a 'pendiente'."""
    if estado not in ESTADOS:
        raise ValueError(f"estado {estado!r} inválido: {' | '.join(ESTADOS)}")
    before = _get_op(op_id)
    if before["estado"] == estado:
        return before  # idempotente: dos clicks simultáneos no duplican audit
    por = (actor or "").lower() or None
    at = datetime.now(UTC)
    _exec(
        "UPDATE operaciones.senebis SET estado=%(estado)s, "
        "completada_por=%(comp_por)s, completada_at=%(comp_at)s, "
        "actualizado_por=%(por)s, actualizado_at=%(at)s WHERE id=%(id)s",
        {"id": op_id, "estado": estado,
         "comp_por": por if estado == "completada" else None,
         "comp_at": at if estado == "completada" else None,
         "por": por, "at": at},
    )
    after = _get_op(op_id)
    _audit(actor, "set_estado", str(op_id), {"before": before, "after": after})
    return after


# ─────────────────────────────────────────────────────────────
# Export Excel (el archivo que se carga en el sistema destino)
# ─────────────────────────────────────────────────────────────

def _celda_export(campo: str, v: Any) -> Any:
    """Valor de la celda: COMITENTE/CARTERA PROPIA numéricos cuando se puede
    (el sistema destino los espera como número; si el CC es texto queda texto
    y el back office lo resuelve a mano)."""
    if campo in _COLS_NUMERICAS_SI_SE_PUEDE and isinstance(v, str) and v.isdigit():
        return int(v)
    return v


def export_xlsx(desde: str | None = None, hasta: str | None = None,
                estado: str | None = None) -> tuple[bytes, str]:
    """Devuelve (bytes del .xlsx, nombre de archivo) con las órdenes filtradas,
    columnas EXACTAS del sistema destino (ID primero). Las órdenes van más
    viejas primero (por ID asc = orden de carga)."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
    except ImportError as e:  # lazy: sin openpyxl la API sigue viva (REGLA #1)
        raise RuntimeError(
            "openpyxl no está instalado — correr `pip install -r requirements.txt` "
            "en el venv del Droplet") from e

    data = listar_ops(desde=desde, hasta=hasta, estado=estado)
    ordenes = sorted(data["ordenes"], key=lambda o: o["id"])

    wb = Workbook()
    ws = wb.active
    ws.title = "SENEBIS"
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1F4E79")
    for col, (titulo, _campo) in enumerate(_COLUMNAS_XLSX, start=1):
        cell = ws.cell(row=1, column=col, value=titulo)
        cell.font = header_font
        cell.fill = header_fill
    ws.freeze_panes = "A2"

    for i, o in enumerate(ordenes, start=2):
        for col, (_titulo, campo) in enumerate(_COLUMNAS_XLSX, start=1):
            ws.cell(row=i, column=col, value=_celda_export(campo, o[campo]))

    # Anchos razonables para abrir y leer sin acomodar nada.
    anchos = (8, 12, 14, 8, 14, 16, 16, 14, 15, 18)
    for col, ancho in enumerate(anchos, start=1):
        ws.column_dimensions[ws.cell(row=1, column=col).column_letter].width = ancho

    buf = BytesIO()
    wb.save(buf)
    hoy = datetime.now(_TZ_AR).strftime("%Y%m%d")
    sufijo = f"_{estado}" if estado else ""
    return buf.getvalue(), f"senebis_{hoy}{sufijo}.xlsx"
