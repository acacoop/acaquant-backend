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
_NEGOCIO_CUENTA_FILTROS = ("todas", "accionistas", "sin_accionistas", "cooperativas")
# Mismo regex que cashflow-view.tsx (case-insensitive sobre el nombre de cuenta).
_COOP_REGEX = r"\bcoop"

# Mapeo de categoría UI (lo que el frontend usa en NEGOCIO_CATS) a las
# categorías persistidas en el boleto. Mantener en sync con CAT_BOLETO_KEYS
# de acaquant-web/src/components/negocio-view.tsx.
_NEGOCIO_UI_CAT_MAP: dict[str, list[str]] = {
    "compra":        ["compra"],
    "venta":         ["venta"],
    "suscripciones": ["suscripcion_fci", "solicitud_suscripcion_fci"],
    "cauc_tom":      ["caucion_tom_ap"],
    "cauc_col":      ["caucion_col_ap"],
}


def _abs_si_categoria(target: str) -> dict:
    """Helper para el pipeline: $abs(importe) si categoria == target, sino 0."""
    return {"$cond": [
        {"$eq": ["$categoria", target]},
        {"$abs": {"$ifNull": ["$importe", 0]}},
        0,
    ]}


def _cuentas_accionistas() -> list[str]:
    """Lista de strings `cuenta` de Cuentas.AccionistasAPI. Cacheada via
    `listar_accionistas` (ttl=3600), así que esta llamada es efectivamente
    barata. Cada doc puede o no traer `cuenta` poblado."""
    db = get_db_cuentas()
    return [
        d["cuenta"]
        for d in db["AccionistasAPI"].find({}, {"_id": 0, "cuenta": 1})
        if d.get("cuenta")
    ]


def _match_cuenta_filter(filtro: str) -> dict:
    """Devuelve el sub-doc de $match que aplica el filtro de cuenta.

    - todas: sin filtro extra.
    - accionistas: cuenta IN lista de AccionistasAPI.
    - sin_accionistas: cuenta NOT IN lista (incluye nulls).
    - cooperativas: cuenta NOT IN lista AND match regex /\\bcoop/i.
    """
    if filtro == "todas":
        return {}
    accs = _cuentas_accionistas()
    if filtro == "accionistas":
        return {"cuenta": {"$in": accs}}
    if filtro == "sin_accionistas":
        return {"cuenta": {"$nin": accs}}
    if filtro == "cooperativas":
        return {
            "cuenta": {
                "$nin": accs,
                "$regex": _COOP_REGEX,
                "$options": "i",
            },
        }
    return {}


@router.get("/negocio/serie")
@cached(ttl=300)
def negocio_serie(
    moneda: str = Query("ARS", description="Filtra serie por moneda (ARS / USD)"),
    cuenta_filter: str = Query(
        "todas",
        description="Filtro de cuenta: todas | accionistas | sin_accionistas | cooperativas",
    ),
):
    """Serie diaria del importe absoluto por categoría, agregada server-side.

    Devuelve una fila por día en orden ascendente con los totales por
    categoría (5 buckets) — el bar chart de /operaciones/negocio consume
    esto directo. Solo días con boletos en la moneda + filtro seleccionados
    aparecen.

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
    if cuenta_filter not in _NEGOCIO_CUENTA_FILTROS:
        raise HTTPException(
            status_code=400,
            detail=f"cuenta_filter inválido: {cuenta_filter!r} ∉ {_NEGOCIO_CUENTA_FILTROS}",
        )
    try:
        coll = get_db_cashflow()["NegocioMovimientos"]
        match_doc = {
            "moneda": moneda,
            "categoria": {"$in": list(_NEGOCIO_SERIE_BOLETO_CATS)},
            **_match_cuenta_filter(cuenta_filter),
        }
        pipeline = [
            {"$match": match_doc},
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
        return {"moneda": moneda, "cuenta_filter": cuenta_filter, "serie": serie}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "negocio_serie failed for moneda=%s cuenta_filter=%s",
            moneda, cuenta_filter,
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/negocio/cuentas")
@cached(ttl=300)
def negocio_cuentas(
    moneda: str = Query("ARS", description="ARS / USD"),
    cuenta_filter: str = Query("todas", description="todas|accionistas|sin_accionistas|cooperativas"),
    categoria: str = Query(..., description="categoría UI: compra|venta|suscripciones|cauc_tom|cauc_col"),
    desde: str = Query(..., description="YYYY-MM-DD inclusive"),
    hasta: str = Query(..., description="YYYY-MM-DD inclusive"),
):
    """Totales acumulados por cuenta para una categoría UI sobre un rango.

    Para el panel DETALLE de /operaciones/negocio: cuando el usuario clickea
    una categoría, este endpoint devuelve el desglose por cuenta sumando
    todos los días en [desde, hasta] (inclusive), respetando moneda +
    cuenta_filter. Ordenado desc por |importe| (mayor → menor).

    Una categoría UI puede mapear a múltiples categorías de boleto (ej.
    suscripciones = suscripcion_fci + solicitud_suscripcion_fci). Esto se
    resuelve server-side via _NEGOCIO_UI_CAT_MAP.
    """
    if moneda not in _NEGOCIO_MONEDAS_VALIDAS:
        raise HTTPException(400, f"moneda inválida: {moneda!r}")
    if cuenta_filter not in _NEGOCIO_CUENTA_FILTROS:
        raise HTTPException(400, f"cuenta_filter inválido: {cuenta_filter!r}")
    if categoria not in _NEGOCIO_UI_CAT_MAP:
        raise HTTPException(
            400,
            f"categoria inválida: {categoria!r} ∉ {list(_NEGOCIO_UI_CAT_MAP)}",
        )
    # Validación cheap del shape ISO (no tipo strict — Mongo compara strings).
    for label, val in (("desde", desde), ("hasta", hasta)):
        try:
            datetime.strptime(val, "%Y-%m-%d")
        except ValueError as e:
            raise HTTPException(400, f"{label} mal formada: {val!r}") from e
    if desde > hasta:
        raise HTTPException(400, f"desde ({desde}) debe ser <= hasta ({hasta})")

    boleto_cats = _NEGOCIO_UI_CAT_MAP[categoria]
    try:
        coll = get_db_cashflow()["NegocioMovimientos"]
        match_doc = {
            "fecha":     {"$gte": desde, "$lte": hasta},
            "moneda":    moneda,
            "categoria": {"$in": boleto_cats},
            **_match_cuenta_filter(cuenta_filter),
        }
        pipeline = [
            {"$match": match_doc},
            {"$group": {
                "_id":         "$cuenta",
                "importe_abs": {"$sum": {"$abs": {"$ifNull": ["$importe", 0]}}},
                "n":           {"$sum": 1},
            }},
            {"$sort": {"importe_abs": -1}},
            {"$project": {
                "_id":         0,
                "cuenta":      {"$ifNull": ["$_id", "(sin cuenta)"]},
                "importe_abs": {"$round": ["$importe_abs", 2]},
                "n":           1,
            }},
        ]
        rows = list(coll.aggregate(pipeline))
        return {
            "moneda":        moneda,
            "cuenta_filter": cuenta_filter,
            "categoria":     categoria,
            "desde":         desde,
            "hasta":         hasta,
            "cuentas":       rows,
            "total_abs":     round(sum(r["importe_abs"] for r in rows), 2),
            "n_total":       sum(r["n"] for r in rows),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "negocio_cuentas failed: moneda=%s cuenta_filter=%s categoria=%s desde=%s hasta=%s",
            moneda, cuenta_filter, categoria, desde, hasta,
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/negocio")
@cached(ttl=60)
def negocio(
    fecha: str | None = Query(None, description="YYYY-MM-DD; default: hoy ART"),
):
    """Metadata del día — solo n_boletos + ultima_ingesta + n_categorias.

    Antes este endpoint devolvía el array completo de boletos del día
    (~700-1500 docs) + agregados precomputados + top_tickers, pero el
    rediseño de /operaciones/negocio dejó de usar todo eso: la vista lee
    serie + cuentas vía /negocio/serie y /negocio/cuentas, y de acá solo
    consume meta. Calcular el shape viejo costaba ~800ms con la base ya
    grande post-backfill — ahora es un único $group cubierto por el index
    en `fecha`.
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
        pipeline = [
            {"$match": {"fecha": fecha_iso}},
            {"$group": {
                "_id":             None,
                "n_boletos":       {"$sum": 1},
                "ultima_ingesta":  {"$max": "$ingestado_en"},
                "categorias":      {"$addToSet": "$categoria"},
            }},
        ]
        rows = list(coll.aggregate(pipeline))
        if rows:
            r = rows[0]
            n_boletos = r.get("n_boletos") or 0
            n_categorias = len(r.get("categorias") or [])
            ts = r.get("ultima_ingesta")
            ultima_ingesta = ts.isoformat() if isinstance(ts, datetime) else None
        else:
            n_boletos, n_categorias, ultima_ingesta = 0, 0, None
        return {
            "meta": {
                "fecha":          fecha_iso,
                "n_boletos":      n_boletos,
                "n_categorias":   n_categorias,
                "ultima_ingesta": ultima_ingesta,
            },
        }
    except Exception as e:
        logger.exception("negocio failed for fecha=%s", fecha_iso)
        raise HTTPException(status_code=500, detail=str(e)) from e
