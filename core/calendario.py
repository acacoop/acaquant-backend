"""Calendario hábil argentino — fuente ÚNICA del repo.

Dos familias de helpers:

1. `dias_habiles_ordenados()` — lee SQL `mercado.dias_habiles` (poblada por
   jobs.dias_habiles). Es el calendario "oficial" de la mesa.
2. Helpers PUROS sobre `holidays.Argentina` (sin tocar la DB): es_habil /
   proximo_habil / habiles_entre / ultimos_habiles / ultimo_habil_del_mes.
   Antes cada job traía su copia — y DOS de las copias (negocio_movimientos,
   market_anchors) solo miraban weekday<5 e ignoraban FERIADOS: la ventana
   de recuperación T+1 se achicaba y el ancla WTD/MTD podía caer en feriado
   (la misma clase de bug 2026-07-20 que ya se arregló una vez a mano).

Único lector de días hábiles del repo (engines + api + jobs lo usan).
"""
from __future__ import annotations

from datetime import date, timedelta

import holidays

from core.postgres import get_pool

_FERIADOS_AR = holidays.Argentina()


def dias_habiles_ordenados(_mongo_client=None) -> list[str]:
    """Lista ASC de días hábiles 'YYYY-MM-DD' desde mercado.dias_habiles (SQL-only).
    `_mongo_client` se ignora — está solo por compat de firma con los call-sites
    de engines que antes pasaban el cliente Mongo."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT to_char(fecha, 'YYYY-MM-DD') FROM mercado.dias_habiles ORDER BY fecha"
        )
        return [r[0] for r in cur.fetchall()]


# ── Helpers puros (holidays.Argentina, sin DB) ───────────────────────────────


def es_habil(d: date) -> bool:
    """L-V y no feriado nacional argentino."""
    return d.weekday() < 5 and d not in _FERIADOS_AR


def proximo_habil(d: date) -> date:
    """Primer día hábil ESTRICTAMENTE posterior a `d`."""
    d = d + timedelta(days=1)
    while not es_habil(d):
        d = d + timedelta(days=1)
    return d


def restar_habiles(d: date, n: int) -> date:
    """`d` menos `n` días hábiles. El inverso de `proximo_habil`, en bloque.

    **Puro**: no depende de que `mercado.dias_habiles` cubra esa fecha. Esa
    diferencia importó el 2026-08-17: `get_cer_liquidacion` resuelve el T−10
    indexando la tabla y devuelve `None` cuando la tabla no llega tan atrás —
    y el que llama lee ese `None` como «no hay CER», que es una conclusión que
    la función nunca afirmó. Con esto se puede resolver la FECHA aunque el
    calendario oficial no la cubra, y recién después buscar el dato.
    """
    if n <= 0:
        return d
    quedan = n
    while quedan > 0:
        d = d - timedelta(days=1)
        if es_habil(d):
            quedan -= 1
    return d


def habiles_entre(desde: date, hasta: date) -> list[date]:
    """Días hábiles en [desde, hasta] inclusive, asc."""
    out, d = [], desde
    while d <= hasta:
        if es_habil(d):
            out.append(d)
        d = d + timedelta(days=1)
    return out


def ultimos_habiles(hoy: date, n_atras: int) -> list[date]:
    """`hoy` + los `n_atras` días hábiles previos (asc). Cuenta por días
    hábiles (no calendario) → la ventana no se come findes NI feriados."""
    dias: list[date] = []
    d = hoy
    while len(dias) < n_atras + 1:
        if es_habil(d) or d == hoy:  # hoy entra siempre (semántica del lookback)
            dias.append(d)
        d -= timedelta(days=1)
    return sorted(dias)


def ultimo_habil_del_mes(anio: int, mes: int) -> date:
    """Último día hábil del mes (camina hacia atrás desde fin de mes)."""
    from calendar import monthrange
    d = date(anio, mes, monthrange(anio, mes)[1])
    while not es_habil(d):
        d = d - timedelta(days=1)
    return d
