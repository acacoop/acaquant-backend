"""GET /api/manager/checks/* — validaciones de consistencia sobre Mongo."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from core.mongo import get_mongo_client_read

router = APIRouter()


@router.get("/checks/debug-comercial")
def check_debug_comercial(
    operador: str | None = Query(None, description="operador_email a auditar"),
    segmento: str | None = Query(None, description="nivel_1 a auditar"),
    moneda: str = Query("ARS", description="ARS | USD"),
):
    """Auditoría del Informe comercial: desglose por cuenta (# ops, volumen,
    arancel) + totales + ticket promedio, para un operador o un segmento."""
    from api.services.comercial import debug_comercial
    return debug_comercial(operador=operador, segmento=segmento, moneda=moneda)


@router.get("/checks/curvas-pendientes")
def check_curvas_pendientes():
    """Docs sin duration en TimeSales agrupados por ticker."""
    client = get_mongo_client_read()
    rows = list(client["Trading"]["TimeSales"].aggregate([
        {"$match": {"duration": {"$exists": False}}},
        {"$group": {"_id": "$ticker", "pendientes": {"$sum": 1}}},
        {"$sort": {"pendientes": -1}},
    ]))
    total = sum(r["pendientes"] for r in rows)
    return {"total": total, "ok": total == 0,
            "tickers": [{"ticker": r["_id"], "pendientes": r["pendientes"]} for r in rows]}


@router.get("/checks/forwards")
def check_forwards():
    """TEA disponible por instrumento en Curvas vs ForwardsLive."""
    client = get_mongo_client_read()
    db = client["Trading"]
    grupos: dict[str, list] = {}
    for d in db["Curvas"].find({}):
        grupos.setdefault(d.get("curva", "?"), []).append(d)

    all_tickers = [i["ticker"] for insts in grupos.values() for i in insts if i.get("ticker")]
    teas_all: dict = {}
    if all_tickers:
        for r in db["TimeSales"].aggregate([
            {"$match": {"ticker": {"$in": all_tickers}, "TEA": {"$exists": True}, "duration": {"$exists": True}}},
            {"$sort": {"timestamp": -1}},
            {"$group": {"_id": "$ticker", "TEA": {"$first": "$TEA"}, "duration": {"$first": "$duration"}, "ts": {"$first": "$timestamp"}}},
        ]):
            teas_all[r["_id"]] = r

    resultado = []
    for curva, instrumentos in sorted(grupos.items()):
        insts = sorted(instrumentos, key=lambda x: x.get("fecha_vencimiento") or "9999")
        tickers_curva = []
        for inst in insts:
            tk = inst.get("ticker", "?")
            datos = teas_all.get(tk)
            tickers_curva.append({
                "ticker":   inst.get("ticker_corto", "?"),
                "vto":      str(inst.get("fecha_vencimiento") or "?")[:10],
                "tea":      round(datos["TEA"] * 100, 4) if datos else None,
                "duration": round(datos["duration"], 4) if datos else None,
                "ultimo":   datos["ts"].strftime("%d/%m %H:%M") if datos and datos.get("ts") else None,
                "ok":       datos is not None,
            })
        live = db["ForwardsLive"].find_one({"curva": curva}, {"tickers": 1})  # perf-ok: PERF002 — endpoint admin, N = curvas (~5), indexado
        live_tickers = live.get("tickers", []) if live else []
        expected = [i.get("ticker_corto") for i in insts if teas_all.get(i.get("ticker"))]
        resultado.append({"curva": curva, "tickers": tickers_curva,
                          "live_ok": live_tickers == expected,
                          "live_tickers": live_tickers, "expected_tickers": expected})
    return resultado


@router.get("/checks/cer")
def check_cer():
    """CER usado en el último trade enriquecido por bono CER."""
    import bisect
    from datetime import date, timedelta
    client = get_mongo_client_read()
    db = client["Trading"]
    curvas_cer = list(db["Curvas"].find({"curva": "cer"},
                                        {"ticker": 1, "ticker_corto": 1, "cer_emision": 1}))
    if not curvas_cer:
        return {"cer_reciente": None, "dias_habiles": 0, "instrumentos": []}

    cer_dict = {d["fecha"]: float(d["valor"]) for d in db["CER"].find({}, {"fecha": 1, "valor": 1})}
    dias_hab = sorted(d["fecha"] for d in db["DiasHabiles"].find({}, {"fecha": 1, "_id": 0}))
    cer_reciente = max(cer_dict.keys()) if cer_dict else None

    def _cer_en_fecha(fd):
        for i in range(7):
            k = (fd - timedelta(days=i)).isoformat()
            if k in cer_dict:
                return k, cer_dict[k]
        return None, None

    def _cer_liq(settle_str, n=10):
        # Último día hábil <= settle_str (dias_hab está sorted asc).
        # bisect_right da el primer índice > settle_str → restamos 1 para el
        # último <=. Si settle es anterior a todo el calendario, idx = -1.
        idx = bisect.bisect_right(dias_hab, settle_str) - 1
        if idx < n:
            return None, None
        return _cer_en_fecha(date.fromisoformat(dias_hab[idx - n]))

    def _next_habil(fd):
        s = fd.isoformat()
        return next((f for f in dias_hab if f > s), None)

    rows = []
    for inst in curvas_cer:
        doc = db["TimeSales"].find_one(  # perf-ok: PERF002 — endpoint admin, N = bonos CER (~20), idx ticker+timestamp
            {"ticker": inst["ticker"], "duration": {"$exists": True}}, sort=[("timestamp", -1)]
        )
        if not doc:
            rows.append({"ticker": inst.get("ticker_corto", "?"), "ok": False})
            continue
        ts = doc["timestamp"]
        fd = ts.date() if hasattr(ts, "date") else date.fromisoformat(str(ts)[:10])
        settle = _next_habil(fd)
        cf, cv = _cer_liq(settle) if settle else (None, None)
        ce = inst.get("cer_emision")
        ratio = round(cv / ce, 6) if (cv and ce) else None
        rows.append({
            "ticker":       inst.get("ticker_corto", "?"),
            "ultimo_trade": ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, "strftime") else str(ts)[:16],
            "settlement":   settle, "cer_fecha": cf,
            "cer_valor":    round(cv, 6) if cv else None,
            "cer_emision":  round(ce, 6) if ce else None,
            "ratio":        ratio,
            "paridad":      round(doc.get("paridad", 0), 2) if doc.get("paridad") else None,
            "ok":           ratio is not None,
        })
    return {"cer_reciente": cer_reciente, "dias_habiles": len(dias_hab), "instrumentos": rows}


@router.get("/checks/tasa-fija")
def check_tasa_fija():
    """Estado de instrumentos tasa_fija en AuM."""
    client = get_mongo_client_read()
    db_t = client["Trading"]
    db_v = client["Valuaciones"]
    curvas_tf = list(db_t["Curvas"].find({"curva": "tasa_fija"}, {"_id": 0, "ticker_corto": 1}))
    if not curvas_tf:
        return {"snapshot": None, "ok": 0, "sin_posicion": 0, "sin_assets": 0, "instrumentos": []}

    t2u: dict[str, list] = {}
    for a in db_v["Assets"].find({}, {"_id": 0, "TICKER": 1, "unidad": 1}):
        t2u.setdefault(a.get("TICKER", ""), []).append(a["unidad"])

    uf = db_v["AuM"].find_one(sort=[("fecha_snapshot", -1)], projection={"fecha_snapshot": 1})
    fm = uf["fecha_snapshot"] if uf else None
    con_pos = {d["unidad"] for d in
               db_v["AuM"].find({"fecha_snapshot": fm}, {"_id": 0, "unidad": 1, "valuacion": 1})
               if (d.get("valuacion") or 0) != 0} if fm else set()

    rows = []
    for c in sorted(curvas_tf, key=lambda x: x.get("ticker_corto", "")):
        tc = c["ticker_corto"]
        uns = t2u.get(tc, [])
        estado = "ok" if any(u in con_pos for u in uns) else ("sin_assets" if not uns else "sin_posicion")
        rows.append({"ticker": tc, "estado": estado})

    return {"snapshot": str(fm)[:10] if fm else None,
            "ok": sum(1 for r in rows if r["estado"] == "ok"),
            "sin_posicion": sum(1 for r in rows if r["estado"] == "sin_posicion"),
            "sin_assets": sum(1 for r in rows if r["estado"] == "sin_assets"),
            "instrumentos": rows}


@router.get("/checks/debug-forward")
def debug_forward(
    tc_a: str = Query(..., description="ticker_corto instrumento A"),
    tc_b: str = Query(..., description="ticker_corto instrumento B"),
):
    """Cálculo paso a paso de la tasa forward entre dos instrumentos."""
    client = get_mongo_client_read()
    db = client["Trading"]

    def _ultima_tea(tc: str):
        full = db["Curvas"].find_one({"ticker_corto": tc}, {"ticker": 1})
        if not full:
            return None, None, None
        doc = db["TimeSales"].find_one(
            {"ticker": full["ticker"], "TEA": {"$exists": True}, "duration": {"$exists": True}},
            sort=[("timestamp", -1)]
        )
        if not doc:
            return None, None, None
        return doc.get("TEA"), doc.get("duration"), doc.get("timestamp")

    tea_a, dur_a, ts_a = _ultima_tea(tc_a)
    tea_b, dur_b, ts_b = _ultima_tea(tc_b)

    base = {
        "tc_a": tc_a, "tea_a": tea_a, "duration_a": dur_a,
        "ts_a": ts_a.strftime("%d/%m %H:%M") if ts_a else None,
        "tc_b": tc_b, "tea_b": tea_b, "duration_b": dur_b,
        "ts_b": ts_b.strftime("%d/%m %H:%M") if ts_b else None,
    }

    if not all(x is not None for x in [tea_a, tea_b, dur_a, dur_b]):
        return {**base, "error": "Faltan TEA o Duration para uno o ambos tickers.", "pasos": [], "forward": None}

    if dur_a <= dur_b:
        ta, ra, na, tb, rb, nb = dur_a, tea_a, tc_a, dur_b, tea_b, tc_b
    else:
        ta, ra, na, tb, rb, nb = dur_b, tea_b, tc_b, dur_a, tea_a, tc_a

    dt = tb - ta
    if dt <= 0:
        return {**base, "error": "Δt ≤ 0, no se puede calcular la forward.", "pasos": [], "forward": None}

    num = (1 + rb) ** tb
    den = (1 + ra) ** ta
    fwd = (num / den) ** (1 / dt) - 1

    pasos = [
        {"paso": f"t corto ({na}) — duration",   "valor": f"{ta:.6f}"},
        {"paso": f"t largo ({nb}) — duration",   "valor": f"{tb:.6f}"},
        {"paso": "Δt",                            "valor": f"{dt:.6f}"},
        {"paso": f"(1+TEA_{nb})^t_largo",         "valor": f"{num:.8f}"},
        {"paso": f"(1+TEA_{na})^t_corto",         "valor": f"{den:.8f}"},
        {"paso": "Cociente num/den",              "valor": f"{num/den:.8f}"},
        {"paso": "Forward resultante",            "valor": f"{fwd*100:.4f}%"},
    ]
    return {**base, "forward": round(fwd * 100, 4), "error": None, "pasos": pasos}


@router.get("/checks/tickers-curvas")
def tickers_curvas():
    """Lista de ticker_corto disponibles en Trading.Curvas."""
    client = get_mongo_client_read()
    return sorted({
        d["ticker_corto"] for d in
        client["Trading"]["Curvas"].find({}, {"ticker_corto": 1})
        if d.get("ticker_corto")
    })


@router.get("/checks/debug-soberano")
def debug_soberano(
    ticker_corto: str = Query(..., description="Ticker corto del bono soberano (ej. GD30D)"),
):
    """Reproduce paso a paso el cálculo del branch soberano de engines/curvas.

    Devuelve el instrumento de Trading.Curvas, el precio convertido a USD
    (aplicando MEP si corresponde), los flujos futuros al settlement con
    su monto calculado, el cashflow que entra al XIRR, y el TEA/duration/
    paridad resultante. Útil para diagnosticar cuando un bono da TEA rara.
    """
    from datetime import UTC, date, datetime

    from engines.curvas import (
        cargar_dias_habiles,
        cargar_mep_actual,
        fecha_flujo,
        macaulay_duration,
        monto_flujo_soberano,
        precio_soberano_a_usd,
        siguiente_dia_habil,
        xirr,
    )

    client = get_mongo_client_read()
    inst = client["Trading"]["Curvas"].find_one({"ticker_corto": ticker_corto})
    if not inst:
        raise HTTPException(404, f"No existe ticker_corto={ticker_corto!r} en Trading.Curvas")

    curva = inst.get("curva")
    if curva != "soberanos":
        raise HTTPException(
            400,
            f"Este check solo aplica a curva='soberanos' — el ticker tiene curva={curva!r}",
        )

    # ── Instrumento + último precio ────────────────────────────────────
    last = client["Trading"]["TimeSales"].find_one(
        {"ticker": inst["ticker"]},
        {"_id": 0, "price": 1, "timestamp": 1},
        sort=[("timestamp", -1)],
    )
    precio = last.get("price") if last else None
    ts_trade = last.get("timestamp") if last else None

    mep = cargar_mep_actual(client)
    precio_usd = precio_soberano_a_usd(precio, inst.get("ticker") or "", mep) if precio else None

    # ── Settlement ─────────────────────────────────────────────────────
    dias_habiles = cargar_dias_habiles(client)
    hoy = datetime.now(UTC).date()
    settlement_str = siguiente_dia_habil(dias_habiles, hoy)
    fecha_settlement = date.fromisoformat(settlement_str) if settlement_str else hoy

    # ── Flujos futuros ─────────────────────────────────────────────────
    flujos_raw = inst.get("flujos") or []
    valor_nominal = float(inst.get("valor_nominal", 100))
    flujos_futuros: list[dict] = []
    for f in flujos_raw:
        fd = fecha_flujo(f)
        if not fd or fd <= fecha_settlement:
            continue
        monto = monto_flujo_soberano(f, valor_nominal)
        if monto <= 0:
            continue
        flujos_futuros.append({
            "fecha":              fd.isoformat(),
            "amortizacion_pct":   f.get("amortizacion_pct", 0),
            "cupon_sobre_residual": f.get("cupon_sobre_residual", 0),
            "residual_previo_pct": f.get("residual_previo_pct", 0),
            "monto_usd":          round(monto, 4),
        })

    total_flujos = round(sum(f["monto_usd"] for f in flujos_futuros), 4)

    # ── XIRR / duration / paridad ──────────────────────────────────────
    tea = None
    duration = None
    paridad = None
    cashflow: list[dict] = []
    if precio_usd and flujos_futuros:
        cashflow.append({"fecha": fecha_settlement.isoformat(), "monto": -round(precio_usd, 4)})
        for f in flujos_futuros:
            cashflow.append({"fecha": f["fecha"], "monto": f["monto_usd"]})

        fechas_dt = [datetime.combine(fecha_settlement, datetime.min.time())] + \
                    [datetime.combine(date.fromisoformat(f["fecha"]), datetime.min.time()) for f in flujos_futuros]
        cf = [-precio_usd] + [f["monto_usd"] for f in flujos_futuros]

        tea_val = xirr(fechas_dt, cf)
        if tea_val is not None:
            tea = round(tea_val, 6)
            fechas_flujos_dt = [datetime.combine(date.fromisoformat(f["fecha"]), datetime.min.time()) for f in flujos_futuros]
            montos_flujos = [f["monto_usd"] for f in flujos_futuros]
            fecha_base_dt = datetime.combine(fecha_settlement, datetime.min.time())
            duration = macaulay_duration(fechas_flujos_dt, montos_flujos, tea_val, fecha_base_dt)

    if precio_usd and flujos_futuros:
        residual_vivo = flujos_futuros[0]["residual_previo_pct"]
        if residual_vivo:
            paridad = round(precio_usd / float(residual_vivo) * 100, 4)

    return {
        "instrumento": {
            "ticker":             inst.get("ticker"),
            "ticker_corto":       inst.get("ticker_corto"),
            "tipo":               inst.get("tipo"),
            "curva":              curva,
            "fecha_emision":      inst.get("fecha_emision"),
            "fecha_vencimiento":  inst.get("fecha_vencimiento"),
            "valor_nominal":      valor_nominal,
            "flujos_total":       len(flujos_raw),
        },
        "precio": {
            "ultimo_trade_ts": ts_trade.isoformat() if hasattr(ts_trade, "isoformat") else None,
            "precio_rofex":   precio,
            "mep":            mep,
            "precio_usd":     round(precio_usd, 4) if precio_usd else None,
        },
        "settlement": fecha_settlement.isoformat(),
        "flujos_futuros": flujos_futuros,
        "total_flujos_usd": total_flujos,
        "cashflow": cashflow,
        "resultado": {
            "tea_pct":  round(tea * 100, 4) if tea is not None else None,
            "duration": duration,
            "paridad":  paridad,
        },
    }


@router.get("/checks/breakevens-debug")
def check_breakevens_debug():
    """Desglose paso a paso del BE para cada par Lecap/Boncap ↔ CER del
    motor live. Devuelve tanto Buscar Objetivo como Fisher clásico para
    que la mesa compare los dos.

    Buscar Objetivo (preferido, usa precio y flujo directos):
      retorno_lecap = flujo_vto_lecap / precio_lecap − 1
      factor        = (1+retorno_lecap) × (precio_cer × cer_emision)
                                       / (vn_cer × cer_actual)
      BE            = factor^(1/meses_pendientes) − 1

    Fisher clásico (fallback, usa TEM y paridad):
      R   = (1 + TEM)^(días/30) − 1
      π   = (1 + R) × (paridad/100) − 1
      BE  = (1 + π)^(30/días) − 1

    meses_pendientes = días entre cer_max publicado y la fecha de liquidación
    del CER del bono (vto − 10 hábiles).
    """
    from datetime import date

    from engines.breakevens import (
        cargar_dias_habiles,
        cargar_pares,
        obtener_paridades,
        obtener_precios,
        obtener_tems,
        obtener_valor_cer,
        ultimo_cer_publicado,
    )
    from engines.curvas import fecha_cer_liquidacion

    client = get_mongo_client_read()
    pares = cargar_pares()
    if not pares:
        return {"pares": [], "fecha_cer_max": None, "cer_actual": None}

    lecap_tickers = [p["lecap_ticker"] for p in pares]
    cer_tickers   = [p["cer_ticker"]   for p in pares]
    tems          = obtener_tems(client, lecap_tickers)
    paridades     = obtener_paridades(client, cer_tickers)
    precios       = obtener_precios(client, lecap_tickers + cer_tickers)
    dias_habiles  = cargar_dias_habiles(client)
    fecha_cer_max = ultimo_cer_publicado(client)
    cer_actual    = obtener_valor_cer(client, fecha_cer_max) if fecha_cer_max else None

    hoy = date.today()
    filas = []
    for par in pares:
        try:
            fecha_vto = date.fromisoformat(par["fecha_vencimiento"])
        except Exception:
            continue
        dias = (fecha_vto - hoy).days
        if dias < 30:
            continue

        tem = tems.get(par["lecap_ticker"])
        paridad = paridades.get(par["cer_ticker"])
        precio_lecap = precios.get(par["lecap_ticker"])
        precio_cer   = precios.get(par["cer_ticker"])
        flujo_vto_lecap = par.get("flujo_vto_lecap")
        vn_cer       = par.get("vn_cer") or 100
        cer_emision  = par.get("cer_emision")

        fecha_liq_cer_str = fecha_cer_liquidacion(
            dias_habiles, par["fecha_vencimiento"], n=10,
        )
        fecha_liq_cer = (
            date.fromisoformat(fecha_liq_cer_str) if fecha_liq_cer_str else None
        )
        meses_pendientes = None
        if fecha_liq_cer and fecha_cer_max:
            try:
                fecha_cer_max_d = date.fromisoformat(fecha_cer_max)
                delta_dias = (fecha_liq_cer - fecha_cer_max_d).days
                if delta_dias > 0:
                    meses_pendientes = delta_dias / 30.0
            except Exception:
                pass

        # Buscar Objetivo
        be_bo = None
        factor_bo = None
        retorno_lecap_directo = None
        if (
            precio_lecap and precio_cer and flujo_vto_lecap
            and cer_emision and cer_actual and meses_pendientes
        ):
            try:
                retorno_lecap_directo = float(flujo_vto_lecap) / float(precio_lecap) - 1
                factor_bo = (
                    (1 + retorno_lecap_directo)
                    * float(precio_cer) * float(cer_emision)
                    / (float(vn_cer) * float(cer_actual))
                )
                if factor_bo > 0:
                    be_bo = factor_bo ** (1 / meses_pendientes) - 1
            except Exception:
                pass

        # Fisher clásico
        be_fisher = None
        retorno_fisher = None
        inflacion_fisher = None
        if tem is not None and paridad is not None:
            try:
                retorno_fisher = (1 + float(tem)) ** (dias / 30) - 1
                inflacion_fisher = (1 + retorno_fisher) * (float(paridad) / 100) - 1
                be_fisher = (1 + inflacion_fisher) ** (30 / dias) - 1
            except Exception:
                pass

        filas.append({
            "lecap":             par["lecap_corto"],
            "cer":               par["cer_corto"],
            "fecha_vto":         par["fecha_vencimiento"],
            "dias":              dias,
            "fecha_cer_liq":     fecha_liq_cer.isoformat() if fecha_liq_cer else None,
            "meses_pendientes":  round(meses_pendientes, 4) if meses_pendientes else None,
            # Buscar Objetivo
            "precio_lecap":      round(float(precio_lecap), 4) if precio_lecap else None,
            "flujo_vto_lecap":   flujo_vto_lecap,
            "precio_cer":        round(float(precio_cer), 4) if precio_cer else None,
            "vn_cer":            vn_cer,
            "cer_emision":       cer_emision,
            "retorno_lecap":     round(retorno_lecap_directo, 6) if retorno_lecap_directo is not None else None,
            "factor_bo":         round(factor_bo, 6) if factor_bo else None,
            "be_buscar_obj":     round(be_bo, 6) if be_bo else None,
            # Fisher (comparación)
            "tem_lecap":         round(float(tem), 6) if tem is not None else None,
            "paridad_cer":       round(float(paridad), 4) if paridad is not None else None,
            "retorno_fisher":    round(retorno_fisher, 6) if retorno_fisher is not None else None,
            "inflacion_fisher":  round(inflacion_fisher, 6) if inflacion_fisher is not None else None,
            "be_fisher":         round(be_fisher, 6) if be_fisher is not None else None,
        })

    return {
        "fecha_cer_max": fecha_cer_max,
        "cer_actual":    cer_actual,
        "pares":         filas,
    }


@router.get("/checks/futuros-dlr")
def check_futuros_dlr():
    """Debug de la curva de futuros DLR — spot, fuente y TNA por outright.

    Lee Trading.FuturosDLRSnapshot tal como lo escribe el motor (no recalcula).
    Útil para confirmar:
      - Qué spot está usando el motor y de qué fuente cayó (oficial / a3500 / mep).
      - Hace cuánto se reescribió el snapshot (stale_min). Fuera de horario de
        mercado los docs quedan viejos — esperable.
      - Dispersión TNA bid vs last vs offer en outrights cortos (ABR/MAY) donde
        un last desactualizado distorsiona la TNA reportada en la watchlist.
    """
    from datetime import UTC, datetime

    client = get_mongo_client_read()
    docs = list(
        client["Trading"]["FuturosDLRSnapshot"]
        .find({}, {"_id": 0})
        .sort("vencimiento", 1)
    )

    if not docs:
        return {"spot": None, "outrights": [], "total": 0}

    primero = docs[0]
    ts = primero.get("updated_at")
    stale_min: float | None = None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        stale_min = round((datetime.now(UTC) - ts).total_seconds() / 60.0, 1)

    spot = {
        "valor":      primero.get("spot_referencia"),
        "fuente":     primero.get("fuente_spot"),
        "stale_min":  stale_min,
    }

    outrights = [
        {
            "ticker":     d.get("ticker"),
            "vto":        d.get("vencimiento"),
            "dias":       d.get("dias_a_vto"),
            "bid":        d.get("bid_price"),
            "last":       d.get("last_price"),
            "offer":      d.get("offer_price"),
            "tna_bid":    d.get("tasa_implicita_tna_bid"),
            "tna_last":   d.get("tasa_implicita_tna"),
            "tna_offer":  d.get("tasa_implicita_tna_offer"),
            "updated_at": d.get("updated_at"),
        }
        for d in docs
    ]

    return {"spot": spot, "outrights": outrights, "total": len(docs)}


@router.get("/checks/debug-tna-futuros")
def check_debug_tna_futuros():
    """Validación paso-a-paso del cálculo de TNA implícita por outright DLR.

    El motor persiste UN solo número en `tasa_implicita_tna` calculado como
    TEA compuesta: `((dlr/spot)^(365/dias) - 1) * 100`. Pero la mesa puede
    estar mirando TNA lineal en el terminal Rofex. Este endpoint muestra
    AMBAS convenciones por outright para que se pueda confirmar contra
    cualquier referencia externa cuál matchea.

    Cálculos por outright (sobre last):
      directo = (last / spot - 1) × 100              → % absoluto al vto
      tna_lineal = directo × (365 / dias)            → anualizado lineal
      tea_compuesta = ((last/spot)^(365/dias) - 1)×100  → anualizado compuesto

    Diferencia esperada:
      30 días  → tna_lineal ≈ tea_compuesta - 0.5
      90 días  → tna_lineal ≈ tea_compuesta - 1.5
      180 días → tna_lineal ≈ tea_compuesta - 3
      365 días → tna_lineal == tea_compuesta (idénticas)
    """
    client = get_mongo_client_read()
    docs = list(
        client["Trading"]["FuturosDLRSnapshot"]
        .find({}, {"_id": 0})
        .sort("vencimiento", 1)
    )

    if not docs:
        return {
            "spot":   {"valor": None, "fuente": None},
            "filas":  [],
            "total":  0,
            "nota":   (
                "Sin docs en Trading.FuturosDLRSnapshot. El motor de futuros DLR "
                "probablemente está caído o aún no escribió. Reiniciá "
                "motor_futuros_dlr.service y volvé a ejecutar."
            ),
        }

    primero = docs[0]
    spot_val = primero.get("spot_referencia")
    spot_fuente = primero.get("fuente_spot")

    def _directo(px, spot, dias):
        if not px or not spot or spot <= 0 or dias <= 0:
            return None
        return round((px / spot - 1) * 100, 4)

    def _tna_lineal(px, spot, dias):
        d = _directo(px, spot, dias)
        if d is None:
            return None
        return round(d * (365 / dias), 4)

    def _tea_compuesta(px, spot, dias):
        if not px or not spot or spot <= 0 or dias <= 0:
            return None
        return round(((px / spot) ** (365 / dias) - 1) * 100, 4)

    filas = []
    for d in docs:
        last = d.get("last_price")
        bid = d.get("bid_price")
        offer = d.get("offer_price")
        dias = d.get("dias_a_vto") or 1
        mid_book = (bid + offer) / 2 if (bid and offer) else None

        filas.append({
            "ticker":             d.get("ticker"),
            "vto":                d.get("vencimiento"),
            "dias":               dias,
            "bid":                bid,
            "last":               last,
            "offer":              offer,
            "mid_book":           round(mid_book, 4) if mid_book else None,
            # Cálculos sobre last
            "directo_last":       _directo(last, spot_val, dias),
            "tna_lineal_last":    _tna_lineal(last, spot_val, dias),
            "tea_compuesta_last": _tea_compuesta(last, spot_val, dias),
            # Cálculos sobre mid del book (si hay puntas)
            "tna_lineal_mid":     _tna_lineal(mid_book, spot_val, dias),
            "tea_compuesta_mid":  _tea_compuesta(mid_book, spot_val, dias),
            # Lo que ESTÁ persistido (TEA hoy, a pesar del nombre)
            "tna_persistida":     d.get("tasa_implicita_tna"),
        })

    return {
        "spot":   {"valor": spot_val, "fuente": spot_fuente},
        "filas":  filas,
        "total":  len(filas),
        "nota":   (
            "El motor persiste TNA LINEAL en 'tasa_implicita_tna' "
            "(convención terminal Rofex). La columna PERSISTIDA debería "
            "matchear TNA LIN (last). Si ves TEA COMP, es porque todavía "
            "no reiniciaste motor_futuros_dlr.service después del último "
            "deploy."
        ),
    }


@router.get("/checks/debug-curva-tea")
def check_debug_curva_tea(ticker: str):
    """Debug paso-a-paso del cálculo de TEA/TNA/Duration de un ticker.

    Replica la lógica de engines/curvas.calcular_campos() devolviendo
    todos los inputs intermedios (instrumento, settlement, CER, MEP/TC,
    flujos futuros, cashflow del XIRR) + el resultado recalculado vs
    el persistido en TimeSales.

    Soporta las 4 curvas: tasa_fija, cer, soberanos, dolar_linked.
    """
    from api.services.debug_curva import debug_calculo_tea
    return debug_calculo_tea(ticker)


@router.get("/checks/debug-pivot")
def check_debug_pivot(
    ticker: str = Query(..., description="Ticker de Trading.PreciosAcciones (ej. NVDA)"),
):
    """Debug paso-a-paso de los pivot points de un ticker.

    Para los 4 timeframes (diario/semanal/mensual/anual) devuelve la ventana
    de fechas consultada, TODAS las velas usadas, de qué vela sale cada
    H/L/C, la fórmula Floor Trader con los números reales y los niveles
    resultantes. Lee `Trading.PreciosAcciones`.
    """
    from quant.pivot_points import debug_4_timeframes
    return debug_4_timeframes(ticker.strip().upper())
