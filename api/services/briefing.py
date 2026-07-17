"""api/services/briefing.py — Briefing de apertura (QuantAI P1, v1 DETERMINISTA).

Arma el contenido del modal de las 10:00 ART de HOME con un modelo de columnas
UNIFORME para todas las filas: HOY · 1D · WTD · MTD.

  - HOY   = último valor/precio (o None → "Sin Ops" si no operó hoy).
  - 1D    = variación vs el cierre anterior. Puede faltar si hoy no hay dato.
  - WTD   = week-to-date (desde el cierre de la rueda previa al lunes de la semana).
  - MTD   = month-to-date (desde el cierre de la rueda previa al 1° del mes).

Punto clave: WTD y MTD se calculan SIEMPRE sobre el último cierre disponible —
sirven aunque hoy no haya operado (no dependen de 1D). Los futuros toman las
anclas que persiste jobs/market_anchors en su doc; los dólares (A3500/MEP/CCL)
se calculan al vuelo desde su propio histórico. El mayorista MAE no persiste
histórico (el feed upsertea una fila) → HOY vivo o "Sin Ops", y sus anclas WTD/MTD
salen de los cierres del A3500 del BCRA, que es el fixing de ese mismo mercado.

TODO determinista (regla de oro 1: la IA nunca es la fuente de un número). La
capa de redacción IA se agrega ARRIBA de esto; este service es su fallback.

Fuentes: home.market_quotes (futuros+anclas) · valuaciones.dolar_oficial_live
(mayorista MAE) · macro.series_macro DOLAR (A3500 BCRA) · valuaciones.dolar
(MEP/CCL histórico).
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from psycopg.rows import dict_row

from api.services import acreencias
from core.postgres import get_pool

_ART = ZoneInfo("America/Argentina/Buenos_Aires")
_FUTUROS_STALE_MIN = 30  # el poller corre cada 1 min; >30 min = feed caído

# Orden y agrupación de lectura de la mesa. El índice fija el orden de aparición;
# el segundo campo agrupa en el cartel. Los labels son los `symbol` de HOME_FUTUROS.
_FUTUROS_ORDEN: list[tuple[str, str]] = [
    ("S&P FUT",    "Índices US"),
    ("NASDAQ FUT", "Índices US"),
    ("WTI",        "Energía"),
    ("BRENT",      "Energía"),
    ("ORO",        "Metales"),
    ("SOJA",       "Granos"),
    ("MAIZ",       "Granos"),
    ("TRIGO",      "Granos"),
    ("BTCUSDT",    "Cripto"),
    ("ETHUSDT",    "Cripto"),
]
_FUT_POS = {lbl: i for i, (lbl, _) in enumerate(_FUTUROS_ORDEN)}
_FUT_GRUPO = dict(_FUTUROS_ORDEN)


def _var_pct(actual: float | None, anterior: float | None) -> float | None:
    if actual is None or not anterior:
        return None
    return round((float(actual) / float(anterior) - 1) * 100, 2)


def _anclas_wtd_mtd(serie: list[tuple[date, float]],
                    hoy_art: date) -> tuple[float | None, float | None]:
    """De una serie de cierres DESCENDENTE por fecha, devuelve los dos valores-ancla:
    el cierre previo al lunes de esta semana (WTD) y el previo al 1° del mes (MTD)."""
    lunes = hoy_art - timedelta(days=hoy_art.weekday())
    primero_mes = hoy_art.replace(day=1)
    wtd = next((v for f, v in serie if f < lunes), None)
    mtd = next((v for f, v in serie if f < primero_mes), None)
    return wtd, mtd


def _rets_hist(serie: list[tuple[date, Any]], hoy_art: date) -> dict[str, Any] | None:
    """Dada una serie de cierres [(fecha, valor), ...] DESCENDENTE por fecha,
    devuelve HOY (último cierre) + 1D/WTD/MTD. Las tres variaciones se anclan al
    último cierre, así que valen aunque hoy todavía no haya rueda."""
    limpia = [(f, float(v)) for f, v in serie if v is not None]
    if not limpia:
        return None
    fecha0, hoy = limpia[0]
    prev = limpia[1][1] if len(limpia) > 1 else None
    wtd, mtd = _anclas_wtd_mtd(limpia, hoy_art)
    return {
        "fecha":   fecha0,
        "hoy":     hoy,
        "ret_1d":  _var_pct(hoy, prev),
        "ret_wtd": _var_pct(hoy, wtd),
        "ret_mtd": _var_pct(hoy, mtd),
    }


def _futuros(cur) -> list[dict[str, Any]]:
    """Los 10 futuros con HOY (last) + 1D (pct_day) + WTD/MTD (anclas del doc)."""
    cur.execute(
        "SELECT data FROM home.market_quotes"
        " WHERE data->>'type' = 'future' OR data->>'grupo' = 'Futuros'"
    )
    out = []
    ahora = datetime.now(UTC)
    for fila in cur.fetchall():
        data = fila["data"]
        label = data.get("symbol")
        upd = data.get("updated_at")
        stale = True
        if upd:
            try:
                ts = datetime.fromisoformat(upd)
                stale = (ahora - ts) > timedelta(minutes=_FUTUROS_STALE_MIN)
            except ValueError:
                pass
        last = data.get("last")
        out.append({
            "label":      label,
            "grupo":      _FUT_GRUPO.get(label, "Otros"),
            "hoy":        last,
            "ret_1d":     round(data["pct_day"], 2) if data.get("pct_day") is not None else None,
            "ret_wtd":    _var_pct(last, data.get("anchor_wtd")),
            "ret_mtd":    _var_pct(last, data.get("anchor_mtd")),
            "updated_at": upd,
            "stale":      stale,
        })
    out.sort(key=lambda r: (_FUT_POS.get(r["label"], 99), r["label"] or ""))
    return out


def _serie_a3500(cur) -> list[tuple[date, float]]:
    """Cierres del A3500 (BCRA) DESCENDENTE por fecha. 45 ruedas alcanzan para
    las anclas WTD/MTD."""
    cur.execute(
        "SELECT fecha, valor FROM macro.series_macro"
        " WHERE serie = 'DOLAR' AND valor IS NOT NULL ORDER BY fecha DESC LIMIT 45"
    )
    return [(f["fecha"], float(f["valor"])) for f in cur.fetchall()]


def _mayorista(cur, hoy_art: date, serie_a3500: list[tuple[date, float]]) -> dict[str, Any]:
    """Mayorista MAE de HOY (live). Si el feed no operó hoy, HOY=None (el front
    muestra 'Sin Ops', nunca un número viejo).

    El feed MAE no persiste histórico (upsertea UNA fila por instrumento), así que
    WTD/MTD se anclan en los cierres del A3500 del BCRA — que ES el fixing de ese
    mismo mercado mayorista. 1D sigue siendo la variación intradía que reporta MAE.
    """
    row = {"label": "Mayorista MAE", "hoy": None, "ret_1d": None,
           "ret_wtd": None, "ret_mtd": None, "ts": None}
    cur.execute(
        "SELECT data, updated_at FROM valuaciones.dolar_oficial_live"
        " WHERE ticker = 'UST$T' AND codigo_segmento = 'M' AND codigo_plazo = '000'"
    )
    fila = cur.fetchone()
    if fila and fila["updated_at"] and fila["updated_at"].astimezone(_ART).date() == hoy_art:
        data = fila["data"]
        hoy = data.get("precioUltimo")
        wtd, mtd = _anclas_wtd_mtd(serie_a3500, hoy_art)
        row.update(hoy=hoy, ret_1d=data.get("variacion"), ts=fila["updated_at"],
                   ret_wtd=_var_pct(hoy, wtd), ret_mtd=_var_pct(hoy, mtd))
    return row


def _a3500(serie_a3500: list[tuple[date, float]], hoy_art: date) -> dict[str, Any] | None:
    r = _rets_hist(serie_a3500, hoy_art)
    if r:
        r["label"] = "A3500 BCRA"
    return r


def _financieros(cur, hoy_art: date) -> list[dict[str, Any]]:
    """MEP y CCL: cierre por día (último valor del día) → 1D/WTD/MTD."""
    cur.execute(
        """
        SELECT DISTINCT ON (date(timestamp)) date(timestamp) AS dia, mep, ccl
        FROM valuaciones.dolar
        WHERE mep IS NOT NULL AND timestamp >= now() - interval '50 days'
        ORDER BY date(timestamp) DESC, timestamp DESC
        LIMIT 45
        """
    )
    filas = cur.fetchall()
    out = []
    for label, campo in (("MEP", "mep"), ("CCL", "ccl")):
        r = _rets_hist([(f["dia"], f[campo]) for f in filas], hoy_art)
        if r:
            r["label"] = label
            out.append(r)
    return out


def _cauciones() -> list[dict[str, Any]]:
    """Caución ARS y USD tal cual la watchlist: el snapshot MÁS ACTUAL de cada
    moneda (el plazo que esté operando — 1d, 3d, el que sea), TNA en %.
    Fuente: mercado.caucion_snapshot (motor de caución), misma que /argy.
    Sin histórico intradía acá → las columnas de retorno van None."""
    from api.services import mercado_hist_sql
    out = []
    for moneda in ("ARS", "USD"):
        docs = mercado_hist_sql.get_caucion(moneda=moneda)
        docs = sorted(docs, key=lambda d: d.get("updated_at") or "", reverse=True)
        snap = docs[0] if docs else None
        if not snap:
            continue
        tna = snap.get("tna_last")
        if tna is None:
            tna = snap.get("tna_closing")
        plazo = snap.get("plazo_dias")
        out.append({
            "label":   f"Caución {moneda}{f' {plazo}d' if plazo else ''} · TNA%",
            "hoy":     tna,
            "ret_1d":  None,
            "ret_wtd": None,
            "ret_mtd": None,
        })
    return out


def briefing_hoy() -> dict[str, Any]:
    """Payload del modal. Tres bloques, cada fila con el mismo shape
    (label, hoy, ret_1d, ret_wtd, ret_mtd). Cada bloque puede venir []/None si su
    fuente no tiene datos — el front decide qué renderizar (failing gracefully)."""
    hoy_art = datetime.now(_ART).date()
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        futuros = _futuros(cur)
        serie_a3500 = _serie_a3500(cur)
        mayorista = _mayorista(cur, hoy_art, serie_a3500)
        a3500 = _a3500(serie_a3500, hoy_art)
        financieros = _financieros(cur, hoy_art)
    oficial = [mayorista] + ([a3500] if a3500 else [])
    # Bonos que pagan hoy (cupón/amort/vto). Estructural sobre curvas, filtrado a lo
    # que hay en cartera. Cacheado por día → el polling del modal no recomputa.
    pagan_hoy = acreencias.bonos_pagan_en_fecha(hoy_art.isoformat())
    return {
        "fecha":       hoy_art.isoformat(),
        "generado":    datetime.now(UTC),
        "futuros":     futuros,       # índices US/energía/metales/granos/cripto
        "oficial":     oficial,       # mayorista MAE (live) + A3500 (fixing)
        "financieros": financieros,   # MEP + CCL
        "cauciones":   _cauciones(),  # TNA ARS/USD del plazo vigente (watchlist)
        "pagan_hoy":   pagan_hoy,     # [] si ninguno paga hoy
    }
