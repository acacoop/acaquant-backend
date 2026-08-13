"""Series macro (CER, DOLAR, BADLAR, TAMAR, RiesgoPais, Inflación…) — SQL-ONLY.

Fuente ÚNICA: `macro.series_macro` (tabla larga serie/fecha/valor), sin fallback.

Lo usan los motores (curvas, breakevens, futuros_dlr). `fecha` es columna date;
los params string 'YYYY-MM-DD' se castean con ::date.
"""
from __future__ import annotations

from core.postgres import get_pool


def serie_dict(serie: str, desde: str | None = None, hasta: str | None = None,
               positivo: bool = False) -> dict[str, float]:
    """{fecha 'YYYY-MM-DD': valor} de una serie. `desde`/`hasta` ISO opcionales
    (incl.). positivo=True exige valor>0 (si no, solo valor IS NOT NULL)."""
    sql = ["SELECT to_char(fecha, 'YYYY-MM-DD') AS f, valor FROM macro.series_macro WHERE serie = %s"]
    sql.append(" AND valor > 0" if positivo else " AND valor IS NOT NULL")
    params: list = [serie]
    if desde:
        sql.append(" AND fecha >= %s::date")
        params.append(desde)
    if hasta:
        sql.append(" AND fecha <= %s::date")
        params.append(hasta)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("".join(sql), tuple(params))
        return {r[0]: float(r[1]) for r in cur.fetchall()}


def punto_asof(serie: str, fecha_iso: str, positivo: bool = False) -> dict | None:
    """Último punto {fecha 'YYYY-MM-DD', valor} con fecha <= fecha_iso, o None.
    Drop-in de un find_one(sort fecha desc) sobre la colección Mongo."""
    cond = " AND valor > 0" if positivo else " AND valor IS NOT NULL"
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT to_char(fecha, 'YYYY-MM-DD') AS fecha, valor FROM macro.series_macro "
            f"WHERE serie = %s AND fecha <= %s::date{cond} ORDER BY fecha DESC LIMIT 1",
            (serie, fecha_iso),
        )
        row = cur.fetchone()
    return {"fecha": row[0], "valor": float(row[1])} if row else None


def valor_en_fecha(serie: str, fecha_iso: str) -> float | None:
    """Valor de la serie en una fecha exacta, o None."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT valor FROM macro.series_macro WHERE serie = %s AND fecha = %s::date",
            (serie, fecha_iso),
        )
        row = cur.fetchone()
    return float(row[0]) if row and row[0] is not None else None


def ultima_fecha(serie: str) -> str | None:
    """Última fecha con dato 'YYYY-MM-DD', o None."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT to_char(max(fecha), 'YYYY-MM-DD') FROM macro.series_macro "
            "WHERE serie = %s AND valor IS NOT NULL",
            (serie,),
        )
        row = cur.fetchone()
    return row[0] if row else None


def ultimo_valor(serie: str, positivo: bool = False) -> float | None:
    """Valor más reciente de la serie (None si no hay). positivo=True exige valor>0."""
    cond = " AND valor > 0" if positivo else " AND valor IS NOT NULL"
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT valor FROM macro.series_macro WHERE serie = %s{cond} "
            "ORDER BY fecha DESC LIMIT 1",
            (serie,),
        )
        row = cur.fetchone()
    return float(row[0]) if row and row[0] is not None else None
