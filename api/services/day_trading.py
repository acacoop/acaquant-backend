"""day_trading.py — service del TRADE LAB intradía (scalping de CEDEARs).

Pregunta que responde: "busco capturar X% intradía comprando/vendiendo
CEDEARs — ¿qué papel me lo está dando HOY, ahora?".

Por cada CEDEAR activo cruza:
  - Trading.CedearsSnapshot (live 1s): last, open/high/low, cierre previo,
    bid/offer (spread), vwap, total_money.
  - Trading.CedearsTimeSales agregado por minuto (UNA aggregation para todo
    el universo, con plata compradora/vendedora por minuto vía el `side`
    inferido por el motor): cierres por minuto → vueltas zigzag ≥ objetivo
    + pata EN CURSO (quant.intraday), momentum 15' por reloj, flujo
    comprador (día y últimos 30'), minutos sin operar.
  - Trading.DayTradingStats (jobs.day_trading_stats, post-cierre): la
    "costumbre" del papel — vueltas promedio por rueda en ~20 ruedas.

Devuelve filas listas para rankear en el frontend + una "idea" heurística
(LONG/SHORT con motivo en criollo). La idea es orientativa: posición en el
rango del día + impulso reciente + lado del VWAP. NO es una recomendación.

Cacheado 15s por objetivo — el poll del frontend (10s) pega casi siempre
al cache y la aggregation del tape corre como mucho 4×/min.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from api.cache import cached
from api.db import get_db_trading
from quant.intraday import analizar_vueltas, momentum_por_tiempo, posicion_en_rango

# Umbral de impulso para la idea por momentum (en % de los últimos 15').
_MOM_FUERTE_PCT = 0.3
# Posición en el rango que define "cerca del piso/techo" del día.
_PISO_PCT = 25.0
_TECHO_PCT = 75.0
# Buckets de objetivo persistidos por jobs.day_trading_stats (mismos presets
# que la UI). El objetivo arbitrario se mapea al bucket más cercano.
UMBRALES_STATS = (0.5, 0.75, 1.0, 1.5)
# Ventana de "costumbre": ruedas hacia atrás para el promedio de vueltas.
_VENTANA_COSTUMBRE = 20


def _minutos_por_ticker(db) -> dict[str, list[dict]]:
    """{ticker_corto: [{m, c, bm, sm} por minuto de HOY, asc]} en UNA aggregation.

    m = minuto ISO · c = último precio del minuto · bm/sm = plata comprada/
    vendida en el minuto (side BUY/SELL inferido por el motor; MID se ignora
    para el flujo pero su precio sí marca el cierre del minuto).
    """
    inicio_hoy = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    cur = db["CedearsTimeSales"].aggregate([
        {"$match": {"timestamp": {"$gte": inicio_hoy}}},
        {"$sort": {"timestamp": 1}},
        {"$group": {
            "_id": {
                "tk": "$ticker_corto",
                "m": {"$dateToString": {"format": "%Y-%m-%dT%H:%M:00Z", "date": "$timestamp"}},
            },
            "c": {"$last": "$price"},
            "bm": {"$sum": {"$cond": [{"$eq": ["$side", "BUY"]}, "$money", 0]}},
            "sm": {"$sum": {"$cond": [{"$eq": ["$side", "SELL"]}, "$money", 0]}},
        }},
        {"$sort": {"_id.m": 1}},
    ])
    out: dict[str, list[dict]] = {}
    for d in cur:
        tk = (d["_id"] or {}).get("tk")
        if not tk or d.get("c") is None:
            continue
        out.setdefault(tk, []).append({
            "m": (d["_id"] or {}).get("m"),
            "c": float(d["c"]),
            "bm": float(d.get("bm") or 0),
            "sm": float(d.get("sm") or 0),
        })
    return out


def _flujo_compra_pct(mins: list[dict], desde: datetime | None = None) -> float | None:
    """% de la plata con lado conocido que fue COMPRA. None si no hay flujo."""
    bm = sm = 0.0
    for x in mins:
        if desde is not None:
            try:
                t = datetime.fromisoformat(str(x["m"]).replace("Z", "+00:00"))
            except ValueError:
                continue
            if t < desde:
                continue
        bm += x["bm"]
        sm += x["sm"]
    tot = bm + sm
    return round(bm / tot * 100, 1) if tot > 0 else None


def bucket_objetivo(objetivo_pct: float) -> float:
    """Bucket de stats más cercano al objetivo pedido."""
    return min(UMBRALES_STATS, key=lambda u: abs(u - objetivo_pct))


def campo_vueltas(bucket: float) -> str:
    """Nombre del campo persistido en DayTradingStats para un bucket.

    0.5 → vueltas_05 · 0.75 → vueltas_075 · 1.0 → vueltas_10 · 1.5 → vueltas_15.
    Única fuente del naming — la usan este service y jobs.day_trading_stats.
    """
    return f"vueltas_{str(bucket).replace('.', '')}"


@cached(ttl=600)
def _costumbre(bucket: float) -> dict[str, dict]:
    """{ticker: {vueltas_prom, rango_prom, n_dias}} de las últimas ~20 ruedas
    persistidas por jobs.day_trading_stats. Vacío si el job nunca corrió.
    """
    db = get_db_trading()
    campo = campo_vueltas(bucket)
    fechas = db["DayTradingStats"].distinct("fecha")
    if not fechas:
        return {}
    usar = sorted(fechas)[-_VENTANA_COSTUMBRE:]
    cur = db["DayTradingStats"].aggregate([
        {"$match": {"fecha": {"$in": usar}}},
        {"$group": {
            "_id": "$ticker",
            "vueltas_prom": {"$avg": f"${campo}"},
            "rango_prom": {"$avg": "$rango_pct"},
            "n_dias": {"$sum": 1},
        }},
    ])
    return {
        d["_id"]: {
            "vueltas_prom": round(d["vueltas_prom"], 1) if d.get("vueltas_prom") is not None else None,
            "rango_prom": round(d["rango_prom"], 2) if d.get("rango_prom") is not None else None,
            "n_dias": d.get("n_dias", 0),
        }
        for d in cur
        if d.get("_id")
    }


def _idea(pos: float | None, mom15: float | None, vs_vwap: float | None) -> dict | None:
    """Heurística orientativa LONG/SHORT con motivo en criollo."""
    if pos is not None and pos <= _PISO_PCT:
        return {
            "lado": "long",
            "motivo": f"está cerca del piso del día ({pos:.0f}% del rango) — "
                      "si rebota, el recorrido para arriba es el rango ya probado",
        }
    if pos is not None and pos >= _TECHO_PCT:
        return {
            "lado": "short",
            "motivo": f"está cerca del techo del día ({pos:.0f}% del rango) — "
                      "si afloja, el recorrido para abajo es el rango ya probado",
        }
    if mom15 is not None and mom15 >= _MOM_FUERTE_PCT and (vs_vwap or 0) > 0:
        return {
            "lado": "long",
            "motivo": f"viene empujando (+{mom15:.2f}% en 15') y opera arriba del "
                      "precio promedio del día (VWAP)",
        }
    if mom15 is not None and mom15 <= -_MOM_FUERTE_PCT and (vs_vwap or 0) < 0:
        return {
            "lado": "short",
            "motivo": f"viene cayendo ({mom15:.2f}% en 15') y opera abajo del "
                      "precio promedio del día (VWAP)",
        }
    return None


@cached(ttl=15)
def get_day_trading(objetivo_pct: float = 0.5) -> dict:
    """Ranking intradía de CEDEARs para un objetivo de captura `objetivo_pct`.

    Returns:
        {
          objetivo_pct, generado, en_rueda: bool, rows: [
            {ticker, nombre, sector, last, dia_pct, intradia_pct, rango_pct,
             low, high, posicion, vueltas, mejor_vuelta_pct, vueltas_hora,
             pata: {dir, pct} | None,            # pata zigzag EN CURSO
             mom15_pct, vs_vwap_pct, spread_pct, total_money, volumen_nominal,
             flujo_compra_pct, flujo30_compra_pct,
             min_sin_operar,                      # papeles dormidos
             prom_vueltas, prom_rango, prom_dias, # costumbre (~20 ruedas)
             idea: {lado, motivo} | None, n_minutos}
          ]
        }
        Orden: vueltas desc, rango desc. `en_rueda` = hay tape de hoy.
    """
    objetivo_pct = max(0.1, min(float(objetivo_pct), 5.0))
    db = get_db_trading()
    ahora = datetime.now(UTC)

    master = {
        m["ticker_corto"]: m
        for m in db["Cedears"].find(
            {"activo": True},
            {"_id": 0, "ticker_corto": 1, "nombre": 1, "sector": 1, "ticker": 1},
        )
        if m.get("ticker_corto")
    }
    snaps = {s["ticker"]: s for s in db["CedearsSnapshot"].find({}, {"_id": 0})}
    minutos = _minutos_por_ticker(db)
    costumbre = _costumbre(bucket=bucket_objetivo(objetivo_pct))

    rows: list[dict] = []
    for tk_corto, m in master.items():
        snap = snaps.get(m.get("ticker"), {})
        last = float(snap.get("last") or 0) or None
        open_ = float(snap.get("open") or 0) or None
        high = float(snap.get("high") or 0) or None
        low = float(snap.get("low") or 0) or None
        close_prev = float(snap.get("close") or 0) or None
        bid = float(snap.get("bid") or 0) or None
        offer = float(snap.get("offer") or 0) or None
        vwap = float(snap.get("vwap") or 0) or None
        total_money = float(snap.get("total_money") or 0) or None
        volumen_nominal = float(snap.get("volume") or 0) or None

        mins = minutos.get(tk_corto, [])
        closes = [x["c"] for x in mins]
        pares = [(x["m"], x["c"]) for x in mins]
        zz = analizar_vueltas(closes, objetivo_pct)
        mom15 = momentum_por_tiempo(pares, 15)
        pos = posicion_en_rango(last, low, high) if last else None

        # Sin snapshot ni tape → el papel no operó / motor apagado: se omite.
        if last is None and not mins:
            continue

        dia_pct = (
            round((last / close_prev - 1) * 100, 2)
            if last and close_prev else None
        )
        rango_pct = (
            round((high - low) / low * 100, 2)
            if high and low and high > low else None
        )
        spread_pct = (
            round((offer - bid) / last * 100, 2)
            if bid and offer and last and offer >= bid else None
        )
        vs_vwap = (
            round((last / vwap - 1) * 100, 2)
            if last and vwap else None
        )

        # Papeles dormidos: minutos de reloj desde el último trade.
        min_sin_operar = None
        if mins:
            try:
                ult = datetime.fromisoformat(str(mins[-1]["m"]).replace("Z", "+00:00"))
                min_sin_operar = max(0, int((ahora - ult).total_seconds() // 60))
            except ValueError:
                pass

        # Ritmo: vueltas por hora de operatoria efectiva (≥30' para no inflar
        # el arranque de la rueda).
        vueltas_hora = None
        if len(mins) >= 2 and zz["vueltas"] > 0:
            try:
                t0 = datetime.fromisoformat(str(mins[0]["m"]).replace("Z", "+00:00"))
                t1 = datetime.fromisoformat(str(mins[-1]["m"]).replace("Z", "+00:00"))
                horas = (t1 - t0).total_seconds() / 3600
                if horas >= 0.5:
                    vueltas_hora = round(zz["vueltas"] / horas, 1)
            except ValueError:
                pass

        c = costumbre.get(tk_corto, {})

        rows.append({
            "ticker":           tk_corto,
            # Ticker BYMA completo — lo necesita la boleta de operar del
            # TRADE LAB (book L2 + /api/ordenes usan el símbolo full).
            "ticker_full":      m.get("ticker"),
            "nombre":           m.get("nombre"),
            "sector":           m.get("sector"),
            "last":             last,
            "dia_pct":          dia_pct,
            "intradia_pct":     (
                round((last / open_ - 1) * 100, 2) if last and open_ else None
            ),
            "rango_pct":        rango_pct,
            "low":              low,
            "high":             high,
            "posicion":         pos,
            "vueltas":          zz["vueltas"],
            "mejor_vuelta_pct": zz["mejor_pct"] if zz["mejor_pct"] > 0 else None,
            "vueltas_hora":     vueltas_hora,
            "pata":             (
                {"dir": "long" if zz["pata_dir"] > 0 else "short", "pct": zz["pata_pct"]}
                if zz["pata_dir"] != 0 else None
            ),
            "mom15_pct":        mom15,
            "vs_vwap_pct":      vs_vwap,
            "spread_pct":       spread_pct,
            "total_money":      total_money,
            "volumen_nominal":  volumen_nominal,
            "flujo_compra_pct":   _flujo_compra_pct(mins),
            "flujo30_compra_pct": _flujo_compra_pct(mins, desde=ahora - timedelta(minutes=30)),
            "min_sin_operar":   min_sin_operar,
            "prom_vueltas":     c.get("vueltas_prom"),
            "prom_rango":       c.get("rango_prom"),
            "prom_dias":        c.get("n_dias"),
            "idea":             _idea(pos, mom15, vs_vwap),
            "n_minutos":        len(mins),
        })

    rows.sort(key=lambda r: (r["vueltas"], r["rango_pct"] or 0), reverse=True)
    return {
        "objetivo_pct": objetivo_pct,
        "generado":     ahora.isoformat(),
        "en_rueda":     bool(minutos),
        "rows":         rows,
    }


@cached(ttl=300)
def get_companeros(ticker: str, n: int = 6) -> dict:
    """Con qué papeles 'se mueve' un ticker (correlación de cierres diarios
    del subyacente USD, ventana 252 ruedas — rv_motor.get_correlation_matrix).

    Returns:
        {ticker, con: [{ticker, rho}], contra: [{ticker, rho}], n_obs}
        `con` = más correlacionados (se mueven parecido) · `contra` = más
        anti-correlacionados (se mueven al revés). Vacíos si no hay serie.
    """
    from api.services.rv_motor import get_correlation_matrix

    tk = ticker.strip().upper()
    m = get_correlation_matrix()
    if tk not in m["tickers"]:
        return {"ticker": tk, "con": [], "contra": [], "n_obs": m.get("n_obs", 0)}

    idx = m["tickers"].index(tk)
    fila = m["matriz"][idx]
    pares = [
        {"ticker": otro, "rho": round(fila[j], 2)}
        for j, otro in enumerate(m["tickers"])
        if otro != tk and fila[j] is not None
    ]
    pares.sort(key=lambda p: p["rho"], reverse=True)
    n = max(1, min(n, 15))
    con = [p for p in pares if p["rho"] > 0][:n]
    contra = [p for p in pares[::-1] if p["rho"] < 0][:n]
    return {"ticker": tk, "con": con, "contra": contra, "n_obs": m.get("n_obs", 0)}
