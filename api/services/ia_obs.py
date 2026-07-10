"""api/services/ia_obs.py — observabilidad del gateway de IA (SQL ia.trazas).

Payload único para la tab OBSERVABILIDAD → IA (un solo roundtrip del front):
resumen de HOY con % del presupuesto global, serie por día, agregado por tarea
y últimas llamadas. SOLO LECTURA — el writer de ia.trazas es core/ai.py.
"""
from __future__ import annotations

from psycopg.rows import dict_row

from core.ai import presupuesto_dia_global
from core.postgres import get_pool


def observabilidad(dias: int = 14, limit: int = 30) -> dict:
    dias = max(1, min(int(dias), 90))
    limit = max(1, min(int(limit), 200))
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT count(*)                                    AS llamadas,
                   count(*) FILTER (WHERE NOT ok)              AS errores,
                   coalesce(sum(tokens_in), 0)::bigint         AS tokens_in,
                   coalesce(sum(tokens_out), 0)::bigint        AS tokens_out
            FROM ia.trazas
            WHERE ts >= date_trunc('day', now())
            """
        )
        hoy = cur.fetchone()

        cur.execute(
            """
            SELECT to_char(ts AT TIME ZONE 'UTC', 'YYYY-MM-DD')      AS fecha,
                   count(*)                                          AS llamadas,
                   count(*) FILTER (WHERE NOT ok)                    AS errores,
                   coalesce(sum(tokens_in), 0)::bigint               AS tokens_in,
                   coalesce(sum(tokens_out), 0)::bigint              AS tokens_out,
                   round(avg(latencia_ms))::int                      AS latencia_ms_avg
            FROM ia.trazas
            WHERE ts >= date_trunc('day', now()) - %s * interval '1 day'
            GROUP BY 1
            ORDER BY 1 DESC
            """,
            (dias,),
        )
        por_dia = cur.fetchall()

        cur.execute(
            """
            SELECT tarea,
                   count(*)                                              AS llamadas,
                   count(*) FILTER (WHERE NOT ok)                        AS errores,
                   coalesce(sum(coalesce(tokens_in, 0)
                              + coalesce(tokens_out, 0)), 0)::bigint     AS tokens,
                   round(avg(latencia_ms))::int                          AS latencia_ms_avg,
                   max(ts)                                               AS ultima
            FROM ia.trazas
            WHERE ts >= date_trunc('day', now()) - %s * interval '1 day'
            GROUP BY tarea
            ORDER BY tokens DESC
            """,
            (dias,),
        )
        por_tarea = cur.fetchall()

        cur.execute(
            """
            SELECT ts, tarea, modelo, usuario, tokens_in, tokens_out,
                   latencia_ms, ok, error
            FROM ia.trazas
            ORDER BY id DESC
            LIMIT %s
            """,
            (limit,),
        )
        ultimas = cur.fetchall()

    presupuesto = presupuesto_dia_global()
    tokens_hoy = int(hoy["tokens_in"]) + int(hoy["tokens_out"])
    return {
        "hoy": {
            **hoy,
            "tokens_total": tokens_hoy,
            "presupuesto_dia": presupuesto,
            "presupuesto_pct": round(100 * tokens_hoy / presupuesto, 1) if presupuesto else None,
        },
        "por_dia": por_dia,
        "por_tarea": por_tarea,
        "ultimas": ultimas,
        "ventana_dias": dias,
    }
