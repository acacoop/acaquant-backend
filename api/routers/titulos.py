"""Router Titulos: endpoints para AssetsAPI y FlujosAPI."""
from fastapi import APIRouter, Query

from api.cache import cached
from api.deps import get_db_titulos
from api.services.renta_fija import _bonos_cer_fijados

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
@cached(ttl=60)
def listar_flujos_titulos(
    ticker: str | None = Query(None, description="Filtrar por ticker (corto, ej: TX26)"),
    curva: str | None = Query(None, description="Filtrar por curva (tasa_fija/cer)"),
    moneda_flujo: str | None = Query(None, description="Filtrar por moneda de flujo (ARS/USD)"),
):
    """Flujos de los instrumentos de Trading.Curvas (vía copia ValuacionesAPI).

    Devuelve cada doc tal cual + dos campos derivados que el frontend usa
    para filtrar la pantalla /renta-fija:
    - `cer_fijado` (bool) — true si el bono es CER y su CER de liquidación
      (vto − 10 hábiles) ya está publicado por BCRA → se comporta como tasa fija.
    - `curva_efectiva` (str) — `"tasa_fija"` si `cer_fijado`, sino la curva
      original. Es la curva que el frontend usa para asignar bono a pestaña.

    TTL bajado a 60s (antes 600s) porque el set `fijados` cambia a diario
    al publicarse el CER del día. 10 min de cache hacía que un bono recién
    fijado tardara hasta 10 min en migrar a tasa fija después del job BCRA.
    """
    db = get_db_titulos()
    filtro = {}
    if ticker:
        filtro["ticker"] = ticker
    if curva:
        filtro["curva"] = curva
    if moneda_flujo:
        filtro["moneda_flujo"] = moneda_flujo

    docs = list(db["ValuacionesAPI"].find(filtro, {"_id": 0}))
    fijados = _bonos_cer_fijados_set_corto()
    for d in docs:
        is_fijado = (d.get("curva") == "cer") and (d.get("ticker") in fijados)
        d["cer_fijado"] = is_fijado
        d["curva_efectiva"] = "tasa_fija" if is_fijado else d.get("curva")
    return docs


def _bonos_cer_fijados_set_corto() -> set[str]:
    """`_bonos_cer_fijados()` devuelve tickers LARGOS ('MERV - XMEV - X15Y6 - 24hs').
    El doc de ValuacionesAPI usa el ticker CORTO ('X15Y6'). Mapeamos vía
    Trading.Curvas para que el match en `listar_flujos_titulos` funcione.
    """
    from api.deps import get_db_trading

    fijados_largos = _bonos_cer_fijados()
    if not fijados_largos:
        return set()
    db = get_db_trading()
    out: set[str] = set()
    for c in db["Curvas"].find(
        {"ticker": {"$in": list(fijados_largos)}},
        {"_id": 0, "ticker_corto": 1},
    ):
        if c.get("ticker_corto"):
            out.add(c["ticker_corto"])
    return out
