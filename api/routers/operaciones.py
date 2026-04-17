"""Router Operaciones: endpoints para MesaAPI (flujo contrapartes) y FlujosAPI (movimientos)."""
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from api.cache import cached
from api.deps import (
    get_db_cuentas,
    get_db_operaciones,
    get_db_portfolio,
    get_db_titulos,
)

logger = logging.getLogger("api.operaciones")

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
    try:
        return _fondos_emisores()
    except Exception as e:
        logger.exception("listar_fondos failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


def _mes_key(value) -> str | None:
    """Normaliza fecha a 'YYYY-MM'. Acepta datetime, date-like o string YYYY-MM-DD."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m")
    s = str(value)
    return s[:7] if len(s) >= 7 else None


@router.get("/flujo-vs-aum")
@cached(ttl=300)
def flujo_vs_aum(
    contraparte: str = Query(..., description="Nombre del fondo (contraparte)"),
    moneda: str = Query("ARS", description="Moneda del flujo (ARS/USD)"),
):
    """Serie mensual de AuM (línea) + flujo operado (barras) para un fondo.

    Agrupación en Python para tolerar docs con fecha null/inválida.
    """
    try:
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
            docs = db_p["AumAPI"].find(
                {"unidad": {"$in": unidades}},
                {"_id": 0, "fecha": 1, "valuacion": 1},
            )
            # (mes, fecha_exacta) → suma valuacion; luego por mes tomamos la última fecha
            por_mes_fecha: dict[tuple[str, datetime], float] = {}
            for d in docs:
                mes = _mes_key(d.get("fecha"))
                if not mes:
                    continue
                val = d.get("valuacion")
                if val is None:
                    continue
                key = (mes, d["fecha"])
                por_mes_fecha[key] = por_mes_fecha.get(key, 0.0) + float(val)

            # Último total del mes (por fecha más reciente dentro del mes)
            por_mes: dict[str, tuple[datetime, float]] = {}
            for (mes, fecha), total in por_mes_fecha.items():
                prev = por_mes.get(mes)
                if prev is None or fecha > prev[0]:
                    por_mes[mes] = (fecha, total)
            aum = [
                {"mes": mes, "total": total}
                for mes, (_, total) in sorted(por_mes.items())
            ]

        db_o = get_db_operaciones()
        docs_flujo = db_o["MesaAPI"].find(
            {"contraparte": contraparte, "moneda": moneda},
            {"_id": 0, "concertacion": 1, "bruto": 1},
        )
        flujo_map: dict[str, float] = {}
        for d in docs_flujo:
            mes = _mes_key(d.get("concertacion"))
            if not mes:
                continue
            flujo_map[mes] = flujo_map.get(mes, 0.0) + float(d.get("bruto") or 0)
        flujo = [{"mes": m, "bruto": v} for m, v in sorted(flujo_map.items())]

        return {
            "contraparte": contraparte,
            "moneda": moneda,
            "unidades": unidades,
            "aum": aum,
            "flujo": flujo,
        }
    except Exception as e:
        logger.exception("flujo_vs_aum failed for contraparte=%s", contraparte)
        raise HTTPException(status_code=500, detail=str(e)) from e
