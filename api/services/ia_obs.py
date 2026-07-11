"""api/services/ia_obs.py — observabilidad del gateway de IA (SQL ia.trazas).

Payload único para la tab OBSERVABILIDAD → IA (un solo roundtrip del front):
resumen de HOY con % del presupuesto global, serie por día, agregado por tarea
y últimas llamadas. SOLO LECTURA — el writer de ia.trazas es core/ai.py.
Excepción: los PRESUPUESTOS (ia.config) se editan acá vía set_presupuestos
(solo admin, ver router), con precedencia tabla > env > default en core/ai.
"""
from __future__ import annotations

from psycopg.rows import dict_row

from core.ai import presupuesto_dia_global
from core.postgres import get_pool


def get_presupuestos() -> dict:
    """Límites vigentes del gateway (resueltos con su precedencia) + auditoría
    de la última edición si los setearon desde el panel."""
    from core import ai

    out = {
        "global_dia": ai.presupuesto_dia_global(),
        "usuario_dia": ai.presupuesto_dia_usuario(),
        "editado": None,
    }
    try:
        with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT clave, valor, updated_at, updated_by FROM ia.config "
                "WHERE clave IN ('budget_dia_global', 'budget_dia_usuario') "
                "ORDER BY updated_at DESC"
            )
            filas = cur.fetchall()
        if filas:
            out["editado"] = {
                "por": filas[0]["updated_by"],
                "cuando": filas[0]["updated_at"].isoformat(),
            }
    except Exception:
        pass  # tabla aún no aplicada → rigen env/default igual
    return out


def set_presupuestos(
    global_dia: int | None, usuario_dia: int | None, actor: str
) -> dict:
    """Edita los topes diarios (tokens). Reglas: enteros positivos; el tope por
    usuario no puede superar el global (el global es techo duro — la suma de
    usuarios puede excederlo en papel, pero ningún usuario individual puede
    tener permitido más que el sistema entero). Audita quién y cuándo."""
    from core import ai

    nuevo_global = int(global_dia) if global_dia is not None else ai.presupuesto_dia_global()
    nuevo_usuario = int(usuario_dia) if usuario_dia is not None else ai.presupuesto_dia_usuario()
    if nuevo_global <= 0 or nuevo_usuario <= 0:
        raise ValueError("los presupuestos deben ser enteros positivos")
    if nuevo_usuario > nuevo_global:
        raise ValueError(
            f"el tope por usuario ({nuevo_usuario:,}) no puede superar el global ({nuevo_global:,})"
        )

    cambios = []
    if global_dia is not None:
        cambios.append(("budget_dia_global", nuevo_global))
    if usuario_dia is not None:
        cambios.append(("budget_dia_usuario", nuevo_usuario))
    with get_pool().connection() as conn, conn.cursor() as cur:
        for clave, valor in cambios:
            cur.execute(
                "INSERT INTO ia.config (clave, valor, updated_by) VALUES (%s, %s, %s) "
                "ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor, "
                "updated_at = now(), updated_by = EXCLUDED.updated_by",
                (clave, valor, actor),
            )
    ai.invalidate_config_cache()  # el gateway los ve en la próxima llamada
    return get_presupuestos()


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
