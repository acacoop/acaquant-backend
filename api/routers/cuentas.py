"""Router Cuentas: endpoints para AccionistasAPI y ContrapartesAPI."""
from fastapi import APIRouter

from api.deps import get_db_cuentas

router = APIRouter(prefix="/api/cuentas", tags=["Cuentas"])


@router.get("/accionistas")
def listar_accionistas():
    db = get_db_cuentas()
    docs = list(db["AccionistasAPI"].find({}, {"_id": 0}))
    return docs


@router.get("/contrapartes")
def listar_contrapartes():
    db = get_db_cuentas()
    docs = list(db["ContrapartesAPI"].find({}, {"_id": 0}))
    return docs
