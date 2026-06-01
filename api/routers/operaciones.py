"""Router Operaciones: endpoints para MesaAPI (flujo contrapartes), FlujosAPI
(movimientos) y NegocioMovimientos (vista de negocio del día)."""
import logging
import threading
import time
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from api.cache import cached
from api.db import get_db_cashflow
from api.deps import (
    get_db_cuentas,
    get_db_operaciones,
    get_db_portfolio,
    get_db_titulos,
)
from api.services._cuentas_filter import (
    VALID_FILTERS as _NEGOCIO_CUENTA_FILTROS_VALID,
)
from api.services._cuentas_filter import (
    match_cuenta_filter as _match_cuenta_filter,
)
from api.services._grupos_scope import (
    aplicar_scope_cuenta,
    filtrar_cuentas_str,
    scope_cuentas,
    verificar_cuenta_str,
)
from api.services._negocio_futuros import match_no_futuros

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
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
):
    db = get_db_operaciones()
    filtro: dict = {}
    if cuenta:
        verificar_cuenta_str(cuenta, scope)
        filtro["cuenta"] = cuenta
    else:
        aplicar_scope_cuenta(filtro, scope)
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
                {"cartera": {"$in": ["FCI", "CARTERA FCI"]}}, {"_id": 0, "emisor": 1}
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
                {"cartera": {"$in": ["FCI", "CARTERA FCI"]}, "emisor": contraparte},
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
# Reuso la tupla canónica del módulo de filtros compartido — se actualiza
# sola cuando se suman tipos nuevos (ej. "productores").
_NEGOCIO_CUENTA_FILTROS = _NEGOCIO_CUENTA_FILTROS_VALID

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


def _importe_convertido(moneda: str) -> dict:
    """|importe| convertido a la moneda destino con el `mep` snapshot de CADA
    boleto (conversión histórica exacta — NO al MEP de hoy). Misma moneda →
    directo; ARS→USD → /mep; USD→ARS → ×mep. Si a un boleto de otra moneda le
    falta `mep`, aporta 0 (no se puede convertir sin su mep del día)."""
    abs_imp = {"$abs": {"$ifNull": ["$importe", 0]}}
    mep = {"$ifNull": ["$mep", 0]}
    if (moneda or "ARS").upper() == "USD":
        return {"$cond": [
            {"$eq": ["$moneda", "USD"]},
            abs_imp,
            {"$cond": [{"$gt": [mep, 0]}, {"$divide": [abs_imp, "$mep"]}, 0]},
        ]}
    return {"$cond": [
        {"$eq": ["$moneda", "ARS"]},
        abs_imp,
        {"$multiply": [abs_imp, mep]},
    ]}


def _valor_si_categoria(target: str, conv: dict) -> dict:
    """`conv` (importe ya convertido a la moneda destino) si categoria ==
    target, sino 0. Para los $group por categoría de la serie / matrix."""
    return {"$cond": [{"$eq": ["$categoria", target]}, conv, 0]}


@router.get("/negocio/serie")
@cached(ttl=300)
def negocio_serie(
    moneda: str = Query("ARS", description="Filtra serie por moneda (ARS / USD)"),
    cuenta_filter: str = Query(
        "todas",
        description="Filtro de cuenta: todas | accionistas | sin_accionistas | cooperativas",
    ),
    cuenta: str | None = Query(
        None,
        description="Match exacto sobre cuenta. Si se envía, override del cuenta_filter.",
    ),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
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
        # NO filtra por moneda: entran ARS y USD, y cada boleto se convierte a
        # la moneda destino con su propio mep (ver _importe_convertido). Así el
        # "volumen operado" es el total dolarizado/pesificado, no solo una moneda.
        match_doc: dict = {
            "categoria": {"$in": list(_NEGOCIO_SERIE_BOLETO_CATS)},
            # Futuros DLR (unidad="USDL"): no entran al gráfico de NEGOCIO.
            **match_no_futuros(),
        }
        if cuenta:
            # Match exacto sobre cuenta — override total del cuenta_filter.
            verificar_cuenta_str(cuenta, scope)
            match_doc["cuenta"] = cuenta
        else:
            match_doc.update(_match_cuenta_filter(cuenta_filter))
        aplicar_scope_cuenta(match_doc, scope)
        conv = _importe_convertido(moneda)
        pipeline = [
            {"$match": match_doc},
            {"$group": {
                "_id":         "$fecha",
                "compra":      {"$sum": _valor_si_categoria("compra", conv)},
                "venta":       {"$sum": _valor_si_categoria("venta", conv)},
                "_susc":       {"$sum": _valor_si_categoria("suscripcion_fci", conv)},
                "_sol_susc":   {"$sum": _valor_si_categoria("solicitud_suscripcion_fci", conv)},
                "cauc_tom":    {"$sum": _valor_si_categoria("caucion_tom_ap", conv)},
                "cauc_col":    {"$sum": _valor_si_categoria("caucion_col_ap", conv)},
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
        return {
            "moneda":        moneda,
            "cuenta_filter": cuenta_filter,
            "cuenta":        cuenta,
            "serie":         serie,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "negocio_serie failed: moneda=%s cuenta_filter=%s cuenta=%s",
            moneda, cuenta_filter, cuenta,
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
    cuenta: str | None = Query(
        None,
        description="Match exacto sobre cuenta. Si se envía, override del cuenta_filter.",
    ),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
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
            "categoria": {"$in": boleto_cats},
            **match_no_futuros(),
        }
        if cuenta:
            verificar_cuenta_str(cuenta, scope)
            match_doc["cuenta"] = cuenta
        else:
            match_doc.update(_match_cuenta_filter(cuenta_filter))
        aplicar_scope_cuenta(match_doc, scope)
        pipeline = [
            {"$match": match_doc},
            {"$group": {
                "_id":         "$cuenta",
                "importe_abs": {"$sum": _importe_convertido(moneda)},
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
            "cuenta":        cuenta,
            "cuentas":       rows,
            "total_abs":     round(sum(r["importe_abs"] for r in rows), 2),
            "n_total":       sum(r["n"] for r in rows),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "negocio_cuentas failed: moneda=%s cuenta_filter=%s categoria=%s desde=%s hasta=%s cuenta=%s",
            moneda, cuenta_filter, categoria, desde, hasta, cuenta,
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/negocio/cuentas-matrix")
@cached(ttl=300)
def negocio_cuentas_matrix(
    moneda: str = Query("ARS"),
    cuenta_filter: str = Query("todas"),
    desde: str = Query(...),
    hasta: str = Query(...),
    cuenta: str | None = Query(None, description="Match exacto sobre cuenta — override del cuenta_filter."),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
):
    """Matrix por cuenta × las 5 categorías UI sobre un rango.

    Para el panel DETALLE cuando NO hay categoría seleccionada: cada
    cuenta tiene una fila con sus totales en compra / venta /
    suscripciones / cauc_tom / cauc_col + total + n boletos. Ordenado
    desc por total.

    Mismo $match que /negocio/serie pero $group por cuenta en lugar de
    por fecha. Una pasada en Mongo = todas las celdas.
    """
    if moneda not in _NEGOCIO_MONEDAS_VALIDAS:
        raise HTTPException(400, f"moneda inválida: {moneda!r}")
    if cuenta_filter not in _NEGOCIO_CUENTA_FILTROS:
        raise HTTPException(400, f"cuenta_filter inválido: {cuenta_filter!r}")
    for label, val in (("desde", desde), ("hasta", hasta)):
        try:
            datetime.strptime(val, "%Y-%m-%d")
        except ValueError as e:
            raise HTTPException(400, f"{label} mal formada: {val!r}") from e
    if desde > hasta:
        raise HTTPException(400, f"desde ({desde}) debe ser <= hasta ({hasta})")

    try:
        coll = get_db_cashflow()["NegocioMovimientos"]
        match_doc = {
            "fecha":     {"$gte": desde, "$lte": hasta},
            "categoria": {"$in": list(_NEGOCIO_SERIE_BOLETO_CATS)},
            **match_no_futuros(),
        }
        if cuenta:
            verificar_cuenta_str(cuenta, scope)
            match_doc["cuenta"] = cuenta
        else:
            match_doc.update(_match_cuenta_filter(cuenta_filter))
        aplicar_scope_cuenta(match_doc, scope)
        conv = _importe_convertido(moneda)
        pipeline = [
            {"$match": match_doc},
            {"$group": {
                "_id":        "$cuenta",
                "compra":     {"$sum": _valor_si_categoria("compra", conv)},
                "venta":      {"$sum": _valor_si_categoria("venta", conv)},
                "_susc":      {"$sum": _valor_si_categoria("suscripcion_fci", conv)},
                "_sol_susc":  {"$sum": _valor_si_categoria("solicitud_suscripcion_fci", conv)},
                "cauc_tom":   {"$sum": _valor_si_categoria("caucion_tom_ap", conv)},
                "cauc_col":   {"$sum": _valor_si_categoria("caucion_col_ap", conv)},
                "n":          {"$sum": 1},
            }},
            {"$project": {
                "_id":           0,
                "cuenta":        {"$ifNull": ["$_id", "(sin cuenta)"]},
                "compra":        {"$round": ["$compra", 2]},
                "venta":         {"$round": ["$venta", 2]},
                "suscripciones": {"$round": [{"$add": ["$_susc", "$_sol_susc"]}, 2]},
                "cauc_tom":      {"$round": ["$cauc_tom", 2]},
                "cauc_col":      {"$round": ["$cauc_col", 2]},
                "n":             1,
                "total": {"$round": [
                    {"$add": ["$compra", "$venta", "$_susc", "$_sol_susc", "$cauc_tom", "$cauc_col"]},
                    2,
                ]},
            }},
            {"$sort": {"total": -1}},
        ]
        rows = list(coll.aggregate(pipeline))
        return {
            "moneda":        moneda,
            "cuenta_filter": cuenta_filter,
            "desde":         desde,
            "hasta":         hasta,
            "cuenta":        cuenta,
            "cuentas":       rows,
            "n_cuentas":     len(rows),
            "total":         round(sum(r.get("total", 0) for r in rows), 2),
            "n_total":       sum(r.get("n", 0) for r in rows),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "negocio_cuentas_matrix failed: moneda=%s cuenta_filter=%s desde=%s hasta=%s cuenta=%s",
            moneda, cuenta_filter, desde, hasta, cuenta,
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/negocio/boletos")
@cached(ttl=60)
def negocio_boletos(
    fecha: str = Query(..., description="YYYY-MM-DD"),
    cuenta: str = Query(..., description="Match exacto sobre cuenta"),
    moneda: str = Query("ARS"),
    categoria: str | None = Query(
        None,
        description="Si se envía, solo boletos de esa categoría UI (compra|venta|...)",
    ),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
):
    """Boletos individuales de una cuenta para un día específico.

    Para el inline-expand del DETALLE en modo DIA: el user clickea una
    cuenta en la tabla y ve los boletos que componen su total. Filtrable
    opcionalmente por categoría UI (cuando hay drill-down activo).

    Solo proyecta los campos que la UI muestra (compromiso entre payload
    y utilidad para el operador).
    """
    if moneda not in _NEGOCIO_MONEDAS_VALIDAS:
        raise HTTPException(400, f"moneda inválida: {moneda!r}")
    try:
        datetime.strptime(fecha, "%Y-%m-%d")
    except ValueError as e:
        raise HTTPException(400, f"fecha mal formada: {fecha!r}") from e
    # Scoping de grupos — 403 si la cuenta pedida no está en el alcance.
    verificar_cuenta_str(cuenta, scope)

    boleto_cats: list[str] | None = None
    if categoria is not None:
        if categoria not in _NEGOCIO_UI_CAT_MAP:
            raise HTTPException(
                400,
                f"categoria inválida: {categoria!r} ∉ {list(_NEGOCIO_UI_CAT_MAP)}",
            )
        boleto_cats = _NEGOCIO_UI_CAT_MAP[categoria]

    try:
        coll = get_db_cashflow()["NegocioMovimientos"]
        # NO filtra por moneda: trae ARS y USD, y cada boleto se convierte abajo
        # a la moneda destino con su propio mep (consistente con los totales).
        match_doc: dict = {
            "fecha":  fecha,
            "cuenta": cuenta,
            **match_no_futuros(),
        }
        if boleto_cats is not None:
            match_doc["categoria"] = {"$in": boleto_cats}
        proj = {
            "_id":         0,
            "comprobante": 1,
            "categoria":   1,
            "op":          1,
            "ticker":      1,
            "cantidad":    1,
            "precio":      1,
            "importe":     1,
            "moneda":      1,
            "mep":         1,
            "plazo":       1,
            "lugar":       1,
            "estado":      1,
            "informacion": 1,
        }
        boletos = list(coll.find(match_doc, proj).sort("comprobante", 1))
        # Convertir cada importe a la moneda destino con el mep histórico del
        # boleto (mantiene el signo). `moneda` queda como la moneda ORIGINAL del
        # boleto. Si falta mep en un cross-moneda, importe → None (sin convertir).
        tgt_usd = moneda.upper() == "USD"
        for b in boletos:
            imp = b.get("importe")
            mep = b.get("mep") or 0
            if imp is not None and b.get("moneda") != moneda:
                if tgt_usd:
                    b["importe"] = (imp / mep) if mep else None
                else:
                    b["importe"] = imp * mep
            b.pop("mep", None)
        return {
            "fecha":     fecha,
            "cuenta":    cuenta,
            "moneda":    moneda,
            "categoria": categoria,
            "boletos":   boletos,
            "n":         len(boletos),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "negocio_boletos failed: fecha=%s cuenta=%s moneda=%s categoria=%s",
            fecha, cuenta, moneda, categoria,
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/negocio/cuentas-list")
@cached(ttl=3600)
def negocio_cuentas_list(scope: tuple[str, ...] | None = Depends(scope_cuentas)):
    """Lista de strings `cuenta` distintos en NegocioMovimientos. Sirve
    como fuente del autocomplete de búsqueda de cuenta. Cacheado 1h —
    el set cambia poco (cuentas nuevas son raras). Limitado al scope de
    grupos del usuario."""
    try:
        coll = get_db_cashflow()["NegocioMovimientos"]
        # distinct() es la operación más liviana para esto — Mongo lo
        # resuelve scaneando el índice si existe (cuenta no tiene index
        # standalone, pero el compuesto fecha_cuenta cubre el campo).
        cuentas = sorted(
            c for c in coll.distinct("cuenta") if c
        )
        cuentas = filtrar_cuentas_str(cuentas, scope)
        return {"cuentas": cuentas, "n": len(cuentas)}
    except Exception as e:
        logger.exception("negocio_cuentas_list failed")
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


# ── COMERCIAL (lente por operador, estilo NEGOCIO) ───────────────────────────
# Vista nueva en OPERACIONES. Lógica en api/services/comercial.py.

@router.get("/comercial/operadores")
def comercial_operadores() -> list[dict]:
    """Operadores para el selector (email, nombre, # cuentas)."""
    from api.services.comercial import listar_operadores_comercial
    return listar_operadores_comercial()


@router.get("/comercial/operador")
def comercial_operador(
    operador: str = Query(..., description="operador_email"),
    moneda: str = Query("ARS"),
) -> dict:
    """Resumen (KPIs) + clientes (tabla + ficha) del operador, en una pasada."""
    from api.services.comercial import operador_comercial
    return operador_comercial(operador=operador, moneda=moneda)


@router.get("/comercial/serie")
def comercial_serie(
    operador: str = Query(..., description="operador_email"),
    metric: str = Query("volumen", description="volumen | aum"),
    moneda: str = Query("ARS"),
    id_cuenta: str | None = Query(None, description="scope a una sola cuenta (interactivo)"),
) -> dict:
    """Serie para el gráfico. Sin id_cuenta → operador; con id_cuenta → cliente."""
    from api.services.comercial import serie_comercial
    return serie_comercial(operador=operador, metric=metric, moneda=moneda, id_cuenta=id_cuenta)


@router.get("/comercial/portafolio")
def comercial_portafolio(
    id_cuenta: str = Query(..., description="id de la cuenta comitente"),
) -> dict:
    """Tenencia del cliente (posiciones de AuM, último snapshot)."""
    from api.services.comercial import portafolio_cliente
    return portafolio_cliente(id_cuenta=id_cuenta)


@router.get("/comercial/operaciones")
def comercial_operaciones(
    id_cuenta: str = Query(..., description="id de la cuenta comitente"),
    limite: int = Query(300, ge=1, le=1000),
) -> dict:
    """Operaciones recientes del cliente (boletos operativos, fecha desc)."""
    from api.services.comercial import operaciones_cliente
    return operaciones_cliente(id_cuenta=id_cuenta, limite=limite)


@router.get("/comercial/analisis")
def comercial_analisis(
    operador: str = Query(..., description="operador_email"),
    moneda: str = Query("ARS", description="ARS | USD"),
) -> dict:
    """Dataset de la vista ANÁLISIS: clientes del operador con estado comercial,
    AuM, última op y niveles de segmentación (estado / churn / distribución)."""
    from api.services.comercial import analisis_comercial
    return analisis_comercial(operador=operador, moneda=moneda)


@router.get("/comercial/actividad-historica")
def comercial_actividad_historica(
    operador: str = Query(..., description="operador_email o '__todos__' (toda la mesa)"),
    desde: str | None = Query(None, description="mes YYYY-MM inclusive"),
    hasta: str | None = Query(None, description="mes YYYY-MM inclusive"),
    moneda: str = Query("ARS", description="ARS | USD"),
) -> dict:
    """Serie mensual de cuentas activas (operaron en el mes calendario) +
    volumen, desde el snapshot `Clientes.ActividadMensual`. Scopeable por
    operador o toda la mesa."""
    from api.services.comercial import actividad_historica
    return actividad_historica(operador=operador, desde=desde, hasta=hasta, moneda=moneda)


# ── INFORME (global, transversal a toda la mesa — no por operador) ───────────

@router.get("/comercial/informe")
def comercial_informe(
    moneda: str = Query("ARS", description="ARS | USD"),
) -> dict:
    """Tablas 2 y 3 del Informe: volumen + aranceles por comercial (ranking) y
    aranceles por segmento. Global (toda la mesa)."""
    from api.services.comercial import informe_comercial
    return informe_comercial(moneda=moneda)


@router.get("/comercial/informe-segmento")
def comercial_informe_segmento(
    hasta: str | None = Query(None, description="mes YYYY-MM (default actual); acumulado a fin de mes"),
    operador: str | None = Query(None, description="opcional: solo cuentas de ese comercial"),
) -> dict:
    """Tabla 1 del Informe: # cuentas por segmento (nivel_1), acumulado a fin del
    mes `hasta` por fecha de alta. `operador` opcional re-scopea a ese comercial."""
    from api.services.comercial import informe_cuentas_por_segmento
    return informe_cuentas_por_segmento(hasta=hasta, operador=operador)


@router.get("/comercial/informe-aranceles-segmento")
def comercial_informe_aranceles_segmento(
    operador: str = Query(..., description="operador_email a desglosar"),
    moneda: str = Query("ARS", description="ARS | USD"),
) -> dict:
    """Q3 re-scopeada a un comercial: aranceles + ticket por segmento, solo de
    sus cuentas."""
    from api.services.comercial import informe_aranceles_segmento
    return informe_aranceles_segmento(operador=operador, moneda=moneda)


@router.get("/comercial/informe-segmento-detalle")
def comercial_informe_segmento_detalle(
    segmento: str = Query("todos", description="nivel_1 a desglosar; 'todos' = todos los segmentos"),
    operador: str | None = Query(None, description="opcional: solo cuentas de ese comercial"),
    moneda: str = Query("ARS", description="ARS | USD"),
) -> dict:
    """Detalle de un segmento (Q4 dinámica): clientes con su arancel +
    operaciones (boletos con arancel) que lo generaron. `segmento='todos'` →
    todos los segmentos (vista por defecto). `operador` opcional."""
    from api.services.comercial import informe_segmento_detalle
    return informe_segmento_detalle(segmento=segmento, operador=operador, moneda=moneda)
