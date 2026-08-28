"""api/services/ia_obs.py — observabilidad del gateway de IA (SQL ia.trazas).

SOLO LECTURA — el writer de ia.trazas es core/ai.py.

⚠️ **Su único lector es el AV AGENT**, no una pantalla: el chequeo `ia:gateway`
de `api/services/salud.py` llama a `observabilidad(dias=1, limit=1)` y mira el
gasto y los errores del día. La tab OBSERVABILIDAD → IA que lo estrenó se dio de
baja el 2026-08-19 (*«el historial de 874 llamadas no se abrió nunca»*) y sus
endpoints el 2026-08-28.

Por eso este módulo quedó SOBREDIMENSIONADO para lo que hace: los filtros
(`tarea`/`usuario`/`solo_error`/`q`), la paginación y el historial existían para
esa tab y hoy nadie los pasa. Achicarlo a lo que el chequeo necesita es un
cambio de comportamiento, no un borrado, y va aparte.
"""
from __future__ import annotations

from psycopg.rows import dict_row

from core.ai import presupuesto_dia_global
from core.postgres import get_pool


def observabilidad(dias: int = 14, limit: int = 60, offset: int = 0,
                   tarea: str | None = None, usuario: str | None = None,
                   solo_error: bool = False, q: str | None = None) -> dict:
    """Payload del panel. `limit`/`offset` paginan las llamadas (historial);
    `tarea`/`usuario`/`solo_error`/`q` las filtran server-side (la tabla es
    grande y el front no debería traérsela entera para filtrar)."""
    dias = max(1, min(int(dias), 90))
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))

    # WHERE de las llamadas (los agregados NO se filtran: son el panorama)
    cond, params = ["TRUE"], {}
    if tarea:
        cond.append("tarea = %(tarea)s")
        params["tarea"] = tarea
    if usuario:
        cond.append("usuario ILIKE %(usuario)s")
        params["usuario"] = f"%{usuario}%"
    if solo_error:
        cond.append("NOT ok")
    if q:
        cond.append("(detalle ILIKE %(q)s OR respuesta ILIKE %(q)s OR error ILIKE %(q)s)")
        params["q"] = f"%{q}%"
    where_llamadas = " AND ".join(cond)

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

        # Agregado por MODELO (hoy) → se pliega a proveedor en Python: los IDs
        # de modelo los conoce core/llm.py, no este service (invariante).
        cur.execute(
            """
            SELECT modelo,
                   count(*)                                              AS llamadas,
                   count(*) FILTER (WHERE NOT ok)                        AS errores,
                   coalesce(sum(coalesce(tokens_in, 0)
                              + coalesce(tokens_out, 0)), 0)::bigint     AS tokens,
                   round(avg(latencia_ms))::int                          AS latencia_ms_avg,
                   -- para estimar el GASTO hace falta el detalle in/out/caché
                   coalesce(sum(tokens_in), 0)::bigint                   AS tokens_in,
                   coalesce(sum(tokens_out), 0)::bigint                  AS tokens_out,
                   coalesce(sum(cache_hit_tokens), 0)::bigint            AS cache_hit,
                   coalesce(sum(tokens_in) FILTER (
                       WHERE ts >= date_trunc('day', now())), 0)::bigint  AS tokens_in_hoy,
                   coalesce(sum(tokens_out) FILTER (
                       WHERE ts >= date_trunc('day', now())), 0)::bigint  AS tokens_out_hoy,
                   coalesce(sum(cache_hit_tokens) FILTER (
                       WHERE ts >= date_trunc('day', now())), 0)::bigint  AS cache_hit_hoy
            FROM ia.trazas
            WHERE ts >= date_trunc('day', now()) - %s * interval '1 day'
            GROUP BY modelo
            """,
            (dias,),
        )
        por_modelo = cur.fetchall()

        cur.execute(
            f"SELECT count(*) AS n FROM ia.trazas WHERE {where_llamadas}", params)
        total_llamadas = int(cur.fetchone()["n"])

        cur.execute(
            f"""
            SELECT id, ts, tarea, modelo, usuario, tokens_in, tokens_out,
                   latencia_ms, ok, error, feedback, detalle, respuesta, razonamiento
            FROM ia.trazas
            WHERE {where_llamadas}
            ORDER BY id DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            {**params, "limit": limit, "offset": offset},
        )
        ultimas = cur.fetchall()

        cur.execute(
            "SELECT DISTINCT tarea FROM ia.trazas ORDER BY tarea")
        tareas = [r["tarea"] for r in cur.fetchall()]

    por_proveedor = _plegar_por_proveedor(por_modelo)
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
        "por_proveedor": por_proveedor,
        "ultimas": ultimas,
        "total_llamadas": total_llamadas,
        "offset": offset,
        "limit": limit,
        "tareas": tareas,
        "ventana_dias": dias,
    }


def _plegar_por_proveedor(por_modelo: list[dict]) -> list[dict]:
    """Agrupa el uso por PROVEEDOR (deepseek/openai) a partir de los IDs de
    modelo, y estima el GASTO en USD desde los tokens (la tabla de precios
    vive en core/llm.py). OpenAI no expone saldo por API — ni con admin key —
    así que el gasto calculado es la única forma de seguirlo desde el panel."""
    from core import llm

    acc: dict[str, dict] = {}
    lat_acc: dict[str, list[tuple[int, int]]] = {}
    for r in por_modelo:
        prov = llm.proveedor_de_modelo(r["modelo"]) or "otro"
        e = acc.setdefault(prov, {
            "proveedor": prov, "modelos": [], "llamadas": 0, "errores": 0,
            "tokens": 0, "latencia_ms_avg": None,
            "costo_usd": 0.0, "costo_usd_hoy": 0.0, "costo_estimable": True,
            "no_entrena": llm.no_entrena(prov) if prov != "otro" else None,
        })
        e["modelos"].append(r["modelo"])
        e["llamadas"] += int(r["llamadas"] or 0)
        e["errores"] += int(r["errores"] or 0)
        e["tokens"] += int(r["tokens"] or 0)
        if r["latencia_ms_avg"] is not None:
            lat_acc.setdefault(prov, []).append(
                (int(r["latencia_ms_avg"]), int(r["llamadas"] or 0)))
        c = llm.costo_estimado(r["modelo"], r["tokens_in"], r["tokens_out"], r["cache_hit"])
        c_hoy = llm.costo_estimado(r["modelo"], r["tokens_in_hoy"], r["tokens_out_hoy"],
                                   r["cache_hit_hoy"])
        if c is None:
            e["costo_estimable"] = False  # modelo sin precio conocido
        else:
            e["costo_usd"] += c
            e["costo_usd_hoy"] += c_hoy or 0.0

    for prov, pares in lat_acc.items():  # promedio ponderado por llamadas
        n = sum(c for _lat, c in pares)
        if n:
            acc[prov]["latencia_ms_avg"] = round(sum(lat * c for lat, c in pares) / n)
    for e in acc.values():
        e["costo_usd"] = round(e["costo_usd"], 4)
        e["costo_usd_hoy"] = round(e["costo_usd_hoy"], 4)
    return sorted(acc.values(), key=lambda e: -e["tokens"])
