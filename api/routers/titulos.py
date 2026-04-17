"""Router Titulos: endpoints para AssetsAPI y FlujosAPI."""
from fastapi import APIRouter, Query

from api.cache import cached
from api.deps import get_db_titulos

router = APIRouter(prefix="/api/titulos", tags=["Titulos"])

_PROJ_ASSETS = {
    "_id": 0, "unidad": 1, "ticker": 1, "emisor": 1,
    "cartera": 1, "clase_activo": 1, "calificacion": 1, "vencimiento": 1,
}


@router.get("/assets")
@cached(ttl=600)
def listar_assets(
    unidad: str | None = Query(None, description="Filtrar por unidad"),
    ticker: str | None = Query(None, description="Filtrar por ticker"),
    cartera: str | None = Query(None, description="Filtrar por cartera"),
    emisor: str | None = Query(None, description="Filtrar por emisor"),
    clase_activo: str | None = Query(None, description="Filtrar por clase de activo"),
):
    db = get_db_titulos()
    filtro = {}
    if unidad:
        filtro["unidad"] = unidad
    if ticker:
        filtro["ticker"] = ticker
    if cartera:
        filtro["cartera"] = cartera
    if emisor:
        filtro["emisor"] = emisor
    if clase_activo:
        filtro["clase_activo"] = clase_activo

    return list(db["AssetsAPI"].find(filtro, _PROJ_ASSETS))


@router.get("/flujos")
@cached(ttl=600)
def listar_flujos_titulos(
    ticker: str | None = Query(None, description="Filtrar por ticker (corto, ej: TX26)"),
    curva: str | None = Query(None, description="Filtrar por curva (tasa_fija/cer)"),
    moneda_flujo: str | None = Query(None, description="Filtrar por moneda de flujo (ARS/USD)"),
):
    db = get_db_titulos()
    filtro = {}
    if ticker:
        filtro["ticker"] = ticker
    if curva:
        filtro["curva"] = curva
    if moneda_flujo:
        filtro["moneda_flujo"] = moneda_flujo

    return list(db["ValuacionesAPI"].find(filtro, {"_id": 0}))
