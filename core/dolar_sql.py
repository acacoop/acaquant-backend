"""core/dolar_sql.py — lecturas SQL del feed MEP/CCL/canje (decomiso Mongo).

Reemplaza `Valuaciones.{DolarSnapshot, Dolar}` (Mongo) por
`valuaciones.{dolar_snapshot, dolar}` (Postgres). SIN fallback a Mongo.
Capa `core/` → usable por engines (curvas/futuros_dlr) y por api/services
(macro/argy/scanner).

- `dolar_snapshot`: 1 fila viva id='current' (motor engines/dolares.py, cada 5s).
- `dolar`: histórico append cada 15min (engines/dolar_mep.py), PK timestamp.

El día se agrupa en horario ART (AT TIME ZONE) para que el chart diario quede
igual que el `$dateToString` de Mongo sobre el timestamp ART.
"""
from __future__ import annotations

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


def ultimo_uva() -> float | None:
    """Último valor UVA (macro.uva, carga manual) por fecha desc."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT valor FROM uva ORDER BY fecha DESC LIMIT 1")
            r = cur.fetchone()
    except Exception:
        return None
    return _f(r[0]) if r else None
