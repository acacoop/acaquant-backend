"""Router Titulos: assets + flujos, DIRECTO desde las fuentes (Valuaciones.Assets
y Trading.Curvas+BondsMaster vía servicio titulos_flujos) — sin espejos *API."""
from fastapi import APIRouter, Query

from api.cache import cached
from api.services.renta_fija import _bonos_cer_fijados
from api.services.titulos_flujos import assets_normalizados, flujos_instrumentos

router = APIRouter(prefix="/api/titulos", tags=["Titulos"])

# Campos que expone el endpoint (subset del doc normalizado).
_PROJ_ASSETS = ("unidad", "ticker", "emisor", "cartera",
                "clase_activo", "calificacion", "vencimiento")


@router.get("/assets")
@cached(ttl=600)
def listar_assets(
    unidad: str | None = Query(None, description="Filtrar por unidad"),
    ticker: str | None = Query(None, description="Filtrar por ticker"),
    cartera: str | None = Query(None, description="Filtrar por cartera"),
    emisor: str | None = Query(None, description="Filtrar por emisor"),
    clase_activo: str | None = Query(None, description="Filtrar por clase de activo"),
):
    def ok(d: dict) -> bool:
        return (
            (not unidad or d.get("unidad") == unidad)
            and (not ticker or d.get("ticker") == ticker)
            and (not cartera or d.get("cartera") == cartera)
            and (not emisor or d.get("emisor") == emisor)
            and (not clase_activo or d.get("clase_activo") == clase_activo)
        )

    return [
        {k: d.get(k) for k in _PROJ_ASSETS}
        for d in assets_normalizados()
        if ok(d)
    ]


@router.get("/flujos")
@cached(ttl=60)
def listar_flujos_titulos(
    ticker: str | None = Query(None, description="Filtrar por ticker (corto, ej: TX26)"),
    curva: str | None = Query(None, description="Filtrar por curva (tasa_fija/cer)"),
    moneda_flujo: str | None = Query(None, description="Filtrar por moneda de flujo (ARS/USD)"),
):
    """Flujos de los instrumentos — DIRECTO desde Trading.Curvas+BondsMaster
    (servicio `titulos_flujos`, sin el espejo materializado ValuacionesAPI).

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
    docs = [
        d for d in flujos_instrumentos()
        if (not ticker or d.get("ticker") == ticker)
        and (not curva or d.get("curva") == curva)
        and (not moneda_flujo or d.get("moneda_flujo") == moneda_flujo)
    ]
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
