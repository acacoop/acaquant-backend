"""Capa de servicio — series macro y clasificación.

Expone dos tools al asistente:

- `obtener_serie_macro(variable, ventana_dias)` — devuelve actual + serie +
  stats + clasificación.
- `clasificar_nivel(variable, ventana_dias)` — wrapper con output compacto
  (solo clasificación + contexto textual), sin la serie entera.

Variables soportadas:
- Macros puras: tamar, cer, dolar, badlar, mep
- (Series por ticker `<TICKER>.<CAMPO>` ELIMINADAS 2026-06-22 — leían TimeSales 90d, MCP-only.)
  Campos: TEA, TEM, paridad, duration, price

Variables bloqueadas por data faltante (devuelven stub con hint):
- ccl, canje, ipc, ipim, riesgo_pais, repo, rem_inflacion

"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from api.cache import cached

# ─────────────────────────────────────────────────────────────────────────────
# Series raw de cotizaciones (BCRA + Dólar financiero)
#
# Wrappers simples a Mongo usados por los endpoints `/api/cotizaciones/*`.
# La tool analítica `obtener_serie_macro` (más abajo) hace lo mismo vía
# `_fetch_serie_macro` genérico + stats. Se mantienen separados porque los
# callers HTTP devuelven data cruda sin stats ni clasificación.
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=5)
def get_ultimo_mep() -> dict:
    """Último valor de dólar MEP/CCL/canje + oficial. Prefiere snapshot live
    (5s desde engines/dolares.py vía WS) y cae al último doc del cron de cierre
    (Valuaciones.Dolar) si el snapshot live no existe.

    Devuelve {mep, ccl, canje, oficial, timestamp, source}. Los 4 campos
    numéricos pueden ser None si los inputs WS no están disponibles.

    El `oficial` viene de valuaciones.dolar_oficial_live (MAE UST$T mayorista
    plazo 000) — fuente única del dólar oficial en toda la app. Es
    independiente de la fuente del MEP/CCL: el snapshot WS no lo escribe."""
    from core import dolar_sql
    from core.dolar_oficial import mid_oficial_live

    snap = dolar_sql.snapshot_live()
    base = snap if (snap and snap.get("mep") is not None) else None
    if base is None:
        doc = dolar_sql.ultimo("mep")
        if doc:
            doc["source"] = "cron_close"
        base = doc or {}

    base["oficial"] = mid_oficial_live("oficial").get("value")
    return base


@cached(ttl=300)
def get_ultimo_uva() -> float | None:
    """Último UVA cargado MANUALMENTE en `macro.uva` (1 valor por fecha).

    Cuando UVA cambia se inserta una fila nueva (carga manual) — el cron diario /
    motor lo levanta solo (vía cache TTL 5min)."""
    from core import dolar_sql
    return dolar_sql.ultimo_uva()


@cached(ttl=300)
def get_historico_mep(desde: str | None = None, hasta: str | None = None) -> list:
    """Serie histórica del dólar MEP (valuaciones.dolar). [{mep, timestamp}]."""
    from core import dolar_sql
    d = datetime.fromisoformat(desde).replace(tzinfo=UTC) if desde else datetime(2000, 1, 1, tzinfo=UTC)
    h = (datetime.fromisoformat(hasta + "T23:59:59").replace(tzinfo=UTC)
         if hasta else datetime.now(UTC))
    return dolar_sql.serie("mep", d, h)


@cached(ttl=60)
def get_historico_dolares(
    desde: str | None = None,
    hasta: str | None = None,
    ventana_dias: int = 30,
) -> dict:
    """3 series de dólar DAILY-CLOSE para el chart de la watchlist ARGY.

    Devuelve `{mep: [...], ccl: [...], oficial: [...]}` con cada item
    `{ts: "YYYY-MM-DD", valor: float}` — un punto por día. Garantiza
    continuidad visual (sin cortes intraday, sin huecos los fines de
    semana cuando se conectan los puntos en el frontend).

    Fuentes (SQL-native, decomiso Mongo):
      - MEP / CCL → último tick del día (ART) en valuaciones.dolar (WS engines/dolares.py).
      - Oficial   → macro.series_macro serie 'DOLAR' (A3500 BCRA fixing diario,
                    jobs/bcra.py 22 UTC L-V). 1 valor por día garantizado.

    Default ventana: últimos `ventana_dias` (30).
    """
    from core import dolar_sql
    from core.series_macro import serie_dict

    # Resolución de ventana
    if hasta:
        hasta_dt = datetime.fromisoformat(hasta + "T23:59:59").replace(tzinfo=UTC)
    else:
        hasta_dt = datetime.now(UTC)
    if desde:
        desde_dt = datetime.fromisoformat(desde).replace(tzinfo=UTC)
    else:
        desde_dt = hasta_dt - timedelta(days=ventana_dias)

    # MEP + CCL: último tick por día (ART) desde valuaciones.dolar.
    dias = dolar_sql.serie_diaria(desde_dt, hasta_dt)
    mep_series = [{"ts": d["ts"], "valor": d["mep"]} for d in dias if d.get("mep") is not None]
    ccl_series = [{"ts": d["ts"], "valor": d["ccl"]} for d in dias if d.get("ccl") is not None]

    # Oficial = serie A3500 BCRA (macro.series_macro). 1 valor por día (string YYYY-MM-DD).
    desde_str = desde_dt.strftime("%Y-%m-%d")
    hasta_str = hasta_dt.strftime("%Y-%m-%d")
    of = serie_dict("DOLAR", desde_str, hasta_str, positivo=True)
    oficial_series = [{"ts": f, "valor": v} for f, v in sorted(of.items())]

    return {"mep": mep_series, "ccl": ccl_series, "oficial": oficial_series}

# ─────────────────────────────────────────────────────────────────────────────
# Tools expuestas al asistente — SQL-native (decomiso Mongo): delegan en macro_sql
# (macro.series_macro + valuaciones.dolar + mercado.mercado_hist). Las colecciones
# Trading.{TAMAR,CER,DOLAR,BADLAR,RiesgoPais,Inflacion*,Caucion} y Valuaciones.Dolar
# fueron dropeadas; macro_sql es la única implementación.
# ─────────────────────────────────────────────────────────────────────────────
