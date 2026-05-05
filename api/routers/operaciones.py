"""Router Operaciones: endpoints para MesaAPI (flujo contrapartes), FlujosAPI
(movimientos) y NegocioMovimientos (vista de negocio del día)."""
import logging
import threading
import time
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query

from api.cache import cached
from api.db import get_db_cashflow
from api.deps import (
    get_db_cuentas,
    get_db_operaciones,
    get_db_portfolio,
    get_db_titulos,
)

logger = logging.getLogger("api.operaciones")

# Cache in-process para helpers sin parámetros (datos que cambian ≤1 vez/semana)
_fondos_cache_data: list | None = None
_fondos_cache_ts: float = 0.0
_fondos_lock = threading.Lock()
_FONDOS_TTL = 600

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
    """Lista de emisores de CashFlow con grupo=Fondos que tienen al menos un asset FCI.

    Cacheado 10 min en proceso — estos datos cambian como mucho una vez por semana.
    """
    global _fondos_cache_data, _fondos_cache_ts
    now = time.time()
    with _fondos_lock:
        if _fondos_cache_data is not None and now < _fondos_cache_ts:
            return _fondos_cache_data

    db_cu = get_db_cuentas()
    fondos_cu = {
        d["nombre"]
        for d in db_cu["ContrapartesAPI"].find(
            {"grupo": "Fondos"}, {"_id": 0, "nombre": 1}
        )
        if d.get("nombre")
    }
    result: list[str] = []
    if fondos_cu:
        db_t = get_db_titulos()
        emisores_fci = {
            d["emisor"]
            for d in db_t["AssetsAPI"].find(
                {"cartera": "CARTERA FCI"}, {"_id": 0, "emisor": 1}
            )
            if d.get("emisor")
        }
        result = sorted(fondos_cu & emisores_fci)

    with _fondos_lock:
        _fondos_cache_data = result
        _fondos_cache_ts = now + _FONDOS_TTL
    return result


@router.get("/fondos")
@cached(ttl=600)
def listar_fondos():
    """Contrapartes con grupo=Fondos que tienen unidades FCI asociadas."""
    try:
        return _fondos_emisores()
    except Exception as e:
        logger.exception("listar_fondos failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/flujo-vs-aum")
@cached(ttl=300)
def flujo_vs_aum(
    contraparte: str = Query(..., description="Nombre del fondo (contraparte)"),
    moneda: str = Query("ARS", description="Moneda del flujo (ARS/USD)"),
):
    """Serie mensual de AuM (línea) + flujo operado (barras) para un fondo.

    Agrupación server-side via $group pipeline para evitar traer docs en bulk.
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
            # Agrupación server-side: suma por fecha → toma la más reciente por mes
            pipeline_aum = [
                {"$match": {"unidad": {"$in": unidades}, "valuacion": {"$ne": None}}},
                {"$group": {"_id": "$fecha", "total": {"$sum": "$valuacion"}}},
                {"$sort": {"_id": 1}},
                {"$group": {
                    "_id": {"$dateToString": {"format": "%Y-%m", "date": "$_id"}},
                    "total": {"$last": "$total"},
                }},
                {"$sort": {"_id": 1}},
                {"$project": {"_id": 0, "mes": "$_id", "total": 1}},
            ]
            aum = list(db_p["AumAPI"].aggregate(pipeline_aum))

        db_o = get_db_operaciones()
        pipeline_flujo = [
            {"$match": {
                "contraparte": contraparte,
                "moneda": moneda,
                "concertacion": {"$type": "string"},
            }},
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
    except Exception as e:
        logger.exception("flujo_vs_aum failed for contraparte=%s", contraparte)
        raise HTTPException(status_code=500, detail=str(e)) from e


# ─────────────────────────────────────────────────────────────────────────────
# NEGOCIO — vista gerencial del día (lee de CashFlow.NegocioMovimientos,
# poblado por jobs/negocio_movimientos.py cada hora 12-22 ART L-V)
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/negocio/fechas")
def negocio_fechas():
    """Lista de fechas distintas con boletos persistidos, ordenadas
    descendente. Usado por el frontend para limitar el selector de fecha
    a días con data real."""
    try:
        coll = get_db_cashflow()["NegocioMovimientos"]
        pipeline = [
            {"$group": {"_id": "$fecha", "n": {"$sum": 1}}},
            {"$sort": {"_id": -1}},
        ]
        rows = list(coll.aggregate(pipeline))
        return {
            "fechas": [{"fecha": r["_id"], "n": r["n"]} for r in rows if r.get("_id")],
        }
    except Exception as e:
        logger.exception("negocio_fechas failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


# Categorías de boleto que entran al chart de la vista gerencial. Algunas
# se combinan en una sola serie para el front (suscripciones = susc + sol_susc).
_NEGOCIO_SERIE_BOLETO_CATS = (
    "compra", "venta",
    "suscripcion_fci", "solicitud_suscripcion_fci",
    "caucion_tom_ap", "caucion_col_ap",
)
_NEGOCIO_MONEDAS_VALIDAS = ("ARS", "USD")


def _abs_si_categoria(target: str) -> dict:
    """Helper para el pipeline: $abs(importe) si categoria == target, sino 0."""
    return {"$cond": [
        {"$eq": ["$categoria", target]},
        {"$abs": {"$ifNull": ["$importe", 0]}},
        0,
    ]}


@router.get("/negocio/serie")
@cached(ttl=300)
def negocio_serie(
    moneda: str = Query("ARS", description="Filtra serie por moneda (ARS / USD)"),
):
    """Serie diaria del importe absoluto por categoría, agregada server-side.

    Devuelve una fila por día en orden ascendente con los totales por
    categoría (5 buckets) — el bar chart de /operaciones/negocio consume
    esto directo. Solo días con boletos en la moneda seleccionada aparecen.

    Buckets devueltos:
      compra, venta — directos.
      suscripciones — suma de suscripcion_fci + solicitud_suscripcion_fci.
      cauc_tom — caucion_tom_ap (apertura).
      cauc_col — caucion_col_ap (apertura).
    """
    if moneda not in _NEGOCIO_MONEDAS_VALIDAS:
        raise HTTPException(
            status_code=400,
            detail=f"moneda inválida: {moneda!r} ∉ {_NEGOCIO_MONEDAS_VALIDAS}",
        )
    try:
        coll = get_db_cashflow()["NegocioMovimientos"]
        pipeline = [
            {"$match": {
                "moneda": moneda,
                "categoria": {"$in": list(_NEGOCIO_SERIE_BOLETO_CATS)},
            }},
            {"$group": {
                "_id":         "$fecha",
                "compra":      {"$sum": _abs_si_categoria("compra")},
                "venta":       {"$sum": _abs_si_categoria("venta")},
                "_susc":       {"$sum": _abs_si_categoria("suscripcion_fci")},
                "_sol_susc":   {"$sum": _abs_si_categoria("solicitud_suscripcion_fci")},
                "cauc_tom":    {"$sum": _abs_si_categoria("caucion_tom_ap")},
                "cauc_col":    {"$sum": _abs_si_categoria("caucion_col_ap")},
            }},
            {"$sort": {"_id": 1}},
            {"$project": {
                "_id":          0,
                "fecha":        "$_id",
                "compra":       {"$round": ["$compra", 2]},
                "venta":        {"$round": ["$venta", 2]},
                "suscripciones": {"$round": [{"$add": ["$_susc", "$_sol_susc"]}, 2]},
                "cauc_tom":     {"$round": ["$cauc_tom", 2]},
                "cauc_col":     {"$round": ["$cauc_col", 2]},
            }},
        ]
        serie = list(coll.aggregate(pipeline))
        return {"moneda": moneda, "serie": serie}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("negocio_serie failed for moneda=%s", moneda)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/negocio")
def negocio(
    fecha: str | None = Query(None, description="YYYY-MM-DD; default: hoy ART"),
):
    """Lee CashFlow.NegocioMovimientos del día y devuelve agregados por
    segmento + lista completa de boletos.

    NO pega a Aunesa — la data la pobla el cron del job. Si el día no
    tiene data todavía (fuera de horario o cron caído), devuelve
    estructuras vacías sin error.
    """
    if fecha:
        try:
            d = datetime.strptime(fecha, "%Y-%m-%d").date()
        except ValueError as e:
            raise HTTPException(status_code=400,
                                detail=f"fecha mal formada: {fecha}") from e
    else:
        d = (datetime.now(UTC) - timedelta(hours=3)).date()
    fecha_iso = d.isoformat()

    try:
        coll = get_db_cashflow()["NegocioMovimientos"]
        boletos = list(coll.find(
            {"fecha": fecha_iso},
            {"_id": 0},
        ).sort([("categoria", 1), ("cuenta", 1), ("comprobante", 1)]))

        # Última hora de ingesta (para mostrar "última actualización" en UI).
        ultima_ingesta = None
        if boletos:
            timestamps = [b.get("ingestado_en") for b in boletos if b.get("ingestado_en")]
            if timestamps:
                ultima_ingesta = max(timestamps)

        # Agregados por categoría (para cards gerenciales).
        agregados: dict[str, dict] = {}
        for b in boletos:
            cat = b.get("categoria") or "otro"
            entry = agregados.setdefault(cat, {
                "categoria":     cat,
                "n":             0,
                "importe_neto":  0.0,
                "importe_abs":   0.0,
                "n_cuentas":     set(),
                "n_tickers":     set(),
                "monedas":       set(),
            })
            entry["n"] += 1
            imp = b.get("importe") or 0
            try:
                imp_f = float(imp)
            except (TypeError, ValueError):
                imp_f = 0.0
            entry["importe_neto"] += imp_f
            entry["importe_abs"]  += abs(imp_f)
            if b.get("cuenta"):
                entry["n_cuentas"].add(b["cuenta"])
            if b.get("ticker"):
                entry["n_tickers"].add(b["ticker"])
            if b.get("moneda"):
                entry["monedas"].add(b["moneda"])

        # Convertir sets a listas/counts para JSON.
        agregados_list = []
        for e in agregados.values():
            agregados_list.append({
                "categoria":     e["categoria"],
                "n":             e["n"],
                "importe_neto":  round(e["importe_neto"], 2),
                "importe_abs":   round(e["importe_abs"], 2),
                "n_cuentas":     len(e["n_cuentas"]),
                "n_tickers":     len(e["n_tickers"]),
                "monedas":       sorted(e["monedas"]),
            })
        agregados_list.sort(key=lambda x: -x["importe_abs"])

        # Top tickers por volumen abs.
        ticker_vol: dict[str, dict] = {}
        for b in boletos:
            t = b.get("ticker")
            if not t:
                continue
            entry = ticker_vol.setdefault(t, {"ticker": t, "n": 0, "importe_abs": 0.0})
            entry["n"] += 1
            entry["importe_abs"] += abs(float(b.get("importe") or 0))
        top_tickers = sorted(
            ticker_vol.values(),
            key=lambda x: -x["importe_abs"],
        )[:20]

        return {
            "meta": {
                "fecha":           fecha_iso,
                "n_boletos":       len(boletos),
                "n_categorias":    len(agregados_list),
                "ultima_ingesta":  (
                    ultima_ingesta.isoformat() if isinstance(ultima_ingesta, datetime)
                    else None
                ),
            },
            "agregados":   agregados_list,
            "top_tickers": top_tickers,
            "boletos":     boletos,
        }
    except Exception as e:
        logger.exception("negocio failed for fecha=%s", fecha_iso)
        raise HTTPException(status_code=500, detail=str(e)) from e
