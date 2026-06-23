"""Serie histórica del canje CCL/MEP intra-bono (ej. AL30C / AL30D − 1).

Mismo bono, dos especies de liquidación distintas: la C (settlement
contado con liqui en USD ~ CCL) y la D (settlement MEP en USD). El
spread mide la brecha cambiaria implícita en ese bono.

NO confundir con "spread legislación" (GD30 vs AL30, bonos distintos al
mismo plazo, mide riesgo crediticio diferencial entre jurisdicciones NY
y Arg). Esto NO es eso.

Toma el cierre diario de los dos tickers desde Trading.CanjeCierre (1 doc por
(ticker, fecha), materializado por jobs/cierre_canje.py) y arma una serie por
día con: precio_c, precio_d, canje. Para el día de hoy, si el cron de cierre aún
no corrió, agrega un punto live con el último trade del día (TimeSales).

Antes agregaba ~540k ticks de TimeSales por request (2.6s cold); ahora lee ~365
docs (<50ms). El cálculo NO se hace en el frontend: queda acá para que la UI solo
renderice una serie ya cocinada.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from api.cache import cached
from api.db import get_db_trading

# Pares predefinidos viven en config.PARES_CANJE (compartidos con el cron de
# cierre, regla de capas). La key 'par' del endpoint matchea ahí.
from config import PARES_CANJE


def _ultimo_trade_dia(db, ticker: str, dia: date) -> float | None:
    """Último trade (price>0) de `ticker` en `dia` desde SQL `mercado.timesales`
    (Trading.TimeSales fue DROPEADA 2026-06-22 — el tape vive solo en Postgres).
    `ts` es naive ART → bounds naive del día ART. `db` queda para los lectores Mongo
    vivos del módulo (CanjeCierre)."""
    from core.postgres import get_pool
    inicio = datetime.combine(dia, datetime.min.time())
    fin = inicio + timedelta(days=1)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT price FROM mercado.timesales WHERE ticker = %s AND price > 0 "
            "AND ts >= %s AND ts < %s ORDER BY ts DESC LIMIT 1",
            (ticker, inicio, fin),
        )
        row = cur.fetchone()
    return float(row[0]) if row and row[0] is not None else None


def _precios_cierre(db, tickers: list[str], desde: date, hasta: date) -> dict[str, dict[date, float]]:
    """{ticker: {fecha: precio_cierre}} leyendo Trading.CanjeCierre (1 doc por
    (ticker, fecha), materializado por el cron). Si el rango incluye hoy y el
    cron aún no corrió, agrega el punto live (último trade del día)."""
    out: dict[str, dict[date, float]] = {tk: {} for tk in tickers}
    cur = db["CanjeCierre"].find(
        {"ticker": {"$in": tickers},
         "fecha": {"$gte": desde.isoformat(), "$lte": hasta.isoformat()},
         "price": {"$gt": 0}},
        {"_id": 0, "ticker": 1, "fecha": 1, "price": 1},
    )
    for r in cur:
        try:
            f = datetime.strptime(r["fecha"], "%Y-%m-%d").date()
        except (ValueError, KeyError):
            continue
        out.setdefault(r["ticker"], {})[f] = float(r["price"])

    hoy = datetime.now(UTC).date()
    if desde <= hoy <= hasta:
        for tk in tickers:
            if hoy not in out.get(tk, {}):
                p = _ultimo_trade_dia(db, tk, hoy)
                if p:
                    out.setdefault(tk, {})[hoy] = p
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
    precios = _precios_cierre(db, [tk_c, tk_d], desde_d, hasta_d)
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
