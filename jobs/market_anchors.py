"""market_anchors.py — anchors diarios de retorno (7d, MTD, YTD, 1Y).

Corre 1×/día post-cierre US (22:00 UTC = 19:00 ART). Para cada símbolo fetchea
candle diario de ~13 meses, calcula el cierre más cercano a cada anchor
(7 días atrás, inicio de semana, primer día del mes, primer día del año,
365 días atrás) y lo guarda en el mismo doc de Market.Quotes. Las anclas se
calculan para el PRÓXIMO día hábil — que es la rueda que las va a consumir
(ver _anchor_timestamps).

Luego la API, al leer las quotes, computa los retornos on-the-fly:
    ret_7d = (last - anchor_7d) / anchor_7d * 100

El bloque de anchors de FX (frankfurter.app) se eliminó junto con las monedas del
watchlist: la mesa las sacó de la vista y la lista de símbolos quedó vacía.

Cron:
    0 22 * * 1-5 cd /root/TradingAV && venv/bin/python -m jobs.market_anchors
"""
from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime, timedelta

from core.pg_mirror import merge_jsonb_native
from core.yahoo import YahooError, stock_candle
from jobs.market_quotes import (
    HOME_FUTUROS,
    HOME_INDICES_YAHOO,
    HOME_STOCKS,
    HOME_TREASURIES,
)

logger = logging.getLogger(__name__)


def _closest_close(times: list[int], closes: list[float], target_ts: int) -> float | None:
    """Cierre más cercano (<=) al target_ts. O null si no hay data previa."""
    last = None
    for t, c in zip(times, closes):
        if t <= target_ts:
            last = c
        else:
            break
    return last


def _proximo_habil(now: datetime) -> datetime:
    """Próximo día hábil a las 00:00 UTC. Vía core.calendario → cuenta FERIADOS
    AR (la copia local anterior solo salteaba findes: el ancla WTD/MTD podía
    caer en un feriado y correr las métricas un día — bug clase 2026-07-20)."""
    from core.calendario import proximo_habil
    d = proximo_habil(now.date())
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def _anchor_timestamps(now: datetime) -> dict[str, int]:
    # El job corre POST-CIERRE (22 UTC) y sus anclas se consumen recién la rueda
    # SIGUIENTE → se calculan para el próximo día hábil, no para el día que corre.
    # Sin esto, la corrida del viernes anclaba el WTD al viernes ANTERIOR y todo
    # el lunes el briefing/watchlist mostraba WTD ≠ 1D (bug 2026-07-20). Ídem MTD
    # e YTD el primer día hábil de mes/año.
    # WTD = cierre de la última rueda ANTES del lunes de la semana del target
    # (típicamente el viernes). Distinto de anchor_7d (rolling 7 días), que se
    # mantiene para /argy. _closest_close toma el cierre <= al target.
    target = _proximo_habil(now)
    inicio_semana = target - timedelta(days=target.weekday())
    return {
        "anchor_7d":  int((target - timedelta(days=7)).timestamp()),
        "anchor_wtd": int((inicio_semana - timedelta(seconds=1)).timestamp()),
        "anchor_mtd": int(datetime(target.year, target.month, 1, tzinfo=UTC).timestamp()),
        "anchor_ytd": int(datetime(target.year, 1, 1, tzinfo=UTC).timestamp()),
        "anchor_1y":  int((target - timedelta(days=365)).timestamp()),
    }


def update_stock_anchors(sym: str, now: datetime) -> bool:
    hasta = int(now.timestamp())
    desde = hasta - 400 * 86400  # ~13 meses de colchón
    try:
        c = stock_candle(sym, "D", desde, hasta)
    except YahooError as e:
        logger.warning("candle %s failed: %s", sym, e)
        return False
    if c.get("s") != "ok":
        logger.warning("candle %s status=%s", sym, c.get("s"))
        return False

    times  = c.get("t")  or []
    closes = c.get("c")  or []
    if not times or not closes:
        return False

    ts_map = _anchor_timestamps(now)
    update = {k: _closest_close(times, closes, ts) for k, ts in ts_map.items()}
    update["anchors_updated_at"] = now

    # MERGE atómico (`data = data || patch`): solo toca los anchors, preserva el
    # precio/intraday que escribe jobs.market_quotes en el MISMO doc jsonb.
    merge_jsonb_native("market_quotes", ["symbol"], [sym], update)
    return True


def ingesta() -> int:
    now = datetime.now(UTC)

    ok_s = fail_s = 0
    for sym, _grupo in HOME_STOCKS:
        if update_stock_anchors(sym, now):
            ok_s += 1
        else:
            fail_s += 1

    # Treasury yields: el yahoo_sym es lo que fetchamos, pero el doc se
    # guarda bajo el display (UST 10Y, etc). Necesitamos update_stock_anchors
    # usando yahoo_sym y luego guardar bajo display.
    ok_t = fail_t = 0
    for yahoo_sym, display in HOME_TREASURIES:
        if _update_treasury_anchors(yahoo_sym, display, now):
            ok_t += 1
        else:
            fail_t += 1

    # Índices locales (MERVAL, etc) — mismo patrón display ≠ yahoo_sym.
    ok_i = fail_i = 0
    for yahoo_sym, display, _grupo in HOME_INDICES_YAHOO:
        if _update_treasury_anchors(yahoo_sym, display, now):
            ok_i += 1
        else:
            fail_i += 1

    # Futuros (S&P/Nasdaq/WTI/Brent/Oro/Soja/Maíz/Trigo/BTC/ETH) — mismo patrón
    # display ≠ yahoo_sym (se fetchea por ES=F, se guarda por 'S&P FUT'). Sin esto
    # los futuros del watchlist/briefing quedan sin retorno semana/mes.
    ok_f = fail_f = 0
    for yahoo_sym, display, _exchange in HOME_FUTUROS:
        if _update_treasury_anchors(yahoo_sym, display, now):
            ok_f += 1
        else:
            fail_f += 1

    logger.info(
        "market_anchors — stocks ok=%d fail=%d · treasuries ok=%d fail=%d · "
        "indices ok=%d fail=%d · futuros ok=%d fail=%d",
        ok_s, fail_s, ok_t, fail_t, ok_i, fail_i, ok_f, fail_f,
    )
    return 0


def _update_treasury_anchors(yahoo_sym: str, display: str, now: datetime) -> bool:
    """Variante de update_stock_anchors que guarda bajo display pero fetchea
    con el yahoo_sym (^IRX, ^TNX, etc)."""
    hasta = int(now.timestamp())
    desde = hasta - 400 * 86400
    try:
        c = stock_candle(yahoo_sym, "D", desde, hasta)
    except YahooError as e:
        logger.warning("treasury candle %s failed: %s", yahoo_sym, e)
        return False
    if c.get("s") != "ok":
        return False
    times  = c.get("t")  or []
    closes = c.get("c")  or []
    if not times or not closes:
        return False
    ts_map = _anchor_timestamps(now)
    update = {k: _closest_close(times, closes, ts) for k, ts in ts_map.items()}
    update["anchors_updated_at"] = now
    merge_jsonb_native("market_quotes", ["symbol"], [display], update)
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(ingesta())
