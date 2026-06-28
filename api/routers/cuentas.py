"""Router Cuentas: accionistas y contrapartes (ambos SQL — fuente única;
Mongo CashFlow.Accionistas/Contrapartes en decomiso/dropeadas)."""
import re

from fastapi import APIRouter

from api.cache import cached
from core.postgres import get_pool

router = APIRouter(prefix="/api/cuentas", tags=["Cuentas"])

_RE_CUENTA = re.compile(r"^\[(\d+)\]\s*(.*)$")


@router.get("/accionistas")
@cached(ttl=3600)
def listar_accionistas():
    """DIRECTO desde SQL clientes.accionistas {cuenta:'[N] NOMBRE', accionista}.
    id_cuenta y nombre se derivan de `cuenta`; grupo = accionista. Carga manual:
    scripts/cargar_accionistas.py."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT cuenta, accionista FROM accionistas "
            "WHERE cuenta IS NOT NULL AND cuenta <> '' ORDER BY cuenta"
        )
        rows = cur.fetchall()
    out = []
    for cuenta, accionista in rows:
        raw = str(cuenta or "").strip()
        if not raw:
            continue
        m = _RE_CUENTA.match(raw)
        out.append({
            "cuenta": raw,
            "id_cuenta": m.group(1) if m else None,
            "nombre": m.group(2).strip() if m else raw,
            "grupo": accionista or "",
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
