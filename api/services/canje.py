"""Serie histórica del canje CCL/MEP intra-bono (ej. AL30C / AL30D − 1).

Mismo bono, dos especies de liquidación distintas: la C (settlement
contado con liqui en USD ~ CCL) y la D (settlement MEP en USD). El
spread mide la brecha cambiaria implícita en ese bono.

NO confundir con "spread legislación" (GD30 vs AL30, bonos distintos al
mismo plazo, mide riesgo crediticio diferencial entre jurisdicciones NY
y Arg). Esto NO es eso.

Toma precios diarios de los dos tickers desde Trading.TimeSales y arma
una serie por día con: precio_c, precio_d, canje. Sin enrichment ni
flujos — solo necesitamos el último precio del día por ticker.

El cálculo NO se hace en el frontend: queda acá para que la UI solo
renderice una serie ya cocinada.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from api.cache import cached
from api.db import get_db_trading

# Pares predefinidos. La key 'par' del endpoint matchea acá. Si el
# usuario quiere un par custom, puede pasar tickers manualmente vía el
# router (no implementado por ahora).
PARES_CANJE: dict[str, dict[str, str]] = {
    "AL30": {
        "c": "MERV - XMEV - AL30C - 24hs",  # CCL
        "d": "MERV - XMEV - AL30D - 24hs",  # MEP
    },
    "GD30": {
        "c": "MERV - XMEV - GD30C - 24hs",
        "d": "MERV - XMEV - GD30D - 24hs",
    },
}


def _ultimos_precios_diarios(db, tickers: list[str], desde: date, hasta: date) -> dict[str, dict[date, float]]:
    """Devuelve {ticker: {fecha: ultimo_precio}} para el rango.

    Toma el ÚLTIMO trade del día (orden DESC + $first). Saltea trades
    con price <= 0.
    """
    inicio = datetime.combine(desde, datetime.min.time(), tzinfo=UTC)
    fin = datetime.combine(hasta + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    pipeline = [
        {"$match": {
            "ticker":    {"$in": tickers},
            "price":     {"$gt": 0},
            "timestamp": {"$gte": inicio, "$lt": fin},
        }},
        {"$addFields": {
            "fecha": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
        }},
        {"$sort": {"timestamp": -1}},
        {"$group": {
            "_id":   {"ticker": "$ticker", "fecha": "$fecha"},
            "price": {"$first": "$price"},
        }},
    ]
    out: dict[str, dict[date, float]] = {tk: {} for tk in tickers}
    for r in db["TimeSales"].aggregate(pipeline):
        tk = r["_id"]["ticker"]
        try:
            f = datetime.strptime(r["_id"]["fecha"], "%Y-%m-%d").date()
        except ValueError:
            continue
        out.setdefault(tk, {})[f] = float(r["price"])
    return out


@cached(ttl=60)
def serie_canje(par: str = "AL30", desde: str | None = None, hasta: str | None = None) -> dict:
    """Devuelve serie histórica del canje para un par.

    Output:
        {
          par: "AL30",
          ticker_c: "...AL30C...",
          ticker_d: "...AL30D...",
          serie: [
            {fecha: "YYYY-MM-DD", precio_c, precio_d, canje},
            ...
          ],
          meta: {
            fechas_solo_c: int,    # días donde solo había precio C
            fechas_solo_d: int,
            primer_dia: "YYYY-MM-DD" | null,
            ultimo_dia:  "YYYY-MM-DD" | null,
          }
        }

    `canje` = precio_c / precio_d − 1 (decimal, ej. 0.024 = 2.4%).
    """
    par_upper = (par or "").upper()
    par_def = PARES_CANJE.get(par_upper)
    if not par_def:
        return {"error": f"par desconocido: {par!r}. Disponibles: {list(PARES_CANJE.keys())}"}

    # Defaults: últimos 365 días.
    hasta_d = (
        datetime.strptime(hasta, "%Y-%m-%d").date() if hasta else date.today()
    )
    desde_d = (
        datetime.strptime(desde, "%Y-%m-%d").date() if desde else hasta_d - timedelta(days=365)
    )
    if desde_d > hasta_d:
        desde_d, hasta_d = hasta_d, desde_d

    db = get_db_trading()
    tk_c = par_def["c"]
    tk_d = par_def["d"]
    precios = _ultimos_precios_diarios(db, [tk_c, tk_d], desde_d, hasta_d)
    map_c = precios.get(tk_c, {})
    map_d = precios.get(tk_d, {})

    fechas = sorted(set(map_c.keys()) | set(map_d.keys()))
    serie: list[dict] = []
    solo_c = 0
    solo_d = 0
    for f in fechas:
        pc = map_c.get(f)
        pd = map_d.get(f)
        if pc is None and pd is not None:
            solo_d += 1
            continue
        if pd is None and pc is not None:
            solo_c += 1
            continue
        if pc is None or pd is None or pd <= 0:
            continue
        canje = pc / pd - 1
        serie.append({
            "fecha":     f.isoformat(),
            "precio_c":  round(pc, 4),
            "precio_d":  round(pd, 4),
            "canje":     round(canje, 6),
        })

    return {
        "par":      par_upper,
        "ticker_c": tk_c,
        "ticker_d": tk_d,
        "serie":    serie,
        "meta": {
            "fechas_solo_c": solo_c,
            "fechas_solo_d": solo_d,
            "primer_dia":    serie[0]["fecha"] if serie else None,
            "ultimo_dia":    serie[-1]["fecha"] if serie else None,
        },
    }
