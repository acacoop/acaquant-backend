"""Router Cuentas: endpoints para AccionistasAPI y ContrapartesAPI."""
from fastapi import APIRouter

from api.cache import cached
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
    db = get_db_cuentas()
    return list(db["ContrapartesAPI"].find({}, _PROJ))
