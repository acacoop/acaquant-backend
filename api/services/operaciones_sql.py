"""api/services/operaciones_sql.py — vista OPERACIONES leyendo de Postgres (Supabase).

Servicio PURO (sin FastAPI). Espejo SQL de los endpoints `/api/operaciones/ops/*` que hoy
leen Mongo (`api/routers/operaciones.py`). Cada función devuelve EXACTAMENTE el mismo shape
que su endpoint para poder hacer dual-run y comparar SQL vs Mongo (ver
scripts/compare_ops_sql_vs_mongo.py). Agrega EN VIVO (sin rollup): Postgres agrega 490k
filas con índice en milisegundos. Ver docs/SQL.md y el plan de migración.

Reglas de traducción Mongo→SQL blindadas (verificadas con diag_ops_sql_nulls):
  * etapa: `IS DISTINCT FROM 'solicitud'` (98% de los docs tienen etapa NULL; `<> 'solicitud'`
    NO matchea NULL en SQL → perdería todo). Equivale al `$ne` de Mongo.
  * es_cierre: `= false` (excluye los 49 NULL, igual que Mongo `{es_cierre:false}`).
  * fechas: concertacion es `date` → se formatea a 'YYYY-MM-DD' a la salida.
  * Decimal→float, $ifNull→COALESCE, $abs→ABS, substr 0-based→to_char.

VolumenMercadoAgro (share de /ops/agro) sigue HÍBRIDO: se lee de Mongo (chica, manual) →
el denominador del share no cambia de fuente, la igualdad se mantiene trivial.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from psycopg.rows import dict_row

from core.postgres import get_pool

_SERIE_VENTANA_DIAS = 550  # ~18 meses (igual que operaciones.py)
_TON = (
    "ABS(COALESCE(cantidad, 0)) * "
    "(CASE WHEN COALESCE(instrumento, '') ~* 'MIN' THEN 10 ELSE 100 END)"
)


# ── infra ────────────────────────────────────────────────────────────────────
def _q(sql: str, params: dict | None = None) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


def _f(x) -> float:
    return float(x or 0)


def _iso(d):
    return d.isoformat() if d is not None else None


def _iso_naive(d):
    """datetime tz-aware (timestamptz de PG) → ISO naive en UTC, igual que Mongo
    (que guarda el datetime sin tz). Mismo instante, sin el sufijo '+00:00'."""
    if d is None:
        return None
    if d.tzinfo is not None:
        d = d.astimezone(UTC).replace(tzinfo=None)
    return d.isoformat()


# ── WHERE compartido (equivale a _ops_match / _arancel_match) ─────────────────
def _ops_where(
    moneda: str | None = None, mercado: str | None = None, operacion: str | None = None,
    denominacion: str | None = None, cuenta: str | None = None, segmento: str | None = None,
    scope: tuple[str, ...] | None = None, *, arancel: bool = False,
) -> tuple[str, dict]:
    """Devuelve (where_sql, params). `arancel=True` → sin filtro de moneda, incluye los
    cierres con arancel (caución), igual que _arancel_match."""
    conds: list[str] = []
    p: dict = {}
    if arancel:
        conds.append("(es_cierre = false OR (es_cierre = true AND arancel <> 0))")
    else:
        conds.append("moneda = %(moneda)s")
        conds.append("es_cierre = false")
        p["moneda"] = moneda
    conds.append("etapa IS DISTINCT FROM 'solicitud'")
    if mercado and mercado.lower() != "todos":
        conds.append("mercado = %(mercado)s")
        p["mercado"] = mercado
    if operacion:
        conds.append("operacion = %(operacion)s")
        p["operacion"] = operacion
    if denominacion:
        conds.append("denominacion = %(denominacion)s")
        p["denominacion"] = denominacion
    if cuenta:
        conds.append("id_cuenta = %(cuenta)s")
        p["cuenta"] = cuenta
    if segmento and segmento.lower() != "todos":
        conds.append("segmento = %(segmento)s")
        p["segmento"] = segmento
    if scope is not None:
        conds.append("id_cuenta = ANY(%(scope)s)")
        p["scope"] = list(scope)
    return " AND ".join(conds), p


# ── selectores (RAW, sin filtros de negocio — igual que en Mongo) ─────────────
def ops_mercados() -> dict:
    rows = _q("SELECT DISTINCT mercado FROM operaciones "
              "WHERE mercado IS NOT NULL AND mercado <> '' ORDER BY mercado")
    return {"mercados": [r["mercado"] for r in rows]}


def ops_segmentos() -> dict:
    rows = _q("SELECT DISTINCT segmento FROM operaciones "
              "WHERE segmento IS NOT NULL AND segmento <> '' ORDER BY segmento")
    return {"segmentos": [r["segmento"] for r in rows]}


def ops_fechas() -> dict:
    rows = _q("SELECT concertacion AS fecha, count(*) AS n FROM operaciones "
              "WHERE concertacion IS NOT NULL GROUP BY concertacion ORDER BY concertacion DESC")
    return {"fechas": [{"fecha": _iso(r["fecha"]), "n": r["n"]} for r in rows]}


def ops_cuentas_list(scope: tuple[str, ...] | None = None) -> dict:
    # Igual que Mongo: NO aplica scope (el endpoint lo recibe pero no lo usa).
    rows = _q("SELECT id_cuenta AS cuenta, max(denominacion) AS denominacion "
              "FROM operaciones WHERE id_cuenta IS NOT NULL "
              "GROUP BY id_cuenta ORDER BY denominacion")
    return {"cuentas": [{"cuenta": r["cuenta"], "denominacion": r["denominacion"]} for r in rows]}


def ops_meta(fecha: str) -> dict:
    # RAW (sin _ops_match): cuenta TODOS los boletos del día, igual que Mongo.
    rows = _q(
        "SELECT count(*) AS n, max(ingestado_en) AS ultima, "
        "count(DISTINCT mercado) FILTER (WHERE mercado IS NOT NULL AND mercado <> '') AS ncat "
        "FROM operaciones WHERE concertacion = %(fecha)s",
        {"fecha": fecha},
    )
    r = rows[0]
    return {"meta": {
        "fecha": fecha,
        "n_boletos": r["n"] or 0,
        "n_categorias": r["ncat"] or 0,
        "ultima_ingesta": _iso_naive(r["ultima"]),
    }}


# ── serie / resumen / boletos (VOLUMEN, _ops_match) ──────────────────────────
def ops_serie(
    moneda: str = "ARS", mercado: str | None = None, operacion: str | None = None,
    denominacion: str | None = None, cuenta: str | None = None, segmento: str | None = None,
    scope: tuple[str, ...] | None = None,
) -> dict:
    where, p = _ops_where(moneda, mercado, operacion, denominacion, cuenta, segmento, scope)
    rows = _q(
        f"SELECT concertacion AS fecha, SUM(COALESCE(bruto, 0)) AS bruto "
        f"FROM operaciones WHERE {where} GROUP BY concertacion ORDER BY concertacion",
        p,
    )
    serie = [{"fecha": _iso(r["fecha"]), "bruto": round(_f(r["bruto"]), 2)} for r in rows]
    return {"moneda": moneda, "mercado": mercado, "serie": serie}


def ops_resumen(
    moneda: str = "ARS", mercado: str | None = None, desde: str = "", hasta: str = "",
    operacion: str | None = None, denominacion: str | None = None, cuenta: str | None = None,
    segmento: str | None = None, scope: tuple[str, ...] | None = None,
) -> dict:
    base, p = _ops_where(moneda, mercado, cuenta=cuenta, segmento=segmento, scope=scope)
    p.update({"desde": desde, "hasta": hasta})
    base = f"{base} AND concertacion >= %(desde)s AND concertacion <= %(hasta)s"

    # por_operacion: filtrada por denominacion (cross-filter), HAVING bruto<>0.
    w_op, p_op = base, dict(p)
    if denominacion:
        w_op += " AND denominacion = %(f_denom)s"
        p_op["f_denom"] = denominacion
    por_operacion = [
        {"operacion": r["operacion"] or "(sin)", "bruto": round(_f(r["bruto"]), 2), "n": r["n"]}
        for r in _q(
            f"SELECT operacion, SUM(COALESCE(bruto,0)) AS bruto, count(*) AS n "
            f"FROM operaciones WHERE {w_op} GROUP BY operacion "
            f"HAVING SUM(COALESCE(bruto,0)) <> 0 ORDER BY bruto DESC", p_op,
        )
    ]
    # por_denominacion: filtrada por operacion (cross-filter), sin HAVING.
    w_dn, p_dn = base, dict(p)
    if operacion:
        w_dn += " AND operacion = %(f_op)s"
        p_dn["f_op"] = operacion
    por_denominacion = [
        {"denominacion": r["denominacion"] or "(sin)",
         "bruto": round(_f(r["bruto"]), 2), "n": r["n"]}
        for r in _q(
            f"SELECT denominacion, SUM(COALESCE(bruto,0)) AS bruto, count(*) AS n "
            f"FROM operaciones WHERE {w_dn} GROUP BY denominacion ORDER BY bruto DESC", p_dn,
        )
    ]
    total = round(sum(r["bruto"] for r in (por_denominacion if denominacion else por_operacion)), 2)
    return {
        "moneda": moneda, "mercado": mercado, "desde": desde, "hasta": hasta,
        "por_operacion": por_operacion, "por_denominacion": por_denominacion, "total": total,
    }


def ops_boletos(
    desde: str = "", hasta: str = "", moneda: str = "ARS", denominacion: str | None = None,
    cuenta: str | None = None, operacion: str | None = None, mercado: str | None = None,
    segmento: str | None = None, scope: tuple[str, ...] | None = None,
) -> dict:
    where, p = _ops_where(moneda, mercado, operacion, denominacion, cuenta, segmento, scope)
    p.update({"desde": desde, "hasta": hasta})
    rows = _q(
        f"SELECT boleto, concertacion, id_cuenta AS cuenta, denominacion, tipo_operacion, "
        f"operacion, mercado, instrumento, condiciones, cantidad, bruto, moneda "
        f"FROM operaciones WHERE {where} "
        f"AND concertacion >= %(desde)s AND concertacion <= %(hasta)s "
        f"ORDER BY bruto DESC NULLS LAST LIMIT 500", p,
    )
    boletos = [{
        "boleto": r["boleto"], "concertacion": _iso(r["concertacion"]), "cuenta": r["cuenta"],
        "denominacion": r["denominacion"], "tipo_operacion": r["tipo_operacion"],
        "operacion": r["operacion"], "mercado": r["mercado"], "instrumento": r["instrumento"],
        "condiciones": r["condiciones"],
        "cantidad": _f(r["cantidad"]) if r["cantidad"] is not None else None,
        "bruto": _f(r["bruto"]) if r["bruto"] is not None else None, "moneda": r["moneda"],
    } for r in rows]
    return {"boletos": boletos, "n": len(boletos)}


# ── ARANCELES (_arancel_match; arancel siempre ABS y en pesos) ───────────────
_ARANCEL = "SUM(ABS(COALESCE(arancel, 0)))"


def _operador_map() -> dict[str, str]:
    rows = _q("SELECT c.id_cuenta, COALESCE(o.nombre, o.email, '(sin operador)') AS op "
              "FROM comitentes c LEFT JOIN operadores o ON o.email = c.operador_email")
    return {str(r["id_cuenta"]): r["op"] for r in rows}


def _op_pred(sel: str) -> tuple[str, dict]:
    """Predicado WHERE para filtrar operaciones por operador == sel (dim=operador)."""
    if sel == "(sin operador)":
        return ("id_cuenta NOT IN "
                "(SELECT id_cuenta FROM comitentes WHERE operador_email IS NOT NULL)", {})
    return ("id_cuenta IN (SELECT c.id_cuenta FROM comitentes c "
            "LEFT JOIN operadores o ON o.email = c.operador_email "
            "WHERE COALESCE(o.nombre, o.email) = %(sel_op)s)", {"sel_op": sel})


def ops_aranceles(
    moneda: str = "ARS", desde: str = "", hasta: str = "", agg: str = "MENSUAL",
    cuenta: str | None = None, instrumento: str | None = None, sel_dim: str | None = None,
    segmento: str | None = None, dim: str = "nivel3", serie_full: bool = False,
    scope: tuple[str, ...] | None = None,
) -> dict:
    fmt = "YYYY-MM" if agg.upper() == "MENSUAL" else "YYYY-MM-DD"
    base, bp = _ops_where(segmento=segmento, scope=scope, arancel=True)

    # SERIE (histórica, ventana ~18m salvo serie_full). En vivo, sin rollup.
    sp = dict(bp)
    serie_where = base
    if not serie_full:
        sp["cutoff"] = (datetime.now(UTC) - timedelta(hours=3)
                        - timedelta(days=_SERIE_VENTANA_DIAS)).date().isoformat()
        serie_where = f"{base} AND concertacion >= %(cutoff)s"
    serie = [
        {"periodo": r["periodo"], "arancel": round(_f(r["ar"]), 2)}
        for r in _q(
            f"SELECT to_char(concertacion, %(fmt)s) AS periodo, {_ARANCEL} AS ar "
            f"FROM operaciones WHERE {serie_where} GROUP BY periodo ORDER BY periodo",
            {**sp, "fmt": fmt},
        )
    ]

    # TABLAS acotadas a [desde,hasta]. Cross-filter 3-way: cada tabla aplica las
    # selecciones de las OTRAS dos.
    tp = {**bp, "desde": desde, "hasta": hasta}
    date_w = f"{base} AND concertacion >= %(desde)s AND concertacion <= %(hasta)s"
    m_cuenta = ("denominacion = %(f_cuenta)s", {"f_cuenta": cuenta}) if cuenta else (None, {})
    m_instr = ("instrumento = %(f_instr)s", {"f_instr": instrumento}) if instrumento else (None, {})
    if sel_dim and dim == "operador":
        m_dim = _op_pred(sel_dim)
    elif sel_dim and dim == "operacion":
        m_dim = ("operacion = %(f_dim)s", {"f_dim": sel_dim})
    elif sel_dim:
        m_dim = ("nivel_3 = %(f_dim)s", {"f_dim": sel_dim})
    else:
        m_dim = (None, {})

    def _tabla(group_expr: str, key: str, *subs: tuple[str | None, dict]) -> list[dict]:
        conds = [date_w]
        p = dict(tp)
        for frag, fp in subs:
            if frag:
                conds.append(frag)
                p.update(fp)
        where = " AND ".join(conds)
        return [
            {key: r[key], "arancel": round(_f(r["ar"]), 2), "n": r["n"]}
            for r in _q(
                f"SELECT COALESCE({group_expr}, '(sin)') AS {key}, {_ARANCEL} AS ar, "
                f"count(*) AS n FROM operaciones WHERE {where} "
                f"GROUP BY {group_expr} HAVING {_ARANCEL} > 0 ORDER BY ar DESC", p,
            )
        ]

    # IZQUIERDA (por_dim): filtrada por cuenta + instrumento (no por sí misma).
    if dim == "operador":
        det = _operador_map()
        conds = [date_w]
        p = dict(tp)
        for frag, fp in (m_cuenta, m_instr):
            if frag:
                conds.append(frag)
                p.update(fp)
        acc: dict[str, dict] = {}
        for r in _q(
            f"SELECT id_cuenta, {_ARANCEL} AS ar, count(*) AS n FROM operaciones "
            f"WHERE {' AND '.join(conds)} GROUP BY id_cuenta", p,
        ):
            op = det.get(str(r["id_cuenta"]), "(sin operador)")
            a = acc.setdefault(op, {"ar": 0.0, "n": 0})
            a["ar"] += _f(r["ar"])
            a["n"] += r["n"]
        por_dim = sorted(
            ({"clave": k, "arancel": round(v["ar"], 2), "n": v["n"]}
             for k, v in acc.items() if v["ar"] > 0),
            key=lambda x: x["arancel"], reverse=True,
        )
    else:
        field = "operacion" if dim == "operacion" else "nivel_3"
        por_dim = _tabla(field, "clave", m_cuenta, m_instr)

    por_cuenta = _tabla("denominacion", "denominacion", m_dim, m_instr)
    por_instrumento = _tabla("instrumento", "instrumento", m_dim, m_cuenta)

    return {
        "moneda": moneda, "desde": desde, "hasta": hasta, "agg": agg, "dim": dim,
        "serie": serie, "por_dim": por_dim, "por_cuenta": por_cuenta,
        "por_instrumento": por_instrumento,
        "total": round(sum(r["arancel"] for r in por_dim), 2),
    }


# ── AGRO (toneladas; share híbrido contra Mongo) ─────────────────────────────
def _agro_serie(rows: list[dict]) -> list[dict]:
    out: dict[str, dict] = {}
    for r in rows:
        d = out.setdefault(r["p"], {"periodo": r["p"], "SOJA": 0.0, "TRIGO": 0.0, "MAIZ": 0.0})
        d[r["c"]] = round(_f(r["ton"]), 0)
    return [out[p] for p in sorted(out)]


def ops_agro(
    desde: str = "", hasta: str = "", agg: str = "MENSUAL", commodity: str | None = None,
    cuenta: str | None = None, scope: tuple[str, ...] | None = None,
) -> dict:
    fmt = "YYYY-MM" if agg.upper() == "MENSUAL" else "YYYY-MM-DD"
    base = "commodity IN ('SOJA', 'TRIGO', 'MAIZ')"
    bp: dict = {}
    if scope is not None:
        base += " AND id_cuenta = ANY(%(scope)s)"
        bp["scope"] = list(scope)
    date_w = f"{base} AND concertacion >= %(desde)s AND concertacion <= %(hasta)s"
    dp = {**bp, "desde": desde, "hasta": hasta}

    # serie (global, por periodo×commodity).
    serie = _agro_serie(_q(
        f"SELECT to_char(concertacion, %(fmt)s) AS p, commodity AS c, SUM({_TON}) AS ton "
        f"FROM operaciones WHERE {date_w} GROUP BY p, commodity", {**dp, "fmt": fmt},
    ))
    # serie_cuenta (solo si hay cuenta elegida).
    serie_cuenta: list[dict] = []
    if cuenta:
        serie_cuenta = _agro_serie(_q(
            f"SELECT to_char(concertacion, %(fmt)s) AS p, commodity AS c, SUM({_TON}) AS ton "
            f"FROM operaciones WHERE {date_w} AND denominacion = %(cuenta)s "
            f"GROUP BY p, commodity", {**dp, "fmt": fmt, "cuenta": cuenta},
        ))
    # totales por commodity (cross-filter: cuenta → filtra commodities).
    w_comm, p_comm = date_w, dict(dp)
    if cuenta:
        w_comm += " AND denominacion = %(cuenta)s"
        p_comm["cuenta"] = cuenta
    tot = {"SOJA": 0.0, "TRIGO": 0.0, "MAIZ": 0.0}
    for r in _q(f"SELECT commodity AS c, SUM({_TON}) AS ton FROM operaciones "
                f"WHERE {w_comm} GROUP BY commodity", p_comm):
        tot[r["c"]] = round(_f(r["ton"]), 0)
    # por_cuenta (cross-filter: commodity → filtra cuentas).
    w_cta, p_cta = date_w, dict(dp)
    if commodity:
        w_cta += " AND commodity = %(commodity)s"
        p_cta["commodity"] = commodity
    por_cuenta = [
        {"denominacion": r["d"] or "(sin)", "toneladas": round(_f(r["ton"]), 0), "n": r["n"]}
        for r in _q(f"SELECT denominacion AS d, SUM({_TON}) AS ton, count(*) AS n "
                    f"FROM operaciones WHERE {w_cta} GROUP BY denominacion ORDER BY ton DESC",
                    p_cta)
    ]
    # por_instrumento (cross-filter: cuenta + commodity).
    w_ins, p_ins = date_w, dict(dp)
    if cuenta:
        w_ins += " AND denominacion = %(cuenta)s"
        p_ins["cuenta"] = cuenta
    if commodity:
        w_ins += " AND commodity = %(commodity)s"
        p_ins["commodity"] = commodity
    por_instrumento = [
        {"instrumento": r["i"] or "(sin)", "toneladas": round(_f(r["ton"]), 0), "n": r["n"]}
        for r in _q(f"SELECT instrumento AS i, SUM({_TON}) AS ton, count(*) AS n "
                    f"FROM operaciones WHERE {w_ins} GROUP BY instrumento ORDER BY ton DESC",
                    p_ins)
    ]
    # nuestro_mensual (histórico completo, sin date) → numerador del share.
    nuestro_m: dict[str, dict] = {}
    for r in _q(f"SELECT to_char(concertacion, 'YYYY-MM') AS p, commodity AS c, SUM({_TON}) AS ton "
                f"FROM operaciones WHERE {base} GROUP BY p, commodity", bp):
        nuestro_m.setdefault(r["p"], {})[r["c"]] = _f(r["ton"])

    # share: denominador desde Mongo (VolumenMercadoAgro, híbrido). Misma lógica que el router.
    from core.mongo import get_mongo_client_read
    mercado: dict[str, dict] = {}
    for d in get_mongo_client_read()["CashFlow"]["VolumenMercadoAgro"].find(
        {}, {"_id": 0, "periodo": 1, "commodity": 1, "toneladas": 1}
    ):
        mercado.setdefault(d["periodo"], {})[d["commodity"]] = d.get("toneladas") or 0
    serie_share = []
    for p in sorted(mercado):
        nm = mercado[p]
        ours = nuestro_m.get(p, {})
        row: dict = {"periodo": p}
        for c in ("SOJA", "TRIGO", "MAIZ"):
            mkt = nm.get(c) or 0
            row[c] = round(100 * (ours.get(c) or 0) / mkt, 2) if mkt else None
            row[f"{c}_nuestro"] = round(ours.get(c) or 0, 0)
            row[f"{c}_mercado"] = round(mkt, 0)
        serie_share.append(row)

    return {
        "desde": desde, "hasta": hasta, "agg": agg,
        "serie": serie, "serie_cuenta": serie_cuenta, "serie_share": serie_share,
        "totales": tot, "por_cuenta": por_cuenta, "por_instrumento": por_instrumento,
    }
