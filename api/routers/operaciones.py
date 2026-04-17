"""Router Operaciones: endpoints para MesaAPI (flujo contrapartes) y FlujosAPI (movimientos)."""
from fastapi import APIRouter, Query

from api.cache import cached
from api.deps import (
    get_db_cuentas,
    get_db_operaciones,
    get_db_portfolio,
    get_db_titulos,
)

router = APIRouter(prefix="/api/operaciones", tags=["Operaciones"])

# Proyección de MesaAPI — solo los campos que consume el frontend.
_PROJ_FLUJO = {
    "_id": 0, "boleto": 1, "concertacion": 1, "tipoOperacion": 1,
    "cuenta": 1, "denominacion": 1, "unidad": 1, "bruto": 1,
    "segmento": 1, "contraparte": 1, "moneda": 1,
}

_PROJ_MOVIMIENTOS = {
    "_id": 0, "boleto": 1, "concertacion": 1, "cuenta": 1,
    "informacion": 1, "bruto": 1, "unidad": 1,
}


@router.get("/flujo")
@cached(ttl=300)
def listar_flujo(
    contraparte: str | None = Query(None, description="Filtrar por contraparte"),
    moneda: str | None = Query(None, description="Filtrar por moneda (ARS/USD)"),
    segmento: str | None = Query(None, description="Filtrar por segmento"),
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
):
    db = get_db_operaciones()
    filtro = {}
    if contraparte:
        filtro["contraparte"] = contraparte
    if moneda:
        filtro["moneda"] = moneda
    if segmento:
        filtro["segmento"] = segmento
    if desde or hasta:
        rango = {}
        if desde:
            rango["$gte"] = desde
        if hasta:
            rango["$lte"] = hasta
        filtro["concertacion"] = rango

    return list(db["MesaAPI"].find(filtro, _PROJ_FLUJO).sort("concertacion", 1))


@router.get("/flujos")
@cached(ttl=300)
def listar_flujos(
    cuenta: str | None = Query(None, description="Filtrar por cuenta (formato [N] NOMBRE)"),
    unidad: str | None = Query(None, description="Filtrar por moneda (ARS/USD)"),
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
):
    db = get_db_operaciones()
    filtro = {}
    if cuenta:
        filtro["cuenta"] = cuenta
    if unidad:
        filtro["unidad"] = unidad
    if desde or hasta:
        rango = {}
        if desde:
            rango["$gte"] = desde
        if hasta:
            rango["$lte"] = hasta
        filtro["concertacion"] = rango

    return list(db["FlujosAPI"].find(filtro, _PROJ_MOVIMIENTOS).sort("concertacion", 1))


# ── Flujo vs AUM (solo Fondos) ──

def _fondos_emisores() -> list[str]:
    """Lista de emisores de CashFlow con grupo=Fondos que tienen al menos un asset FCI."""
    db_cu = get_db_cuentas()
    fondos_cu = {
        d["nombre"]
        for d in db_cu["ContrapartesAPI"].find(
            {"grupo": "Fondos"}, {"_id": 0, "nombre": 1}
        )
        if d.get("nombre")
    }
    if not fondos_cu:
        return []
    db_t = get_db_titulos()
    emisores_fci = {
        d["emisor"]
        for d in db_t["AssetsAPI"].find(
            {"cartera": "CARTERA FCI"}, {"_id": 0, "emisor": 1}
        )
        if d.get("emisor")
    }
    return sorted(fondos_cu & emisores_fci)


@router.get("/fondos")
@cached(ttl=600)
def listar_fondos():
    """Contrapartes con grupo=Fondos que tienen unidades FCI asociadas."""
    return _fondos_emisores()


@router.get("/flujo-vs-aum")
@cached(ttl=300)
def flujo_vs_aum(
    contraparte: str = Query(..., description="Nombre del fondo (contraparte)"),
    moneda: str = Query("ARS", description="Moneda del flujo (ARS/USD)"),
):
    """Serie mensual de AuM (línea) + flujo operado (barras) para un fondo."""
    db_t = get_db_titulos()
    unidades = [
        d["unidad"]
        for d in db_t["AssetsAPI"].find(
            {"cartera": "CARTERA FCI", "emisor": contraparte},
            {"_id": 0, "unidad": 1},
        )
        if d.get("unidad")
    ]

    aum: list[dict] = []
    if unidades:
        db_p = get_db_portfolio()
        pipeline_aum = [
            {"$match": {"unidad": {"$in": unidades}}},
            {"$project": {
                "_id": 0, "fecha": 1, "valuacion": 1,
                "mes": {"$dateToString": {"format": "%Y-%m", "date": "$fecha"}},
            }},
            {"$group": {
                "_id": {"mes": "$mes", "fecha": "$fecha"},
                "total": {"$sum": "$valuacion"},
            }},
            {"$sort": {"_id.fecha": 1}},
            {"$group": {
                "_id": "$_id.mes",
                "total": {"$last": "$total"},
            }},
            {"$sort": {"_id": 1}},
            {"$project": {"_id": 0, "mes": "$_id", "total": 1}},
        ]
        aum = list(db_p["AumAPI"].aggregate(pipeline_aum))

    db_o = get_db_operaciones()
    pipeline_flujo = [
        {"$match": {"contraparte": contraparte, "moneda": moneda}},
        {"$group": {
            "_id": {"$substr": ["$concertacion", 0, 7]},
            "bruto": {"$sum": "$bruto"},
        }},
        {"$sort": {"_id": 1}},
        {"$project": {"_id": 0, "mes": "$_id", "bruto": 1}},
    ]
    flujo = list(db_o["MesaAPI"].aggregate(pipeline_flujo))

    return {
        "contraparte": contraparte,
        "moneda": moneda,
        "unidades": unidades,
        "aum": aum,
        "flujo": flujo,
    }
