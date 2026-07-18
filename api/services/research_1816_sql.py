"""api/services/research_1816_sql.py — lectura de las series de 1816 para el
LABORATORIO de series/spreads de la vista RESEARCH (pilar A). Doc: docs/VISTA_RESEARCH.md.

Lee `research.mkt_1816_series` (lo puebla jobs/mercado_1816_series). Puro (sin
FastAPI). 0 créditos: todo sale de la DB. Tres cosas:
  - universo(): los bonos disponibles (para los selectores), agrupados por curva.
  - series(): la serie de un campo para N bonos (overlay).
  - spread(): la serie A−B en el tiempo + stats de valor relativo (dónde está el
    spread de HOY vs su propia historia: percentil, z-score, mín/máx).
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from statistics import fmean, pstdev

from api.cache import cached
from core.postgres import get_pool

logger = logging.getLogger(__name__)

# Campos guardados (jobs/mercado_1816_series._CAMPOS). tea/paridad son FRACCIÓN.
CAMPOS = ("tea", "paridad", "precioClean", "duration")
_CAMPO_DEFAULT = "tea"


def _campo_ok(campo: str) -> str:
    return campo if campo in CAMPOS else _CAMPO_DEFAULT


def _rango(desde: str | None, hasta: str | None) -> tuple[date, date]:
    """Default: últimos 6 meses. Parsea YYYY-MM-DD; ignora lo inválido."""
    hoy = datetime.now(UTC).date()
    def _p(s, dflt):
        try:
            return datetime.strptime(s, "%Y-%m-%d").date() if s else dflt
        except (TypeError, ValueError):
            return dflt
    return _p(desde, hoy - timedelta(days=182)), _p(hasta, hoy)


@cached(ttl=300)
def universo() -> dict:
    """Bonos disponibles para los selectores, agrupados por curva. Toma los que
    TIENEN series (join con lo que realmente se bajó), enriquecidos con el catálogo."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.ticker,
                       coalesce(i.curva, w.curva, 'otros') AS curva,
                       i.denominacion, i.moneda_pago, i.fecha_vencimiento,
                       min(s.fecha) AS desde, max(s.fecha) AS hasta
                FROM research.mkt_1816_series s
                LEFT JOIN research.mkt_1816_instrumentos i ON i.ticker = s.ticker
                LEFT JOIN research.mkt_1816_watch w ON w.ticker = s.ticker
                GROUP BY s.ticker, i.curva, w.curva, i.denominacion,
                         i.moneda_pago, i.fecha_vencimiento
                ORDER BY curva, s.ticker
                """
            )
            cols = [c.name for c in cur.description]
            filas = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    except Exception as e:
        logger.warning("research_1816_sql.universo falló (%s)", e)
        return {"curvas": [], "total": 0}
    grupos: dict[str, list] = {}
    for f in filas:
        grupos.setdefault(f["curva"], []).append({
            "ticker": f["ticker"],
            "denominacion": f.get("denominacion"),
            "moneda": f.get("moneda_pago"),
            "vencimiento": f["fecha_vencimiento"].isoformat() if f.get("fecha_vencimiento") else None,
            "desde": f["desde"].isoformat() if f.get("desde") else None,
            "hasta": f["hasta"].isoformat() if f.get("hasta") else None,
        })
    return {"curvas": [{"curva": c, "bonos": b} for c, b in sorted(grupos.items())],
            "total": len(filas), "campos": list(CAMPOS)}


def series(tickers: list[str], campo: str, desde: str | None = None,
           hasta: str | None = None) -> dict:
    """Serie del `campo` para cada ticker (overlay). {campo, desde, hasta,
    series:[{ticker, puntos:[[fecha, valor], …]}]}."""
    tickers = [t.strip().upper() for t in (tickers or []) if t.strip()][:8]
    campo = _campo_ok(campo)
    if not tickers:
        return {"campo": campo, "series": []}
    d0, d1 = _rango(desde, hasta)
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT ticker, fecha, valor FROM research.mkt_1816_series "
                "WHERE ticker = ANY(%s) AND campo = %s AND fecha BETWEEN %s AND %s "
                "ORDER BY ticker, fecha",
                (tickers, campo, d0, d1),
            )
            por_tk: dict[str, list] = {}
            for tk, fecha, valor in cur.fetchall():
                por_tk.setdefault(tk, []).append([fecha.isoformat(), valor])
    except Exception as e:
        logger.warning("research_1816_sql.series falló (%s)", e)
        return {"campo": campo, "series": []}
    # respeta el orden pedido
    return {"campo": campo, "desde": d0.isoformat(), "hasta": d1.isoformat(),
            "series": [{"ticker": t, "puntos": por_tk.get(t, [])} for t in tickers]}


def spread(a: str, b: str, campo: str, desde: str | None = None,
           hasta: str | None = None) -> dict:
    """Serie A−B en el tiempo + stats de valor relativo (percentil/z del spread de
    HOY contra su propia historia). El corazón del laboratorio."""
    a, b = a.strip().upper(), b.strip().upper()
    campo = _campo_ok(campo)
    d0, d1 = _rango(desde, hasta)
    if not a or not b:
        return {"a": a, "b": b, "campo": campo, "puntos": [], "stats": None}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT x.fecha, (x.valor - y.valor) AS spread
                FROM research.mkt_1816_series x
                JOIN research.mkt_1816_series y
                  ON y.fecha = x.fecha AND y.ticker = %s AND y.campo = %s
                WHERE x.ticker = %s AND x.campo = %s AND x.fecha BETWEEN %s AND %s
                  AND x.valor IS NOT NULL AND y.valor IS NOT NULL
                ORDER BY x.fecha
                """,
                (b, campo, a, campo, d0, d1),
            )
            puntos = [[f.isoformat(), s] for f, s in cur.fetchall()]
    except Exception as e:
        logger.warning("research_1816_sql.spread falló (%s)", e)
        return {"a": a, "b": b, "campo": campo, "puntos": [], "stats": None}
    vals = [p[1] for p in puntos if p[1] is not None]
    stats = None
    if vals:
        actual = vals[-1]
        n = len(vals)
        media = fmean(vals)
        sd = pstdev(vals) if n > 1 else 0.0
        percentil = round(sum(1 for v in vals if v <= actual) / n * 100, 1)
        stats = {
            "actual": actual, "min": min(vals), "max": max(vals),
            "media": media, "z": round((actual - media) / sd, 2) if sd else 0.0,
            "percentil": percentil, "n": n,
        }
    return {"a": a, "b": b, "campo": campo, "desde": d0.isoformat(),
            "hasta": d1.isoformat(), "puntos": puntos, "stats": stats}
