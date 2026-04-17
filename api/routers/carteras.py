"""Router Portfolio: endpoints para CarterasAPI y AumAPI."""
from datetime import datetime

from fastapi import APIRouter, Query

from api.cache import cached
from api.deps import get_db_portfolio, get_db_titulos, get_db_valuaciones

router = APIRouter(prefix="/api/portfolio", tags=["Portfolio"])

_PROJ_CARTERAS = {
    "_id": 0, "id_cuenta": 1, "unidad": 1, "cantidad": 1,
    "precio": 1, "timestamp": 1,
}
_PROJ_AUM = {
    "_id": 0, "fecha": 1, "id_cuenta": 1, "unidad": 1,
    "cantidad": 1, "cuenta": 1, "precio": 1, "valuacion": 1,
}


@router.get("/carteras")
@cached(ttl=300)
def listar_carteras(
    id_cuenta: str | None = Query(None, description="Filtrar por id de cuenta"),
    unidad: str | None = Query(None, description="Filtrar por unidad/instrumento"),
):
    db = get_db_portfolio()
    filtro = {}
    if id_cuenta:
        filtro["id_cuenta"] = id_cuenta
    if unidad:
        filtro["unidad"] = unidad

    return list(db["CarterasAPI"].find(filtro, _PROJ_CARTERAS))


@router.get("/aum")
@cached(ttl=300)
def listar_aum(
    id_cuenta: str | None = Query(None, description="Filtrar por id de cuenta"),
    unidad: str | None = Query(None, description="Filtrar por unidad/instrumento"),
    cuenta: str | None = Query(None, description="Filtrar por cuenta (formato [N] NOMBRE)"),
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
    ultimo: bool = Query(False, description="Si true, devuelve solo el último snapshot (fecha más reciente)"),
):
    db = get_db_portfolio()
    filtro: dict = {}

    if ultimo:
        last = db["AumAPI"].find_one(
            {}, {"fecha": 1, "_id": 0}, sort=[("fecha", -1)],
        )
        if not last:
            return []
        filtro["fecha"] = last["fecha"]

    if id_cuenta:
        filtro["id_cuenta"] = id_cuenta
    if unidad:
        filtro["unidad"] = unidad
    if cuenta:
        filtro["cuenta"] = cuenta
    if not ultimo and (desde or hasta):
        rango: dict = {}
        if desde:
            rango["$gte"] = datetime.strptime(desde, "%Y-%m-%d")
        if hasta:
            rango["$lte"] = datetime.strptime(hasta, "%Y-%m-%d")
        filtro["fecha"] = rango

    return list(db["AumAPI"].find(filtro, _PROJ_AUM))


def _fci_assets_map() -> dict[str, dict]:
    """Mapea unidad → {emisor, ticker} para unidades con CARTERA=CARTERA FCI."""
    db_t = get_db_titulos()
    docs = db_t["AssetsAPI"].find(
        {"cartera": "CARTERA FCI"},
        {"_id": 0, "unidad": 1, "emisor": 1, "ticker": 1},
    )
    return {d["unidad"]: {"emisor": d.get("emisor", ""), "ticker": d.get("ticker", "")}
            for d in docs}


@router.get("/fci-serie")
@cached(ttl=300)
def fci_serie(
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
):
    """Serie histórica FCI: total por fecha + desglose por emisor.

    Lee Valuaciones.AuMResumenFCI (rollup 1 doc/fecha) y enriquece con EMISOR
    desde TitulosAPI.AssetsAPI.
    """
    db_v = get_db_valuaciones()

    # fecha_snapshot se almacena como string "YYYY-MM-DD" (jobs/aum_resumen_fci.py).
    # Los strings ISO ordenan lexicográficamente, así que $gte/$lte sobre string
    # funciona para rangos de fechas.
    filtro: dict = {}
    if desde or hasta:
        rango: dict = {}
        if desde:
            rango["$gte"] = desde
        if hasta:
            rango["$lte"] = hasta
        filtro["fecha_snapshot"] = rango

    cursor = db_v["AuMResumenFCI"].find(filtro, {"_id": 0}).sort("fecha_snapshot", 1)
    assets_map = _fci_assets_map()

    out = []
    for doc in cursor:
        fecha = doc.get("fecha_snapshot")
        if isinstance(fecha, datetime):
            fecha_str = fecha.strftime("%Y-%m-%d")
        else:
            fecha_str = str(fecha)[:10]

        por_emisor: dict[str, float] = {}
        total = 0.0
        for u in doc.get("unidades", []):
            unidad = u.get("unidad", "")
            val = float(u.get("valuacion_total") or 0)
            total += val
            emisor = assets_map.get(unidad, {}).get("emisor", "") or "SIN EMISOR"
            por_emisor[emisor] = por_emisor.get(emisor, 0.0) + val

        out.append({"fecha": fecha_str, "total": total, "por_emisor": por_emisor})

    return out


@router.get("/fci-snapshot")
@cached(ttl=300)
def fci_snapshot(fecha: str = Query(..., description="Fecha snapshot (YYYY-MM-DD)")):
    """Snapshot FCI en una fecha: detalle por unidad/emisor/cuenta.

    Lee Valuaciones.AuM (fuente de verdad, actualizada diario por cron) con
    fecha_snapshot string y unidades FCI. Enriquece con TICKER/EMISOR desde
    TitulosAPI.AssetsAPI.
    """
    db_v = get_db_valuaciones()
    assets_map = _fci_assets_map()
    unidades_fci = list(assets_map.keys())

    if not unidades_fci:
        return []

    docs = db_v["AuM"].find(
        {"fecha_snapshot": fecha, "unidad": {"$in": unidades_fci}},
        {"_id": 0, "unidad": 1, "cuenta": 1, "id_cuenta": 1,
         "valuacion": 1, "cantidad": 1},
    )

    out = []
    for d in docs:
        unidad = d.get("unidad", "")
        meta = assets_map.get(unidad, {})
        out.append({
            "unidad": unidad,
            "emisor": meta.get("emisor", "SIN EMISOR") or "SIN EMISOR",
            "ticker": meta.get("ticker", unidad),
            "cuenta": d.get("cuenta", ""),
            "id_cuenta": d.get("id_cuenta", ""),
            "valuacion": float(d.get("valuacion") or 0),
            "cantidad": float(d.get("cantidad") or 0),
        })

    return out
