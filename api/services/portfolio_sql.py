"""api/services/portfolio_sql.py — vista PORTFOLIO / AuM leyendo de Postgres (Supabase).

Servicio PURO (sin FastAPI). Espejo SQL de `api/services/portfolio.py`. Mismo shape de salida
→ dual-run + comparación (scripts/compare_portfolio_sql_vs_mongo.py). Ver
docs/MIGRACION_MONGO_SUPABASE.md.

Reglas: AuM "último snapshot" = `max(fecha_snapshot)` GLOBAL; exclusión de la cuenta 255 de la
VISTA (no de la persistencia) con `id_cuenta IS DISTINCT FROM '255'`; valuación ya viene
calculada en `aum.valuacion` (ARS); Decimal→float; fecha date→ISO. El AuM excluye sus filtros
de persistencia (jobs/_aum_filters) en la escritura, no acá.

Estado: Chunk 1 (raw: listar_aum, listar_cuentas). Agregados (total_*, fci_*) y renta fija/CER
+ PnL en progreso.
"""
from __future__ import annotations

from datetime import datetime

from psycopg.rows import dict_row

from core.postgres import get_pool


def _q(sql: str, params: dict | None = None) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


def _f(x):
    return float(x) if x is not None else None


def _dt(d):
    """date → datetime a medianoche (mismo contrato que portfolio._fecha_dt: AumAPI servía
    `fecha` como datetime). None si d es None."""
    return datetime(d.year, d.month, d.day) if d is not None else None


def _iso(d):
    """date → 'YYYY-MM-DD' string. None → None. (acepta str ya formateado)."""
    if d is None:
        return None
    return d.isoformat() if hasattr(d, "isoformat") else str(d)[:10]


def _max_snap() -> object | None:
    return _q("SELECT max(fecha_snapshot) AS f FROM aum")[0]["f"]


def listar_aum(id_cuenta: str | None = None, unidad: str | None = None,
               cuenta: str | None = None, desde: str | None = None, hasta: str | None = None,
               ultimo: bool = False, scope: tuple[str, ...] | None = None) -> list:
    conds: list[str] = []
    p: dict = {}
    if ultimo:
        last = _max_snap()
        if last is None:
            return []
        conds.append("fecha_snapshot = %(f)s")
        p["f"] = last
    if id_cuenta:
        conds.append("id_cuenta = %(idc)s")
        p["idc"] = id_cuenta
    elif scope is not None:
        conds.append("id_cuenta = ANY(%(scope)s)")
        p["scope"] = list(scope)
    if unidad:
        conds.append("unidad = %(u)s")
        p["u"] = unidad
    if cuenta:
        conds.append("cuenta = %(c)s")
        p["c"] = cuenta
    if not ultimo and (desde or hasta):
        if desde:
            conds.append("fecha_snapshot >= %(desde)s")
            p["desde"] = desde
        if hasta:
            conds.append("fecha_snapshot <= %(hasta)s")
            p["hasta"] = hasta
    where = " AND ".join(conds) if conds else "TRUE"
    rows = _q(f"SELECT fecha_snapshot, id_cuenta, unidad, cantidad, cuenta, precio, valuacion "
              f"FROM aum WHERE {where}", p)
    return [{
        "fecha": _dt(r["fecha_snapshot"]), "id_cuenta": r["id_cuenta"], "unidad": r["unidad"],
        "cantidad": _f(r["cantidad"]), "cuenta": r["cuenta"], "precio": _f(r["precio"]),
        "valuacion": _f(r["valuacion"]),
    } for r in rows]


def listar_cuentas(scope: tuple[str, ...] | None = None) -> list[dict]:
    last = _max_snap()
    if last is None:
        return []
    p: dict = {"f": last}
    sc = ""
    if scope is not None:
        sc = " AND id_cuenta = ANY(%(scope)s)"
        p["scope"] = list(scope)
    return [{"id_cuenta": r["id_cuenta"], "cuenta": r["cuenta"]} for r in _q(
        f"SELECT id_cuenta, max(cuenta) AS cuenta FROM aum "
        f"WHERE fecha_snapshot = %(f)s{sc} GROUP BY id_cuenta ORDER BY id_cuenta", p)]


# ── helpers Chunk 2 (agregados AuM) ──────────────────────────────────────────
_FCI_CARTERAS = "('FCI', 'CARTERA FCI')"


def _mep_for_date(fecha_iso: str, cache: dict) -> float | None:
    """Último mep <= end-of-day(fecha) desde la tabla dolar (= get_mep_for_date). Cacheado."""
    if fecha_iso not in cache:
        try:
            eod = datetime.fromisoformat(fecha_iso + "T23:59:59")
        except (ValueError, TypeError):
            cache[fecha_iso] = None
            return None
        r = _q("SELECT mep FROM dolar WHERE mep IS NOT NULL AND timestamp <= %(e)s "
               "ORDER BY timestamp DESC LIMIT 1", {"e": eod})
        cache[fecha_iso] = (_f(r[0]["mep"]) if r and r[0]["mep"] is not None else None)
    return cache[fecha_iso]


def _cuenta_filter_sql(filtro: str, pfx: str = "v.") -> tuple[str, dict]:
    """Fragmento del filtro de cuenta (= match_cuenta_filter). `pfx` prefija las columnas."""
    if filtro == "productores":
        return (f"{pfx}id_cuenta IN (SELECT id_cuenta FROM comitentes "
                f"WHERE nivel_1 = 'PRODUCTORES')", {})
    if filtro == "accionistas":
        return (f"{pfx}cuenta IN (SELECT cuenta FROM accionistas)", {})
    if filtro == "sin_accionistas":
        return (f"{pfx}cuenta NOT IN (SELECT cuenta FROM accionistas)", {})
    if filtro == "cooperativas":
        return (f"{pfx}cuenta NOT IN (SELECT cuenta FROM accionistas) "
                f"AND {pfx}cuenta ~* '\\ycoop'", {})
    return ("", {})


def _resolve_snap(fecha_pedida: str):
    """Último fecha_snapshot <= fecha_pedida (= _resolve_fecha_snapshot). date o None."""
    r = _q("SELECT max(fecha_snapshot) AS f FROM aum WHERE fecha_snapshot <= %(f)s",
           {"f": fecha_pedida})
    return r[0]["f"]


def fci_serie(desde: str | None = None, hasta: str | None = None,
              cuenta_filter: str = "todas", scope: tuple[str, ...] | None = None) -> list:
    # Mongo: con filtro/scope excluye 255 (camino raw); sin filtro usa rollup (sin 255).
    excl = (cuenta_filter and cuenta_filter != "todas") or scope is not None
    conds = [f"a.cartera IN {_FCI_CARTERAS}"]
    p: dict = {}
    if excl:
        conds.append("v.id_cuenta IS DISTINCT FROM '255'")
    frag, fp = _cuenta_filter_sql(cuenta_filter)
    if frag:
        conds.append(frag)
        p.update(fp)
    if scope is not None:
        conds.append("v.id_cuenta = ANY(%(scope)s)")
        p["scope"] = list(scope)
    if desde:
        conds.append("v.fecha_snapshot >= %(desde)s")
        p["desde"] = desde
    if hasta:
        conds.append("v.fecha_snapshot <= %(hasta)s")
        p["hasta"] = hasta
    rows = _q(f"SELECT v.fecha_snapshot AS fecha, "
              f"COALESCE(NULLIF(a.emisor, ''), 'SIN EMISOR') AS emisor, SUM(v.valuacion) AS val "
              f"FROM aum v JOIN assets a ON a.unidad = v.unidad WHERE {' AND '.join(conds)} "
              f"GROUP BY v.fecha_snapshot, emisor ORDER BY v.fecha_snapshot", p)
    bucket: dict[str, dict] = {}
    for r in rows:
        f = _iso(r["fecha"])
        val = _f(r["val"]) or 0.0
        b = bucket.setdefault(f, {"total": 0.0, "por_emisor": {}})
        b["total"] += val
        b["por_emisor"][r["emisor"]] = b["por_emisor"].get(r["emisor"], 0.0) + val
    return [{"fecha": f, "total": v["total"], "por_emisor": v["por_emisor"]}
            for f, v in sorted(bucket.items())]


def fci_snapshot(fecha: str, cuenta_filter: str = "todas",
                 scope: tuple[str, ...] | None = None) -> list:
    conds = [f"a.cartera IN {_FCI_CARTERAS}", "v.fecha_snapshot = %(f)s",
             "v.id_cuenta IS DISTINCT FROM '255'"]
    p: dict = {"f": fecha}
    frag, fp = _cuenta_filter_sql(cuenta_filter)
    if frag:
        conds.append(frag)
        p.update(fp)
    if scope is not None:
        conds.append("v.id_cuenta = ANY(%(scope)s)")
        p["scope"] = list(scope)
    rows = _q(f"SELECT v.unidad, COALESCE(NULLIF(a.emisor, ''), 'SIN EMISOR') AS emisor, "
              f"COALESCE(a.ticker, '') AS ticker, v.cuenta, v.id_cuenta, v.valuacion, v.cantidad "
              f"FROM aum v JOIN assets a ON a.unidad = v.unidad WHERE {' AND '.join(conds)}", p)
    return [{
        "unidad": r["unidad"], "emisor": r["emisor"], "ticker": r["ticker"],
        "cuenta": r["cuenta"] or "", "id_cuenta": r["id_cuenta"] or "",
        "valuacion": _f(r["valuacion"]) or 0.0, "cantidad": _f(r["cantidad"]) or 0.0,
    } for r in rows]


def total_serie(desde: str | None = None, hasta: str | None = None,
                cuenta_filter: str = "todas", moneda: str = "ARS",
                scope: tuple[str, ...] | None = None) -> dict:
    conds = ["v.id_cuenta IS DISTINCT FROM '255'"]
    p: dict = {}
    frag, fp = _cuenta_filter_sql(cuenta_filter)
    if frag:
        conds.append(frag)
        p.update(fp)
    if scope is not None:
        conds.append("v.id_cuenta = ANY(%(scope)s)")
        p["scope"] = list(scope)
    if desde:
        conds.append("v.fecha_snapshot >= %(desde)s")
        p["desde"] = desde
    if hasta:
        conds.append("v.fecha_snapshot <= %(hasta)s")
        p["hasta"] = hasta
    rows = _q(f"SELECT v.fecha_snapshot AS fecha, COALESCE(NULLIF(a.cartera, ''), 'OTROS') AS cartera, "
              f"SUM(v.valuacion) AS val FROM aum v LEFT JOIN assets a ON a.unidad = v.unidad "
              f"WHERE {' AND '.join(conds)} GROUP BY v.fecha_snapshot, cartera "
              f"ORDER BY v.fecha_snapshot", p)
    mep_cache: dict = {}
    sin_mep: set = set()
    bucket: dict[str, dict] = {}
    for r in rows:
        f = _iso(r["fecha"])
        val = _f(r["val"]) or 0.0
        if moneda == "USD":
            mep = _mep_for_date(f, mep_cache)
            if mep:
                val = val / mep
            else:
                sin_mep.add(f)
        b = bucket.setdefault(f, {"total": 0.0, "por_cartera": {}})
        b["total"] += val
        b["por_cartera"][r["cartera"]] = b["por_cartera"].get(r["cartera"], 0.0) + val
    serie = []
    for f, v in sorted(bucket.items()):
        row = {"fecha": f, "total": v["total"], "por_cartera": v["por_cartera"]}
        if moneda == "USD":
            row["mep_used"] = mep_cache.get(f)
        serie.append(row)
    return {"serie": serie, "moneda": moneda, "fechas_sin_mep": sorted(sin_mep)}


def total_snapshot(fecha: str, cuenta_filter: str = "todas", moneda: str = "ARS",
                   scope: tuple[str, ...] | None = None) -> dict:
    conds = ["v.fecha_snapshot = %(f)s", "v.id_cuenta IS DISTINCT FROM '255'"]
    p: dict = {"f": fecha}
    frag, fp = _cuenta_filter_sql(cuenta_filter)
    if frag:
        conds.append(frag)
        p.update(fp)
    if scope is not None:
        conds.append("v.id_cuenta = ANY(%(scope)s)")
        p["scope"] = list(scope)
    rows = _q(f"SELECT v.unidad, COALESCE(NULLIF(a.cartera, ''), 'OTROS') AS cartera, "
              f"v.tipo_titulo AS tipo, v.cuenta, v.id_cuenta, v.valuacion, v.cantidad "
              f"FROM aum v LEFT JOIN assets a ON a.unidad = v.unidad WHERE {' AND '.join(conds)}", p)
    mep = None
    mep_missing = False
    if moneda == "USD":
        mep = _mep_for_date(fecha, {})
        mep_missing = mep is None
    out = []
    for r in rows:
        val = _f(r["valuacion"]) or 0.0
        if moneda == "USD" and mep:
            val = val / mep
        out.append({
            "unidad": r["unidad"], "cartera": r["cartera"], "tipo": r["tipo"] or "",
            "cuenta": r["cuenta"] or "", "id_cuenta": r["id_cuenta"] or "",
            "valuacion": val, "cantidad": _f(r["cantidad"]) or 0.0,
        })
    return {"docs": out, "moneda": moneda, "mep_used": mep, "mep_missing": mep_missing}


def total_diff(fecha_actual: str, fecha_anterior: str, moneda: str = "ARS",
               cuenta_filter: str = "todas", scope: tuple[str, ...] | None = None) -> dict:
    fa = _iso(_resolve_snap(fecha_actual)) or fecha_actual
    fant = _iso(_resolve_snap(fecha_anterior)) or fecha_anterior
    base = ["id_cuenta IS DISTINCT FROM '255'"]
    bp: dict = {}
    frag, fp = _cuenta_filter_sql(cuenta_filter, pfx="")
    if frag:
        base.append(frag)
        bp.update(fp)
    if scope is not None:
        base.append("id_cuenta = ANY(%(scope)s)")
        bp["scope"] = list(scope)

    def _agg(fecha: str) -> dict[str, dict]:
        p = {**bp, "f": fecha}
        rows = _q(f"SELECT id_cuenta, max(cuenta) AS cuenta, SUM(valuacion) AS saldo "
                  f"FROM aum WHERE {' AND '.join(base)} AND fecha_snapshot = %(f)s "
                  f"GROUP BY id_cuenta", p)
        return {r["id_cuenta"]: {"cuenta": r["cuenta"], "saldo": _f(r["saldo"]) or 0.0}
                for r in rows}

    map_act, map_ant = _agg(fa), _agg(fant)
    mep_act = mep_ant = None
    mep_missing_act = mep_missing_ant = False
    if moneda == "USD":
        mep_act = _mep_for_date(fa, {})
        mep_ant = _mep_for_date(fant, {})
        if mep_act:
            for r in map_act.values():
                r["saldo"] = r["saldo"] / mep_act
        else:
            mep_missing_act = True
        if mep_ant:
            for r in map_ant.values():
                r["saldo"] = r["saldo"] / mep_ant
        else:
            mep_missing_ant = True

    filas = []
    for cid in set(map_act) | set(map_ant):
        a = map_act.get(cid)
        n = map_ant.get(cid)
        saldo_act = a["saldo"] if a else None
        saldo_ant = n["saldo"] if n else None
        diff = (saldo_act or 0.0) - (saldo_ant or 0.0)
        filas.append({
            "id_cuenta": cid, "cuenta": (a or n or {}).get("cuenta", "") or "",
            "saldo_actual": saldo_act, "saldo_anterior": saldo_ant, "diff": diff,
            "es_nueva": saldo_ant is None, "es_cerrada": saldo_act is None,
        })
    filas.sort(key=lambda r: abs(r["diff"]), reverse=True)
    return {
        "fecha_actual_pedida": fecha_actual, "fecha_anterior_pedida": fecha_anterior,
        "fecha_actual_resuelta": fa, "fecha_anterior_resuelta": fant, "moneda": moneda,
        "mep_actual": mep_act, "mep_anterior": mep_ant,
        "mep_missing_actual": mep_missing_act, "mep_missing_anterior": mep_missing_ant,
        "filas": filas, "total_diff": sum(f["diff"] for f in filas), "n_total": len(filas),
        "n_nuevas": sum(1 for f in filas if f["es_nueva"]),
        "n_cerradas": sum(1 for f in filas if f["es_cerrada"]),
    }
