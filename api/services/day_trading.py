"""day_trading.py — service del TRADE LAB intradía (scalping de CEDEARs).

Pregunta que responde: "busco capturar X% intradía comprando/vendiendo
CEDEARs — ¿qué papel me lo está dando HOY, ahora?".

Por cada CEDEAR activo cruza:
  - Trading.CedearsSnapshot (live 1s): last, open/high/low, cierre previo,
    bid/offer (spread), vwap, total_money.
  - Trading.CedearsTimeSales agregado por minuto (UNA aggregation para todo
    el universo): cierres por minuto → vueltas zigzag ≥ objetivo
    (quant.intraday.contar_vueltas), momentum 15', mejor pata del día.

Devuelve filas listas para rankear en el frontend + una "idea" heurística
(LONG/SHORT con motivo en criollo). La idea es orientativa: posición en el
rango del día + impulso reciente + lado del VWAP. NO es una recomendación.

Cacheado 15s por objetivo — el poll del frontend (10s) pega casi siempre
al cache y la aggregation del tape corre como mucho 4×/min.
"""
from __future__ import annotations

from datetime import UTC, datetime

from api.cache import cached
from api.db import get_db_trading
from quant.intraday import contar_vueltas, momentum_pct, posicion_en_rango

# Umbral de impulso para la idea por momentum (en % de los últimos 15').
_MOM_FUERTE_PCT = 0.3
# Posición en el rango que define "cerca del piso/techo" del día.
_PISO_PCT = 25.0
_TECHO_PCT = 75.0


def _cierres_por_minuto(db) -> dict[str, list[float]]:
    """{ticker_corto: [cierres por minuto de HOY, asc]} en UNA aggregation.

    Mismo patrón que get_cedears_intraday pero para todo el universo: agrupa
    el tape por (ticker, minuto) y se queda con el último precio del minuto.
    La colección solo contiene la rueda de hoy (se vacía al cierre), así que
    el match por timestamp recorta poco pero protege el arranque del día.
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
        }},
        {"$sort": {"_id.m": 1}},
    ])
    out: dict[str, list[float]] = {}
    for d in cur:
        tk = (d["_id"] or {}).get("tk")
        c = d.get("c")
        if tk and c is not None:
            out.setdefault(tk, []).append(float(c))
    return out


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
            {ticker, nombre, sector, last, dia_pct, rango_pct, posicion,
             vueltas, mejor_vuelta_pct, mom15_pct, vs_vwap_pct,
             spread_pct, total_money, idea: {lado, motivo} | None,
             n_minutos}
          ]
        }
        Orden: vueltas desc, rango desc. `en_rueda` = hay tape de hoy.
    """
    objetivo_pct = max(0.1, min(float(objetivo_pct), 5.0))
    db = get_db_trading()

    master = {
        m["ticker_corto"]: m
        for m in db["Cedears"].find(
            {"activo": True},
            {"_id": 0, "ticker_corto": 1, "nombre": 1, "sector": 1, "ticker": 1},
        )
        if m.get("ticker_corto")
    }
    snaps = {
        s["ticker"]: s
        for s in db["CedearsSnapshot"].find({}, {"_id": 0})
    }
    minutos = _cierres_por_minuto(db)

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

        closes = minutos.get(tk_corto, [])
        vueltas, mejor = contar_vueltas(closes, objetivo_pct)
        mom15 = momentum_pct(closes, 15)
        pos = posicion_en_rango(last, low, high) if last else None

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

        # Sin snapshot ni tape → el papel no operó / motor apagado: se omite.
        if last is None and not closes:
            continue

        rows.append({
            "ticker":           tk_corto,
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
            "vueltas":          vueltas,
            "mejor_vuelta_pct": mejor if mejor > 0 else None,
            "mom15_pct":        mom15,
            "vs_vwap_pct":      vs_vwap,
            "spread_pct":       spread_pct,
            "total_money":      total_money,
            "idea":             _idea(pos, mom15, vs_vwap),
            "n_minutos":        len(closes),
        })

    rows.sort(key=lambda r: (r["vueltas"], r["rango_pct"] or 0), reverse=True)
    return {
        "objetivo_pct": objetivo_pct,
        "generado":     datetime.now(UTC).isoformat(),
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
