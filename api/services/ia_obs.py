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


def saldo() -> dict:
    """Estado de TODOS los proveedores configurados (ruteo multi-proveedor,
    2026-07-21): cuál está activo, qué modelos usa, si se compromete a no
    entrenar con lo que le mandamos, y su saldo si lo expone. Se mantiene
    `saldos` plano para compat del panel viejo."""
    from core import llm

    proveedores = llm.estado_proveedores()
    principal = next((p for p in proveedores if p["saldo"]), None)
    return {
        "proveedores": proveedores,
        # compat: el saldo del que expone uno (hoy solo el default)
        "disponible": (principal or {}).get("saldo", {}).get("disponible")
        if principal else None,
        "saldos": (principal or {}).get("saldo", {}).get("saldos", []) if principal else [],
    }


def get_presupuestos() -> dict:
    """Límites vigentes del gateway (resueltos con su precedencia) + auditoría
    de la última edición si los setearon desde el panel."""
    from core import ai

    out = {
        "global_dia": ai.presupuesto_dia_global(),
        "usuario_dia": ai.presupuesto_dia_usuario(),
        "excepciones": [],  # límites personales que pisan el tope general
        "editado": None,
    }
    try:
        with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT clave, valor, updated_at, updated_by FROM ia.config "
                "WHERE clave IN ('budget_dia_global', 'budget_dia_usuario') "
                "   OR clave LIKE 'budget_dia_usuario:%' "
                "ORDER BY updated_at DESC"
            )
            filas = cur.fetchall()
        if filas:
            out["editado"] = {
                "por": filas[0]["updated_by"],
                "cuando": filas[0]["updated_at"].isoformat(),
            }
        out["excepciones"] = sorted(
            [
                {"usuario": f["clave"].split(":", 1)[1], "valor": int(f["valor"])}
                for f in filas
                if f["clave"].startswith("budget_dia_usuario:")
            ],
            key=lambda e: e["usuario"],
        )
    except Exception:
        pass  # tabla aún no aplicada → rigen env/default igual
    return out


def set_presupuesto_usuario(email: str, valor: int | None, actor: str) -> dict:
    """Excepción PERSONAL de tope diario para un usuario (ej. el admin se da
    más margen que el general). valor None = borrar la excepción (vuelve al
    tope general). Mismas reglas: positivo y ≤ global."""
    from core import ai

    email = (email or "").strip().lower()
    if not email or "@" not in email:
        raise ValueError("email inválido")
    if valor is not None:
        valor = int(valor)
        if valor <= 0:
            raise ValueError("el límite debe ser un entero positivo")
        if valor > ai.presupuesto_dia_global():
            raise ValueError(
                f"el límite personal ({valor:,}) no puede superar el global "
                f"({ai.presupuesto_dia_global():,})"
            )
    clave = f"budget_dia_usuario:{email}"
    with get_pool().connection() as conn, conn.cursor() as cur:
        if valor is None:
            cur.execute("DELETE FROM ia.config WHERE clave = %s", (clave,))
        else:
            cur.execute(
                "INSERT INTO ia.config (clave, valor, updated_by) VALUES (%s, %s, %s) "
                "ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor, "
                "updated_at = now(), updated_by = EXCLUDED.updated_by",
                (clave, valor, actor),
            )
    ai.invalidate_config_cache()
    return get_presupuestos()


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
