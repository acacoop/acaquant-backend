"""api/services/aum_sql.py — lectura de AuM desde SQL `portafolio.tenencia`.

Migración Mongo→SQL de la serie de AuM (vista NEGOCIO / endpoint flujo-vs-aum).
Lee SQL `portafolio.tenencia` (fechas corregidas por la regla H1) aplicando las
MISMAS exclusiones que `Valuaciones.AuM` (`jobs._aum_filters.is_excluded`) para
que el total sea fiel y no se cuele CDC/OTC/cash/contrapartes.

Motor seleccionable en el endpoint: default SQL; `AUM_SQL=0` (env) o
`?_engine=mongo` vuelve a Mongo. El path Mongo queda intacto → reversión = sacar
la env + restart. Cobertura: las fechas que tenga el backfill en
`portafolio.tenencia` (período fiscal en adelante).
"""
from __future__ import annotations

import threading

from core.postgres import get_pool
from jobs._aum_filters import (
    is_excluded,
    load_contrapartes_id_cuentas,
    load_contrapartes_names,
)

# Listas de contrapartes (reglas 3/4) cacheadas en proceso — cambian con poca
# frecuencia (cuando el equipo edita Contrapartes). Mismo criterio que jobs/aum.py.
_lock = threading.Lock()
_ids: frozenset[str] | None = None
_names: frozenset[str] | None = None


def _contrapartes() -> tuple[frozenset[str], frozenset[str]]:
    global _ids, _names
    if _ids is None or _names is None:
        with _lock:
            if _ids is None:
                _ids = load_contrapartes_id_cuentas()
            if _names is None:
                _names = load_contrapartes_names()
    return _ids, _names  # type: ignore[return-value]


def _f(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _mensual_ultimo(por_fecha: dict[str, float]) -> dict[str, float]:
    """{fecha_iso: total} → {mes 'YYYY-MM': total del ÚLTIMO día presente del mes}.
    Mismo criterio que el `$group` con `$last` del pipeline Mongo del endpoint."""
    ult: dict[str, str] = {}
    for fecha in por_fecha:
        mes = fecha[:7]
        if mes not in ult or fecha > ult[mes]:
            ult[mes] = fecha
    return {mes: por_fecha[fecha] for mes, fecha in ult.items()}


def serie_aum_mensual_sql(unidades: list[str]) -> list[dict]:
    """[{mes:'YYYY-MM', total: float}] de AuM para esas unidades, desde SQL.

    Grano idéntico al de Mongo: por mes, el total del último día hábil presente.
    Aplica `is_excluded` (CDC/OTC/cash/contrapartes) sobre cada fila.
    """
    if not unidades:
        return []
    cont_ids, cont_names = _contrapartes()
    por_fecha: dict[str, float] = {}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT fecha::text, id_cuenta, cuenta, unidad, valuacion "
            "FROM portafolio.tenencia "
            "WHERE unidad = ANY(%s) AND valuacion IS NOT NULL",
            (unidades,))
        for fecha, idc, cuenta, unidad, val in cur.fetchall():
            if is_excluded(cuenta, unidad, id_cuenta=str(idc),
                           contrapartes_ids=cont_ids, contrapartes_names=cont_names):
                continue
            por_fecha[fecha] = por_fecha.get(fecha, 0.0) + _f(val)
    mensual = _mensual_ultimo(por_fecha)
    return [{"mes": mes, "total": total} for mes, total in sorted(mensual.items())]
