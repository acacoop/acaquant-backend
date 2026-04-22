"""Capa de servicio — portfolio / AuM / carteras / FCI.

Lógica pura (sin FastAPI) sobre las colecciones `PortfolioAPI`, `TitulosAPI`,
`Valuaciones`. El router `api/routers/carteras.py` es un thin wrapper que
parsea query params y delega acá. Beneficios del corte:

- Tests deterministas sin levantar uvicorn.
- Agente puede dispatchear directo si en el futuro se relajan los
  `BLOCKED_PATH_PREFIXES`.
- Caché compartido entre router y eventual dispatch interno.

Regla de valuación centralizada en `_valuacion_api`. Fórmulas — ver
`docs/ARCHITECTURE.md` §9 / CLAUDE.md §1.
"""
from __future__ import annotations

from datetime import datetime

from api.cache import cached
from api.db import get_db_portfolio, get_db_titulos, get_db_trading, get_db_valuaciones

_PROJ_CARTERAS = {
    "_id": 0, "id_cuenta": 1, "unidad": 1, "cantidad": 1,
    "precio": 1, "timestamp": 1,
}
_PROJ_AUM = {
    "_id": 0, "fecha": 1, "id_cuenta": 1, "unidad": 1,
    "cantidad": 1, "cuenta": 1, "precio": 1, "valuacion": 1,
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers internos
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=600)
def _fci_assets_map() -> dict[str, dict]:
    """Mapea unidad → {emisor, ticker} para unidades con CARTERA=CARTERA FCI.

    Cacheado 10 min — los assets FCI cambian como mucho mensualmente.
    """
    db_t = get_db_titulos()
    return {
        d["unidad"]: {"emisor": d.get("emisor", ""), "ticker": d.get("ticker", "")}
        for d in db_t["AssetsAPI"].find(
            {"cartera": "CARTERA FCI"},
            {"_id": 0, "unidad": 1, "emisor": 1, "ticker": 1},
        )
    }


@cached(ttl=600)
def _assets_enrich_map() -> dict[str, dict]:
    """unidad → {cartera, clase_activo} desde TitulosAPI.AssetsAPI (cacheado 10 min)."""
    db_t = get_db_titulos()
    return {
        d["unidad"]: {
            "cartera": d.get("cartera") or "OTROS",
            "clase_activo": d.get("clase_activo") or "",
        }
        for d in db_t["AssetsAPI"].find(
            {}, {"_id": 0, "unidad": 1, "cartera": 1, "clase_activo": 1}
        )
        if d.get("unidad")
    }


def _valuacion_api(cant: float, px: float, cartera: str, clase_activo: str) -> float:
    """Regla de valuación: FCI o clase OTROS → P×Q directo, resto → P×Q/100."""
    if clase_activo == "OTROS" or "FCI" in cartera:
        return cant * px
    return cant * px / 100


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints raw: /carteras, /aum
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=300)
def listar_carteras(
    id_cuenta: str | None = None,
    unidad: str | None = None,
) -> list:
    """Posiciones actuales desde PortfolioAPI.CarterasAPI."""
    db = get_db_portfolio()
    filtro: dict = {}
    if id_cuenta:
        filtro["id_cuenta"] = id_cuenta
    if unidad:
        filtro["unidad"] = unidad
    return list(db["CarterasAPI"].find(filtro, _PROJ_CARTERAS))


@cached(ttl=300)
def listar_aum(
    id_cuenta: str | None = None,
    unidad: str | None = None,
    cuenta: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    ultimo: bool = False,
) -> list:
    """Snapshots AuM filtrables por cuenta/unidad/rango. `ultimo=True` ignora rango."""
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


# ─────────────────────────────────────────────────────────────────────────────
# /resumen y /detalle
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=300)
def resumen_portfolio(id_cuenta: str | None = None) -> dict:
    """Resumen ejecutivo: lista de cuentas + breakdown por CARTERA del mes actual.

    Lee PortfolioAPI.CarterasAPI (mes vigente) + TitulosAPI.AssetsAPI (clasificación).
    Sin id_cuenta devuelve solo la lista de cuentas.
    """
    db_p = get_db_portfolio()
    assets = _assets_enrich_map()

    ids = sorted({
        str(d["id_cuenta"])
        for d in db_p["CarterasAPI"].find({}, {"_id": 0, "id_cuenta": 1})
        if d.get("id_cuenta")
    })
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


@cached(ttl=300)
def detalle_portfolio(id_cuenta: str) -> dict:
    """Posiciones individuales de una cuenta, enriquecidas con AssetsAPI.

    Devuelve cada posición con ticker, emisor, clase_activo, cartera,
    calificacion, vencimiento, cantidad, precio, valuacion y pct_share.
    """
    db_p = get_db_portfolio()
    assets = _assets_enrich_map()

    db_t = get_db_titulos()
    assets_full: dict[str, dict] = {
        d["unidad"]: d
        for d in db_t["AssetsAPI"].find({}, {"_id": 0})
        if d.get("unidad")
    }

    posiciones: list[dict] = []
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


# ─────────────────────────────────────────────────────────────────────────────
# /tasa-fija y /cer (buckets del último snapshot AuM)
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=300)
def tasa_fija_snapshot() -> dict:
    """Posiciones de Tasa Fija del último snapshot AuM.

    Join: AumAPI (último) → AssetsAPI (clase_activo=FIJA) → ValuacionesAPI (curva=tasa_fija).
    Devuelve tickers con valuacion total, cobro proyectado y detalle por cuenta.
    """
    db_p = get_db_portfolio()
    db_t = get_db_titulos()

    assets_fija: dict[str, str] = {}
    for d in db_t["AssetsAPI"].find(
        {"clase_activo": {"$in": ["FIJA", "fija"]}},
        {"_id": 0, "unidad": 1, "ticker": 1},
    ):
        if d.get("unidad") and d.get("ticker"):
            assets_fija[d["unidad"]] = d["ticker"]

    if not assets_fija:
        return {"fecha": None, "total_valuacion": 0, "total_cobro": 0, "tickers": []}

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

    last = db_p["AumAPI"].find_one({}, {"fecha": 1, "_id": 0}, sort=[("fecha", -1)])
    if not last:
        return {"fecha": None, "total_valuacion": 0, "total_cobro": 0, "tickers": []}

    fecha = last["fecha"]
    unidades_fija = list(assets_fija.keys())

    docs = list(db_p["AumAPI"].find(
        {"fecha": fecha, "unidad": {"$in": unidades_fija}},
        {"_id": 0, "unidad": 1, "cuenta": 1, "id_cuenta": 1, "valuacion": 1, "cantidad": 1},
    ))

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
        fv = t["fecha_vencimiento"]
        if fv:
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


@cached(ttl=300)
def cer_snapshot() -> dict:
    """Posiciones CER del último snapshot AuM.

    Join: ValuacionesAPI (curva=cer) → AssetsAPI (por ticker) → AumAPI (último).
    Devuelve tickers con valuacion total, cantidad (VN) y detalle por cuenta.

    A diferencia de tasa_fija_snapshot NO se calcula "cobro proyectado" porque el
    cash flow de un bono CER al vto depende del CER futuro (no se puede proyectar
    determinísticamente). Se agrega `paridad` y `tea` del último trade enriquecido
    en Trading.TimeSales si están disponibles.
    """
    db_p = get_db_portfolio()
    db_t = get_db_titulos()
    db_tr = get_db_trading()

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

    tea_paridad: dict[str, dict] = {}
    try:
        curvas_map = {
            c["ticker_corto"]: c["ticker"]
            for c in db_tr["Curvas"].find(
                {"ticker_corto": {"$in": list(tickers_cer.keys())}},
                {"_id": 0, "ticker": 1, "ticker_corto": 1},
            )
            if c.get("ticker") and c.get("ticker_corto")
        }
        if curvas_map:
            ticker_to_short = {v: k for k, v in curvas_map.items()}
            pipeline = [
                # Exigir también duration: hay docs con TEA stampeada pero sin
                # duration (hueco de enriquecimiento). Sin este filtro, el
                # $first podía devolver null en duration.
                {"$match": {"ticker": {"$in": list(curvas_map.values())},
                            "TEA": {"$exists": True},
                            "duration": {"$exists": True}}},
                {"$sort": {"timestamp": -1}},
                {"$group": {
                    "_id":       "$ticker",
                    "TEA":       {"$first": "$TEA"},
                    "paridad":   {"$first": "$paridad"},
                    "duration":  {"$first": "$duration"},
                    "timestamp": {"$first": "$timestamp"},
                }},
            ]
            for row in db_tr["TimeSales"].aggregate(pipeline):
                short = ticker_to_short.get(row["_id"])
                if short:
                    tea_paridad[short] = {
                        "tea":      row.get("TEA"),
                        "paridad":  row.get("paridad"),
                        "duration": row.get("duration"),
                    }
    except Exception:
        pass

    last = db_p["AumAPI"].find_one({}, {"fecha": 1, "_id": 0}, sort=[("fecha", -1)])
    if not last:
        return {"fecha": None, "total_valuacion": 0, "tickers": []}

    fecha = last["fecha"]
    unidades = list(unidad_to_ticker.keys())

    docs = list(db_p["AumAPI"].find(
        {"fecha": fecha, "unidad": {"$in": unidades}},
        {"_id": 0, "unidad": 1, "cuenta": 1, "id_cuenta": 1, "valuacion": 1, "cantidad": 1},
    ))

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


# ─────────────────────────────────────────────────────────────────────────────
# FCI (/fci-serie, /fci-snapshot)
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=300)
def fci_serie(desde: str | None = None, hasta: str | None = None) -> list:
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


@cached(ttl=300)
def fci_snapshot(fecha: str) -> list:
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
