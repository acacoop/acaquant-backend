"""Capa de servicio — renta fija (MarketSnapshot + TimeSales + Curvas).

Funciones puras (sin FastAPI, sin HTTP) que consultan la data de renta fija
local: snapshot de libro/trades, histórico de trades por ticker, serie diaria
por curva, y el tool maestro `listar_curva` que enriquece la curva con TEA/
TEM/paridad/duration/convexity.

Helpers compartidos (`resolver_ticker_exacto`, `_ticker_filter`, constantes
`_CURVAS_VALIDAS`) viven acá porque la capa de renta fija es la que define
tickers y curvas; el resto de services los importa desde este módulo.
"""
from __future__ import annotations

import logging
import re
from datetime import date

from api.cache import cached
from core import curvas_sql

logger = logging.getLogger(__name__)

# `dual` se suma con el rediseño 2026-08-15: los duales NO tienen `curva='dual'`
# (viven bajo cer/tamar), así que se resuelven por el EJE `ajuste` — ver
# `_fetch_curva_docs` y docs/RENTA_FIJA.md §0.
_CURVAS_VALIDAS = ("cer", "tasa_fija", "tamar", "soberanos", "dolar_linked", "dual")
_ORDENES_VALIDOS = ("vencimiento", "volumen_dia", "tea", "duration")


def _ticker_filter(instrumento: str) -> dict:
    """Regex substring escape — compat con ticker corto o completo."""
    return {"$regex": re.escape(instrumento), "$options": "i"}


def resolver_ticker_exacto(instrumento: str) -> str | None:
    """Resuelve un ticker (corto o completo) al ticker completo de ROFEX.

    Evita regex table-scan cuando se puede usar match exacto (índice
    (ticker, timestamp) en TimeSales). Si el input ya trae ' - ', se asume
    completo. Si es corto, se busca en Trading.Curvas.ticker_corto.

    Devuelve el ticker completo o None si no se pudo resolver.
    """
    instr = (instrumento or "").strip()
    if not instr:
        return None
    if " - " in instr:
        return instr
    doc = curvas_sql.find_one(instr)
    return doc.get("ticker") if doc else None


# ─────────────────────────────────────────────────────────────────────────────
# Renta Fija (MarketSnapshot)
# ─────────────────────────────────────────────────────────────────────────────


def _tc_breakeven(precio: float | None, flujo_vto: float | None,
                  mep: float | None) -> float | None:
    """TC al que el bono en pesos comprado hoy y mantenido a vto empata
    contra haber comprado dólar MEP hoy.

        TC_BE = MEP × (flujo_vencimiento / precio_actual)

    Solo aplica a tasa fija (incluye CER ya fijados, donde el flujo final
    está determinado). Devuelve None si falta cualquier input.
    """
    if not precio or not flujo_vto or not mep:
        return None
    if precio <= 0 or flujo_vto <= 0 or mep <= 0:
        return None
    return round(mep * (flujo_vto / precio), 2)


@cached(ttl=10)
def get_renta_fija(instrumento: str | None = None) -> list:
    """Snapshot de renta fija con métricas live + TC breakeven (tasa fija).

    TTL=10s (subido de 5s 2026-04-28): con el frontend poleando cada 5s
    en /snapshot-live + 8 users en mesa, el cache de 5s se vencía en
    cada poll y el backend recalculaba 12 veces/min. Con 10s, recalcula
    6/min — 50% menos trabajo. El delay máximo del dato visto por el
    user pasa de 5s a 10s, despreciable para los bonos de la mesa.

    El TC BE se calcula on-the-fly: requiere `flujo_vencimiento` (de
    Trading.Curvas) + last_price (del snapshot) + MEP live (macro).
    Sólo se popula para tickers cuya curva sea tasa_fija nativa o CER ya
    fijado (mismo set que `listar_curva` cuando curva='tasa_fija').
    """
    # SQL-native (decomiso Mongo): delega en el twin SQL (mismo shape). Trading.MarketSnapshot
    # dropeada → la fuente es mercado.market_snapshot + mercado.curvas.
    from api.services import renta_fija_sql
    return renta_fija_sql.get_renta_fija(instrumento=instrumento)


# ─────────────────────────────────────────────────────────────────────────────
# Histórico de trades + curvas (Trading.TimeSales)
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=15)
def get_historico_trades(instrumento: str | None = None) -> list:
    """Trades de HOY solamente (sin histórico). Si un bono operó hoy aparece; si no, no.
    El nombre quedó por compat — ya NO trae días viejos. Match EXACTO por ticker (índice)."""
    # SQL-native (decomiso Mongo): delega en el twin SQL. Trading.TimeSales dropeada →
    # la fuente es mercado.timesales (solo trades de hoy).
    from api.services import renta_fija_sql
    return renta_fija_sql.get_historico_trades(instrumento=instrumento)


@cached(ttl=30)
def _bonos_cer_fijados() -> set[str]:
    """Tickers de bonos CER cuyo CER de liquidación del VTO ya fue publicado
    por el BCRA → efectivamente tasa fija desde ya. Se recalcula por request
    (barato: 1 query CER + 1 query DiasHabiles + loop chico).

    El `db` se resuelve adentro (no como arg) porque el decorador
    `@cached` solo acepta kwargs hashables — pasar el handle de Mongo
    como posicional dispara `takes 0 positional arguments but 1 was given`
    y rompe todo `listar_curva(curva ∈ {cer, tasa_fija})`.

    Fallback: si la función falla por cualquier motivo (Mongo down, dato
    faltante, comparación de tipos), devuelve set vacío y logea. Así
    `listar_curva` sigue devolviendo bonos aunque la reasignación CER↔
    tasa_fija se pierda. Sin esto, todo `listar_curva` con curva ∈ {cer,
    tasa_fija} se cae con un error opaco "error del service".
    """
    # SQL-native (decomiso Mongo): delega en el twin SQL. Trading.CER/DiasHabiles dropeadas →
    # la fuente es macro.series_macro + mercado.dias_habiles.
    from api.services import renta_fija_sql
    return renta_fija_sql._bonos_cer_fijados()


def listar_curva(
    curva: str,
    ordenar_por: str = "vencimiento",
    vencimiento_min_meses: float | None = None,
    vencimiento_max_meses: float | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Lista los bonos de una curva con metadata enriquecida.

    Reasignación automática CER → tasa_fija: los bonos CER cuyo CER de
    liquidación del vencimiento ya está publicado por el BCRA se comportan
    como tasa fija (su flujo final está determinado). Por eso:
      - `curva='cer'`       → incluye solo los CER que TODAVÍA no están fijados.
      - `curva='tasa_fija'` → incluye tasa fija propia + CER ya fijados, con
                              un flag `cer_fijado=true` para el frontend.

    Devuelve para cada instrumento: ticker, ticker_corto, tipo, vencimiento,
    precio, TEA/TEM, paridad, duration, volumen del día. Ordenable por
    vencimiento (default), volumen, TEA o duration. Filtrable por horizonte
    (vencimiento_min/max_meses).
    """
    if curva not in _CURVAS_VALIDAS:
        return []
    if ordenar_por not in _ORDENES_VALIDOS:
        ordenar_por = "vencimiento"

    # SQL-native (decomiso Mongo): delega en el twin SQL (mismo shape). Trading.MarketSnapshot/
    # Curvas dropeadas → la fuente es mercado.market_snapshot + mercado.curvas.
    from api.services import renta_fija_sql
    return renta_fija_sql.listar_curva(
        curva=curva,
        ordenar_por=ordenar_por,
        vencimiento_min_meses=vencimiento_min_meses,
        vencimiento_max_meses=vencimiento_max_meses,
        limit=limit,
    )


@cached(ttl=60)
def get_historico_curva(curva: str) -> list:
    """Serie diaria por ticker de una curva: último precio + enriquecimiento.

    SQL-NATIVE (cutover SnapshotsCierre 2026-06-24): `Trading.SnapshotsCierre`
    (Mongo) fue migrada → dropeada. El cierre histórico vive en
    `mercado.snapshots_cierre_hist`; el live-fallback de hoy en
    `mercado.market_snapshot`. Esta función (path "Mongo" del selector
    RENTA_FIJA_SQL) ya NO lee Mongo — delega en el twin SQL, que produce el
    MISMO shape. Mantenemos la función para no romper el selector ni los
    callers; converge a una sola implementación.
    """
    from api.services import renta_fija_sql
    return renta_fija_sql.get_historico_curva(curva=curva)


def get_retorno_total_data(curva: str) -> dict:
    """Datos para la vista 'Retorno Total' consolidada.

    Junta en un solo payload todo lo que el frontend necesita para
    calcular retornos en ARS y en USD con carry-forward:

      - `rows`: precios diarios por (fecha, ticker) — `get_historico_curva`.
      - `mep` / `oficial`: serie diaria del dólar (último valor del día),
        SOLO para curvas en pesos (`tasa_fija`, `cer`). Para `soberanos`
        los precios ya están en USD → ambas series vuelven vacías.
      - `flujos`: calendario de cupones/amortizaciones por ticker, en cash
        real por 100 de VN — para que el frontend calcule RETORNO TOTAL
        (precio + cobros), no solo variación de precio. Ver
        `_calendario_flujos`.

    El cálculo de retornos se hace 100% en el frontend (una sola fuente
    de la lógica, antes duplicada entre retorno-total y carry-trade).

    Los helpers de serie de dólar se importan de `carry_trade` adentro de
    la función a propósito: si ese import fallara, solo se cae este
    endpoint — no el resto de la API.
    """
    rows = get_historico_curva(curva=curva)
    out: dict = {"curva": curva, "rows": rows, "mep": {}, "oficial": {}, "flujos": {}}
    if curva in ("tasa_fija", "cer") and rows:
        from api.services.carry_trade import (
            _serie_mep_diaria,
            _serie_oficial_diaria,
        )
        fechas = sorted({r["fecha"] for r in rows if r.get("fecha")})
        if fechas:
            desde = date.fromisoformat(fechas[0])
            hasta = date.fromisoformat(fechas[-1])
            # _serie_*_diaria ya leen SQL (dolar_sql / macro.series_macro); el 1er
            # arg quedó por compat de firma y se ignora.
            out["mep"] = {
                f.isoformat(): round(v, 4)
                for f, v in _serie_mep_diaria(None, desde, hasta).items()
            }
            out["oficial"] = {
                f.isoformat(): round(v, 4)
                for f, v in _serie_oficial_diaria(None, desde, hasta).items()
            }
    if rows:
        out["flujos"] = _calendario_flujos(curva)
    return out


def _calendario_flujos(curva: str) -> dict[str, list[dict]]:
    """Calendario de flujos por ticker: `{ticker_corto: [{fecha, monto}]}`.

    `monto` = cash real cobrado por cada 100 de VN, en la MISMA escala que
    el precio de mercado de la vista:
      - `tasa_fija`: amortización + interés (absolutos).
      - `cer`: (amort% + cupón) CER-ajustado a la fecha de pago — se
        multiplica por `CER_liquidación / cer_emisión`. El precio de
        mercado de un bono CER ya viene CER-ajustado, así que el cobro
        también tiene que estarlo (sin esto un bono que amortiza muestra
        una "pérdida" fake porque el precio cae al devolver capital).
      - `soberanos`: amortización + cupón en USD.

    Flujos CER sin CER de liquidación disponible (futuros, o más viejos
    que la ventana de `Trading.DiasHabiles`) se omiten — no entran a
    ningún rango comparable de la vista.
    """
    from engines.curvas import (
        cargar_cer,
        cargar_dias_habiles,
        fecha_flujo,
        get_cer_liquidacion,
        monto_flujo,
        monto_flujo_cer,
        monto_flujo_soberano,
    )

    docs = curvas_sql.por_curva(curva)

    cer_dict: dict = {}
    dias_habiles: list = []
    if curva == "cer":
        cer_dict = cargar_cer(dias=1200)        # SQL-only (macro.series_macro)
        dias_habiles = cargar_dias_habiles()    # SQL-only (mercado.dias_habiles)

    out: dict[str, list[dict]] = {}
    for d in docs:
        tk = d.get("ticker_corto") or d.get("ticker")
        if not tk:
            continue
        cer_emision = d.get("cer_emision")
        cal: list[dict] = []
        for f in d.get("flujos") or []:
            fd = fecha_flujo(f)
            if not fd:
                continue
            if curva == "tasa_fija":
                monto = monto_flujo(f)
            elif curva == "soberanos":
                monto = monto_flujo_soberano(f, 100)
            else:  # cer
                if not cer_emision:
                    continue
                cer_liq = get_cer_liquidacion(cer_dict, dias_habiles, fd.isoformat())
                if not cer_liq:
                    continue
                monto = monto_flujo_cer(f, 100) * cer_liq / float(cer_emision)
            if monto and monto > 0:
                cal.append({"fecha": fd.isoformat(), "monto": round(monto, 6)})
        if cal:
            cal.sort(key=lambda x: x["fecha"])
            out[tk] = cal
    return out
