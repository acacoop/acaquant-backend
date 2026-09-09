"""api/services/research_1816_sql.py — lectura de las series de 1816 para el
LABORATORIO de series/spreads de la vista RESEARCH (pilar A). Doc: docs/RESEARCH.md.

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
from core import mercado_1816
from core.postgres import get_pool

logger = logging.getLogger(__name__)

# Campos guardados (jobs/mercado_1816_series._CAMPOS). tea/paridad son FRACCIÓN.
CAMPOS = ("tea", "paridad", "precioClean", "duration")
# Qué campos vienen en FRACCIÓN (0.42 = 42%). Dato del ALMACENAMIENTO: vive acá,
# donde se guarda, y no repetido en cada consumidor — un `campo in ("tea",
# "paridad")` suelto en otro módulo se desincroniza en silencio al sumar un campo.
CAMPOS_FRACCION = ("tea", "paridad")
_CAMPO_DEFAULT = "tea"


def escala(campo: str) -> float:
    """Factor para mostrar el campo en % (1.0 si ya viene en su unidad)."""
    return 100.0 if campo in CAMPOS_FRACCION else 1.0


def _campo_ok(campo: str) -> str:
    return campo if campo in CAMPOS else _CAMPO_DEFAULT


def _moneda_efectiva(objetivo: str, guardadas: dict[str, int]) -> str:
    """La moneda con la que hay que LEER un ticker: la que le corresponde
    (`objetivo`) si esa serie existe; si todavía no se bajó, la que haya.

    El fallback existe por la VENTANA entre el deploy y el rebajado: apenas sale
    este cambio, los hard dollar tienen solo la serie vieja en `ars` y exigir
    `mep` los borraría del selector y dejaría los gráficos en blanco. Con el
    fallback siguen dibujando lo de siempre —rotulado CCL, que es lo que es— y
    pasan a MEP solos en cuanto el backfill termina.
    """
    if objetivo in guardadas:
        return objetivo
    return max(guardadas, key=lambda m: guardadas[m]) if guardadas else objetivo


@cached(ttl=300)
def _monedas(tickers: tuple[str, ...]) -> dict[str, str]:
    """{ticker: moneda} — CUÁL de las series guardadas es LA del ticker.

    ⚠️ **Sin esto el laboratorio duplica puntos en silencio.** `moneda` es parte
    de la PK de `mkt_1816_series`, así que las filas nuevas en `mep` (los hard
    dollar, desde 2026-09-09) CONVIVEN con las viejas en `ars` del mismo ticker,
    fecha y campo. Un `WHERE ticker = …` sin moneda devuelve las dos, y el JOIN
    de `spread()` devuelve el producto cartesiano: dos puntos por fecha, ninguno
    marcado como sospechoso. Qué moneda le toca a cada bono lo decide la MISMA
    función que usa el writer (`core.mercado_1816.moneda_series`) para que no
    puedan discrepar — REGLA #9.

    Las filas viejas en `ars` de un hard dollar NO se borran: son el único
    registro del tramo anterior a lo que la API deja rebajar (topea en 1 año) y
    acá quedan invisibles, que es lo que hace falta.
    """
    if not tickers:
        return {}
    # La moneda que le TOCA a cada ticker sale del cliente, que es donde vive la
    # convención y de donde la toman también los jobs. Acá solo se decide qué
    # hay guardado.
    objetivo = mercado_1816.monedas_de(list(tickers))
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker, moneda, count(*) FROM research.mkt_1816_series "
                    "WHERE ticker = ANY(%s) GROUP BY ticker, moneda", (list(tickers),))
        guardadas: dict[str, dict[str, int]] = {}
        for tk, mon, n in cur.fetchall():
            guardadas.setdefault(tk, {})[mon] = n
    return {tk: _moneda_efectiva(objetivo.get(tk, "ars"), guardadas.get(tk, {}))
            for tk in tickers}


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
            # Agrupado por (ticker, MONEDA), no solo por ticker: un hard dollar
            # tiene las dos series —la vieja en `ars` y la nueva en `mep`— y el
            # rango de cada una es distinto (la de `mep` arranca donde llegó el
            # rebajado, porque la API topea en 1 año). Mezclarlas anunciaría una
            # historia más larga que la que el gráfico realmente puede dibujar.
            cur.execute(
                """
                SELECT s.ticker, s.moneda,
                       coalesce(i.curva, w.curva, 'otros') AS curva,
                       i.denominacion, i.moneda_pago, i.fecha_vencimiento,
                       min(s.fecha) AS desde, max(s.fecha) AS hasta,
                       count(*) AS n
                FROM research.mkt_1816_series s
                LEFT JOIN research.mkt_1816_instrumentos i ON i.ticker = s.ticker
                LEFT JOIN research.mkt_1816_watch w ON w.ticker = s.ticker
                GROUP BY s.ticker, s.moneda, i.curva, w.curva, i.denominacion,
                         i.moneda_pago, i.fecha_vencimiento
                ORDER BY curva, s.ticker
                """
            )
            cols = [c.name for c in cur.description]
            filas = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    except Exception as e:
        logger.warning("research_1816_sql.universo falló (%s)", e)
        return {"curvas": [], "total": 0}
    # MISMA regla que `_monedas` (objetivo + fallback a lo que haya), calculada
    # sobre las filas ya traídas para no volver a la base.
    guardadas: dict[str, dict[str, int]] = {}
    for f in filas:
        guardadas.setdefault(f["ticker"], {})[f["moneda"]] = f["n"]
    grupos: dict[str, list] = {}
    for f in filas:
        # Se queda la fila de la moneda que manda para ese bono; las otras son
        # historia invisible (ver `_monedas`).
        moneda = _moneda_efectiva(mercado_1816.moneda_series(f.get("moneda_pago")),
                                  guardadas.get(f["ticker"], {}))
        if f.get("moneda") != moneda:
            continue
        grupos.setdefault(f["curva"], []).append({
            "ticker": f["ticker"],
            "denominacion": f.get("denominacion"),
            "moneda": f.get("moneda_pago"),
            # A QUÉ DÓLAR está la serie de este bono. Viaja hasta la UI para que
            # la pregunta que abrió todo esto —«¿esto está en MEP o en CCL?»— se
            # conteste mirando la pantalla y no leyendo un job.
            "dolar": ("MEP" if moneda == "mep"
                      else "CCL" if (f.get("moneda_pago") or "").strip().upper() == "USD"
                      else "ARS"),
            "vencimiento": f["fecha_vencimiento"].isoformat() if f.get("fecha_vencimiento") else None,
            "desde": f["desde"].isoformat() if f.get("desde") else None,
            "hasta": f["hasta"].isoformat() if f.get("hasta") else None,
        })
    total = sum(len(b) for b in grupos.values())
    return {"curvas": [{"curva": c, "bonos": b} for c, b in sorted(grupos.items())],
            "total": total, "campos": list(CAMPOS)}


def series(tickers: list[str], campo: str, desde: str | None = None,
           hasta: str | None = None) -> dict:
    """Serie del `campo` para cada ticker (overlay). {campo, desde, hasta,
    series:[{ticker, puntos:[[fecha, valor], …]}]}."""
    tickers = [t.strip().upper() for t in (tickers or []) if t.strip()][:8]
    campo = _campo_ok(campo)
    if not tickers:
        return {"campo": campo, "series": []}
    d0, d1 = _rango(desde, hasta)
    # Fuera del `with`: `_monedas` toma su propia conexión del pool y anidarlas
    # es pedirle dos al mismo tiempo por request.
    monedas = _monedas(tuple(tickers))
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            # Un OR por ticker en vez de `ticker = ANY(...)`: cada bono tiene SU
            # moneda (los hard dollar en `mep`, el resto en `ars`) y el par
            # (ticker, moneda) es lo que identifica una serie. Son ≤8 ramas —
            # `tickers` está topeado arriba— y cada una entra por el mismo índice
            # (ticker, campo, fecha).
            cond = " OR ".join(["(ticker = %s AND moneda = %s)"] * len(tickers))
            pares: list = []
            for tk in tickers:
                pares += [tk, monedas.get(tk, "ars")]
            cur.execute(
                f"SELECT ticker, fecha, valor FROM research.mkt_1816_series "
                f"WHERE ({cond}) AND campo = %s AND fecha BETWEEN %s AND %s "
                "ORDER BY ticker, fecha",
                (*pares, campo, d0, d1),
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
    monedas = _monedas((a, b))
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            # `x.moneda` / `y.moneda` NO son decoración: sin ellas el JOIN cruza
            # las filas viejas en `ars` con las nuevas en `mep` y devuelve hasta
            # cuatro spreads por fecha, todos plausibles.
            cur.execute(
                """
                SELECT x.fecha, (x.valor - y.valor) AS spread
                FROM research.mkt_1816_series x
                JOIN research.mkt_1816_series y
                  ON y.fecha = x.fecha AND y.ticker = %s AND y.campo = %s
                 AND y.moneda = %s
                WHERE x.ticker = %s AND x.campo = %s AND x.moneda = %s
                  AND x.fecha BETWEEN %s AND %s
                  AND x.valor IS NOT NULL AND y.valor IS NOT NULL
                ORDER BY x.fecha
                """,
                (b, campo, monedas.get(b, "ars"),
                 a, campo, monedas.get(a, "ars"), d0, d1),
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
