"""GET /api/manager/checks/* — validaciones de consistencia sobre Mongo."""
from __future__ import annotations

from fastapi import APIRouter, Query

from core.mongo import get_mongo_client_read

router = APIRouter()


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
        live = db["ForwardsLive"].find_one({"curva": curva}, {"tickers": 1})
        live_tickers = live.get("tickers", []) if live else []
        expected = [i.get("ticker_corto") for i in insts if teas_all.get(i.get("ticker"))]
        resultado.append({"curva": curva, "tickers": tickers_curva,
                          "live_ok": live_tickers == expected,
                          "live_tickers": live_tickers, "expected_tickers": expected})
    return resultado


@router.get("/checks/cer")
def check_cer():
    """CER usado en el último trade enriquecido por bono CER."""
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
        idx = next((i for i, f in enumerate(dias_hab) if f <= settle_str), None)
        if idx is None or idx < n:
            return None, None
        return _cer_en_fecha(date.fromisoformat(dias_hab[idx - n]))

    def _next_habil(fd):
        s = fd.isoformat()
        return next((f for f in dias_hab if f > s), None)

    rows = []
    for inst in curvas_cer:
        doc = db["TimeSales"].find_one(
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
