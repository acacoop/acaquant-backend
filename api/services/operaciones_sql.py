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


# Valor de `moneda` que pide el VOLUMEN DOLARIZADO (todo → USD con el mep del
# boleto). Opción EXTRA del selector, además de ARS/USD nativos. Solo SQL (el
# rollup de Mongo no trae el bruto en USD).
_DOLARIZAR = "USD_DOL"


# ── WHERE compartido (equivale a _ops_match / _arancel_match) ─────────────────
def _ops_where(
    moneda: str | None = None, mercado: str | None = None, operacion: str | None = None,
    denominacion: str | None = None, cuenta: str | None = None, segmento: str | None = None,
    scope: tuple[str, ...] | None = None, *, arancel: bool = False, operador: str | None = None,
    excluir: tuple[str, ...] | None = None, nivel_3: str | None = None,
) -> tuple[str, dict]:
    """Devuelve (where_sql, params). `arancel=True` → sin filtro de moneda, incluye los
    cierres con arancel (caución), igual que _arancel_match."""
    conds: list[str] = []
    p: dict = {}
    if arancel:
        conds.append("(es_cierre = false OR (es_cierre = true AND arancel <> 0))")
    elif moneda == _DOLARIZAR:
        # Dolarizado: entran ARS y USD (sin filtro de moneda); cada boleto se
        # convierte a USD con su mep en la suma del volumen (ver _bruto_expr).
        conds.append("es_cierre = false")
    else:
        conds.append("moneda = %(moneda)s")
        conds.append("es_cierre = false")
        p["moneda"] = moneda
    # FCI bilateral: contar UNA vez — suscripción por su SOLICITUD (día del pedido),
    # rescate por su LIQUIDACIÓN. Excluye suscripción+liquidación y rescate+solicitud.
    # Equivale al $nor de _ops_match. COALESCE evita que los NULL propaguen a NULL.
    conds.append(
        "NOT ((COALESCE(operacion,'') = 'Suscripción' AND COALESCE(etapa,'') = 'liquidacion') "
        "OR (COALESCE(operacion,'') = 'Rescate' AND COALESCE(etapa,'') = 'solicitud'))")
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
    if nivel_3 and nivel_3.lower() != "todos":
        # nivel_3 congelado en el boleto (columna propia de operaciones.operaciones),
        # NO el vigente del comitente — es el segmento al momento de la operación.
        conds.append("nivel_3 = %(nivel_3)s")
        p["nivel_3"] = nivel_3
    if operador:
        conds.append("id_cuenta IN (SELECT id_cuenta FROM comitentes WHERE operador_email = %(operador)s)")
        p["operador"] = operador
    if excluir:
        # Ocultar cuentas elegidas por el usuario. Compara contra la denominación tal
        # como se muestra ('(sin)' para NULL/'') → coincide con lo que llega del front.
        conds.append("COALESCE(NULLIF(denominacion, ''), '(sin)') <> ALL(%(excluir)s)")
        p["excluir"] = list(excluir)
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


def ops_niveles3() -> dict:
    """Valores distintos de nivel_3 (segmento congelado en el boleto) presentes en
    operaciones.operaciones — catálogo para el filtro nivel_3 de la vista OPERACIONES."""
    rows = _q("SELECT DISTINCT nivel_3 FROM operaciones "
              "WHERE nivel_3 IS NOT NULL AND nivel_3 <> '' ORDER BY nivel_3")
    return {"niveles3": [r["nivel_3"] for r in rows]}


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
def _arancel_expr(moneda: str) -> str:
    """Expr SQL agregada del arancel (ABS). El arancel se guarda SIEMPRE en pesos →
    en USD/USD_DOL se convierte con el mep de cada boleto (arancel/mep; sin mep → 0).
    En ARS queda en pesos. Mismo criterio de dolarización que _bruto_expr."""
    if moneda in (_DOLARIZAR, "USD"):
        return ("SUM(CASE WHEN COALESCE(mep, 0) > 0 "
                "THEN ABS(COALESCE(arancel, 0)) / mep ELSE 0 END)")
    return "SUM(ABS(COALESCE(arancel, 0)))"


def _bruto_expr(moneda: str) -> str:
    """Expr SQL agregada del volumen. 'USD_DOL' → suma cada boleto convertido a USD
    con su mep (USD directo; ARS / mep; sin mep → 0, se descarta en silencio). ARS/USD
    nativos → bruto tal cual (el filtro de moneda lo pone _ops_where)."""
    if moneda == _DOLARIZAR:
        return ("SUM(CASE WHEN moneda = 'USD' THEN COALESCE(bruto, 0) "
                "WHEN moneda = 'ARS' AND COALESCE(mep, 0) > 0 THEN COALESCE(bruto, 0) / mep "
                "ELSE 0 END)")
    return "SUM(COALESCE(bruto, 0))"


def ops_serie(
    moneda: str = "ARS", mercado: str | None = None, operacion: str | None = None,
    denominacion: str | None = None, cuenta: str | None = None, segmento: str | None = None,
    scope: tuple[str, ...] | None = None, operador: str | None = None,
    excluir: tuple[str, ...] | None = None, nivel_3: str | None = None,
) -> dict:
    where, p = _ops_where(moneda, mercado, operacion, denominacion, cuenta, segmento, scope,
                          operador=operador, excluir=excluir, nivel_3=nivel_3)
    rows = _q(
        f"SELECT concertacion AS fecha, {_bruto_expr(moneda)} AS bruto "
        f"FROM operaciones WHERE {where} GROUP BY concertacion ORDER BY concertacion",
        p,
    )
    serie = [{"fecha": _iso(r["fecha"]), "bruto": round(_f(r["bruto"]), 2)} for r in rows]
    return {"moneda": moneda, "mercado": mercado, "serie": serie}


def ops_resumen(
    moneda: str = "ARS", mercado: str | None = None, desde: str = "", hasta: str = "",
    operacion: str | None = None, denominacion: str | None = None, cuenta: str | None = None,
    segmento: str | None = None, scope: tuple[str, ...] | None = None,
    instrumento: str | None = None, operador: str | None = None,
    excluir: tuple[str, ...] | None = None, nivel_3: str | None = None,
) -> dict:
    base, p = _ops_where(moneda, mercado, cuenta=cuenta, segmento=segmento, scope=scope,
                         operador=operador, excluir=excluir, nivel_3=nivel_3)
    p.update({"desde": desde, "hasta": hasta})
    base = f"{base} AND concertacion >= %(desde)s AND concertacion <= %(hasta)s"
    bexpr = _bruto_expr(moneda)

    def _xf(*, denom=False, op=False, instr=False) -> tuple[str, dict]:
        """WHERE base + las selecciones cruzadas pedidas (cada tabla aplica las de las otras)."""
        w, pp = base, dict(p)
        if denom and denominacion:
            w += " AND denominacion = %(f_denom)s"; pp["f_denom"] = denominacion
        if op and operacion:
            w += " AND operacion = %(f_op)s"; pp["f_op"] = operacion
        if instr and instrumento:
            w += " AND instrumento = %(f_instr)s"; pp["f_instr"] = instrumento
        return w, pp

    # arancel POR FILA: se suma en la MISMA query que el bruto (mismo WHERE: moneda +
    # es_cierre=false + cross-filters) → es el arancel exacto de las operaciones mostradas.
    # Sigue la MISMA dolarización que el bruto (USD/USD_DOL → /mep). El arancel de caución
    # (cierres, es_cierre=true) NO entra acá — ese vive en la tab ARANCELES dedicada.
    aexpr = _arancel_expr(moneda)

    # por_operacion: cruzada por denominacion + instrumento, HAVING bruto<>0.
    w_op, p_op = _xf(denom=True, instr=True)
    por_operacion = [
        {"operacion": r["operacion"] or "(sin)", "bruto": round(_f(r["bruto"]), 2),
         "arancel": round(_f(r["ar"]), 2), "n": r["n"]}
        for r in _q(f"SELECT operacion, {bexpr} AS bruto, {aexpr} AS ar, count(*) AS n FROM operaciones "
                    f"WHERE {w_op} GROUP BY operacion HAVING {bexpr} <> 0 ORDER BY bruto DESC", p_op)
    ]
    # por_denominacion: cruzada por operacion + instrumento.
    w_dn, p_dn = _xf(op=True, instr=True)
    por_denominacion = [
        {"denominacion": r["denominacion"] or "(sin)", "bruto": round(_f(r["bruto"]), 2),
         "arancel": round(_f(r["ar"]), 2), "n": r["n"]}
        for r in _q(f"SELECT denominacion, {bexpr} AS bruto, {aexpr} AS ar, count(*) AS n FROM operaciones "
                    f"WHERE {w_dn} GROUP BY denominacion ORDER BY bruto DESC", p_dn)
    ]
    # por_instrumento (títulos): cruzada por operacion + denominacion, HAVING bruto<>0.
    w_in, p_in = _xf(op=True, denom=True)
    por_instrumento = [
        {"instrumento": r["instrumento"] or "(sin)", "bruto": round(_f(r["bruto"]), 2),
         "arancel": round(_f(r["ar"]), 2), "n": r["n"]}
        for r in _q(f"SELECT instrumento, {bexpr} AS bruto, {aexpr} AS ar, count(*) AS n FROM operaciones "
                    f"WHERE {w_in} GROUP BY instrumento HAVING {bexpr} <> 0 ORDER BY bruto DESC", p_in)
    ]
    total = round(sum(r["bruto"] for r in (por_denominacion if denominacion else por_operacion)), 2)
    return {
        "moneda": moneda, "mercado": mercado, "desde": desde, "hasta": hasta,
        "por_operacion": por_operacion, "por_denominacion": por_denominacion,
        "por_instrumento": por_instrumento, "total": total,
    }


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
    scope: tuple[str, ...] | None = None, operador: str | None = None,
) -> dict:
    fmt = "YYYY-MM" if agg.upper() == "MENSUAL" else "YYYY-MM-DD"
    base, bp = _ops_where(segmento=segmento, scope=scope, arancel=True)
    # Filtro madre por operador: scopea TODO (serie + tablas) a las cuentas de ese
    # operador (subquery a comitentes). Se mete en `base` → aplica uniforme.
    if operador:
        base = (f"{base} AND id_cuenta IN "
                f"(SELECT id_cuenta FROM comitentes WHERE operador_email = %(f_op)s)")
        bp["f_op"] = operador

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
    cuenta: str | None = None, scope: tuple[str, ...] | None = None, nivel5: str | None = None,
) -> dict:
    fmt = "YYYY-MM" if agg.upper() == "MENSUAL" else "YYYY-MM-DD"
    base = "commodity IN ('SOJA', 'TRIGO', 'MAIZ')"
    bp: dict = {}
    if scope is not None:
        base += " AND id_cuenta = ANY(%(scope)s)"
        bp["scope"] = list(scope)
    if nivel5:
        base += " AND id_cuenta IN (SELECT id_cuenta FROM comitentes WHERE nivel_5 = %(nivel5)s)"
        bp["nivel5"] = nivel5
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

    # share: denominador del market-share desde mercado.volumen_mercado_agro (SQL-native,
    # decomiso Mongo: CashFlow.VolumenMercadoAgro dropeada). `{periodo: {commodity: toneladas}}`.
    from api.services import cashflow_sql as _cf_sql
    mercado = _cf_sql.volumen_mercado_agro()
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
