"""core/dolar_sql.py — lecturas SQL del feed MEP/CCL/canje.

El feed vive en `valuaciones.{dolar_snapshot, dolar}` (Postgres), sin fallback.
Capa `core/` → usable por engines (curvas/futuros_dlr) y por api/services
(macro/argy/scanner).

- `dolar_snapshot`: 1 fila viva id='current' (motor engines/dolares.py, cada 5s).
- `dolar`: histórico append cada 15min (engines/dolar_mep.py), PK timestamp.

El día se agrupa en horario ART (AT TIME ZONE) para que el chart diario quede
sobre el timestamp ART.
"""
from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Sequence
from datetime import datetime

from core.postgres import get_pool

_ART = "America/Argentina/Buenos_Aires"
_CAMPOS = {"mep", "ccl", "canje"}


def _f(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def snapshot_live() -> dict | None:
    """Fila viva del snapshot MEP/CCL (id='current') o None.
    Devuelve {timestamp, mep, ccl, canje, source}."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT ts, mep, ccl, canje, source FROM dolar_snapshot WHERE id = 'current'")
            r = cur.fetchone()
    except Exception:
        return None
    if not r:
        return None
    return {"timestamp": r[0], "mep": _f(r[1]), "ccl": _f(r[2]),
            "canje": _f(r[3]), "source": r[4]}


def ultimo(campo: str = "mep", antes_de: datetime | None = None) -> dict | None:
    """Último registro histórico (valuaciones.dolar) con `campo` > 0.
    `antes_de`: si se pasa, exige timestamp < antes_de (cierre día previo).
    Devuelve {timestamp, mep, ccl, canje} o None."""
    if campo not in _CAMPOS:
        raise ValueError(campo)
    sql = (f"SELECT timestamp, mep, ccl, canje FROM dolar "
           f"WHERE {campo} IS NOT NULL AND {campo} > 0")
    args: list = []
    if antes_de is not None:
        sql += " AND timestamp < %s"
        args.append(antes_de)
    sql += " ORDER BY timestamp DESC LIMIT 1"
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(sql, args)
            r = cur.fetchone()
    except Exception:
        return None
    if not r:
        return None
    return {"timestamp": r[0], "mep": _f(r[1]), "ccl": _f(r[2]), "canje": _f(r[3])}


def serie(campo: str, desde: datetime, hasta: datetime) -> list[dict]:
    """Serie cruda (timestamp, valor) de valuaciones.dolar en [desde, hasta]."""
    if campo not in _CAMPOS:
        raise ValueError(campo)
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT timestamp, {campo} FROM dolar "
                f"WHERE timestamp >= %s AND timestamp <= %s AND {campo} IS NOT NULL "
                f"ORDER BY timestamp",
                (desde, hasta))
            rows = cur.fetchall()
    except Exception:
        return []
    return [{"timestamp": r[0], campo: _f(r[1])} for r in rows]


def serie_diaria(desde: datetime, hasta: datetime) -> list[dict]:
    """Último tick por día (ART) de mep + ccl, para el chart de la watchlist.
    Devuelve [{ts: 'YYYY-MM-DD', mep, ccl}] ordenado por día."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT d, mep, ccl FROM ("
                "  SELECT to_char(timestamp AT TIME ZONE %s, 'YYYY-MM-DD') AS d,"
                "         mep, ccl,"
                "         row_number() OVER ("
                "           PARTITION BY to_char(timestamp AT TIME ZONE %s, 'YYYY-MM-DD')"
                "           ORDER BY timestamp DESC) AS rn"
                "  FROM dolar WHERE timestamp >= %s AND timestamp <= %s"
                ") t WHERE rn = 1 ORDER BY d",
                (_ART, _ART, desde, hasta))
            rows = cur.fetchall()
    except Exception:
        return []
    return [{"ts": r[0], "mep": _f(r[1]), "ccl": _f(r[2])} for r in rows]


def por_dia(campo: str, desde: datetime | None = None,
            hasta: datetime | None = None) -> dict[str, float]:
    """{ 'YYYY-MM-DD' (ART): último valor del día } de `campo` en [desde, hasta].
    Drop-in del aggregate Mongo `$last por día`."""
    if campo not in _CAMPOS:
        raise ValueError(campo)
    sql = [
        "SELECT d, v FROM ("
        f"  SELECT to_char(timestamp AT TIME ZONE %s, 'YYYY-MM-DD') AS d, {campo} AS v,"
        "         row_number() OVER ("
        "           PARTITION BY to_char(timestamp AT TIME ZONE %s, 'YYYY-MM-DD')"
        "           ORDER BY timestamp DESC) AS rn"
        f"  FROM dolar WHERE {campo} IS NOT NULL"
    ]
    params: list = [_ART, _ART]
    if desde is not None:
        sql.append(" AND timestamp >= %s")
        params.append(desde)
    if hasta is not None:
        sql.append(" AND timestamp <= %s")
        params.append(hasta)
    sql.append(") t WHERE rn = 1 ORDER BY d")
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("".join(sql), tuple(params))
            rows = cur.fetchall()
    except Exception:
        return {}
    return {r[0]: _f(r[1]) for r in rows if r[1] is not None}


def por_dia_multi(campos: Sequence[str], desde: datetime | None = None,
                  hasta: datetime | None = None) -> dict[str, dict[str, float]]:
    """{ 'YYYY-MM-DD' (ART): {campo: último valor del día} } para VARIOS campos en
    UNA sola pasada de la tabla (equivale a llamar `por_dia` una vez por campo).

    El "último del día" se resuelve POR CAMPO, no por fila: si el último tick del
    día trae `ccl` nulo, el CCL del día es el del tick anterior con CCL — que es
    exactamente lo que hace `por_dia` con su `WHERE {campo} IS NOT NULL`."""
    cols = list(campos)
    for c in cols:
        if c not in _CAMPOS:
            raise ValueError(c)
    sel = ", ".join(
        f"(array_agg({c} ORDER BY timestamp DESC) FILTER (WHERE {c} IS NOT NULL))[1] AS {c}"
        for c in cols)
    sql = [f"SELECT to_char(timestamp AT TIME ZONE %s, 'YYYY-MM-DD') AS d, {sel} FROM dolar"]
    params: list = [_ART]
    cond: list[str] = []
    if desde is not None:
        cond.append("timestamp >= %s")
        params.append(desde)
    if hasta is not None:
        cond.append("timestamp <= %s")
        params.append(hasta)
    if cond:
        sql.append(" WHERE " + " AND ".join(cond))
    sql.append(" GROUP BY 1 ORDER BY 1")
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("".join(sql), tuple(params))
            rows = cur.fetchall()
    except Exception:
        return {}
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        vals = {c: _f(v) for c, v in zip(cols, r[1:], strict=True) if v is not None}
        if vals:
            out[r[0]] = vals
    return out


def mep_para_fecha(fecha_iso: str) -> float | None:
    """Último MEP con timestamp <= fin-de-día(fecha) ART. None si no hay anterior.
    Drop-in de api/services/_mep.get_mep_for_date sobre valuaciones.dolar."""
    from zoneinfo import ZoneInfo
    try:
        target = datetime.fromisoformat(fecha_iso + "T23:59:59").replace(tzinfo=ZoneInfo(_ART))
    except ValueError:
        return None
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT mep FROM dolar WHERE mep IS NOT NULL AND mep > 0 "
                "AND timestamp <= %s ORDER BY timestamp DESC LIMIT 1",
                (target,))
            r = cur.fetchone()
    except Exception:
        return None
    return _f(r[0]) if r else None


def es_fecha_dia(fecha_iso: str) -> bool:
    """True si `fecha_iso` es exactamente 'YYYY-MM-DD'. Los helpers de serie
    comparan fechas como STRING (lexicográfico == cronológico) — cualquier otro
    formato (ISO con hora, formato básico sin guiones) rompe esa equivalencia y
    debe resolverse con `mep_para_fecha`, no contra la serie."""
    if not isinstance(fecha_iso, str) or len(fecha_iso) != 10:
        return False
    try:
        datetime.strptime(fecha_iso, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def mep_serie_dias() -> list[tuple[str, float]]:
    """[('YYYY-MM-DD' ART, último MEP > 0 de ese día)] ascendente — UNA query.

    Base para resolver `mep_para_fecha` de MUCHAS fechas sin ir 1 vez por fecha:
    el último MEP <= fin-de-día(F) es el último MEP del último día CON DATO <= F
    (arrastre). `mep_en_serie` hace esa búsqueda.

    OJO: filtra `mep > 0` igual que `mep_para_fecha` — `por_dia('mep')` NO lo hace
    (solo IS NOT NULL), así que NO es intercambiable con esto."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT d, v FROM ("
                "  SELECT to_char(timestamp AT TIME ZONE %s, 'YYYY-MM-DD') AS d, mep AS v,"
                "         row_number() OVER ("
                "           PARTITION BY to_char(timestamp AT TIME ZONE %s, 'YYYY-MM-DD')"
                "           ORDER BY timestamp DESC) AS rn"
                "  FROM dolar WHERE mep IS NOT NULL AND mep > 0"
                ") t WHERE rn = 1 ORDER BY d",
                (_ART, _ART))
            rows = cur.fetchall()
    except Exception:
        return []
    out: list[tuple[str, float]] = []
    for d, v in rows:
        f = _f(v)
        if f is not None:
            out.append((d, f))
    return out


def mep_en_serie(serie: list[tuple[str, float]], dias: list[str],
                 fecha_iso: str) -> float | None:
    """MEP de `fecha_iso` contra una serie de `mep_serie_dias` — MISMA semántica que
    `mep_para_fecha` (arrastra el último día con dato). `dias` son las claves de
    `serie` (se pasan aparte para no recomputarlas en cada lookup)."""
    if not es_fecha_dia(fecha_iso):
        return mep_para_fecha(fecha_iso)
    i = bisect_right(dias, fecha_iso) - 1
    return serie[i][1] if i >= 0 else None


def mep_por_fecha(fechas: Iterable[str]) -> dict[str, float | None]:
    """{fecha_iso: MEP} para un lote de fechas, en UNA query en vez de una por fecha.
    Mismo valor que `mep_para_fecha` para cada una (incluido el arrastre del último
    día con dato)."""
    pedidas = list(dict.fromkeys(fechas))
    if not pedidas:
        return {}
    serie = mep_serie_dias()
    dias = [d for d, _ in serie]
    return {f: mep_en_serie(serie, dias, f) for f in pedidas}


def ultimo_uva() -> float | None:
    """Último valor UVA (macro.uva, carga manual) por fecha desc."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT valor FROM uva ORDER BY fecha DESC LIMIT 1")
            r = cur.fetchone()
    except Exception:
        return None
    return _f(r[0]) if r else None
