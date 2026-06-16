"""Router Operaciones: endpoints para MesaAPI (flujo contrapartes), FlujosAPI
(movimientos) y NegocioMovimientos (vista de negocio del día)."""
import logging
import threading
import time
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from api.cache import cached
from api.db import get_db_cashflow, get_db_clientes
from api.services import comercial as _com
from api.services import comercial_sql as _com_sql
from api.services import negocio_sql as _neg_sql
from api.services import operaciones_sql as _ops_sql
from api.services import operaciones_view as _ov
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
from api.services.operaciones_view import (
    OPS_MONEDAS as _OPS_MONEDAS,
)
from api.services.operaciones_view import (
    ddmmyyyy_a_iso as _ddmmyyyy_a_iso,
)
from api.services.operaciones_view import (
    importe_convertido as _importe_convertido,
)
from api.services.operaciones_view import (
    motor as _motor,
)
from api.services.operaciones_view import (
    ops_match as _ops_match,
)
from api.services.operaciones_view import (
    valor_si_categoria as _valor_si_categoria,
)
from api.services.titulos_flujos import assets_normalizados
from core.postgres import get_pool

logger = logging.getLogger("api.operaciones")

# Cache in-process para helpers sin parámetros (datos que cambian ≤1 vez/semana)
_fondos_cache_data: list | None = None
_fondos_cache_ts: float = 0.0
_fondos_lock = threading.Lock()
_FONDOS_TTL = 600

router = APIRouter(prefix="/api/operaciones", tags=["Operaciones"])

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
    # cuenta (id) → {contraparte, grupo}. SQL clientes.contrapartes. La cuenta es la CLAVE.
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id_cuenta, contraparte, segmento FROM contrapartes "
                    "WHERE id_cuenta IS NOT NULL AND id_cuenta <> ''")
        cp_map = {
            str(idc).strip(): {"contraparte": cp or "", "grupo": seg or ""}
            for idc, cp, seg in cur.fetchall() if idc not in (None, "")
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

    # SQL clientes.contrapartes (segmento=Fondos): grupo=segmento, nombre=contraparte.
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT contraparte FROM contrapartes "
                    "WHERE segmento = 'Fondos' AND contraparte IS NOT NULL")
        fondos_cu = {r[0] for r in cur.fetchall() if r[0]}
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
        if cuenta:
            # Match exacto sobre cuenta — override total del cuenta_filter.
            # La verificación contra el scope es HTTP (403) → queda en el router.
            verificar_cuenta_str(cuenta, scope)
        return _ov.negocio_serie_mongo(
            moneda=moneda, cuenta_filter=cuenta_filter, cuenta=cuenta, scope=scope,
        )
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
# La serie del gráfico de aranceles se acota por defecto a esta ventana (cubre
# de sobra los botones 1W…1A). El aggregate sobre toda la historia escanea
# ~180k-300k docs (~1-1.5s, medido scripts/diag_perf_aranceles) y aggregation
# NO cubre el $group → el FETCH es inevitable. El botón ALL del front pide
# serie_full=True para traer la historia completa bajo demanda.
# El cierre de caución NO entra a ninguna sumatoria (la apertura ya cuenta el
# volumen). Se filtra por el campo materializado `es_cierre` (ver
# operaciones_informes._aplicar_enrich) → indexable, en vez de `$not /Cierre/`
# que forzaba un COLLSCAN. Requiere el backfill de es_cierre en los docs viejos.


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


@router.get("/ops/serie")
@cached(ttl=300)
def ops_serie(
    moneda: str = Query("ARS"),
    mercado: str | None = Query(None, description="Filtra por mercado (vacío = todos)"),
    operacion: str | None = Query(None, description="Filtra el gráfico a una operacion"),
    denominacion: str | None = Query(None, description="Filtra el gráfico a una denominacion"),
    cuenta: str | None = Query(None, description="Filtra a una cuenta (búsqueda)"),
    segmento: str | None = Query(None, description="Filtra por segmento (nivel_1)"),
    operador: str | None = Query(None, description="Filtra por operador (operador_email)"),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
    _engine: str | None = Query(None, include_in_schema=False),
):
    """Serie diaria: Σ bruto por fecha (las barras del gráfico).

    Caso común (sin filtro de alta cardinalidad ni scope) → lee el rollup
    OpsSerieDiaria (jobs/ops_rollup) en vez de escanear toda Operaciones. Con
    denominacion/cuenta/scoped/operador → live (ya filtra por índice, no escanea)."""
    if moneda not in _OPS_MONEDAS:
        raise HTTPException(status_code=400, detail=f"moneda inválida: {moneda!r}")
    if moneda == "USD_DOL" or _motor(_engine) == "sql":  # dolarizado → SQL siempre
        return _ops_sql.ops_serie(moneda=moneda, mercado=mercado, operacion=operacion,
                                  denominacion=denominacion, cuenta=cuenta, segmento=segmento,
                                  scope=scope, operador=operador)
    return _ov.ops_serie_mongo(
        moneda=moneda, mercado=mercado, operacion=operacion,
        denominacion=denominacion, cuenta=cuenta, segmento=segmento,
        operador=operador, scope=scope,
    )


@router.get("/ops/resumen")
@cached(ttl=300)
def ops_resumen(
    moneda: str = Query("ARS"),
    mercado: str | None = Query(None),
    desde: str = Query(..., description="YYYY-MM-DD"),
    hasta: str = Query(..., description="YYYY-MM-DD"),
    operacion: str | None = Query(None, description="Selección de operacion (cross-filter)"),
    denominacion: str | None = Query(None, description="Selección de denominacion (cross-filter)"),
    instrumento: str | None = Query(None, description="Selección de instrumento/título (cross-filter)"),
    cuenta: str | None = Query(None, description="Filtra a una cuenta (búsqueda)"),
    segmento: str | None = Query(None, description="Filtra por segmento (nivel_1)"),
    operador: str | None = Query(None, description="Filtra por operador (operador_email)"),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
    _engine: str | None = Query(None, include_in_schema=False),
):
    """Scope [desde,hasta]: Σ bruto por operacion, por denominacion (cuentas) y por
    instrumento (títulos).

    Cross-filter 3-way: cada tabla aplica las selecciones de las OTRAS dos
    (operacion ↔ denominacion ↔ instrumento). `cuenta`/`segmento`/`operador` filtran las tres.
    """
    if moneda not in _OPS_MONEDAS:
        raise HTTPException(status_code=400, detail=f"moneda inválida: {moneda!r}")
    if moneda == "USD_DOL" or _motor(_engine) == "sql":  # dolarizado → SQL siempre
        return _ops_sql.ops_resumen(moneda=moneda, mercado=mercado, desde=desde, hasta=hasta,
                                    operacion=operacion, denominacion=denominacion, cuenta=cuenta,
                                    segmento=segmento, scope=scope, instrumento=instrumento,
                                    operador=operador)
    return _ov.ops_resumen_mongo(
        moneda=moneda, mercado=mercado, desde=desde, hasta=hasta,
        operacion=operacion, denominacion=denominacion, instrumento=instrumento,
        cuenta=cuenta, segmento=segmento, operador=operador, scope=scope,
    )


@router.get("/ops/agro")
@cached(ttl=300)
def ops_agro(
    desde: str = Query(..., description="YYYY-MM-DD"),
    hasta: str = Query(..., description="YYYY-MM-DD"),
    agg: str = Query("MENSUAL", description="MENSUAL | DIARIO"),
    commodity: str | None = Query(None, description="SOJA/TRIGO/MAIZ (cross-filter)"),
    cuenta: str | None = Query(None, description="Filtra a una cuenta (denominación exacta)"),
    nivel5: str | None = Query(None, description="Filtra por nivel_5 de Clientes.Comitentes"),
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
                                 cuenta=cuenta, scope=scope, nivel5=nivel5)
    return _ov.ops_agro_mongo(
        desde=desde, hasta=hasta, agg=agg, commodity=commodity,
        cuenta=cuenta, nivel5=nivel5, scope=scope,
    )


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
    operador: str | None = Query(None, description="Filtra madre por operador (operador_email)"),
    dim: str = Query("nivel3", description="Dimensión de la tabla izquierda: nivel3 | operacion | operador"),
    serie_full: bool = Query(False, description="True = serie histórica completa (botón ALL); default ~18m"),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
    _engine: str | None = Query(None, include_in_schema=False),
):
    """Σ aranceles por periodo (gráfico), por nivel_3 (izq) y por cliente (der).

    La SERIE (histórica, antes escaneaba todo) sale del rollup OpsSerieDiaria
    cuando no hay scope ni operador; las TABLAS por_nivel3/por_cuenta son
    date-bounded → live (ya usan índice)."""
    if moneda not in _OPS_MONEDAS:
        raise HTTPException(status_code=400, detail=f"moneda inválida: {moneda!r}")
    if _motor(_engine) == "sql":
        return _ops_sql.ops_aranceles(moneda=moneda, desde=desde, hasta=hasta, agg=agg,
                                      cuenta=cuenta, instrumento=instrumento, sel_dim=sel_dim,
                                      segmento=segmento, dim=dim, serie_full=serie_full,
                                      scope=scope, operador=operador)
    return _ov.ops_aranceles_mongo(
        moneda=moneda, desde=desde, hasta=hasta, agg=agg, cuenta=cuenta,
        instrumento=instrumento, sel_dim=sel_dim, segmento=segmento,
        operador=operador, dim=dim, serie_full=serie_full, scope=scope,
    )


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


@router.get("/ops/niveles5")
@cached(ttl=600)
def ops_niveles5():
    """Valores distintos de nivel_5 (Clientes.Comitentes), para el filtro AGRO."""
    vals = get_db_clientes()["Comitentes"].distinct("nivel_5", {"nivel_5": {"$nin": [None, ""]}})
    return {"niveles5": sorted(str(v) for v in vals if v)}


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
            "condiciones": 1, "cantidad": 1, "bruto": 1, "moneda": 1, "etapa": 1}
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


@router.get("/comercial/cobros-futuros")
def comercial_cobros_futuros(
    operador: str = Query(..., description="operador_email o '__todos__'"),
    nivel_1: str | None = Query(None, description="filtro madre nivel_1"),
    nivel_3: str | None = Query(None, description="filtro madre nivel_3"),
    referido: str | None = Query(None, description="filtro madre referido"),
) -> dict:
    """Cobros futuros (acreencias) del scope: serie diaria acumulable + totales por
    cliente. Mongo-only (CashFlow.Acreencias no tiene espejo SQL)."""
    return _com.cobros_futuros(
        operador=operador, nivel_1=nivel_1, nivel_3=nivel_3, referido=referido)


@router.get("/comercial/cobros-futuros/cliente")
def comercial_cobros_futuros_cliente(
    id_cuenta: str = Query(..., description="id de la cuenta comitente"),
) -> dict:
    """Detalle de cobros futuros de un cliente: serie acumulable + títulos que cobra."""
    return _com.cobros_futuros_cliente(id_cuenta=id_cuenta)


@router.get("/comercial/referido-clientes")
def comercial_referido_clientes(
    referido: str = Query(..., description="nombre del referido (empresa referidora)"),
    moneda: str = Query("ARS"),
) -> dict:
    """Vista REFERIDOS: cuentas referidas por una empresa con AuM + volumen (mes/año)
    + arancel (mes/total). Mongo-only (reusa los helpers del tablero comercial)."""
    return _com.referido_clientes(referido=referido, moneda=moneda)


@router.get("/comercial/referido-fci")
def comercial_referido_fci(
    referido: str = Query(..., description="nombre del referido (empresa referidora)"),
    desde: str = Query(..., description="YYYY-MM-DD inclusive"),
    hasta: str = Query(..., description="YYYY-MM-DD inclusive"),
    moneda: str = Query("ARS"),
) -> dict:
    """Vista REFERIDOS — tabla FCI: dinero en cartera FCI (saldo promedio diario
    del rango) de las cuentas del referido, abierto por sociedad gerente. Base
    para la comisión de la coop. Mongo-only (reusa helpers del tablero comercial)."""
    return _com.referido_fci(referido=referido, desde=desde, hasta=hasta, moneda=moneda)


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
