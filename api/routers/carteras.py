"""Router Portfolio: endpoints para CarterasAPI y AumAPI."""
import threading
import time
from datetime import datetime

from fastapi import APIRouter, Query

from api.cache import cached
from api.deps import get_db_portfolio, get_db_titulos, get_db_trading, get_db_valuaciones

_fci_assets_cache_data: dict | None = None
_fci_assets_cache_ts: float = 0.0
_fci_assets_lock = threading.Lock()
_FCI_ASSETS_TTL = 600

_assets_enrich_cache: dict | None = None
_assets_enrich_ts: float = 0.0
_assets_enrich_lock = threading.Lock()

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
    """Mapea unidad → {emisor, ticker} para unidades con CARTERA=CARTERA FCI.

    Cacheado 10 min en proceso — los assets FCI cambian como mucho mensualmente.
    """
    global _fci_assets_cache_data, _fci_assets_cache_ts
    now = time.time()
    with _fci_assets_lock:
        if _fci_assets_cache_data is not None and now < _fci_assets_cache_ts:
            return _fci_assets_cache_data

    db_t = get_db_titulos()
    result = {
        d["unidad"]: {"emisor": d.get("emisor", ""), "ticker": d.get("ticker", "")}
        for d in db_t["AssetsAPI"].find(
            {"cartera": "CARTERA FCI"},
            {"_id": 0, "unidad": 1, "emisor": 1, "ticker": 1},
        )
    }
    with _fci_assets_lock:
        _fci_assets_cache_data = result
        _fci_assets_cache_ts = now + _FCI_ASSETS_TTL
    return result


def _assets_enrich_map() -> dict[str, dict]:
    """unidad → {cartera, clase_activo} desde TitulosAPI.AssetsAPI (cacheado 10 min)."""
    global _assets_enrich_cache, _assets_enrich_ts
    now = time.time()
    with _assets_enrich_lock:
        if _assets_enrich_cache is not None and now < _assets_enrich_ts:
            return _assets_enrich_cache
    db_t = get_db_titulos()
    result = {
        d["unidad"]: {
            "cartera": d.get("cartera") or "OTROS",
            "clase_activo": d.get("clase_activo") or "",
        }
        for d in db_t["AssetsAPI"].find(
            {}, {"_id": 0, "unidad": 1, "cartera": 1, "clase_activo": 1}
        )
        if d.get("unidad")
    }
    with _assets_enrich_lock:
        _assets_enrich_cache = result
        _assets_enrich_ts = now + 600
    return result


def _valuacion_api(cant: float, px: float, cartera: str, clase_activo: str) -> float:
    """Regla de valuación: FCI o clase OTROS → P×Q directo, resto → P×Q/100."""
    if clase_activo == "OTROS" or "FCI" in cartera:
        return cant * px
    return cant * px / 100


@router.get("/resumen")
@cached(ttl=300)
def resumen_portfolio(
    id_cuenta: str | None = Query(None, description="id_cuenta para filtrar breakdown por cartera"),
):
    """Resumen ejecutivo: lista de cuentas + breakdown por CARTERA del mes actual.

    Lee PortfolioAPI.CarterasAPI (mes vigente) + TitulosAPI.AssetsAPI (clasificación).
    Sin id_cuenta devuelve solo la lista de cuentas.
    """
    db_p = get_db_portfolio()
    assets = _assets_enrich_map()

    # Cuentas desde CarterasAPI (fuente de verdad del mes actual)
    ids = sorted({
        str(d["id_cuenta"])
        for d in db_p["CarterasAPI"].find({}, {"_id": 0, "id_cuenta": 1})
        if d.get("id_cuenta")
    })
    # Nombres desde AuMAPI (tiene el campo 'cuenta' completo)
    aum_nombres = {
        str(d["_id"]): d.get("cuenta", "")
        for d in db_p["AumAPI"].aggregate([
            {"$group": {"_id": "$id_cuenta", "cuenta": {"$first": "$cuenta"}}},
        ])
    }
    cuentas = [
        {"id_cuenta": i, "cuenta": aum_nombres.get(i, i)}
        for i in ids
    ]

    if not id_cuenta:
        return {"cuentas": cuentas, "mes_actual": {}, "mes_anterior": {}}

    # Mes actual desde CarterasAPI (cantidad × precio con regla valuación)
    por_cartera: dict[str, float] = {}
    for d in db_p["CarterasAPI"].find(
        {"id_cuenta": id_cuenta},
        {"_id": 0, "unidad": 1, "cantidad": 1, "precio": 1},
    ):
        cant = float(d.get("cantidad") or 0)
        px = float(d.get("precio") or 0)
        asset = assets.get(d.get("unidad", ""), {})
        cartera = asset.get("cartera", "OTROS")
        clase = asset.get("clase_activo", "")
        val = _valuacion_api(cant, px, cartera, clase)
        por_cartera[cartera] = por_cartera.get(cartera, 0.0) + val
    mes_actual = {k: round(v, 2) for k, v in sorted(por_cartera.items()) if v > 0}

    # Mes anterior desde Valuaciones.CarterasII (valuacion ya pre-calculada por aum.py)
    db_v = get_db_valuaciones()
    por_cartera_prev: dict[str, float] = {}
    for d in db_v["CarterasII"].find(
        {"id_cuenta": id_cuenta},
        {"_id": 0, "unidad": 1, "valuacion": 1},
    ):
        val = float(d.get("valuacion") or 0)
        cartera = assets.get(d.get("unidad", ""), {}).get("cartera", "OTROS")
        por_cartera_prev[cartera] = por_cartera_prev.get(cartera, 0.0) + val
    mes_anterior = {k: round(v, 2) for k, v in sorted(por_cartera_prev.items()) if v > 0}

    return {"cuentas": cuentas, "mes_actual": mes_actual, "mes_anterior": mes_anterior}


@router.get("/detalle")
@cached(ttl=300)
def detalle_portfolio(
    id_cuenta: str = Query(..., description="id_cuenta de la cuenta a consultar"),
):
    """Posiciones individuales de una cuenta, enriquecidas con AssetsAPI.

    Devuelve cada posición con ticker, emisor, clase_activo, cartera,
    calificacion, vencimiento, cantidad, precio, valuacion y pct_share.
    """
    db_p = get_db_portfolio()
    assets = _assets_enrich_map()

    # Enrich map completo (ticker, emisor, calificacion, vencimiento)
    db_t = get_db_titulos()
    assets_full: dict[str, dict] = {
        d["unidad"]: d
        for d in db_t["AssetsAPI"].find({}, {"_id": 0})
        if d.get("unidad")
    }

    posiciones = []
    total = 0.0

    for d in db_p["CarterasAPI"].find(
        {"id_cuenta": id_cuenta},
        {"_id": 0, "unidad": 1, "cantidad": 1, "precio": 1},
    ):
        cant = float(d.get("cantidad") or 0)
        px = float(d.get("precio") or 0)
        unidad = d.get("unidad", "")
        enrich = assets.get(unidad, {})
        full = assets_full.get(unidad, {})
        cartera = enrich.get("cartera", "OTROS")
        clase = enrich.get("clase_activo", "")
        val = _valuacion_api(cant, px, cartera, clase)
        total += val
        posiciones.append({
            "unidad": unidad,
            "ticker": full.get("ticker") or unidad,
            "emisor": full.get("emisor") or "-",
            "clase_activo": clase or "-",
            "cartera": cartera,
            "calificacion": full.get("calificacion") or "-",
            "vencimiento": full.get("vencimiento"),
            "cantidad": round(cant, 6),
            "precio": round(px, 6),
            "valuacion": round(val, 2),
        })

    posiciones.sort(key=lambda p: (p["cartera"], -p["valuacion"]))
    for p in posiciones:
        p["pct"] = round(p["valuacion"] / total * 100, 2) if total > 0 else 0.0

    return {"posiciones": posiciones, "total": round(total, 2)}


@router.get("/tasa-fija")
@cached(ttl=300)
def tasa_fija_snapshot():
    """Posiciones de Tasa Fija del último snapshot AuM.

    Join: AumAPI (último) → AssetsAPI (clase_activo=FIJA) → ValuacionesAPI (curva=tasa_fija).
    Devuelve tickers con valuacion total, cobro proyectado y detalle por cuenta.
    """
    db_p = get_db_portfolio()
    db_t = get_db_titulos()

    # 1. AssetsAPI: unidad → ticker para clase_activo FIJA (puede ser mayúscula o minúscula)
    assets_fija: dict[str, str] = {}
    for d in db_t["AssetsAPI"].find(
        {"clase_activo": {"$in": ["FIJA", "fija"]}},
        {"_id": 0, "unidad": 1, "ticker": 1},
    ):
        if d.get("unidad") and d.get("ticker"):
            assets_fija[d["unidad"]] = d["ticker"]

    if not assets_fija:
        return {"fecha": None, "total_valuacion": 0, "total_cobro": 0, "tickers": []}

    # 2. ValuacionesAPI: ticker → {flujo_vencimiento, fecha_vencimiento}
    flujos_map: dict[str, dict] = {}
    for d in db_t["ValuacionesAPI"].find(
        {"curva": "tasa_fija"},
        {"_id": 0, "ticker": 1, "flujo_vencimiento": 1, "fecha_vencimiento": 1},
    ):
        t = d.get("ticker")
        if t:
            flujos_map[t] = {
                "flujo_vencimiento": float(d.get("flujo_vencimiento") or 0),
                "fecha_vencimiento": d.get("fecha_vencimiento"),
            }

    # 3. AumAPI último snapshot, filtrar a unidades FIJA
    last = db_p["AumAPI"].find_one({}, {"fecha": 1, "_id": 0}, sort=[("fecha", -1)])
    if not last:
        return {"fecha": None, "total_valuacion": 0, "total_cobro": 0, "tickers": []}

    fecha = last["fecha"]
    unidades_fija = list(assets_fija.keys())

    docs = list(db_p["AumAPI"].find(
        {"fecha": fecha, "unidad": {"$in": unidades_fija}},
        {"_id": 0, "unidad": 1, "cuenta": 1, "id_cuenta": 1, "valuacion": 1, "cantidad": 1},
    ))

    # 4. Agrupar por ticker
    by_ticker: dict[str, dict] = {}
    for d in docs:
        unidad = d.get("unidad", "")
        ticker = assets_fija.get(unidad, "")
        if not ticker or ticker not in flujos_map:
            continue
        flujo = flujos_map[ticker]
        cant = float(d.get("cantidad") or 0)
        val  = float(d.get("valuacion") or 0)
        cobro = cant * flujo["flujo_vencimiento"] / 100

        if ticker not in by_ticker:
            by_ticker[ticker] = {
                "ticker":            ticker,
                "fecha_vencimiento": flujo["fecha_vencimiento"],
                "flujo_vencimiento": flujo["flujo_vencimiento"],
                "valuacion":         0.0,
                "cantidad":          0.0,
                "cobro_proyectado":  0.0,
                "cuentas":           [],
            }
        by_ticker[ticker]["valuacion"]        += val
        by_ticker[ticker]["cantidad"]         += cant
        by_ticker[ticker]["cobro_proyectado"] += cobro
        by_ticker[ticker]["cuentas"].append({
            "cuenta":           d.get("cuenta", ""),
            "id_cuenta":        d.get("id_cuenta", ""),
            "valuacion":        round(val, 2),
            "cantidad":         round(cant, 2),
            "cobro_proyectado": round(cobro, 2),
        })

    tickers = sorted(by_ticker.values(), key=lambda t: t.get("fecha_vencimiento") or "")
    for t in tickers:
        t["valuacion"]       = round(t["valuacion"], 2)
        t["cantidad"]        = round(t["cantidad"], 2)
        t["cobro_proyectado"]= round(t["cobro_proyectado"], 2)
        # fecha_vencimiento como string YYYY-MM-DD
        fv = t["fecha_vencimiento"]
        if fv and not isinstance(fv, str):
            t["fecha_vencimiento"] = str(fv)[:10]
        elif fv:
            t["fecha_vencimiento"] = str(fv)[:10]

    total_val   = round(sum(t["valuacion"]        for t in tickers), 2)
    total_cobro = round(sum(t["cobro_proyectado"] for t in tickers), 2)
    fecha_str = str(fecha)[:10] if fecha else None

    return {
        "fecha":           fecha_str,
        "total_valuacion": total_val,
        "total_cobro":     total_cobro,
        "tickers":         tickers,
    }


@router.get("/cer")
@cached(ttl=300)
def cer_snapshot():
    """Posiciones CER del último snapshot AuM.

    Join: ValuacionesAPI (curva=cer) → AssetsAPI (por ticker) → AumAPI (último).
    Devuelve tickers con valuacion total, cantidad (VN) y detalle por cuenta.

    A diferencia de /tasa-fija NO se calcula "cobro proyectado" porque el cash
    flow de un bono CER al vto depende del CER futuro (no se puede proyectar
    determinísticamente). Se agrega `paridad` y `tea` del último trade
    enriquecido en Trading.TimeSales si están disponibles.
    """
    db_p = get_db_portfolio()
    db_t = get_db_titulos()
    db_tr = get_db_trading()

    # 1. ValuacionesAPI: tickers con curva=cer → {ticker, fecha_vencimiento}
    tickers_cer: dict[str, dict] = {}
    for d in db_t["ValuacionesAPI"].find(
        {"curva": "cer"},
        {"_id": 0, "ticker": 1, "fecha_vencimiento": 1},
    ):
        t = d.get("ticker")
        if t:
            fv = d.get("fecha_vencimiento")
            tickers_cer[t] = {
                "fecha_vencimiento": str(fv)[:10] if fv else None,
            }

    if not tickers_cer:
        return {"fecha": None, "total_valuacion": 0, "tickers": []}

    # 2. AssetsAPI: resolver unidad ↔ ticker. Los bonos CER pueden tener más
    # de una unidad (distintos settlements); matcheamos por ticker.
    ticker_to_unidades: dict[str, list[str]] = {}
    unidad_to_ticker: dict[str, str] = {}
    for d in db_t["AssetsAPI"].find(
        {"ticker": {"$in": list(tickers_cer.keys())}},
        {"_id": 0, "unidad": 1, "ticker": 1},
    ):
        u = d.get("unidad")
        t = d.get("ticker")
        if u and t:
            ticker_to_unidades.setdefault(t, []).append(u)
            unidad_to_ticker[u] = t

    if not unidad_to_ticker:
        return {"fecha": None, "total_valuacion": 0, "tickers": []}

    # 3. Último TEA/paridad por ticker desde Trading.TimeSales (opcional,
    # best-effort: buscamos en los últimos trades por ticker_corto).
    tea_paridad: dict[str, dict] = {}
    try:
        # En TimeSales el ticker incluye el prefijo ROFEX (MERV - XMEV - TX26 - 24hs).
        # Buscamos con regex substring del ticker corto.
        for short_ticker in tickers_cer:
            last = db_tr["TimeSales"].find_one(
                {"ticker": {"$regex": short_ticker, "$options": "i"},
                 "TEA": {"$exists": True}},
                {"_id": 0, "TEA": 1, "paridad": 1, "duration": 1, "timestamp": 1},
                sort=[("timestamp", -1)],
            )
            if last:
                tea_paridad[short_ticker] = {
                    "tea":      last.get("TEA"),
                    "paridad":  last.get("paridad"),
                    "duration": last.get("duration"),
                }
    except Exception:
        pass

    # 4. AumAPI último snapshot, filtrar a unidades CER
    last = db_p["AumAPI"].find_one({}, {"fecha": 1, "_id": 0}, sort=[("fecha", -1)])
    if not last:
        return {"fecha": None, "total_valuacion": 0, "tickers": []}

    fecha = last["fecha"]
    unidades = list(unidad_to_ticker.keys())

    docs = list(db_p["AumAPI"].find(
        {"fecha": fecha, "unidad": {"$in": unidades}},
        {"_id": 0, "unidad": 1, "cuenta": 1, "id_cuenta": 1, "valuacion": 1, "cantidad": 1},
    ))

    # 5. Agrupar por ticker
    by_ticker: dict[str, dict] = {}
    for d in docs:
        unidad = d.get("unidad", "")
        ticker = unidad_to_ticker.get(unidad, "")
        if not ticker:
            continue
        meta = tickers_cer[ticker]
        cant = float(d.get("cantidad") or 0)
        val  = float(d.get("valuacion") or 0)

        if ticker not in by_ticker:
            tp = tea_paridad.get(ticker, {})
            by_ticker[ticker] = {
                "ticker":            ticker,
                "fecha_vencimiento": meta["fecha_vencimiento"],
                "valuacion":         0.0,
                "cantidad":          0.0,
                "tea":               tp.get("tea"),
                "paridad":           tp.get("paridad"),
                "duration":          tp.get("duration"),
                "cuentas":           [],
            }
        by_ticker[ticker]["valuacion"] += val
        by_ticker[ticker]["cantidad"]  += cant
        by_ticker[ticker]["cuentas"].append({
            "cuenta":    d.get("cuenta", ""),
            "id_cuenta": d.get("id_cuenta", ""),
            "valuacion": round(val, 2),
            "cantidad":  round(cant, 2),
        })

    tickers_out = sorted(by_ticker.values(), key=lambda t: t.get("fecha_vencimiento") or "")
    for t in tickers_out:
        t["valuacion"] = round(t["valuacion"], 2)
        t["cantidad"]  = round(t["cantidad"], 2)

    total_val = round(sum(t["valuacion"] for t in tickers_out), 2)
    fecha_str = str(fecha)[:10] if fecha else None

    return {
        "fecha":           fecha_str,
        "total_valuacion": total_val,
        "tickers":         tickers_out,
    }


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

    cursor = db_v["AuMResumenFCI"].find(filtro, {"_id": 0}).sort("fecha_snapshot", 1).limit(730)
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
