"""Router Cuentas: accionistas y contrapartes, DIRECTO desde las colecciones
fuente en CashFlow (sin los espejos CuentasAPI.*API)."""
import re

from fastapi import APIRouter

from api.cache import cached
from api.db import get_db_cashflow

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
    """DIRECTO desde CashFlow.Contrapartes (sin la copia intermedia
    CuentasAPI.ContrapartesAPI). nombre = contraparte, grupo = segmento."""
    coll = get_db_cashflow()["Contrapartes"]
    out = []
    for d in coll.find(
        {"cuenta": {"$exists": True, "$ne": ""}},
        {"_id": 0, "cuenta": 1, "contraparte": 1, "segmento": 1},
    ):
        c = str(d.get("cuenta") or "").strip()
        if not c:
            continue
        out.append({
            "cuenta": c, "id_cuenta": c,
            "nombre": d.get("contraparte") or "",
            "grupo": d.get("segmento") or "",
        })
    return out
