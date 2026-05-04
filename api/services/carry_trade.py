"""Serie de carry trade en USD para una curva (tasa_fija / cer).

Calcula el retorno acumulado en USD de cada bono ARS, descontando la
variación del MEP (o CCL) en el mismo período. Permite ver si el carry
en pesos vence o no a la devaluación implícita.

Fórmula exacta (no aproximación lineal — los retornos en Argentina son
grandes y la diferencia importa):
    carry_usd = (1 + ret_ars) / (1 + var_dolar) − 1

Output por ticker, día por día, listo para graficar como line chart en
el frontend.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from api.cache import cached
from api.db import get_db_trading, get_db_valuaciones

_CURVAS_VALIDAS = ("tasa_fija", "cer")
_DOLARES_VALIDOS = ("mep", "oficial")


def _serie_mep_diaria(db_val, desde: date, hasta: date) -> dict[date, float]:
    """Último valor de MEP por día desde Valuaciones.Dolar (cron + WS)."""
    inicio = datetime.combine(desde, datetime.min.time())
    fin = datetime.combine(hasta + timedelta(days=1), datetime.min.time())
    pipeline = [
        {"$match": {
            "timestamp": {"$gte": inicio, "$lt": fin},
            "mep":       {"$gt": 0},
        }},
        {"$addFields": {
            "fecha": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
        }},
        {"$sort": {"timestamp": -1}},
        {"$group": {
            "_id":   "$fecha",
            "valor": {"$first": "$mep"},
        }},
    ]
    out: dict[date, float] = {}
    for r in db_val["Dolar"].aggregate(pipeline):
        try:
            f = datetime.strptime(r["_id"], "%Y-%m-%d").date()
        except ValueError:
            continue
        out[f] = float(r["valor"])
    return out


def _serie_oficial_diaria(db_trd, desde: date, hasta: date) -> dict[date, float]:
    """Serie A3500 (BCRA) diaria desde Trading.DOLAR. Fecha guardada como
    string 'YYYY-MM-DD'."""
    desde_s = desde.isoformat()
    hasta_s = hasta.isoformat()
    out: dict[date, float] = {}
    for d in db_trd["DOLAR"].find(
        {"fecha": {"$gte": desde_s, "$lte": hasta_s}, "valor": {"$gt": 0}},
        {"_id": 0, "fecha": 1, "valor": 1},
    ):
        try:
            f = datetime.strptime(d["fecha"], "%Y-%m-%d").date()
        except (ValueError, KeyError):
            continue
        out[f] = float(d["valor"])
    return out


def _precios_diarios_curva(db_trd, curva: str, desde: date, hasta: date) -> dict[str, dict[date, float]]:
    """{ticker_corto: {fecha: ultimo_precio}} para todos los bonos de la curva.

    Lee Trading.SnapshotsCierre (1 doc por (curva, fecha, ticker)). Solo
    días con cierre real persistido por jobs/snapshot_cierre — descarta
    de raíz fines de semana y feriados (donde antes aparecían fechas
    fantasma del aggregate sobre TimeSales).

    Si el rango incluye el día de hoy y aún no hay cierre persistido (el
    cron snapshot_cierre corre 20:25 UTC), agrega un punto live leyendo
    Trading.MarketSnapshot — así el frontend siempre muestra "hasta hoy"
    sin esperar al cron.
    """
    out: dict[str, dict[date, float]] = {}
    cur = db_trd["SnapshotsCierre"].find(
        {
            "curva":         curva,
            "ts_cierre":     {"$gte": desde.isoformat(), "$lte": hasta.isoformat()},
            "ultimo_precio": {"$gt": 0},
        },
        {"_id": 0, "ts_cierre": 1, "ticker": 1, "ticker_corto": 1, "ultimo_precio": 1},
    )
    for r in cur:
        try:
            f = datetime.strptime(r["ts_cierre"], "%Y-%m-%d").date()
        except (ValueError, KeyError):
            continue
        corto = r.get("ticker_corto") or r.get("ticker")
        out.setdefault(corto, {})[f] = float(r["ultimo_precio"])

    hoy = datetime.utcnow().date()
    if desde <= hoy <= hasta and not any(hoy in serie for serie in out.values()):
        live = _precios_live_curva(db_trd, curva)
        for corto, price in live.items():
            out.setdefault(corto, {})[hoy] = price
    return out


def _precios_live_curva(db_trd, curva: str) -> dict[str, float]:
    """{ticker_corto: last_price} desde Trading.MarketSnapshot para los
    tickers de la curva. Usado como punto live cuando el cron de cierre
    aún no corrió.
    """
    meta = {
        d["ticker"]: d.get("ticker_corto") or d["ticker"]
        for d in db_trd["Curvas"].find(
            {"curva": curva}, {"ticker": 1, "ticker_corto": 1},
        )
    }
    if not meta:
        return {}
    out: dict[str, float] = {}
    for r in db_trd["MarketSnapshot"].find(
        {"ticker": {"$in": list(meta.keys())}, "metrics.last_price": {"$gt": 0}},
        {"_id": 0, "ticker": 1, "metrics.last_price": 1},
    ):
        price = (r.get("metrics") or {}).get("last_price")
        if price is None:
            continue
        out[meta[r["ticker"]]] = float(price)
    return out


@cached(ttl=60)
def serie_carry_trade(
    curva: str = "tasa_fija",
    desde: str | None = None,
    hasta: str | None = None,
    dolar: str = "mep",
) -> dict:
    """Serie diaria de carry en USD por bono.

    Output:
        {
          curva, dolar, ticker_dolar: 'mep'|'ccl',
          fecha_base, fecha_final,
          serie: [
            {fecha, dolar, <ticker1>: carry_usd_pct, <ticker2>: ..., ...},
            ...
          ],
          tabla: [
            {ticker, base, final, ret_ars, var_dolar, carry_usd},
            ...
          ]
        }

    `serie` con valores en % (multiplicados por 100, redondeados 3 dec)
    listos para ser graficados directo. La base = primer día con
    precio + dólar disponibles. Los días donde un bono no tenía precio
    se rellenan con null para que recharts conecte líneas con
    `connectNulls`.
    """
    if curva not in _CURVAS_VALIDAS:
        return {"error": f"curva invalida. Disponibles: {list(_CURVAS_VALIDAS)}"}
    dolar_l = (dolar or "mep").lower()
    if dolar_l not in _DOLARES_VALIDOS:
        return {"error": f"dolar invalido. Disponibles: {list(_DOLARES_VALIDOS)}"}

    hasta_d = (
        datetime.strptime(hasta, "%Y-%m-%d").date() if hasta else date.today()
    )
    desde_d = (
        datetime.strptime(desde, "%Y-%m-%d").date()
        if desde else hasta_d - timedelta(days=180)
    )
    if desde_d > hasta_d:
        desde_d, hasta_d = hasta_d, desde_d

    db_trd = get_db_trading()
    db_val = get_db_valuaciones()

    precios = _precios_diarios_curva(db_trd, curva, desde_d, hasta_d)
    if not precios:
        return {"error": "no hay precios en la curva para el rango"}

    if dolar_l == "oficial":
        dolares = _serie_oficial_diaria(db_trd, desde_d, hasta_d)
    else:  # mep
        dolares = _serie_mep_diaria(db_val, desde_d, hasta_d)
    if not dolares:
        return {"error": f"no hay serie de {dolar_l} para el rango"}

    # Fechas comunes (al menos algún ticker + dolar). Para que un día
    # aparezca en la serie alcanza con que haya dolar — el ticker se
    # llena con null si no operó.
    fechas_dolar = set(dolares.keys())
    fechas_ticker = set()
    for serie_t in precios.values():
        fechas_ticker |= serie_t.keys()
    fechas = sorted(fechas_dolar & fechas_ticker)
    if not fechas:
        return {"error": "no hay fechas comunes entre precios y dolar"}

    # Base por ticker = primer día del rango donde tenía precio Y había
    # dolar. Bases por ticker pueden diferir (un bono nuevo arranca
    # más tarde) pero todos comparten el mismo "dolar_base" si comparten
    # primer día. Para simplicidad: cada ticker normaliza con SU primer
    # día disponible junto con SU dolar de ese día. Eso da carry desde
    # el primer dato de cada bono.
    base_ticker: dict[str, tuple[float, float, date]] = {}  # ticker → (precio_base, dolar_base, fecha)
    for tk, serie_t in precios.items():
        for f in fechas:
            if f in serie_t and f in dolares:
                base_ticker[tk] = (serie_t[f], dolares[f], f)
                break

    if not base_ticker:
        return {"error": "ningún ticker tiene base válida"}

    # Construir serie día por día.
    serie: list[dict] = []
    for f in fechas:
        d_actual = dolares.get(f)
        if d_actual is None:
            continue
        row: dict = {"fecha": f.isoformat(), "dolar": round(d_actual, 4)}
        for tk, (p_base, d_base, f_base) in base_ticker.items():
            if f < f_base:
                continue
            p_actual = precios[tk].get(f)
            if p_actual is None or p_base <= 0 or d_base <= 0:
                continue
            ret_ars = p_actual / p_base - 1
            var_dol = d_actual / d_base - 1
            carry_usd = (1 + ret_ars) / (1 + var_dol) - 1
            row[tk] = round(carry_usd * 100, 3)
        serie.append(row)

    # Tabla resumen: último vs base por ticker.
    tabla: list[dict] = []
    if serie:
        ult_row = serie[-1]
        ult_dolar = ult_row.get("dolar")
        for tk, (p_base, d_base, f_base) in base_ticker.items():
            ult_precio = precios[tk].get(fechas[-1])
            if ult_precio is None or p_base <= 0 or d_base <= 0 or not ult_dolar:
                continue
            ret_ars = ult_precio / p_base - 1
            var_dol = ult_dolar / d_base - 1
            carry_usd = (1 + ret_ars) / (1 + var_dol) - 1
            tabla.append({
                "ticker":     tk,
                "base":       round(p_base, 4),
                "final":      round(ult_precio, 4),
                "fecha_base": f_base.isoformat(),
                "ret_ars":    round(ret_ars * 100, 3),
                "var_dolar":  round(var_dol * 100, 3),
                "carry_usd":  round(carry_usd * 100, 3),
            })
        tabla.sort(key=lambda x: -x["carry_usd"])

    return {
        "curva":        curva,
        "dolar":        dolar_l,
        "fecha_base":   serie[0]["fecha"] if serie else None,
        "fecha_final":  serie[-1]["fecha"] if serie else None,
        "serie":        serie,
        "tabla":        tabla,
    }
