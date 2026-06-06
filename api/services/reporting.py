"""api/services/reporting.py — reportería que lee de la capa SQL (Postgres/Supabase).

Servicio PURO (sin FastAPI). Lee del espejo relacional de SOLO LECTURA (Fase C de
docs/SQL.md). El valor: lo que en Mongo exige un join app-side entre DBs distintas
(Clientes.Comitentes + Valuaciones.AuM están en bases SEPARADAS → no se pueden joinear
server-side), en Postgres es una sola query.

Reusable por un futuro router HTTP: las funciones aceptan `conn` opcional para que el
endpoint le pase una conexión del pool (Fase C.2); si no, abren una propia (scripts).
"""
from __future__ import annotations

from core.postgres import connect


def _rows(conn, sql: str, params=None) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


# Ranking de operadores por AuM del último snapshot. El JOIN cruza 3 tablas que en
# Mongo viven en 2 DBs distintas (operadores/comitentes ← Clientes, aum ← Valuaciones).
_SQL_RANKING_OPERADORES = """
with ultima as (select max(fecha_snapshot) as f from aum),
aum_cta as (
    select a.id_cuenta, sum(a.valuacion) as aum
    from aum a, ultima u
    where a.fecha_snapshot = u.f
    group by a.id_cuenta
)
select
    o.email                      as operador_email,
    o.nombre                     as operador,
    count(distinct c.id_cuenta)  as n_cuentas,
    coalesce(sum(ac.aum), 0)     as aum_total
from operadores o
left join comitentes c on c.operador_email = o.email
left join aum_cta    ac on ac.id_cuenta = c.id_cuenta
group by o.email, o.nombre
order by aum_total desc nulls last
"""

_SQL_TOTAL_ULTIMO_SNAPSHOT = """
select max(fecha_snapshot) as fecha,
       coalesce(sum(valuacion) filter (
           where fecha_snapshot = (select max(fecha_snapshot) from aum)), 0) as total
from aum
"""


def reporte_operadores(conn=None) -> dict:
    """{fecha, aum_total, ranking[]}.

    `aum_total` es el AuM del último snapshot completo. La suma del ranking puede ser
    MENOR que `aum_total`: la diferencia es AuM de cuentas sin operador asignado (o sin
    comitente) — un hallazgo de cobertura comercial, no un error.
    """
    own = conn is None
    conn = conn or connect()
    try:
        meta = _rows(conn, _SQL_TOTAL_ULTIMO_SNAPSHOT)[0]
        ranking = _rows(conn, _SQL_RANKING_OPERADORES)
        return {
            "fecha": meta["fecha"],
            "aum_total": float(meta["total"] or 0),
            "ranking": ranking,
        }
    finally:
        if own:
            conn.close()
