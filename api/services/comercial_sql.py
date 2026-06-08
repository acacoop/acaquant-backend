"""api/services/comercial_sql.py — vista COMERCIAL leyendo de Postgres (Supabase).

Servicio PURO (sin FastAPI). Espejo SQL de `api/services/comercial.py` (los endpoints
`/api/operaciones/comercial/*`). Mismo shape de salida → dual-run + comparación
(scripts/compare_comercial_sql_vs_mongo.py). Ver docs/MIGRACION_MONGO_SUPABASE.md.

Reusa de comercial.py la lógica de presentación que NO toca Mongo: `_cv` (dolarización al
MEP actual), `_factor_usd`, `estado_comercial`, `_hoy_art`, y las tuplas de categorías. Las
agregaciones (que en Mongo eran pipelines) se hacen en vivo en SQL.

Reglas SQL: `unidad IS DISTINCT FROM 'USDL'` (excluir futuros), pesificación por `mep` del
boleto, AuM "último snapshot" = `max(fecha_snapshot)` GLOBAL, `etapa IS DISTINCT FROM
'solicitud'` para arancel, Decimal→float, date→ISO. Filtro de cuentas del operador por
subquery sobre `comitentes` activas (estado='Activa').

Estado: Chunk 1 (selector + portafolio + operaciones + serie). Resto en progreso.
"""
from __future__ import annotations

from datetime import date, timedelta

from psycopg.rows import dict_row

from api.services.comercial import (
    _CATS_OPERACIONES,
    _CATS_VOLUMEN,
    _cv,
    _factor_usd,
    _hoy_art,
    estado_comercial,
)
from core.postgres import get_pool

# Ficha embebida en cada cliente (= _FICHA_FIELDS de comercial.py). denominacion sale de
# `cuentas` (no de comitentes); el resto de `comitentes`.
_FICHA = ("denominacion", "telefono", "email", "nivel_1", "nivel_2", "nivel_3", "nivel_4",
          "nivel_5", "primer_contacto_comercial", "riesgo_la_ft", "division", "adc", "dma",
          "referido")
_ANALISIS = ("denominacion", "telefono", "nivel_1", "nivel_2", "nivel_3", "nivel_4", "nivel_5")

# Pesificación de un boleto (ARS directo; USD × mep del boleto). = _PESIF de comercial.py.
_PESIF = ("CASE WHEN moneda = 'ARS' THEN abs(COALESCE(importe, 0)) "
          "ELSE abs(COALESCE(importe, 0)) * COALESCE(mep, 0) END")


def _q(sql: str, params: dict | None = None) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


def _f(x) -> float:
    return float(x or 0)


def _iso(d):
    return d.isoformat() if d is not None else None


def _comitentes_where(operador: str, p: dict, nivel_1: str | None = None,
                      nivel_3: str | None = None, referido: str | None = None,
                      alias: str = "") -> str:
    """WHERE de comitentes activas: operador (o __todos__) + nivel_1/nivel_3/referido
    opcionales (todos se CRUZAN). Muta `p` con los params. `alias` para prefijar las
    columnas (ej. 'c.') cuando hay JOIN."""
    a = f"{alias}." if alias else ""
    conds = [f"{a}estado = 'Activa'"]
    if operador != "__todos__":
        p["op"] = operador
        conds.append(f"{a}operador_email = %(op)s")
    if nivel_1:
        p["n1"] = nivel_1
        conds.append(f"{a}nivel_1 = %(n1)s")
    if nivel_3:
        p["n3"] = nivel_3
        conds.append(f"{a}nivel_3 = %(n3)s")
    if referido:
        p["ref"] = referido
        conds.append(f"{a}referido = %(ref)s")
    return " AND ".join(conds)


def _scope_cuentas(operador: str, p: dict, nivel_1: str | None = None,
                   nivel_3: str | None = None, referido: str | None = None) -> str:
    """Fragmento `id_cuenta IN (SELECT ... FROM comitentes WHERE ...)` scopeado al
    operador + nivel_1/nivel_3/referido (intersección). Muta `p` con los params."""
    return (f"id_cuenta IN (SELECT id_cuenta FROM comitentes "
            f"WHERE {_comitentes_where(operador, p, nivel_1, nivel_3, referido)})")


def _ids_operador(operador: str, nivel_1: str | None = None, nivel_3: str | None = None,
                  referido: str | None = None) -> list[str]:
    """ids de cuenta activas del scope (operador + niveles + referido)."""
    p: dict = {}
    where = _comitentes_where(operador, p, nivel_1, nivel_3, referido)
    return sorted(r["id_cuenta"] for r in _q(
        f"SELECT id_cuenta FROM comitentes WHERE {where}", p))


def _aum_por_cuenta_sql(operador: str, nivel_1: str | None = None,
                        nivel_3: str | None = None, referido: str | None = None) -> dict[str, float]:
    """AuM (último snapshot GLOBAL) por id_cuenta, scopeado al operador + filtros."""
    snap = _q("SELECT max(fecha_snapshot) AS f FROM aum")[0]["f"]
    if snap is None:
        return {}
    p: dict = {"f": snap}
    scope = _scope_cuentas(operador, p, nivel_1, nivel_3, referido)
    return {r["id_cuenta"]: _f(r["aum"]) for r in _q(
        f"SELECT id_cuenta, SUM(valuacion) AS aum FROM aum "
        f"WHERE fecha_snapshot = %(f)s AND {scope} GROUP BY id_cuenta", p)}


def dimensiones_comercial() -> dict:
    """Combos distintos (operador, nivel_1, nivel_3, referido) de cuentas activas —
    para poblar y CRUZAR los filtros madre en el frontend."""
    rows = _q(
        "SELECT c.operador_email, o.nombre AS operador_nombre, c.nivel_1, c.nivel_3, "
        "c.referido, count(*) AS n FROM comitentes c "
        "LEFT JOIN operadores o ON o.email = c.operador_email "
        "WHERE c.estado = 'Activa' AND c.operador_email IS NOT NULL "
        "GROUP BY c.operador_email, o.nombre, c.nivel_1, c.nivel_3, c.referido"
    )
    return {"combos": [
        {"operador_email": r["operador_email"], "operador_nombre": r["operador_nombre"],
         "nivel_1": r["nivel_1"], "nivel_3": r["nivel_3"], "referido": r["referido"],
         "n_cuentas": r["n"]} for r in rows]}


def listar_operadores_comercial() -> list[dict]:
    rows = _q(
        "SELECT c.operador_email AS operador_email, o.nombre AS operador_nombre, "
        "count(*) AS n_cuentas FROM comitentes c "
        "LEFT JOIN operadores o ON o.email = c.operador_email "
        "WHERE c.estado = 'Activa' AND c.operador_email IS NOT NULL "
        "GROUP BY c.operador_email, o.nombre ORDER BY n_cuentas DESC"
    )
    return [{"operador_email": r["operador_email"], "operador_nombre": r["operador_nombre"],
             "n_cuentas": r["n_cuentas"]} for r in rows]


def portafolio_cliente(*, id_cuenta: str) -> dict:
    snap = _q("SELECT max(fecha_snapshot) AS f FROM aum")[0]["f"]
    if snap is None:
        return {"id_cuenta": str(id_cuenta), "fecha_snapshot": None, "total": 0.0, "posiciones": []}
    rows = _q("SELECT unidad, SUM(valuacion) AS valuacion FROM aum "
              "WHERE fecha_snapshot = %(f)s AND id_cuenta = %(idc)s "
              "GROUP BY unidad ORDER BY valuacion DESC", {"f": snap, "idc": str(id_cuenta)})
    total = sum(_f(r["valuacion"]) for r in rows)
    posiciones = [{
        "unidad": r["unidad"], "valuacion": round(_f(r["valuacion"]), 2),
        "pct": round(100.0 * _f(r["valuacion"]) / total, 2) if total else 0.0,
    } for r in rows]
    return {"id_cuenta": str(id_cuenta), "fecha_snapshot": _iso(snap),
            "total": round(total, 2), "posiciones": posiciones}


def operaciones_cliente(*, id_cuenta: str, limite: int = 300) -> dict:
    rows = _q(
        "SELECT fecha, comprobante, categoria, op, ticker, cantidad, precio, importe, moneda, "
        "plazo FROM negocio_movimientos WHERE id_cuenta = %(idc)s AND categoria = ANY(%(cats)s) "
        "AND unidad IS DISTINCT FROM 'USDL' ORDER BY fecha DESC, comprobante DESC LIMIT %(lim)s",
        {"idc": str(id_cuenta), "cats": list(_CATS_OPERACIONES), "lim": int(limite)},
    )
    ops = [{
        "fecha": _iso(r["fecha"]), "comprobante": r["comprobante"], "categoria": r["categoria"],
        "op": r["op"], "ticker": r["ticker"],
        "cantidad": _f(r["cantidad"]) if r["cantidad"] is not None else None,
        "precio": _f(r["precio"]) if r["precio"] is not None else None,
        "importe": _f(r["importe"]) if r["importe"] is not None else None,
        "moneda": r["moneda"], "plazo": r["plazo"],
    } for r in rows]
    return {"id_cuenta": str(id_cuenta), "n": len(ops), "operaciones": ops}


def serie_comercial(*, operador: str, metric: str = "volumen", moneda: str = "ARS",
                    id_cuenta: str | None = None, nivel_1: str | None = None,
                    nivel_3: str | None = None, referido: str | None = None) -> dict:
    factor = _factor_usd(moneda)
    p: dict = {}
    if id_cuenta:
        scope = "id_cuenta = %(idc)s"
        p["idc"] = str(id_cuenta)
    else:
        scope = _scope_cuentas(operador, p, nivel_1, nivel_3, referido)

    if metric == "aum":
        rows = _q(f"SELECT fecha_snapshot AS fecha, SUM(valuacion) AS v FROM aum "
                  f"WHERE {scope} GROUP BY fecha_snapshot ORDER BY fecha_snapshot", p)
    else:
        p["cats"] = list(_CATS_VOLUMEN)
        rows = _q(f"SELECT fecha, SUM({_PESIF}) AS v FROM negocio_movimientos "
                  f"WHERE {scope} AND categoria = ANY(%(cats)s) AND unidad IS DISTINCT FROM 'USDL' "
                  f"GROUP BY fecha ORDER BY fecha", p)
    serie = [{"fecha": _iso(r["fecha"]), "valor": _cv(_f(r["v"]), factor)} for r in rows]
    return {"operador": operador, "id_cuenta": id_cuenta, "metric": metric,
            "moneda": moneda, "serie": serie}


def clientes_por_fecha(*, operador: str, desde: str, hasta: str,
                       moneda: str = "ARS", nivel_1: str | None = None,
                       nivel_3: str | None = None, referido: str | None = None) -> dict:
    """Clientes que OPERARON en el rango [desde, hasta] con su volumen del período.
    Alimenta la interactividad del chart de volumen (click en barra → tabla del día/semana/mes)."""
    factor = _factor_usd(moneda)
    aum = _aum_por_cuenta_sql(operador, nivel_1, nivel_3, referido)
    p: dict = {"desde": desde, "hasta": hasta, "cats": list(_CATS_VOLUMEN)}
    scope = _scope_cuentas(operador, p, nivel_1, nivel_3, referido)
    vol: dict[str, float] = {}
    for r in _q(
        f"SELECT id_cuenta, SUM({_PESIF}) AS v FROM negocio_movimientos "
        f"WHERE {scope} AND categoria = ANY(%(cats)s) AND unidad IS DISTINCT FROM 'USDL' "
        f"AND fecha >= %(desde)s AND fecha <= %(hasta)s GROUP BY id_cuenta", p,
    ):
        vol[r["id_cuenta"]] = _f(r["v"])
    ficha = _ficha_por_cuenta(operador, _FICHA, nivel_1, nivel_3, referido)
    clientes = [{
        "id_cuenta": idc,
        "denominacion": (ficha.get(idc, {}).get("denominacion") or "—"),
        "aum": _cv(aum.get(idc, 0.0), factor),
        "volumen_periodo": _cv(v, factor),
        "ficha": {k: ficha.get(idc, {}).get(k) for k in _FICHA},
    } for idc, v in vol.items() if v]
    clientes.sort(key=lambda x: x["volumen_periodo"], reverse=True)
    return {"operador": operador, "moneda": moneda, "desde": desde, "hasta": hasta,
            "total_volumen": _cv(sum(vol.values()), factor),
            "n_clientes": len(clientes), "clientes": clientes}


def _ficha_por_cuenta(operador: str, campos: tuple[str, ...], nivel_1: str | None = None,
                      nivel_3: str | None = None, referido: str | None = None) -> dict[str, dict]:
    """{id_cuenta: {campos}} de comitentes activas (+ denominacion de cuentas) del scope."""
    cols = ", ".join(f"c.{c}" if c != "denominacion" else "u.denominacion" for c in campos)
    p: dict = {}
    where = _comitentes_where(operador, p, nivel_1, nivel_3, referido, alias="c")
    rows = _q(f"SELECT c.id_cuenta, {cols} FROM comitentes c "
              f"LEFT JOIN cuentas u ON u.id_cuenta = c.id_cuenta WHERE {where}", p)
    return {r["id_cuenta"]: r for r in rows}


def operador_comercial(*, operador: str, moneda: str = "ARS", nivel_1: str | None = None,
                       nivel_3: str | None = None, referido: str | None = None) -> dict:
    factor = _factor_usd(moneda)
    hoy = _hoy_art()
    mtd, ytd = hoy.replace(day=1).isoformat(), hoy.replace(month=1, day=1).isoformat()
    ids = _ids_operador(operador, nivel_1, nivel_3, referido)
    aum = _aum_por_cuenta_sql(operador, nivel_1, nivel_3, referido)

    # Volumen YTD y MTD por cuenta en una pasada.
    p: dict = {"ytd": ytd, "mtd": mtd, "cats": list(_CATS_VOLUMEN)}
    scope = _scope_cuentas(operador, p, nivel_1, nivel_3, referido)
    vol_ytd: dict[str, float] = {}
    vol_mtd: dict[str, float] = {}
    for r in _q(
        f"SELECT id_cuenta, "
        f"SUM(CASE WHEN fecha >= %(ytd)s THEN {_PESIF} ELSE 0 END) AS vy, "
        f"SUM(CASE WHEN fecha >= %(mtd)s THEN {_PESIF} ELSE 0 END) AS vm "
        f"FROM negocio_movimientos WHERE {scope} AND categoria = ANY(%(cats)s) "
        f"AND unidad IS DISTINCT FROM 'USDL' GROUP BY id_cuenta", p,
    ):
        vol_ytd[r["id_cuenta"]] = _f(r["vy"])
        vol_mtd[r["id_cuenta"]] = _f(r["vm"])

    ficha = _ficha_por_cuenta(operador, _FICHA, nivel_1, nivel_3, referido)
    clientes = [{
        "id_cuenta": idc,
        "denominacion": (ficha.get(idc, {}).get("denominacion") or "—"),
        "aum": _cv(aum.get(idc, 0.0), factor),
        "volumen_ytd": _cv(vol_ytd.get(idc, 0.0), factor),
        "ficha": {k: ficha.get(idc, {}).get(k) for k in _FICHA},
    } for idc in ids]
    clientes.sort(key=lambda x: x["aum"], reverse=True)
    return {
        "operador": operador, "moneda": moneda,
        "resumen": {
            "aum_gestionado": _cv(sum(aum.get(idc, 0.0) for idc in ids), factor),
            "n_clientes": len(ids),
            "volumen_mtd": _cv(sum(vol_mtd.values()), factor),
            "volumen_ytd": _cv(sum(vol_ytd.values()), factor),
        },
        "clientes": clientes,
    }


def analisis_comercial(*, operador: str, dias_activa: int = 45, dias_dormida: int = 90,
                       moneda: str = "ARS", nivel_1: str | None = None,
                       nivel_3: str | None = None, referido: str | None = None) -> dict:
    ids = _ids_operador(operador, nivel_1, nivel_3, referido)
    if not ids and operador != "__todos__":
        return {"operador": operador, "dias_activa": dias_activa,
                "dias_dormida": dias_dormida, "clientes": []}
    hoy = _hoy_art()
    factor = _factor_usd(moneda)
    factor_cupo = _factor_usd("USD")  # cupo SIEMPRE en USD al MEP del día
    aum = _aum_por_cuenta_sql(operador, nivel_1, nivel_3, referido)
    year_start = date(hoy.year, 1, 1).isoformat()
    month_start = hoy.replace(day=1).isoformat()

    # Última operación EVER por cuenta (Operaciones, fuente de verdad).
    p: dict = {}
    scope = _scope_cuentas(operador, p, nivel_1, nivel_3, referido)
    ult_op = {r["id_cuenta"]: _iso(r["ult"]) for r in _q(
        f"SELECT id_cuenta, max(concertacion) AS ult FROM operaciones WHERE {scope} "
        f"GROUP BY id_cuenta", p) if r["ult"] is not None}

    ficha = _ficha_por_cuenta(operador, _ANALISIS, nivel_1, nivel_3, referido)
    p_cupo: dict = {}
    cupos = {r["id_cuenta"]: r for r in _q(
        "SELECT id_cuenta, cupo_transaccional_ars, cupo_usado_ars FROM comitentes "
        f"WHERE {_comitentes_where(operador, p_cupo, nivel_1, nivel_3, referido)}", p_cupo)}

    clientes = []
    for idc in ids:
        ult = ult_op.get(idc)
        dias = (hoy - date.fromisoformat(ult)).days if ult else None
        dias_win = dias if (dias is not None and dias <= dias_dormida) else None
        est = estado_comercial(dias_win, ult is not None, dias_activa, dias_dormida)
        f = ficha.get(idc, {})
        cupo = cupos.get(idc, {})
        trans, usado = cupo.get("cupo_transaccional_ars"), cupo.get("cupo_usado_ars")
        clientes.append({
            "id_cuenta": idc, "denominacion": f.get("denominacion") or "—",
            "aum": _cv(aum.get(idc, 0.0), factor), "ultima_op": ult, "dias_sin_operar": dias,
            "estado": est, "opero_ytd": bool(ult) and ult >= year_start,
            "opero_mtd": bool(ult) and ult >= month_start,
            "cupo_transaccional_usd": (_cv(float(trans), factor_cupo)
                                       if trans is not None and factor_cupo else None),
            "cupo_usado_usd": (_cv(float(usado), factor_cupo)
                               if usado is not None and factor_cupo else None),
            **{n: f.get(n) for n in _ANALISIS if n != "denominacion"},
        })
    clientes.sort(key=lambda x: x["aum"], reverse=True)
    return {"operador": operador, "dias_activa": dias_activa,
            "dias_dormida": dias_dormida, "clientes": clientes}


def actividad_historica(*, operador: str, desde: str | None = None,
                        hasta: str | None = None, moneda: str = "ARS") -> dict:
    """Serie mensual de cuentas activas desde la tabla actividad_mensual (snapshot
    point-in-time, ya espejado). operador == __todos__ → toda la mesa."""
    factor = _factor_usd(moneda)
    where = "1=1"
    p: dict = {}
    if operador != "__todos__":
        where += " AND operador_email = %(op)s"
        p["op"] = operador
    if desde:
        where += " AND year_month >= %(desde)s"
        p["desde"] = desde
    if hasta:
        where += " AND year_month <= %(hasta)s"
        p["hasta"] = hasta
    serie = [{
        "year_month": r["year_month"], "n_activas": r["n_activas"],
        "volumen": _cv(_f(r["volumen_ars"]), factor),
    } for r in _q(
        f"SELECT year_month, count(*) AS n_activas, SUM(volumen_ars) AS volumen_ars "
        f"FROM actividad_mensual WHERE {where} GROUP BY year_month ORDER BY year_month", p)]
    return {"operador": operador, "serie": serie}


# ── INFORME (global, transversal a la mesa) ──────────────────────────────────
def _fin_de_mes(anio: int, mes: int) -> date:
    """Último día del mes (date). fecha_alta_legajo es date en SQL → comparación por día."""
    ini_sig = date(anio + 1, 1, 1) if mes == 12 else date(anio, mes + 1, 1)
    return ini_sig - timedelta(days=1)


def informe_cuentas_por_segmento(*, hasta: str | None = None,
                                 operador: str | None = None) -> dict:
    hoy = _hoy_art()
    anio, mes = (int(hasta[:4]), int(hasta[5:7])) if hasta else (hoy.year, hoy.month)
    corte = _fin_de_mes(anio, mes)
    where = "estado = 'Activa' AND fecha_alta_legajo <= %(corte)s"
    p: dict = {"corte": corte}
    if operador:
        where += " AND operador_email = %(op)s"
        p["op"] = operador
    segmentos = [{"segmento": r["segmento"], "n": r["n"]} for r in _q(
        f"SELECT COALESCE(nivel_1, '(sin segmentar)') AS segmento, count(*) AS n "
        f"FROM comitentes WHERE {where} GROUP BY COALESCE(nivel_1, '(sin segmentar)') "
        f"ORDER BY n DESC", p)]
    fa = _q("SELECT min(fecha_alta_legajo) AS f FROM comitentes "
            "WHERE fecha_alta_legajo IS NOT NULL")[0]["f"]
    mes_min = f"{fa.year:04d}-{fa.month:02d}" if fa else f"{hoy.year:04d}-{hoy.month:02d}"
    return {
        "mes": f"{anio:04d}-{mes:02d}", "mes_min": mes_min,
        "mes_actual": f"{hoy.year:04d}-{hoy.month:02d}",
        "total": sum(s["n"] for s in segmentos), "segmentos": segmentos,
    }


def _rollup_por_cuenta(mes_start: str, scope: str | None, p: dict) -> dict[str, dict]:
    """{id_cuenta: {vol_total, vol_mes, n_ops, ar_total, ar_mes}} en vivo (reemplaza
    ComercialCache). vol/n_ops de negocio_movimientos (cats), arancel de operaciones."""
    p["cats"] = list(_CATS_VOLUMEN)
    p["mes"] = mes_start
    w_vol = "unidad IS DISTINCT FROM 'USDL' AND categoria = ANY(%(cats)s)"
    w_ar = "arancel > 0 AND etapa IS DISTINCT FROM 'solicitud'"
    if scope:
        w_vol += f" AND {scope}"
        w_ar += f" AND {scope}"
    rows = _q(
        f"WITH vol AS (SELECT id_cuenta, "
        f"  SUM({_PESIF}) AS vol_total, "
        f"  SUM(CASE WHEN fecha >= %(mes)s THEN {_PESIF} ELSE 0 END) AS vol_mes, "
        f"  count(*) AS n_ops FROM negocio_movimientos WHERE {w_vol} GROUP BY id_cuenta), "
        f"ar AS (SELECT id_cuenta, SUM(arancel) AS ar_total, "
        f"  SUM(CASE WHEN concertacion >= %(mes)s THEN arancel ELSE 0 END) AS ar_mes "
        f"  FROM operaciones WHERE {w_ar} GROUP BY id_cuenta) "
        f"SELECT COALESCE(v.id_cuenta, a.id_cuenta) AS id_cuenta, "
        f"  COALESCE(v.vol_total,0) AS vol_total, COALESCE(v.vol_mes,0) AS vol_mes, "
        f"  COALESCE(v.n_ops,0) AS n_ops, COALESCE(a.ar_total,0) AS ar_total, "
        f"  COALESCE(a.ar_mes,0) AS ar_mes "
        f"FROM vol v FULL OUTER JOIN ar a ON v.id_cuenta = a.id_cuenta", p)
    return {r["id_cuenta"]: r for r in rows if r["id_cuenta"]}


def _ticket(vol: float, n: int) -> float:
    return round(vol / n, 2) if n else 0.0


def informe_comercial(*, moneda: str = "ARS") -> dict:
    hoy = _hoy_art()
    factor = _factor_usd(moneda)
    mes_start = hoy.replace(day=1).isoformat()
    por_cuenta = _rollup_por_cuenta(mes_start, None, {})

    detalle = {r["id_cuenta"]: r for r in _q(
        "SELECT c.id_cuenta, c.operador_email, o.nombre AS operador_nombre, c.nivel_1 "
        "FROM comitentes c LEFT JOIN operadores o ON o.email = c.operador_email "
        "WHERE c.estado = 'Activa'")}

    ops: dict[str, dict] = {}
    segs: dict[str, dict] = {}
    for idc, agg in por_cuenta.items():
        info = detalle.get(idc)
        # Cuenta que NO figura en Comitentes Activa (no-cliente: propia/inactiva/
        # cancelada) → fuera del informe. Mismo criterio que la versión Mongo
        # (comercial.informe_comercial). `detalle` ya está en memoria → skip gratis.
        if info is None:
            continue
        key = (info.get("operador_email") or "").strip().lower() or "(sin operador)"
        o = ops.get(key)
        if o is None:
            o = ops[key] = {
                "operador_email": info.get("operador_email"),
                "operador_nombre": (info.get("operador_nombre") or info.get("operador_email")
                                    or "(sin operador)"),
                "vol_total": 0.0, "vol_mes": 0.0, "ar_total": 0.0, "ar_mes": 0.0, "n_ops": 0,
            }
        for k in ("vol_total", "vol_mes", "ar_total", "ar_mes"):
            o[k] += _f(agg[k])
        o["n_ops"] += int(agg["n_ops"] or 0)

        seg = info.get("nivel_1") or "(sin segmentar)"
        s = segs.get(seg)
        if s is None:
            s = segs[seg] = {"segmento": seg, "ar_total": 0.0, "ar_mes": 0.0,
                             "vol_total": 0.0, "n_ops": 0, "n_cuentas": 0}
        s["ar_total"] += _f(agg["ar_total"])
        s["ar_mes"] += _f(agg["ar_mes"])
        s["vol_total"] += _f(agg["vol_total"])
        s["n_ops"] += int(agg["n_ops"] or 0)
        if _f(agg["ar_total"]) > 0:
            s["n_cuentas"] += 1

    for o in ops.values():
        for k in ("vol_total", "vol_mes", "ar_total", "ar_mes"):
            o[k] = _cv(o[k], factor)
    comerciales = sorted(ops.values(), key=lambda x: x["vol_total"], reverse=True)
    for i, o in enumerate(comerciales, 1):
        o["rank"] = i
        o["ticket_promedio"] = _ticket(o["vol_total"], o["n_ops"])
    for s in segs.values():
        for k in ("ar_total", "ar_mes", "vol_total"):
            s[k] = _cv(s[k], factor)
    segmentos = sorted(segs.values(), key=lambda x: x["ar_total"], reverse=True)
    for s in segmentos:
        s["ticket_promedio"] = _ticket(s["vol_total"], s["n_ops"])
    return {"mes_actual": f"{hoy.year:04d}-{hoy.month:02d}",
            "comerciales": comerciales, "aranceles_segmento": segmentos}


def informe_aranceles_segmento(*, operador: str, moneda: str = "ARS") -> dict:
    hoy = _hoy_art()
    factor = _factor_usd(moneda)
    mes_start = hoy.replace(day=1).isoformat()
    cuentas = {r["id_cuenta"]: (r["nivel_1"] or "(sin segmentar)") for r in _q(
        "SELECT id_cuenta, nivel_1 FROM comitentes WHERE operador_email = %(op)s "
        "AND estado = 'Activa'", {"op": operador})}
    if not cuentas:
        return {"operador": operador, "aranceles_segmento": []}
    scope = "id_cuenta = ANY(%(ids)s)"
    por_cuenta = _rollup_por_cuenta(mes_start, scope, {"ids": list(cuentas)})
    segs: dict[str, dict] = {}
    for idc, agg in por_cuenta.items():
        seg = cuentas.get(idc, "(sin segmentar)")
        s = segs.get(seg)
        if s is None:
            s = segs[seg] = {"segmento": seg, "ar_total": 0.0, "ar_mes": 0.0,
                             "vol_total": 0.0, "n_ops": 0, "n_cuentas": 0}
        s["vol_total"] += _f(agg["vol_total"])
        s["n_ops"] += int(agg["n_ops"] or 0)
        s["ar_total"] += _f(agg["ar_total"])
        s["ar_mes"] += _f(agg["ar_mes"])
        if _f(agg["ar_total"]) > 0:
            s["n_cuentas"] += 1
    out = sorted(segs.values(), key=lambda x: x["ar_total"], reverse=True)
    for s in out:
        for k in ("ar_total", "ar_mes", "vol_total"):
            s[k] = _cv(s[k], factor)
        s["ticket_promedio"] = _ticket(s["vol_total"], s["n_ops"])
    return {"operador": operador, "aranceles_segmento": out}


def informe_segmento_detalle(*, segmento: str | None = None, operador: str | None = None,
                             moneda: str = "ARS") -> dict:
    hoy = _hoy_art()
    factor = _factor_usd(moneda)
    mes_start = hoy.replace(day=1).isoformat()
    where = "c.estado = 'Activa'"
    p: dict = {}
    if not segmento or segmento == "todos":
        pass
    elif segmento == "(sin segmentar)":
        where += " AND c.nivel_1 IS NULL"
    else:
        where += " AND c.nivel_1 = %(seg)s"
        p["seg"] = segmento
    if operador:
        where += " AND c.operador_email = %(op)s"
        p["op"] = operador
    detalle = {r["id_cuenta"]: r["denominacion"] for r in _q(
        f"SELECT c.id_cuenta, u.denominacion FROM comitentes c "
        f"LEFT JOIN cuentas u ON u.id_cuenta = c.id_cuenta WHERE {where}", p)}
    ids = list(detalle)
    if not ids:
        return {"segmento": segmento or "todos", "n_clientes": 0,
                "clientes": [], "operaciones": []}

    pa = {"ids": ids, "mes": mes_start}
    clientes = []
    for r in _q("SELECT id_cuenta, SUM(arancel) AS ar_total, "
                "SUM(CASE WHEN concertacion >= %(mes)s THEN arancel ELSE 0 END) AS ar_mes "
                "FROM operaciones WHERE id_cuenta = ANY(%(ids)s) AND arancel > 0 "
                "AND etapa IS DISTINCT FROM 'solicitud' GROUP BY id_cuenta", pa):
        idc = r["id_cuenta"]
        clientes.append({
            "id_cuenta": idc, "denominacion": detalle.get(idc) or "—",
            "arancel_total": _cv(_f(r["ar_total"]), factor),
            "arancel_mes": _cv(_f(r["ar_mes"]), factor),
        })
    clientes.sort(key=lambda x: x["arancel_total"], reverse=True)

    operaciones = []
    for r in _q("SELECT concertacion, id_cuenta, boleto, instrumento, operacion, "
                "tipo_operacion, bruto, moneda, arancel FROM operaciones "
                "WHERE id_cuenta = ANY(%(ids)s) AND arancel > 0 "
                "AND etapa IS DISTINCT FROM 'solicitud' "
                "ORDER BY concertacion DESC, boleto DESC LIMIT 500", {"ids": ids}):
        idc = r["id_cuenta"]
        operaciones.append({
            "fecha": _iso(r["concertacion"]), "id_cuenta": idc,
            "denominacion": detalle.get(idc) or "—", "comprobante": r["boleto"],
            "ticker": r["instrumento"], "categoria": r["operacion"],
            "op": r["tipo_operacion"],
            "importe": _f(r["bruto"]) if r["bruto"] is not None else None,
            "moneda": r["moneda"], "arancel": _cv(_f(r["arancel"]), factor),
        })
    return {"segmento": segmento or "todos", "n_clientes": len(clientes),
            "clientes": clientes, "operaciones": operaciones}
