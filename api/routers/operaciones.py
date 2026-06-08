"""Router Operaciones: endpoints para MesaAPI (flujo contrapartes), FlujosAPI
(movimientos) y NegocioMovimientos (vista de negocio del día)."""
import logging
import os
import threading
import time
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from api.cache import cached
from api.db import get_db_cashflow, get_db_clientes, get_db_valuaciones
from api.services import comercial as _com
from api.services import comercial_sql as _com_sql
from api.services import negocio_sql as _neg_sql
from api.services import operaciones_sql as _ops_sql
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
from api.services.titulos_flujos import assets_normalizados

logger = logging.getLogger("api.operaciones")

# Cache in-process para helpers sin parámetros (datos que cambian ≤1 vez/semana)
_fondos_cache_data: list | None = None
_fondos_cache_ts: float = 0.0
_fondos_lock = threading.Lock()
_FONDOS_TTL = 600

router = APIRouter(prefix="/api/operaciones", tags=["Operaciones"])

def _ddmmyyyy_a_iso(raw: str | None) -> str | None:
    """'02/07/2025' (dd/mm/yyyy) → '2025-07-02'. None si no parsea.
    CashFlow.Movimientos guarda la fecha en dd/mm/yyyy; la API la sirve en ISO."""
    try:
        return datetime.strptime((raw or "").strip(), "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


@router.get("/flujo")
@cached(ttl=300)
def listar_flujo(
    contraparte: str | None = Query(None, description="Filtrar por contraparte"),
    moneda: str | None = Query(None, description="Filtrar por moneda (ARS/USD)"),
    segmento: str | None = Query(None, description="Filtrar por segmento (sesión de mercado)"),
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
):
    """Flujo de contrapartes — DIRECTO desde CashFlow.Operaciones (match por
    `cuenta` con CashFlow.Contrapartes). Reemplaza la copia intermedia MesaAPI:
    trae `tipoOperacion` y `cuenta` reales (que MesaAPI no tenía) y el `segmento`
    (sesión de mercado) se deriva del tipo_operacion. Excluye Futuros/Opciones."""
    dbc = get_db_cashflow()
    # cuenta (id) → {contraparte, grupo}. La cuenta es la CLAVE de match.
    cp_map = {
        str(d.get("cuenta")).strip(): {
            "contraparte": d.get("contraparte") or "",
            "grupo": d.get("segmento") or "",
        }
        for d in dbc["Contrapartes"].find(
            {"cuenta": {"$exists": True, "$ne": ""}},
            {"_id": 0, "cuenta": 1, "contraparte": 1, "segmento": 1},
        )
        if d.get("cuenta") not in (None, "")
    }
    # Excluye Futuros/Opciones y las Caución COLOCADORA (apertura+cierre): vienen
    # en pares y duplican/ensucian la vista. La caución tomadora se mantiene.
    match: dict = {
        "cuenta": {"$in": list(cp_map)},
        "tipo_operacion": {"$not": {"$regex": "Futuros|Opciones|colocadora", "$options": "i"}},
    }
    if moneda:
        match["moneda"] = moneda
    if desde or hasta:
        rango = {}
        if desde:
            rango["$gte"] = desde
        if hasta:
            rango["$lte"] = hasta
        match["concertacion"] = rango

    proj = {"_id": 0, "boleto": 1, "concertacion": 1, "tipo_operacion": 1, "cuenta": 1,
            "denominacion": 1, "instrumento": 1, "bruto": 1, "moneda": 1}
    out = []
    for d in dbc["Operaciones"].find(match, proj).sort("concertacion", 1):
        cuenta = str(d.get("cuenta") or "").strip()
        cp = cp_map.get(cuenta, {})
        tipo = d.get("tipo_operacion") or ""
        seg = tipo.split()[0] if tipo else ""  # sesión de mercado (Concurrencia/SENEBI/…)
        if contraparte and cp.get("contraparte") != contraparte:
            continue
        if segmento and seg != segmento:
            continue
        out.append({
            "boleto":        d.get("boleto"),
            "concertacion":  d.get("concertacion"),
            "tipoOperacion": tipo,
            "cuenta":        cuenta,
            "denominacion":  d.get("denominacion"),
            "unidad":        d.get("instrumento"),
            "bruto":         d.get("bruto"),
            "segmento":      seg,
            "contraparte":   cp.get("contraparte"),
            "moneda":        d.get("moneda"),
        })
    return out


@router.get("/flujos")
@cached(ttl=300)
def listar_flujos(
    cuenta: str | None = Query(None, description="Filtrar por cuenta (formato [N] NOMBRE)"),
    unidad: str | None = Query(None, description="Filtrar por moneda (ARS/USD)"),
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
):
    # DIRECTO desde CashFlow.Movimientos (sin el espejo OperacionesAPI.FlujosAPI):
    # comprobante→boleto, total→bruto, fecha(dd/mm/yyyy)→concertacion(iso). El
    # rango de fechas y el orden se resuelven en Python porque la fecha está en
    # dd/mm/yyyy (no ordenable como string en Mongo). El set es chico (~12k docs,
    # casi siempre filtrado por cuenta).
    db = get_db_cashflow()
    filtro: dict = {}
    if cuenta:
        verificar_cuenta_str(cuenta, scope)
        filtro["cuenta"] = cuenta
    else:
        aplicar_scope_cuenta(filtro, scope)
    if unidad:
        filtro["unidad"] = unidad

    proj = {"_id": 0, "comprobante": 1, "cuenta": 1, "fecha": 1,
            "informacion": 1, "total": 1, "unidad": 1}
    out = []
    for d in db["Movimientos"].find(filtro, proj):
        iso = _ddmmyyyy_a_iso(d.get("fecha"))
        if desde and (iso is None or iso < desde):
            continue
        if hasta and (iso is None or iso > hasta):
            continue
        out.append({
            "boleto":       d.get("comprobante"),
            "concertacion": iso,
            "cuenta":       d.get("cuenta"),
            "informacion":  d.get("informacion"),
            "bruto":        d.get("total"),
            "unidad":       d.get("unidad"),
        })
    out.sort(key=lambda r: r["concertacion"] or "")
    return out


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

    # DIRECTO desde CashFlow.Contrapartes (sin el espejo CuentasAPI.ContrapartesAPI):
    # grupo=segmento, nombre=contraparte.
    db_cf = get_db_cashflow()
    fondos_cu = {
        d["contraparte"]
        for d in db_cf["Contrapartes"].find(
            {"segmento": "Fondos"}, {"_id": 0, "contraparte": 1}
        )
        if d.get("contraparte")
    }
    result: list[str] = []
    if fondos_cu:
        # DIRECTO desde Valuaciones.Assets (vía servicio): emisores con cartera FCI.
        emisores_fci = {
            d["emisor"]
            for d in assets_normalizados()
            if d.get("cartera") in ("FCI", "CARTERA FCI") and d.get("emisor")
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
        # DIRECTO desde Valuaciones.Assets (vía servicio): unidades FCI del emisor.
        unidades = [
            d["unidad"]
            for d in assets_normalizados()
            if d.get("cartera") in ("FCI", "CARTERA FCI")
            and d.get("emisor") == contraparte and d.get("unidad")
        ]

        aum: list[dict] = []
        if unidades:
            # DIRECTO desde Valuaciones.AuM (sin el espejo PortfolioAPI.AumAPI):
            # fecha_snapshot es string 'YYYY-MM-DD' → mes = substr(0,7).
            db_v = get_db_valuaciones()
            pipeline_aum = [
                {"$match": {"unidad": {"$in": unidades}, "valuacion": {"$ne": None}}},
                {"$group": {"_id": "$fecha_snapshot", "total": {"$sum": "$valuacion"}}},
                {"$sort": {"_id": 1}},
                {"$group": {
                    "_id": {"$substr": ["$_id", 0, 7]},
                    "total": {"$last": "$total"},
                }},
                {"$sort": {"_id": 1}},
                {"$project": {"_id": 0, "mes": "$_id", "total": 1}},
            ]
            aum = list(db_v["AuM"].aggregate(pipeline_aum))

        # DIRECTO desde CashFlow.Flujo (sin el espejo OperacionesAPI.MesaAPI):
        # contraparte/moneda/concertacion/bruto existen idénticos en la fuente.
        db_cf = get_db_cashflow()
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
        flujo = list(db_cf["Flujo"].aggregate(pipeline_flujo))

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
def negocio_fechas(_engine: str | None = Query(None, include_in_schema=False)):
    """Lista de fechas distintas con boletos persistidos, ordenadas
    descendente. Usado por el frontend para limitar el selector de fecha
    a días con data real."""
    if _motor(_engine, "NEGOCIO_SQL") == "sql":
        return _neg_sql.negocio_fechas()
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
    _engine: str | None = Query(None, include_in_schema=False),
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
    if _motor(_engine, "NEGOCIO_SQL") == "sql":
        return _neg_sql.negocio_serie(moneda=moneda, cuenta_filter=cuenta_filter,
                                      cuenta=cuenta, scope=scope)
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
        aplicar_scope_cuenta(match_doc, scope, campo="id_cuenta")
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
    _engine: str | None = Query(None, include_in_schema=False),
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
    if _motor(_engine, "NEGOCIO_SQL") == "sql":
        return _neg_sql.negocio_cuentas(moneda=moneda, cuenta_filter=cuenta_filter,
                                        categoria=categoria, desde=desde, hasta=hasta,
                                        cuenta=cuenta, scope=scope)

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
        aplicar_scope_cuenta(match_doc, scope, campo="id_cuenta")
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
    _engine: str | None = Query(None, include_in_schema=False),
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
    if _motor(_engine, "NEGOCIO_SQL") == "sql":
        return _neg_sql.negocio_cuentas_matrix(moneda=moneda, cuenta_filter=cuenta_filter,
                                               desde=desde, hasta=hasta, cuenta=cuenta,
                                               scope=scope)

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
        aplicar_scope_cuenta(match_doc, scope, campo="id_cuenta")
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
    _engine: str | None = Query(None, include_in_schema=False),
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

    if _motor(_engine, "NEGOCIO_SQL") == "sql":
        return _neg_sql.negocio_boletos(fecha=fecha, cuenta=cuenta, moneda=moneda,
                                        categoria=categoria, scope=scope)
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
def negocio_cuentas_list(scope: tuple[str, ...] | None = Depends(scope_cuentas),
                         _engine: str | None = Query(None, include_in_schema=False)):
    """Lista de strings `cuenta` distintos en NegocioMovimientos. Sirve
    como fuente del autocomplete de búsqueda de cuenta. Cacheado 1h —
    el set cambia poco (cuentas nuevas son raras). Limitado al scope de
    grupos del usuario."""
    if _motor(_engine, "NEGOCIO_SQL") == "sql":
        return _neg_sql.negocio_cuentas_list(scope=scope)
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
    _engine: str | None = Query(None, include_in_schema=False),
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

    if _motor(_engine, "NEGOCIO_SQL") == "sql":
        return _neg_sql.negocio(fecha=fecha_iso)
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


# ─────────────────────────────────────────────────────────────────────────────
# OPERACIONES (vista MOVIMIENTOS) — lee CashFlow.Operaciones (fuente: API
# informes), enriquecida con moneda/mercado/operacion por
# scripts/enrich_operaciones.py. Reemplaza a NEGOCIO (consolidados) para
# operaciones de mercado. Importe = |bruto|, agrupado por `operacion`.
# ─────────────────────────────────────────────────────────────────────────────

# ARS/USD = volumen en moneda nativa. USD_DOL = volumen DOLARIZADO (ARS+USD
# convertidos a USD con el mep de cada boleto). USD_DOL vive SOLO en SQL (el
# rollup de Mongo no trae el bruto en USD) → se fuerza el motor SQL para esa opción.
_OPS_MONEDAS = ("ARS", "USD", "USD_DOL")


def _motor(req: str | None, env: str = "OPERACIONES_SQL") -> str:
    """Motor de datos para una vista. Override por request `?_engine=sql|mongo` (A/B en prod);
    si no, el global `env`=1 → 'sql', sino 'mongo'. Mongo es el default hasta el cutover. Cada
    vista tiene su flag (OPERACIONES_SQL, NEGOCIO_SQL, …) → cutover independiente. La salida
    SQL == Mongo (validado por scripts/compare_*). El scope se aplica en ambos motores."""
    if req in ("sql", "mongo"):
        return req
    return "sql" if os.getenv(env) == "1" else "mongo"

# La serie del gráfico de aranceles se acota por defecto a esta ventana (cubre
# de sobra los botones 1W…1A). El aggregate sobre toda la historia escanea
# ~180k-300k docs (~1-1.5s, medido scripts/diag_perf_aranceles) y aggregation
# NO cubre el $group → el FETCH es inevitable. El botón ALL del front pide
# serie_full=True para traer la historia completa bajo demanda.
_SERIE_VENTANA_DIAS = 550  # ~18 meses

# El cierre de caución NO entra a ninguna sumatoria (la apertura ya cuenta el
# volumen). Se filtra por el campo materializado `es_cierre` (ver
# operaciones_informes._aplicar_enrich) → indexable, en vez de `$not /Cierre/`
# que forzaba un COLLSCAN. Requiere el backfill de es_cierre en los docs viejos.


def _ops_match(
    moneda: str,
    mercado: str | None,
    operacion: str | None = None,
    denominacion: str | None = None,
    cuenta: str | None = None,
    segmento: str | None = None,
) -> dict:
    # etapa=solicitud (pedido FCI bilateral, comprobante DOC) NO suma: la
    # liquidación (CL) ya cuenta esa operación → evita doble conteo. $ne también
    # matchea los docs SIN etapa (boletos normales y liquidaciones). El "flujo del
    # día" (solicitudes) se consultará aparte cuando se exponga.
    m: dict = {"moneda": moneda, "es_cierre": False,
               "etapa": {"$ne": "solicitud"}}
    if mercado and mercado.lower() != "todos":
        m["mercado"] = mercado
    if operacion:
        m["operacion"] = operacion
    if denominacion:
        m["denominacion"] = denominacion
    if cuenta:
        m["cuenta"] = cuenta
    if segmento and segmento.lower() != "todos":
        m["segmento"] = segmento
    return m


def _arancel_match(
    moneda: str,
    mercado: str | None = None,
    *,
    segmento: str | None = None,
    denominacion: str | None = None,
    cuenta: str | None = None,
) -> dict:
    """Como `_ops_match` pero para sumar ARANCEL: incluye los CIERRES con arancel.

    El arancel de caución vive SOLO en el cierre (es_cierre=True); las aperturas
    NO traen arancel (verificado con scripts/diag_aranceles_caucion: $121,6M en el
    cierre, 0 en la apertura). El filtro de volumen (_ops_match → es_cierre=False)
    los excluía y los PERDÍA. Acá los incluimos sin arrastrar compras/ventas: los
    cierres que NO son caución no tienen arancel, así que el `$or` sólo suma fees.
    """
    m = _ops_match(moneda, mercado, denominacion=denominacion, cuenta=cuenta, segmento=segmento)
    del m["es_cierre"]
    m["$or"] = [{"es_cierre": False}, {"es_cierre": True, "arancel": {"$ne": 0}}]
    return m


@router.get("/ops/mercados")
@cached(ttl=300)
def ops_mercados(_engine: str | None = Query(None, include_in_schema=False)):
    """Mercados distintos (para el selector). Cacheado."""
    if _motor(_engine) == "sql":
        return _ops_sql.ops_mercados()
    db = get_db_cashflow()["Operaciones"]
    return {"mercados": sorted(x for x in db.distinct("mercado") if x)}


@router.get("/ops/fechas")
@cached(ttl=120)
def ops_fechas(_engine: str | None = Query(None, include_in_schema=False)):
    """Fechas con operaciones (desc) + count, para el selector de fecha."""
    if _motor(_engine) == "sql":
        return _ops_sql.ops_fechas()
    db = get_db_cashflow()["Operaciones"]
    rows = list(db.aggregate([
        {"$group": {"_id": "$concertacion", "n": {"$sum": 1}}},
        {"$sort": {"_id": -1}},
    ]))
    return {"fechas": [{"fecha": r["_id"], "n": r["n"]} for r in rows if r.get("_id")]}


@router.get("/ops/meta")
@cached(ttl=120)
def ops_meta(fecha: str = Query(..., description="YYYY-MM-DD"),
             _engine: str | None = Query(None, include_in_schema=False)):
    """Metadata del día: # boletos + última ingesta + # mercados."""
    if _motor(_engine) == "sql":
        return _ops_sql.ops_meta(fecha=fecha)
    db = get_db_cashflow()["Operaciones"]
    rows = list(db.aggregate([
        {"$match": {"concertacion": fecha}},
        {"$group": {"_id": None, "n": {"$sum": 1}, "ultima": {"$max": "$ingestado_en"},
                    "mercados": {"$addToSet": "$mercado"}}},
    ]))
    if not rows:
        return {"meta": {"fecha": fecha, "n_boletos": 0, "n_categorias": 0, "ultima_ingesta": None}}
    r = rows[0]
    ts = r.get("ultima")
    return {"meta": {
        "fecha":          fecha,
        "n_boletos":      r.get("n", 0),
        "n_categorias":   len([m for m in (r.get("mercados") or []) if m]),
        "ultima_ingesta": ts.isoformat() if isinstance(ts, datetime) else None,
    }}


def _serie_bruto_rollup(cf, moneda, mercado, operacion, segmento) -> list[dict] | None:
    """Serie Σ bruto por fecha desde el rollup CashFlow.OpsSerieDiaria (histórico
    CERRADO, fecha < hoy) + el día de HOY en vivo (live-fallback). Devuelve None si
    el rollup está vacío (no construido) → el caller cae a la query live completa.

    Solo para el caso común (sin filtro de alta cardinalidad ni scope). El rollup
    ya pre-filtra es_cierre/solicitud igual que _ops_match → equivalente."""
    hoy = (datetime.now(UTC) - timedelta(hours=3)).date().isoformat()  # ART
    m: dict = {"moneda": moneda, "fecha": {"$lt": hoy}}
    if mercado and mercado.lower() != "todos":
        m["mercado"] = mercado
    if operacion:
        m["operacion"] = operacion
    if segmento and segmento.lower() != "todos":
        m["segmento"] = segmento
    rollup = list(cf["OpsSerieDiaria"].aggregate([
        {"$match": m},
        {"$group": {"_id": "$fecha", "bruto": {"$sum": "$bruto"}}},
    ]))
    if not rollup:
        return None  # rollup no construido para esta moneda → live
    serie = {r["_id"]: r["bruto"] for r in rollup}
    # Hoy en vivo (un solo día → índice concertacion, barato). El rollup llega a ayer.
    hoy_match = _ops_match(moneda, mercado, operacion, None, None, segmento)
    hoy_match["concertacion"] = hoy
    hoy_doc = next(iter(cf["Operaciones"].aggregate([
        {"$match": hoy_match},
        {"$group": {"_id": None, "bruto": {"$sum": {"$ifNull": ["$bruto", 0]}}}},
    ])), None)
    if hoy_doc and hoy_doc.get("bruto"):
        serie[hoy] = hoy_doc["bruto"]
    return [{"fecha": f, "bruto": round(serie[f], 2)} for f in sorted(serie)]


@router.get("/ops/serie")
@cached(ttl=300)
def ops_serie(
    moneda: str = Query("ARS"),
    mercado: str | None = Query(None, description="Filtra por mercado (vacío = todos)"),
    operacion: str | None = Query(None, description="Filtra el gráfico a una operacion"),
    denominacion: str | None = Query(None, description="Filtra el gráfico a una denominacion"),
    cuenta: str | None = Query(None, description="Filtra a una cuenta (búsqueda)"),
    segmento: str | None = Query(None, description="Filtra por segmento (nivel_1)"),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
    _engine: str | None = Query(None, include_in_schema=False),
):
    """Serie diaria: Σ bruto por fecha (las barras del gráfico).

    Caso común (sin filtro de alta cardinalidad ni scope) → lee el rollup
    OpsSerieDiaria (jobs/ops_rollup) en vez de escanear toda Operaciones. Con
    denominacion/cuenta/scoped → live (ya filtra por índice, no escanea todo)."""
    if moneda not in _OPS_MONEDAS:
        raise HTTPException(status_code=400, detail=f"moneda inválida: {moneda!r}")
    if moneda == "USD_DOL" or _motor(_engine) == "sql":  # dolarizado → SQL siempre
        return _ops_sql.ops_serie(moneda=moneda, mercado=mercado, operacion=operacion,
                                  denominacion=denominacion, cuenta=cuenta, segmento=segmento,
                                  scope=scope)
    cf = get_db_cashflow()
    serie: list[dict] | None = None
    if not denominacion and not cuenta and scope is None:
        serie = _serie_bruto_rollup(cf, moneda, mercado, operacion, segmento)
    if serie is None:
        match = _ops_match(moneda, mercado, operacion, denominacion, cuenta, segmento)
        aplicar_scope_cuenta(match, scope, campo="cuenta")
        serie = list(cf["Operaciones"].aggregate([
            {"$match": match},
            {"$group": {"_id": "$concertacion", "bruto": {"$sum": {"$ifNull": ["$bruto", 0]}}}},
            {"$sort": {"_id": 1}},
            {"$project": {"_id": 0, "fecha": "$_id", "bruto": {"$round": ["$bruto", 2]}}},
        ]))
    return {"moneda": moneda, "mercado": mercado, "serie": serie}


@router.get("/ops/resumen")
@cached(ttl=300)
def ops_resumen(
    moneda: str = Query("ARS"),
    mercado: str | None = Query(None),
    desde: str = Query(..., description="YYYY-MM-DD"),
    hasta: str = Query(..., description="YYYY-MM-DD"),
    operacion: str | None = Query(None, description="Selección de operacion (cross-filter)"),
    denominacion: str | None = Query(None, description="Selección de denominacion (cross-filter)"),
    cuenta: str | None = Query(None, description="Filtra a una cuenta (búsqueda)"),
    segmento: str | None = Query(None, description="Filtra por segmento (nivel_1)"),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
    _engine: str | None = Query(None, include_in_schema=False),
):
    """Scope [desde,hasta]: Σ bruto por operacion y por denominacion.

    Cross-filter: si hay `denominacion` seleccionada, la tabla de operaciones se
    filtra a esa denominacion; si hay `operacion` seleccionada, la de
    denominaciones se filtra a esa operacion. `cuenta`/`segmento` filtran ambas.
    """
    if moneda not in _OPS_MONEDAS:
        raise HTTPException(status_code=400, detail=f"moneda inválida: {moneda!r}")
    if moneda == "USD_DOL" or _motor(_engine) == "sql":  # dolarizado → SQL siempre
        return _ops_sql.ops_resumen(moneda=moneda, mercado=mercado, desde=desde, hasta=hasta,
                                    operacion=operacion, denominacion=denominacion, cuenta=cuenta,
                                    segmento=segmento, scope=scope)
    db = get_db_cashflow()["Operaciones"]
    base = _ops_match(moneda, mercado, cuenta=cuenta, segmento=segmento)
    base["concertacion"] = {"$gte": desde, "$lte": hasta}
    aplicar_scope_cuenta(base, scope, campo="cuenta")
    filtro_op = {"denominacion": denominacion} if denominacion else {}
    filtro_denom = {"operacion": operacion} if operacion else {}
    facet = list(db.aggregate([
        {"$match": base},
        {"$facet": {
            "por_operacion": [
                {"$match": filtro_op} if filtro_op else {"$match": {}},
                {"$group": {"_id": "$operacion", "bruto": {"$sum": {"$ifNull": ["$bruto", 0]}},
                            "n": {"$sum": 1}}},
                {"$match": {"bruto": {"$ne": 0}}},
                {"$sort": {"bruto": -1}},
                {"$project": {"_id": 0, "operacion": {"$ifNull": ["$_id", "(sin)"]},
                              "bruto": {"$round": ["$bruto", 2]}, "n": 1}},
            ],
            "por_denominacion": [
                {"$match": filtro_denom} if filtro_denom else {"$match": {}},
                {"$group": {"_id": "$denominacion", "bruto": {"$sum": {"$ifNull": ["$bruto", 0]}},
                            "n": {"$sum": 1}}},
                {"$sort": {"bruto": -1}},
                {"$project": {"_id": 0, "denominacion": {"$ifNull": ["$_id", "(sin)"]},
                              "bruto": {"$round": ["$bruto", 2]}, "n": 1}},
            ],
        }},
    ]))
    f = facet[0] if facet else {}
    por_op = f.get("por_operacion", [])
    por_denom = f.get("por_denominacion", [])
    # Total = el de la dimensión filtrada (si hay selección) o el global.
    total = round(sum(r["bruto"] for r in (por_denom if denominacion else por_op)), 2)
    return {
        "moneda": moneda, "mercado": mercado, "desde": desde, "hasta": hasta,
        "por_operacion": por_op, "por_denominacion": por_denom, "total": total,
    }


@router.get("/ops/agro")
@cached(ttl=300)
def ops_agro(
    desde: str = Query(..., description="YYYY-MM-DD"),
    hasta: str = Query(..., description="YYYY-MM-DD"),
    agg: str = Query("MENSUAL", description="MENSUAL | DIARIO"),
    commodity: str | None = Query(None, description="SOJA/TRIGO/MAIZ (cross-filter)"),
    cuenta: str | None = Query(None, description="Filtra a una cuenta (denominación exacta)"),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
    _engine: str | None = Query(None, include_in_schema=False),
):
    """Futuros agropecuarios: Σ TONELADAS por periodo (mes/día) y commodity
    (SOJA/TRIGO/MAIZ). Lógica: tipo 'Futuros' sin 'Financieros', sin OTC;
    toneladas = |cantidad| × (10 si 'MIN' en instrumento, sino 100).

    Devuelve: `serie` (Σ por periodo en [desde,hasta], chart de la izq),
    `serie_cuenta` (idem SOLO de la cuenta elegida, chart de la der; vacía sin `cuenta`),
    `serie_share` (% mensual nuestro/mercado por commodity, tab "Share de
    mercado"; lee CashFlow.VolumenMercadoAgro), `totales` (Σ por commodity),
    `por_cuenta` y `por_instrumento` (acotados al rango [desde,hasta])."""
    if _motor(_engine) == "sql":
        return _ops_sql.ops_agro(desde=desde, hasta=hasta, agg=agg, commodity=commodity,
                                 cuenta=cuenta, scope=scope)
    dbcf = get_db_cashflow()
    db = dbcf["Operaciones"]
    plen = 7 if agg.upper() == "MENSUAL" else 10
    inst = {"$ifNull": ["$instrumento", ""]}
    # `commodity` (SOJA/TRIGO/MAIZ) se materializa en la ingesta — ver
    # operaciones_informes.clasificar_commodity. Match indexado (índice parcial
    # commodity_concertacion) → no escanea la colección con regex. Backfill de
    # docs viejos: scripts/backfill_commodity_operaciones.py.
    # serie/serie_cuenta (gráficos de VOLUMEN) y las tablas se acotan al rango
    # [desde,hasta] (date_m) → el toolbar Desde/Hasta maneja el gráfico (su "ALL"
    # = el rango elegido). El share mensual (nuestro_mensual) SÍ sigue histórico.
    match: dict = {"commodity": {"$in": ["SOJA", "TRIGO", "MAIZ"]}}
    aplicar_scope_cuenta(match, scope, campo="cuenta")
    date_m = {"$match": {"concertacion": {"$gte": desde, "$lte": hasta}}}
    addf = {
        "toneladas": {"$multiply": [
            {"$abs": {"$ifNull": ["$cantidad", 0]}},
            {"$cond": [{"$regexMatch": {"input": inst, "regex": "MIN", "options": "i"}}, 10, 100]},
        ]},
        "periodo": {"$substr": ["$concertacion", 0, plen]},
    }
    # Cross-filter independiente: `cuenta` filtra commodities + instrumentos;
    # `commodity` filtra cuentas + instrumentos. El gráfico global queda FIJO; el
    # de cuenta sólo existe cuando hay `cuenta` elegida.
    f_comm = [{"$match": {"denominacion": cuenta}}] if cuenta else []   # cuenta → filtra commodities
    f_cta = [{"$match": {"commodity": commodity}}] if commodity else []  # commodity → filtra cuentas
    serie_grp = {"$group": {"_id": {"p": "$periodo", "c": "$commodity"}, "ton": {"$sum": "$toneladas"}}}
    facet_spec: dict = {
        "serie": [date_m, serie_grp],
        "por_commodity": [date_m, *f_comm,
                          {"$group": {"_id": "$commodity", "ton": {"$sum": "$toneladas"}}}],
        "por_cuenta": [date_m, *f_cta,
                       {"$group": {"_id": "$denominacion", "ton": {"$sum": "$toneladas"}, "n": {"$sum": 1}}},
                       {"$sort": {"ton": -1}}],
        "por_instrumento": [date_m, *f_comm, *f_cta,
                            {"$group": {"_id": "$instrumento", "ton": {"$sum": "$toneladas"}, "n": {"$sum": 1}}},
                            {"$sort": {"ton": -1}}],
        # Nuestro volumen agregado a MES (histórico) → numerador del market share.
        "nuestro_mensual": [{"$group": {
            "_id": {"p": {"$substr": ["$concertacion", 0, 7]}, "c": "$commodity"},
            "ton": {"$sum": "$toneladas"}}}],
    }
    if cuenta:
        facet_spec["serie_cuenta"] = [{"$match": {"denominacion": cuenta}}, date_m, serie_grp]
    facet = list(db.aggregate([
        {"$match": match},
        {"$addFields": addf},
        {"$facet": facet_spec},
    ]))
    f = facet[0] if facet else {}

    def _serie(rows) -> list[dict]:
        out: dict[str, dict] = {}
        for r in rows:
            p, c = r["_id"]["p"], r["_id"]["c"]
            d = out.setdefault(p, {"periodo": p, "SOJA": 0.0, "TRIGO": 0.0, "MAIZ": 0.0})
            d[c] = round(r["ton"], 0)
        return [out[p] for p in sorted(out)]

    tot = {"SOJA": 0.0, "TRIGO": 0.0, "MAIZ": 0.0}
    for r in f.get("por_commodity", []):
        tot[r["_id"]] = round(r["ton"], 0)
    por_cuenta = [{"denominacion": r["_id"] or "(sin)", "toneladas": round(r["ton"], 0), "n": r["n"]}
                  for r in f.get("por_cuenta", [])]
    por_instrumento = [{"instrumento": r["_id"] or "(sin)", "toneladas": round(r["ton"], 0), "n": r["n"]}
                       for r in f.get("por_instrumento", [])]

    # Market share MENSUAL: nuestro_mensual / volumen de mercado. Sólo para los
    # meses con dato de mercado cargado (CashFlow.VolumenMercadoAgro, manual).
    # share = None si falta el mercado de ese commodity → el front lo muestra como —.
    nuestro_m: dict[str, dict] = {}
    for r in f.get("nuestro_mensual", []):
        nuestro_m.setdefault(r["_id"]["p"], {})[r["_id"]["c"]] = r["ton"]
    mercado: dict[str, dict] = {}
    for d in dbcf["VolumenMercadoAgro"].find(
        {}, {"_id": 0, "periodo": 1, "commodity": 1, "toneladas": 1}
    ):
        mercado.setdefault(d["periodo"], {})[d["commodity"]] = d.get("toneladas") or 0
    serie_share = []
    for p in sorted(mercado):
        nm = mercado[p]
        ours = nuestro_m.get(p, {})
        row: dict = {"periodo": p}
        for c in ("SOJA", "TRIGO", "MAIZ"):
            mkt = nm.get(c) or 0
            row[c] = round(100 * (ours.get(c) or 0) / mkt, 2) if mkt else None
            row[f"{c}_nuestro"] = round(ours.get(c) or 0, 0)
            row[f"{c}_mercado"] = round(mkt, 0)
        serie_share.append(row)

    return {
        "desde": desde, "hasta": hasta, "agg": agg,
        "serie": _serie(f.get("serie", [])),
        "serie_cuenta": _serie(f.get("serie_cuenta", [])),
        "serie_share": serie_share,
        "totales": tot, "por_cuenta": por_cuenta, "por_instrumento": por_instrumento,
    }


def _serie_arancel_rollup(cf, segmento, plen, serie_full) -> list[dict] | None:
    """Serie Σ arancel (PESOS, todas las monedas) por periodo desde OpsSerieDiaria
    (histórico < hoy) + hoy en vivo. El arancel es siempre en pesos → NO se filtra
    por moneda. None si el rollup está vacío → live. plen=7 mensual / 10 diario."""
    hoy = (datetime.now(UTC) - timedelta(hours=3)).date().isoformat()  # ART
    m: dict = {"fecha": {"$lt": hoy}}
    if segmento and segmento.lower() != "todos":
        m["segmento"] = segmento
    if not serie_full:
        cutoff = (datetime.now(UTC) - timedelta(hours=3)
                  - timedelta(days=_SERIE_VENTANA_DIAS)).date().isoformat()
        m["fecha"] = {"$gte": cutoff, "$lt": hoy}
    rollup = list(cf["OpsSerieDiaria"].aggregate([
        {"$match": m},
        {"$group": {"_id": {"$substr": ["$fecha", 0, plen]}, "ar": {"$sum": "$arancel"}}},
    ]))
    if not rollup:
        return None
    serie = {r["_id"]: r["ar"] for r in rollup}
    # Hoy en vivo (un día → índice concertacion). abs(arancel), sin filtro de moneda.
    # _arancel_match: incluye los cierres de caución (es donde está el arancel).
    hoy_match = _arancel_match("ARS", segmento=segmento)
    hoy_match.pop("moneda", None)
    hoy_match["concertacion"] = hoy
    hoy_doc = next(iter(cf["Operaciones"].aggregate([
        {"$match": hoy_match},
        {"$group": {"_id": None, "ar": {"$sum": {"$abs": {"$ifNull": ["$arancel", 0]}}}}},
    ])), None)
    if hoy_doc and hoy_doc.get("ar"):
        p = hoy[:plen]
        serie[p] = serie.get(p, 0) + hoy_doc["ar"]
    return [{"periodo": p, "arancel": round(serie[p], 2)} for p in sorted(serie)]


@router.get("/ops/aranceles")
@cached(ttl=300)
def ops_aranceles(
    moneda: str = Query("ARS"),
    desde: str = Query(..., description="YYYY-MM-DD"),
    hasta: str = Query(..., description="YYYY-MM-DD"),
    agg: str = Query("MENSUAL", description="MENSUAL | DIARIO"),
    cuenta: str | None = Query(None, description="Cross-filter: denominación seleccionada"),
    instrumento: str | None = Query(None, description="Cross-filter: instrumento seleccionado"),
    sel_dim: str | None = Query(None, description="Cross-filter: valor seleccionado de la dim izquierda"),
    segmento: str | None = Query(None, description="Filtra por segmento (nivel_1)"),
    dim: str = Query("nivel3", description="Dimensión de la tabla izquierda: nivel3 | operacion | operador"),
    serie_full: bool = Query(False, description="True = serie histórica completa (botón ALL); default ~18m"),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
    _engine: str | None = Query(None, include_in_schema=False),
):
    """Σ aranceles por periodo (gráfico), por nivel_3 (izq) y por cliente (der).

    La SERIE (histórica, antes escaneaba todo) sale del rollup OpsSerieDiaria
    cuando no hay scope; las TABLAS por_nivel3/por_cuenta son date-bounded → live
    (ya usan índice)."""
    if moneda not in _OPS_MONEDAS:
        raise HTTPException(status_code=400, detail=f"moneda inválida: {moneda!r}")
    if _motor(_engine) == "sql":
        return _ops_sql.ops_aranceles(moneda=moneda, desde=desde, hasta=hasta, agg=agg,
                                      cuenta=cuenta, instrumento=instrumento, sel_dim=sel_dim,
                                      segmento=segmento, dim=dim, serie_full=serie_full,
                                      scope=scope)
    cf = get_db_cashflow()
    db = cf["Operaciones"]
    plen = 7 if agg.upper() == "MENSUAL" else 10
    # El arancel es SIEMPRE en pesos y hay UN solo valor (aunesa_aranceles guarda
    # aranceles["ARS"]). NO existe "arancel en USD" → no se filtra ni convierte por
    # moneda; el toggle de moneda no aplica a esta vista.
    arancel = {"$abs": {"$ifNull": ["$arancel", 0]}}

    # SERIE: rollup (sumando TODAS las monedas) + hoy live; fallback a live si scoped.
    serie: list[dict] | None = None
    if scope is None:
        serie = _serie_arancel_rollup(cf, segmento, plen, serie_full)
    if serie is None:
        match_s = _arancel_match("ARS", segmento=segmento)
        match_s.pop("moneda", None)
        aplicar_scope_cuenta(match_s, scope, campo="cuenta")
        if serie_full:
            serie_ventana: list[dict] = []
        else:
            cutoff = (datetime.now(UTC) - timedelta(hours=3)
                      - timedelta(days=_SERIE_VENTANA_DIAS)).date().isoformat()
            serie_ventana = [{"$match": {"concertacion": {"$gte": cutoff}}}]
        serie = list(db.aggregate([
            {"$match": match_s},
            *serie_ventana,
            {"$group": {"_id": {"$substr": ["$concertacion", 0, plen]}, "ar": {"$sum": arancel}}},
            {"$sort": {"_id": 1}},
            {"$project": {"_id": 0, "periodo": "$_id", "arancel": {"$round": ["$ar", 2]}}},
        ]))

    # TABLAS (acotadas a [desde,hasta] → rápidas por índice). CROSS-FILTER COMPLETO:
    # las 3 tablas (dim izq · cuentas · instrumentos) se filtran entre sí — cada una
    # aplica las selecciones de las OTRAS dos, no la propia.
    match_t = _arancel_match("ARS", segmento=segmento)
    match_t.pop("moneda", None)   # un solo arancel en pesos → sin filtro de moneda
    aplicar_scope_cuenta(match_t, scope, campo="cuenta")
    date_m = {"$match": {"concertacion": {"$gte": desde, "$lte": hasta}}}

    # Mapa operador (lazy): id_cuenta → operador (nombre/email/"(sin operador)").
    _op_map: dict[str, str] = {}

    def _operador_map() -> dict[str, str]:
        if not _op_map:
            _op_map.update({
                str(c["id_cuenta"]): (c.get("operador_nombre") or c.get("operador_email") or "(sin operador)")
                for c in get_db_clientes()["Comitentes"].find(
                    {}, {"_id": 0, "id_cuenta": 1, "operador_email": 1, "operador_nombre": 1})
            })
        return _op_map

    def _match_operador(valor: str) -> dict:
        m = _operador_map()
        if valor == "(sin operador)":
            return {"cuenta": {"$nin": [k for k, v in m.items() if v != "(sin operador)"]}}
        return {"cuenta": {"$in": [k for k, v in m.items() if v == valor]}}

    # Sub-match de cada selección (None si no hay).
    if sel_dim and dim == "operador":
        m_dim: dict | None = _match_operador(sel_dim)
    elif sel_dim and dim == "operacion":
        m_dim = {"operacion": sel_dim}
    elif sel_dim:
        m_dim = {"nivel_3": sel_dim}
    else:
        m_dim = None
    m_cuenta = {"denominacion": cuenta} if cuenta else None
    m_instr = {"instrumento": instrumento} if instrumento else None

    def _tabla(campo: str, key: str, *subs: dict | None) -> list[dict]:
        etapas = [{"$match": s} for s in subs if s]
        return list(db.aggregate([
            {"$match": match_t}, date_m, *etapas,
            {"$group": {"_id": campo, "ar": {"$sum": arancel}, "n": {"$sum": 1}}},
            {"$match": {"ar": {"$gt": 0}}},
            {"$sort": {"ar": -1}},
            {"$project": {"_id": 0, key: {"$ifNull": ["$_id", "(sin)"]},
                          "arancel": {"$round": ["$ar", 2]}, "n": 1}},
        ]))

    # IZQUIERDA (por_dim): filtrada por cuenta + instrumento (no por sí misma).
    if dim == "operador":
        det = _operador_map()
        acc: dict[str, dict] = {}
        etapas = [{"$match": s} for s in (m_cuenta, m_instr) if s]
        for r in db.aggregate([{"$match": match_t}, date_m, *etapas,
                {"$group": {"_id": "$cuenta", "ar": {"$sum": arancel}, "n": {"$sum": 1}}}]):
            op = det.get(str(r["_id"]), "(sin operador)")
            a = acc.setdefault(op, {"ar": 0.0, "n": 0})
            a["ar"] += r["ar"]
            a["n"] += r["n"]
        por_dim = sorted(
            ({"clave": k, "arancel": round(v["ar"], 2), "n": v["n"]}
             for k, v in acc.items() if v["ar"] > 0),
            key=lambda x: x["arancel"], reverse=True,
        )
    else:
        field = "$operacion" if dim == "operacion" else "$nivel_3"
        por_dim = _tabla(field, "clave", m_cuenta, m_instr)

    # DERECHA ARRIBA (por_cuenta): filtrada por dim + instrumento.
    por_cuenta = _tabla("$denominacion", "denominacion", m_dim, m_instr)
    # DERECHA ABAJO (por_instrumento): filtrada por dim + cuenta.
    por_instrumento = _tabla("$instrumento", "instrumento", m_dim, m_cuenta)

    return {
        "moneda": moneda, "desde": desde, "hasta": hasta, "agg": agg, "dim": dim,
        "serie": serie,
        "por_dim": por_dim,
        "por_cuenta": por_cuenta,
        "por_instrumento": por_instrumento,
        "total": round(sum(r["arancel"] for r in por_dim), 2),
    }


@router.get("/ops/cuentas-list")
@cached(ttl=3600)
def ops_cuentas_list(scope: tuple[str, ...] | None = Depends(scope_cuentas),
                     _engine: str | None = Query(None, include_in_schema=False)):
    """Denominaciones (+ cuenta) distintas — fuente del buscador."""
    if _motor(_engine) == "sql":
        return _ops_sql.ops_cuentas_list(scope=scope)
    db = get_db_cashflow()["Operaciones"]
    rows = list(db.aggregate([
        {"$group": {"_id": "$cuenta", "denom": {"$first": "$denominacion"}}},
        {"$sort": {"denom": 1}},
    ]))
    return {"cuentas": [{"cuenta": r["_id"], "denominacion": r.get("denom")}
                        for r in rows if r.get("_id")]}


@router.get("/ops/segmentos")
@cached(ttl=600)
def ops_segmentos(_engine: str | None = Query(None, include_in_schema=False)):
    """Segmentos (nivel_1) distintos, para el filtro."""
    if _motor(_engine) == "sql":
        return _ops_sql.ops_segmentos()
    db = get_db_cashflow()["Operaciones"]
    return {"segmentos": sorted(s for s in db.distinct("segmento") if s)}


@router.get("/ops/boletos")
@cached(ttl=60)
def ops_boletos(
    desde: str = Query(..., description="YYYY-MM-DD"),
    hasta: str = Query(..., description="YYYY-MM-DD"),
    moneda: str = Query("ARS"),
    denominacion: str | None = Query(None),
    cuenta: str | None = Query(None),
    operacion: str | None = Query(None),
    mercado: str | None = Query(None),
    segmento: str | None = Query(None),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
    _engine: str | None = Query(None, include_in_schema=False),
):
    """Boletos individuales (drill-down al tocar una cuenta/denominación)."""
    if moneda == "USD_DOL" or _motor(_engine) == "sql":  # dolarizado → SQL (trae ARS+USD)
        return _ops_sql.ops_boletos(desde=desde, hasta=hasta, moneda=moneda,
                                    denominacion=denominacion, cuenta=cuenta, operacion=operacion,
                                    mercado=mercado, segmento=segmento, scope=scope)
    db = get_db_cashflow()["Operaciones"]
    match = _ops_match(moneda, mercado, operacion, denominacion, cuenta, segmento)
    match["concertacion"] = {"$gte": desde, "$lte": hasta}
    aplicar_scope_cuenta(match, scope, campo="cuenta")
    proj = {"_id": 0, "boleto": 1, "concertacion": 1, "cuenta": 1, "denominacion": 1,
            "tipo_operacion": 1, "operacion": 1, "mercado": 1, "instrumento": 1,
            "condiciones": 1, "cantidad": 1, "bruto": 1, "moneda": 1}
    boletos = list(db.find(match, proj).sort("bruto", -1).limit(500))
    return {"boletos": boletos, "n": len(boletos)}


# ── COMERCIAL (lente por operador, estilo NEGOCIO) ───────────────────────────
# Vista nueva en OPERACIONES. Lógica en api/services/comercial.py.

def _com_motor(_engine: str | None):
    """Devuelve el módulo de servicio (SQL o Mongo) según el flag COMERCIAL_SQL / ?_engine."""
    return _com_sql if _motor(_engine, "COMERCIAL_SQL") == "sql" else _com


@router.get("/comercial/operadores")
def comercial_operadores(_engine: str | None = Query(None, include_in_schema=False)) -> list[dict]:
    """Operadores para el selector (email, nombre, # cuentas)."""
    return _com_motor(_engine).listar_operadores_comercial()


@router.get("/comercial/dimensiones")
def comercial_dimensiones(_engine: str | None = Query(None, include_in_schema=False)) -> dict:
    """Combos (operador, nivel_1, nivel_3) de cuentas activas → pueblan y cruzan
    los 3 filtros madre de la vista OPERADORES."""
    return _com_motor(_engine).dimensiones_comercial()


@router.get("/comercial/operador")
def comercial_operador(
    operador: str = Query(..., description="operador_email"),
    moneda: str = Query("ARS"),
    nivel_1: str | None = Query(None, description="filtro madre nivel_1 (cruza con operador/nivel_3)"),
    nivel_3: str | None = Query(None, description="filtro madre nivel_3 (cruza con operador/nivel_1)"),
    referido: str | None = Query(None, description="filtro madre referido (cruza con los demás)"),
    _engine: str | None = Query(None, include_in_schema=False),
) -> dict:
    """Resumen (KPIs) + clientes (tabla + ficha) del operador, en una pasada."""
    return _com_motor(_engine).operador_comercial(
        operador=operador, moneda=moneda, nivel_1=nivel_1, nivel_3=nivel_3, referido=referido)


@router.get("/comercial/serie")
def comercial_serie(
    operador: str = Query(..., description="operador_email"),
    metric: str = Query("volumen", description="volumen | aum"),
    moneda: str = Query("ARS"),
    id_cuenta: str | None = Query(None, description="scope a una sola cuenta (interactivo)"),
    nivel_1: str | None = Query(None, description="filtro madre nivel_1"),
    nivel_3: str | None = Query(None, description="filtro madre nivel_3"),
    referido: str | None = Query(None, description="filtro madre referido"),
    _engine: str | None = Query(None, include_in_schema=False),
) -> dict:
    """Serie para el gráfico. Sin id_cuenta → operador; con id_cuenta → cliente."""
    return _com_motor(_engine).serie_comercial(
        operador=operador, metric=metric, moneda=moneda, id_cuenta=id_cuenta,
        nivel_1=nivel_1, nivel_3=nivel_3, referido=referido)


@router.get("/comercial/clientes-por-fecha")
def comercial_clientes_por_fecha(
    operador: str = Query(..., description="operador_email"),
    desde: str = Query(..., description="ISO YYYY-MM-DD (inicio del período del bar)"),
    hasta: str = Query(..., description="ISO YYYY-MM-DD (fin del período del bar)"),
    moneda: str = Query("ARS"),
    nivel_1: str | None = Query(None, description="filtro madre nivel_1"),
    nivel_3: str | None = Query(None, description="filtro madre nivel_3"),
    referido: str | None = Query(None, description="filtro madre referido"),
    _engine: str | None = Query(None, include_in_schema=False),
) -> dict:
    """Clientes que operaron en el rango (click en una barra del chart de volumen)."""
    return _com_motor(_engine).clientes_por_fecha(
        operador=operador, desde=desde, hasta=hasta, moneda=moneda,
        nivel_1=nivel_1, nivel_3=nivel_3, referido=referido)


@router.get("/comercial/portafolio")
def comercial_portafolio(
    id_cuenta: str = Query(..., description="id de la cuenta comitente"),
    _engine: str | None = Query(None, include_in_schema=False),
) -> dict:
    """Tenencia del cliente (posiciones de AuM, último snapshot)."""
    return _com_motor(_engine).portafolio_cliente(id_cuenta=id_cuenta)


@router.get("/comercial/operaciones")
def comercial_operaciones(
    id_cuenta: str = Query(..., description="id de la cuenta comitente"),
    limite: int = Query(300, ge=1, le=1000),
    _engine: str | None = Query(None, include_in_schema=False),
) -> dict:
    """Operaciones recientes del cliente (boletos operativos, fecha desc)."""
    return _com_motor(_engine).operaciones_cliente(id_cuenta=id_cuenta, limite=limite)


@router.get("/comercial/analisis")
def comercial_analisis(
    operador: str = Query(..., description="operador_email"),
    moneda: str = Query("ARS", description="ARS | USD"),
    nivel_1: str | None = Query(None, description="filtro madre nivel_1"),
    nivel_3: str | None = Query(None, description="filtro madre nivel_3"),
    referido: str | None = Query(None, description="filtro madre referido"),
    _engine: str | None = Query(None, include_in_schema=False),
) -> dict:
    """Dataset de la vista ANÁLISIS: clientes del operador con estado comercial,
    AuM, última op y niveles de segmentación (estado / churn / distribución)."""
    return _com_motor(_engine).analisis_comercial(
        operador=operador, moneda=moneda, nivel_1=nivel_1, nivel_3=nivel_3, referido=referido)


@router.get("/comercial/actividad-historica")
def comercial_actividad_historica(
    operador: str = Query(..., description="operador_email o '__todos__' (toda la mesa)"),
    desde: str | None = Query(None, description="mes YYYY-MM inclusive"),
    hasta: str | None = Query(None, description="mes YYYY-MM inclusive"),
    moneda: str = Query("ARS", description="ARS | USD"),
    _engine: str | None = Query(None, include_in_schema=False),
) -> dict:
    """Serie mensual de cuentas activas (operaron en el mes calendario) +
    volumen, desde el snapshot `Clientes.ActividadMensual`. Scopeable por
    operador o toda la mesa."""
    return _com_motor(_engine).actividad_historica(
        operador=operador, desde=desde, hasta=hasta, moneda=moneda)


# ── INFORME (global, transversal a toda la mesa — no por operador) ───────────

@router.get("/comercial/informe")
def comercial_informe(
    moneda: str = Query("ARS", description="ARS | USD"),
    _engine: str | None = Query(None, include_in_schema=False),
) -> dict:
    """Tablas 2 y 3 del Informe: volumen + aranceles por comercial (ranking) y
    aranceles por segmento. Global (toda la mesa)."""
    return _com_motor(_engine).informe_comercial(moneda=moneda)


@router.get("/comercial/informe-segmento")
def comercial_informe_segmento(
    hasta: str | None = Query(None, description="mes YYYY-MM (default actual); acumulado a fin de mes"),
    operador: str | None = Query(None, description="opcional: solo cuentas de ese comercial"),
    _engine: str | None = Query(None, include_in_schema=False),
) -> dict:
    """Tabla 1 del Informe: # cuentas por segmento (nivel_1), acumulado a fin del
    mes `hasta` por fecha de alta. `operador` opcional re-scopea a ese comercial."""
    return _com_motor(_engine).informe_cuentas_por_segmento(hasta=hasta, operador=operador)


@router.get("/comercial/informe-aranceles-segmento")
def comercial_informe_aranceles_segmento(
    operador: str = Query(..., description="operador_email a desglosar"),
    moneda: str = Query("ARS", description="ARS | USD"),
    _engine: str | None = Query(None, include_in_schema=False),
) -> dict:
    """Q3 re-scopeada a un comercial: aranceles + ticket por segmento, solo de
    sus cuentas."""
    return _com_motor(_engine).informe_aranceles_segmento(operador=operador, moneda=moneda)


@router.get("/comercial/informe-segmento-detalle")
def comercial_informe_segmento_detalle(
    segmento: str = Query("todos", description="nivel_1 a desglosar; 'todos' = todos los segmentos"),
    operador: str | None = Query(None, description="opcional: solo cuentas de ese comercial"),
    moneda: str = Query("ARS", description="ARS | USD"),
    _engine: str | None = Query(None, include_in_schema=False),
) -> dict:
    """Detalle de un segmento (Q4 dinámica): clientes con su arancel +
    operaciones (boletos con arancel) que lo generaron. `segmento='todos'` →
    todos los segmentos (vista por defecto). `operador` opcional."""
    return _com_motor(_engine).informe_segmento_detalle(
        segmento=segmento, operador=operador, moneda=moneda)
