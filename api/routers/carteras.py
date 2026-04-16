"""Router Carteras: endpoint para CarterasAPI (posiciones por cuenta)."""
from fastapi import APIRouter, Query

from api.deps import get_db_carteras

router = APIRouter(prefix="/api/carteras", tags=["Carteras"])


@router.get("/")
def listar_carteras(
    id_cuenta: str | None = Query(None, description="Filtrar por id de cuenta"),
    unidad: str | None = Query(None, description="Filtrar por unidad/instrumento"),
):
    db = get_db_carteras()
    filtro = {}
    if id_cuenta:
        filtro["id_cuenta"] = id_cuenta
    if unidad:
        filtro["unidad"] = unidad

    docs = list(db["CarterasAPI"].find(filtro, {"_id": 0}))
    return docs
