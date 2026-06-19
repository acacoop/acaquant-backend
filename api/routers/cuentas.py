"""Router Cuentas: accionistas (Mongo CashFlow.Accionistas) y contrapartes
(SQL clientes.contrapartes — fuente única; Mongo CashFlow.Contrapartes fue dropeada)."""
import re

from fastapi import APIRouter

from api.cache import cached
from api.db import get_db_cashflow
from core.postgres import get_pool

router = APIRouter(prefix="/api/cuentas", tags=["Cuentas"])

_RE_CUENTA = re.compile(r"^\[(\d+)\]\s*(.*)$")


@router.get("/accionistas")
@cached(ttl=3600)
def listar_accionistas():
    """DIRECTO desde CashFlow.Accionistas {cuenta:'[N] NOMBRE', accionista}.
    id_cuenta y nombre se derivan de `cuenta`; grupo = accionista."""
    coll = get_db_cashflow()["Accionistas"]
    out = []
    for d in coll.find({}, {"_id": 0, "cuenta": 1, "accionista": 1}):
        raw = str(d.get("cuenta") or "").strip()
        if not raw:
            continue
        m = _RE_CUENTA.match(raw)
        out.append({
            "cuenta": raw,
            "id_cuenta": m.group(1) if m else None,
            "nombre": m.group(2).strip() if m else raw,
            "grupo": d.get("accionista") or "",
        })
    return out


@router.get("/contrapartes")
@cached(ttl=3600)
def listar_contrapartes():
    """DIRECTO desde SQL clientes.contrapartes (fuente única; Mongo CashFlow.Contrapartes
    fue dropeada). id_cuenta = clave, nombre = contraparte, grupo = segmento."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id_cuenta, contraparte, segmento FROM contrapartes "
            "WHERE id_cuenta IS NOT NULL AND id_cuenta <> '' ORDER BY id_cuenta"
        )
        rows = cur.fetchall()
    return [
        {"cuenta": idc, "id_cuenta": idc, "nombre": cp or "", "grupo": seg or ""}
        for idc, cp, seg in rows
    ]
