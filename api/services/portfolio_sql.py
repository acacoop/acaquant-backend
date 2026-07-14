"""api/services/portfolio_sql.py — vista PORTFOLIO / AuM leyendo de Postgres (Supabase).

Servicio PURO (sin FastAPI). Espejo SQL de `api/services/portfolio.py`: MISMO shape de
salida → dual-run (carteras.py elige Mongo o SQL por `?_engine` / `PORTFOLIO_SQL`). El
frontend de /aum no cambia; solo cambia de dónde salen los datos.

Fuente: **`portafolio.tenencia`** (fechas reales corregidas, regla H1). Solo cuentan las
filas marcadas `aum = 'si'` (columna seteada por `scripts/marcar_aum_tenencia` con el
filtro probado `_aum_filters`). La columna de fecha es `fecha` (se expone como
`fecha_snapshot` en el SELECT para no tocar el resto). La 255 SÍ aparece en SQL (se sacó
la exclusión de la vista, a pedido del usuario; el path Mongo la sigue ocultando). `cartera`/`emisor`/`ticker` se
enriquecen con JOIN a `assets` (igual que el path Mongo enriquece con Valuaciones.Assets).
`tipo` no existe en tenencia → va vacío (no se usa para los totales).
"""
from __future__ import annotations

from datetime import datetime

from api.services._sql import _f, _q

_SRC = "portafolio.tenencia"   # fuente única del AuM SQL (filtrar siempre por aum='si')


def _dt(d):
    """date → datetime a medianoche (mismo contrato que portfolio._fecha_dt). None si None."""
    return datetime(d.year, d.month, d.day) if d is not None else None


def _iso(d):
    """date → 'YYYY-MM-DD' string. None → None. (acepta str ya formateado)."""
    if d is None:
        return None
    return d.isoformat() if hasattr(d, "isoformat") else str(d)[:10]


def _max_snap() -> object | None:
    return _q(f"SELECT max(fecha) AS f FROM {_SRC} WHERE aum = 'si'")[0]["f"]


def listar_aum(id_cuenta: str | None = None, unidad: str | None = None,
               cuenta: str | None = None, desde: str | None = None, hasta: str | None = None,
               ultimo: bool = False, scope: tuple[str, ...] | None = None) -> list:
    conds: list[str] = ["aum = 'si'"]
    p: dict = {}
    if ultimo:
        last = _max_snap()
        if last is None:
            return []
        conds.append("fecha = %(f)s")
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
            conds.append("fecha >= %(desde)s")
            p["desde"] = desde
        if hasta:
            conds.append("fecha <= %(hasta)s")
            p["hasta"] = hasta
    rows = _q(f"SELECT fecha AS fecha_snapshot, id_cuenta, unidad, cantidad, cuenta, precio, "
              f"valuacion FROM {_SRC} WHERE {' AND '.join(conds)}", p)
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
        f"SELECT id_cuenta, max(cuenta) AS cuenta FROM {_SRC} "
        f"WHERE fecha = %(f)s AND aum = 'si'{sc} GROUP BY id_cuenta ORDER BY id_cuenta", p)]


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
    """Último `fecha` <= fecha_pedida con aum='si' (= _resolve_fecha_snapshot). date o None."""
    r = _q(f"SELECT max(fecha) AS f FROM {_SRC} WHERE aum = 'si' AND fecha <= %(f)s",
           {"f": fecha_pedida})
    return r[0]["f"]


def fci_serie(desde: str | None = None, hasta: str | None = None,
              cuenta_filter: str = "todas", scope: tuple[str, ...] | None = None) -> list:
    conds = [f"a.cartera IN {_FCI_CARTERAS}", "v.aum = 'si'"]
    p: dict = {}
    frag, fp = _cuenta_filter_sql(cuenta_filter)
    if frag:
        conds.append(frag)
        p.update(fp)
    if scope is not None:
        conds.append("v.id_cuenta = ANY(%(scope)s)")
        p["scope"] = list(scope)
    if desde:
        conds.append("v.fecha >= %(desde)s")
        p["desde"] = desde
    if hasta:
        conds.append("v.fecha <= %(hasta)s")
        p["hasta"] = hasta
    rows = _q(f"SELECT v.fecha AS fecha, "
              f"COALESCE(NULLIF(a.emisor, ''), 'SIN EMISOR') AS emisor, SUM(v.valuacion) AS val "
              f"FROM {_SRC} v JOIN portafolio.assets a ON a.unidad = v.unidad WHERE {' AND '.join(conds)} "
              f"GROUP BY v.fecha, emisor ORDER BY v.fecha", p)
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
    conds = [f"a.cartera IN {_FCI_CARTERAS}", "v.fecha = %(f)s", "v.aum = 'si'"]
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
              f"FROM {_SRC} v JOIN portafolio.assets a ON a.unidad = v.unidad WHERE {' AND '.join(conds)}", p)
    return [{
        "unidad": r["unidad"], "emisor": r["emisor"], "ticker": r["ticker"],
        "cuenta": r["cuenta"] or "", "id_cuenta": r["id_cuenta"] or "",
        "valuacion": _f(r["valuacion"]) or 0.0, "cantidad": _f(r["cantidad"]) or 0.0,
    } for r in rows]


def total_serie(desde: str | None = None, hasta: str | None = None,
                cuenta_filter: str = "todas", moneda: str = "ARS",
                scope: tuple[str, ...] | None = None) -> dict:
    conds = ["v.aum = 'si'"]
    p: dict = {}
    frag, fp = _cuenta_filter_sql(cuenta_filter)
    if frag:
        conds.append(frag)
        p.update(fp)
    if scope is not None:
        conds.append("v.id_cuenta = ANY(%(scope)s)")
        p["scope"] = list(scope)
    if desde:
        conds.append("v.fecha >= %(desde)s")
        p["desde"] = desde
    if hasta:
        conds.append("v.fecha <= %(hasta)s")
        p["hasta"] = hasta
    rows = _q(f"SELECT v.fecha AS fecha, COALESCE(NULLIF(a.cartera, ''), 'OTROS') AS cartera, "
              f"SUM(v.valuacion) AS val FROM {_SRC} v LEFT JOIN portafolio.assets a ON a.unidad = v.unidad "
              f"WHERE {' AND '.join(conds)} "
              f"GROUP BY v.fecha, COALESCE(NULLIF(a.cartera, ''), 'OTROS') ORDER BY v.fecha", p)
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
    conds = ["v.fecha = %(f)s", "v.aum = 'si'"]
    p: dict = {"f": fecha}
    frag, fp = _cuenta_filter_sql(cuenta_filter)
    if frag:
        conds.append(frag)
        p.update(fp)
    if scope is not None:
        conds.append("v.id_cuenta = ANY(%(scope)s)")
        p["scope"] = list(scope)
    rows = _q(f"SELECT v.unidad, COALESCE(NULLIF(a.cartera, ''), 'OTROS') AS cartera, "
              f"NULL::text AS tipo, v.cuenta, v.id_cuenta, v.valuacion, v.cantidad "
              f"FROM {_SRC} v LEFT JOIN portafolio.assets a ON a.unidad = v.unidad "
              f"WHERE {' AND '.join(conds)}", p)
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
    base = ["aum = 'si'"]
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
                  f"FROM {_SRC} WHERE {' AND '.join(base)} AND fecha = %(f)s "
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
