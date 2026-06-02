"""Router Cuentas: endpoints para AccionistasAPI y Contrapartes."""
from fastapi import APIRouter

from api.cache import cached
from api.db import get_db_cashflow
from api.deps import get_db_cuentas

router = APIRouter(prefix="/api/cuentas", tags=["Cuentas"])

_PROJ = {"_id": 0, "cuenta": 1, "id_cuenta": 1, "nombre": 1, "grupo": 1}


@router.get("/accionistas")
@cached(ttl=3600)
def listar_accionistas():
    db = get_db_cuentas()
    return list(db["AccionistasAPI"].find({}, _PROJ))


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
