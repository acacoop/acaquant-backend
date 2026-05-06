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
from api.services._cuentas_filter import match_cuenta_filter
from api.services._mep import get_mep_for_date

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

    Lee `Valuaciones.Assets` UPPERCASE (fuente de verdad). Misma fuente que
    `jobs/aum_resumen_fci.py::_fci_unidades` para que el set de unidades FCI
    sea idéntico entre el rollup que alimenta el KPI "TOTAL FCI HOY" y el
    snapshot panel — antes leíamos `TitulosAPI.AssetsAPI` (lowercase, copia
    derivada) y se desincronizaban cuando se editaba el master sin correr
    el sync. Cacheado 10 min — los assets FCI cambian como mucho mensualmente.
    """
    db_v = get_db_valuaciones()
    return {
        d["unidad"]: {"emisor": d.get("EMISOR", ""), "ticker": d.get("TICKER", "")}
        for d in db_v["Assets"].find(
            {"CARTERA": "CARTERA FCI"},
            {"_id": 0, "unidad": 1, "EMISOR": 1, "TICKER": 1},
        )
        if d.get("unidad")
    }


@cached(ttl=600)
def _assets_enrich_map() -> dict[str, dict]:
    """unidad → {cartera, clase_activo} desde Valuaciones.Assets UPPERCASE
    (fuente de verdad — ver `_fci_assets_map` para la motivación)."""
    db_v = get_db_valuaciones()
    return {
        d["unidad"]: {
            "cartera": d.get("CARTERA") or "OTROS",
            "clase_activo": d.get("CLASE_ACTIVO") or "",
        }
        for d in db_v["Assets"].find(
            {}, {"_id": 0, "unidad": 1, "CARTERA": 1, "CLASE_ACTIVO": 1}
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
            # Lee última TEA/paridad/duration desde MarketSnapshot.metrics.
            # Antes agregaba TimeSales con $group/$first; este path lee
            # 1 doc por ticker (find directo). El valor es idéntico:
            # curvas.py escribe en MarketSnapshot la misma TEA/duration/
            # paridad del último trade enriquecido.
            for row in db_tr["MarketSnapshot"].find(
                {"ticker": {"$in": list(curvas_map.values())},
                 "metrics.TEA": {"$exists": True, "$ne": None},
                 "metrics.duration": {"$exists": True, "$ne": None}},
                {"_id": 0, "ticker": 1,
                 "metrics.TEA": 1, "metrics.paridad": 1, "metrics.duration": 1},
            ):
                short = ticker_to_short.get(row["ticker"])
                if not short:
                    continue
                m = row.get("metrics") or {}
                tea_paridad[short] = {
                    "tea":      m.get("TEA"),
                    "paridad":  m.get("paridad"),
                    "duration": m.get("duration"),
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
def fci_serie(
    desde: str | None = None,
    hasta: str | None = None,
    cuenta_filter: str = "todas",
) -> list:
    """Serie histórica FCI: total por fecha + desglose por emisor.

    Sin filtro de cuenta (default "todas"): lee `Valuaciones.AuMResumenFCI`
    (rollup 1 doc/fecha — barato, ya pre-agregado).

    Con filtro de cuenta: el rollup no soporta breakdown por cuenta, así que
    cae a `Valuaciones.AuM` raw + $match cuenta_filter + $group por
    (fecha_snapshot, unidad). Mismo set de unidades FCI que el rollup
    (definido por `_fci_assets_map`, fuente Valuaciones.Assets UPPERCASE).
    """
    db_v = get_db_valuaciones()
    assets_map = _fci_assets_map()

    if cuenta_filter and cuenta_filter != "todas":
        # Camino raw — paga la performance del filter.
        unidades_fci = list(assets_map.keys())
        if not unidades_fci:
            return []
        match: dict = {"unidad": {"$in": unidades_fci}}
        match.update(match_cuenta_filter(cuenta_filter))
        if desde or hasta:
            rango: dict = {}
            if desde:
                rango["$gte"] = desde
            if hasta:
                rango["$lte"] = hasta
            match["fecha_snapshot"] = rango
        pipeline = [
            {"$match": match},
            {"$group": {
                "_id": {"fecha": "$fecha_snapshot", "unidad": "$unidad"},
                "valuacion_total": {"$sum": "$valuacion"},
            }},
            {"$sort": {"_id.fecha": 1}},
        ]
        rows = list(db_v["AuM"].aggregate(pipeline))
        bucket: dict[str, dict] = {}
        for r in rows:
            fecha_str = str(r["_id"]["fecha"])[:10]
            unidad = r["_id"]["unidad"]
            val = float(r.get("valuacion_total") or 0)
            emisor = assets_map.get(unidad, {}).get("emisor", "") or "SIN EMISOR"
            b = bucket.setdefault(fecha_str, {"total": 0.0, "por_emisor": {}})
            b["total"] += val
            b["por_emisor"][emisor] = b["por_emisor"].get(emisor, 0.0) + val
        return [
            {"fecha": f, "total": v["total"], "por_emisor": v["por_emisor"]}
            for f, v in sorted(bucket.items())
        ]

    # Camino default — rollup pre-agregado.
    filtro: dict = {}
    if desde or hasta:
        rango = {}
        if desde:
            rango["$gte"] = desde
        if hasta:
            rango["$lte"] = hasta
        filtro["fecha_snapshot"] = rango

    cursor = db_v["AuMResumenFCI"].find(filtro, {"_id": 0}).sort("fecha_snapshot", 1).limit(730)

    out = []
    for doc in cursor:
        fecha = doc.get("fecha_snapshot")
        fecha_str = fecha.strftime("%Y-%m-%d") if isinstance(fecha, datetime) else str(fecha)[:10]

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
def fci_snapshot(fecha: str, cuenta_filter: str = "todas") -> list:
    """Snapshot FCI en una fecha: detalle por unidad/emisor/cuenta.

    Lee `Valuaciones.AuM` (fuente de verdad). Mismo set de unidades FCI
    que `fci_serie` (definido por `_fci_assets_map`).
    """
    db_v = get_db_valuaciones()
    assets_map = _fci_assets_map()
    unidades_fci = list(assets_map.keys())

    if not unidades_fci:
        return []

    match: dict = {"fecha_snapshot": fecha, "unidad": {"$in": unidades_fci}}
    match.update(match_cuenta_filter(cuenta_filter))

    docs = db_v["AuM"].find(
        match,
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


# ─────────────────────────────────────────────────────────────────────────────
# TOTAL (/total-serie, /total-snapshot) — agregado por CARTERA, no por emisor.
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=300)
def total_serie(
    desde: str | None = None,
    hasta: str | None = None,
    cuenta_filter: str = "todas",
    moneda: str = "ARS",
) -> dict:
    """Serie histórica del AuM total agrupado por CARTERA.

    Lee `Valuaciones.AuM` raw y enriquece cada unidad con `cartera` desde
    `Valuaciones.Assets` UPPERCASE.

    Si `moneda == "USD"`: convierte cada doc dividiendo por el MEP de la
    fecha del snapshot (todo el AuM se persiste en ARS, incluso unidades
    USD/USDC). Las fechas para las que no hay MEP se reportan en
    `fechas_sin_mep` y conservan el valor en ARS sin convertir, para que
    el caller pueda decidir cómo mostrarlas.

    Returns:
        {
          serie: [{fecha, total, por_cartera, mep_used (si USD)}, ...],
          moneda: "ARS" | "USD",
          fechas_sin_mep: [<list>]  (siempre [], salvo moneda=USD)
        }
    """
    db_v = get_db_valuaciones()
    enrich = _assets_enrich_map()

    match: dict = {}
    match.update(match_cuenta_filter(cuenta_filter))
    if desde or hasta:
        rango: dict = {}
        if desde:
            rango["$gte"] = desde
        if hasta:
            rango["$lte"] = hasta
        match["fecha_snapshot"] = rango

    pipeline = [
        {"$match": match} if match else {"$match": {}},
        {"$group": {
            "_id": {"fecha": "$fecha_snapshot", "unidad": "$unidad"},
            "valuacion_total": {"$sum": "$valuacion"},
        }},
        {"$sort": {"_id.fecha": 1}},
    ]

    rows = list(db_v["AuM"].aggregate(pipeline))

    # Cache MEP por fecha — varias unidades de la misma fecha lo comparten.
    mep_cache: dict[str, float | None] = {}
    fechas_sin_mep: set[str] = set()

    def _mep(f: str) -> float | None:
        if f not in mep_cache:
            mep_cache[f] = get_mep_for_date(f)
        return mep_cache[f]

    bucket: dict[str, dict] = {}
    for r in rows:
        fecha_str = str(r["_id"]["fecha"])[:10]
        unidad = r["_id"]["unidad"]
        val = float(r.get("valuacion_total") or 0)
        if moneda == "USD":
            mep = _mep(fecha_str)
            if mep:
                val = val / mep
            else:
                fechas_sin_mep.add(fecha_str)
        cartera = enrich.get(unidad, {}).get("cartera") or "OTROS"
        b = bucket.setdefault(fecha_str, {"total": 0.0, "por_cartera": {}})
        b["total"] += val
        b["por_cartera"][cartera] = b["por_cartera"].get(cartera, 0.0) + val

    serie = []
    for f, v in sorted(bucket.items()):
        row = {"fecha": f, "total": v["total"], "por_cartera": v["por_cartera"]}
        if moneda == "USD":
            row["mep_used"] = mep_cache.get(f)
        serie.append(row)

    return {
        "serie": serie,
        "moneda": moneda,
        "fechas_sin_mep": sorted(fechas_sin_mep),
    }


@cached(ttl=300)
def total_snapshot(
    fecha: str,
    cuenta_filter: str = "todas",
    moneda: str = "ARS",
) -> dict:
    """Snapshot del AuM total en una fecha: detalle por unidad/cartera/cuenta.

    Si `moneda == "USD"`: divide cada `valuacion` por el MEP de `fecha`. Si
    no hay MEP disponible para esa fecha, devuelve los valores en ARS y
    setea `mep_missing=True` para que el frontend muestre un aviso.
    """
    db_v = get_db_valuaciones()
    enrich = _assets_enrich_map()

    match: dict = {"fecha_snapshot": fecha}
    match.update(match_cuenta_filter(cuenta_filter))

    docs = list(db_v["AuM"].find(
        match,
        {"_id": 0, "unidad": 1, "cuenta": 1, "id_cuenta": 1,
         "valuacion": 1, "cantidad": 1, "tipoTitulo": 1},
    ))

    mep: float | None = None
    mep_missing = False
    if moneda == "USD":
        mep = get_mep_for_date(fecha)
        if mep is None:
            mep_missing = True

    out = []
    for d in docs:
        unidad = d.get("unidad", "")
        meta = enrich.get(unidad, {})
        cartera = meta.get("cartera") or "OTROS"
        val = float(d.get("valuacion") or 0)
        if moneda == "USD" and mep:
            val = val / mep
        out.append({
            "unidad": unidad,
            "cartera": cartera,
            "tipo": d.get("tipoTitulo") or "",
            "cuenta": d.get("cuenta", ""),
            "id_cuenta": d.get("id_cuenta", ""),
            "valuacion": val,
            "cantidad": float(d.get("cantidad") or 0),
        })

    return {
        "docs": out,
        "moneda": moneda,
        "mep_used": mep,
        "mep_missing": mep_missing,
    }
