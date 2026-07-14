"""api/services/valuaciones_sql.py — espejo SQL de api/services/valuaciones.py.

Migración progresiva de la vista Portfolio (/valuaciones, dentro de CARTERAS) a SQL.
Lee `portafolio.tenencia` (fechas corregidas) filtrando `aum = 'si'` (mismas exclusiones
que el AuM real). Enriquece con la tabla `assets` (igual que el path Mongo con Assets).

Dual-run: el router elige Mongo o SQL por `?_engine=sql` / flag `VALUACIONES_SQL`.
Default Mongo → la vista en vivo NO cambia hasta probar con ?_engine=sql. Mismo shape de
salida que valuaciones.py para que el frontend no cambie.

Estado: posiciones_actuales, serie, mensual (vía cierres_fecha_data), variación.
Pendiente: consolidado (es un cache iterativo, ver jobs/consolidado_cuentas + tabla SQL).
"""
from __future__ import annotations

from api.services._sql import _q


def _f(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _iso(d) -> str | None:
    if d is None:
        return None
    return d.isoformat() if hasattr(d, "isoformat") else str(d)[:10]


def _vacio(id_cuenta: str, fecha: str | None) -> dict:
    return {"id_cuenta": id_cuenta, "fecha": fecha, "posiciones": [], "total": 0.0, "n": 0}


def _resolver_fecha(id_cuenta: str, fecha: str | None, asof: bool) -> str | None:
    """Misma lógica que valuaciones.posiciones_actuales: fecha exacta, asof (<=), o latest."""
    if fecha:
        ex = _q("SELECT 1 FROM portafolio.tenencia "
                "WHERE id_cuenta = %(c)s AND fecha = %(f)s AND aum = 'si' LIMIT 1",
                {"c": id_cuenta, "f": fecha})
        if ex:
            return fecha
        if not asof:
            return None
        r = _q("SELECT max(fecha) AS f FROM portafolio.tenencia "
               "WHERE id_cuenta = %(c)s AND fecha <= %(f)s AND aum = 'si'",
               {"c": id_cuenta, "f": fecha})
        return _iso(r[0]["f"]) if r and r[0]["f"] is not None else None
    r = _q("SELECT max(fecha) AS f FROM portafolio.tenencia "
           "WHERE id_cuenta = %(c)s AND aum = 'si'", {"c": id_cuenta})
    return _iso(r[0]["f"]) if r and r[0]["f"] is not None else None


def cierres_fecha_data(id_cuenta: str, cartera: str | None = None) -> list[dict]:
    """Cierres por fecha (valuación total + n posiciones) desde SQL — mismo shape que el
    `pipeline_fechas` de valuaciones._valuacion_mensual: [{_id: 'YYYY-MM-DD', valuacion, n}]
    ordenado asc. Lo consume la tabla mensual (TEA/TWR) reusando el mismo cálculo."""
    conds = ["id_cuenta = %(c)s", "aum = 'si'"]
    p: dict = {"c": id_cuenta}
    if cartera:
        conds.append("cartera = %(cart)s")
        p["cart"] = cartera
    rows = _q(f"SELECT fecha, SUM(valuacion) AS valuacion, COUNT(*) AS n "
              f"FROM portafolio.tenencia WHERE {' AND '.join(conds)} "
              f"GROUP BY fecha ORDER BY fecha", p)
    return [{"_id": _iso(r["fecha"]), "valuacion": _f(r["valuacion"]), "n": int(r["n"])}
            for r in rows]


def serie_valor_cuenta(id_cuenta: str, desde: str | None = None,
                       hasta: str | None = None) -> dict:
    """Serie diaria del valor total — SQL. Mismo shape que valuaciones.serie_valor_cuenta:
    suma `valuacion` por fecha (aum='si'), una fila por día con valor + n posiciones."""
    conds = ["id_cuenta = %(c)s", "aum = 'si'"]
    p: dict = {"c": id_cuenta}
    if desde:
        conds.append("fecha >= %(d)s")
        p["d"] = desde
    if hasta:
        conds.append("fecha <= %(h)s")
        p["h"] = hasta
    rows = _q(f"SELECT fecha, ROUND(SUM(valuacion), 2) AS valuacion, COUNT(*) AS n "
              f"FROM portafolio.tenencia WHERE {' AND '.join(conds)} "
              f"GROUP BY fecha ORDER BY fecha", p)
    serie = [{"fecha": _iso(r["fecha"]), "valuacion": _f(r["valuacion"]), "n": int(r["n"])}
             for r in rows]
    return {
        "id_cuenta": id_cuenta, "desde": desde, "hasta": hasta, "serie": serie,
        "ultimo": serie[-1] if serie else None,
        "primero": serie[0] if serie else None,
    }


def posiciones_actuales(id_cuenta: str, fecha: str | None = None,
                        cartera: str | None = None, asof: bool = False) -> dict:
    """Posiciones de un fecha_snapshot dado — SQL. Mismo shape que valuaciones.py.

    `tipo` (tipoTitulo) no existe en tenencia → None. `vencimiento` no está en la tabla
    assets SQL → None. El resto (ticker/emisor/clase/cartera/calificación) sale del JOIN.
    """
    fecha = _resolver_fecha(id_cuenta, fecha, asof)
    if fecha is None:
        return _vacio(id_cuenta, fecha)

    p = {"c": id_cuenta, "f": fecha}
    cart = ""
    if cartera:
        cart = " AND a.cartera = %(cart)s"
        p["cart"] = cartera
    rows = _q(
        "SELECT v.unidad, v.cantidad, v.precio, v.valuacion, "
        "a.cartera, a.clase_activo, a.ticker, a.emisor, a.calificacion "
        "FROM portafolio.tenencia v LEFT JOIN portafolio.assets a ON a.unidad = v.unidad "
        f"WHERE v.id_cuenta = %(c)s AND v.fecha = %(f)s AND v.aum = 'si'{cart}", p)

    by_unidad: dict[str, dict] = {}
    for r in rows:
        unidad = r["unidad"]
        if not unidad:
            continue
        st = by_unidad.setdefault(unidad, {
            "unidad": unidad, "cantidad": 0.0, "precio": _f(r["precio"]), "valuacion": 0.0,
            "cartera": r["cartera"], "clase_activo": r["clase_activo"],
            "ticker": r["ticker"], "emisor": r["emisor"], "calificacion": r["calificacion"],
        })
        st["cantidad"] += _f(r["cantidad"])
        st["valuacion"] += _f(r["valuacion"])
        st["precio"] = _f(r["precio"])

    ordenadas = sorted(by_unidad.values(), key=lambda x: -x["valuacion"])
    total = sum(x["valuacion"] for x in ordenadas)
    return {
        "id_cuenta": id_cuenta,
        "fecha": fecha,
        "posiciones": [
            {
                "unidad":       x["unidad"],
                "ticker":       x["ticker"] or x["unidad"],
                "emisor":       x["emisor"] or "-",
                "clase_activo": x["clase_activo"] or "-",
                "cartera":      x["cartera"] or "",
                "calificacion": x["calificacion"] or "-",
                "vencimiento":  None,
                "tipo":         None,
                "cantidad":     round(x["cantidad"], 4),
                "precio":       round(x["precio"], 4),
                "valuacion":    round(x["valuacion"], 2),
                "share":        round(x["valuacion"] / total * 100, 2) if total else None,
            }
            for x in ordenadas
        ],
        "total": round(total, 2),
        "n":     len(ordenadas),
    }


def variacion_titulos(id_cuenta: str, fecha: str) -> dict:
    """Espejo SQL de valuaciones.variacion_titulos — lee `portafolio.tenencia`.

    Descompone la variación del portfolio entre `fecha` (cierre de mes) y el cierre
    del mes anterior, por unidad, separando efecto MERCADO (precio) de OPERADO
    (cantidad). Cash → `otros`. Mismo shape que el path Mongo (que quedó roto al
    eliminarse Valuaciones.AuM 2026-06-15 → esta es la fuente real).

        delta_mercado = (pe_act − pe_prev) × cantidad_prev
        delta_operado = (cant_act − cant_prev) × pe_act
        delta_total   = val_act − val_prev = delta_mercado + delta_operado

    `tipo` (tipoTitulo) no existe en SQL → None; el cash se detecta por `_es_cash`
    sobre la unidad (mismo criterio canónico que el resto del código).
    """
    from api.services.valuaciones import _es_cash

    base = {"id_cuenta": id_cuenta, "fecha": fecha, "fecha_anterior": None,
            "filas": [], "otros": None, "totales": None}

    frows = _q("SELECT DISTINCT fecha FROM portafolio.tenencia "
               "WHERE id_cuenta = %(c)s AND aum = 'si' ORDER BY fecha", {"c": id_cuenta})
    fechas = [_iso(r["fecha"]) for r in frows if r["fecha"] is not None]
    if fecha not in fechas:
        return {**base, "error": "fecha sin snapshot para la cuenta"}

    # Comparar contra el CIERRE DEL MES ANTERIOR (último snapshot del mes calendario),
    # no contra el snapshot cronológico previo (hay snapshots diarios). Igual que Mongo.
    cierres: dict[str, str] = {}
    for f in fechas:  # asc → el último del mes gana
        cierres[f[:7]] = f
    cierres_ord = [cierres[m] for m in sorted(cierres)]
    if fecha in cierres_ord:
        idx = cierres_ord.index(fecha)
        if idx == 0:
            return {**base, "error": "no hay mes anterior — es el primer mes"}
        fecha_prev = cierres_ord[idx - 1]
    else:
        idx = fechas.index(fecha)
        if idx == 0:
            return {**base, "error": "no hay snapshot anterior"}
        fecha_prev = fechas[idx - 1]

    def _cargar(f: str) -> dict[str, dict]:
        rows = _q("SELECT unidad, SUM(cantidad) AS cantidad, SUM(valuacion) AS valuacion "
                  "FROM portafolio.tenencia WHERE id_cuenta = %(c)s AND fecha = %(f)s "
                  "AND aum = 'si' GROUP BY unidad", {"c": id_cuenta, "f": f})
        return {r["unidad"]: {"cantidad": _f(r["cantidad"]), "valuacion": _f(r["valuacion"])}
                for r in rows if r["unidad"]}

    prev = _cargar(fecha_prev)
    act = _cargar(fecha)

    filas: list[dict] = []
    otros = {"delta_mercado": 0.0, "delta_operado": 0.0, "delta_total": 0.0,
             "val_anterior": 0.0, "val_actual": 0.0, "n": 0}

    for u in set(prev) | set(act):
        p = prev.get(u)
        a = act.get(u)
        cant_prev = p["cantidad"] if p else 0.0
        cant_act = a["cantidad"] if a else 0.0
        val_prev = p["valuacion"] if p else 0.0
        val_act = a["valuacion"] if a else 0.0
        delta_total = val_act - val_prev
        if cant_prev != 0 and cant_act != 0:
            pe_prev = val_prev / cant_prev
            pe_act = val_act / cant_act
            delta_mercado = (pe_act - pe_prev) * cant_prev
            delta_operado = (cant_act - cant_prev) * pe_act
        else:
            delta_mercado = 0.0
            delta_operado = delta_total

        if _es_cash(u, None):
            otros["delta_mercado"] += delta_mercado
            otros["delta_operado"] += delta_operado
            otros["delta_total"] += delta_total
            otros["val_anterior"] += val_prev
            otros["val_actual"] += val_act
            otros["n"] += 1
        else:
            estado = "ambos" if (p and a) else ("nuevo" if a else "cerrado")
            filas.append({
                "unidad":        u,
                "tipo":          None,
                "val_anterior":  round(val_prev, 2),
                "val_actual":    round(val_act, 2),
                "delta_mercado": round(delta_mercado, 2),
                "delta_operado": round(delta_operado, 2),
                "delta_total":   round(delta_total, 2),
                "estado":        estado,
            })

    filas.sort(key=lambda r: -abs(r["delta_total"]))

    tot_merc = sum(r["delta_mercado"] for r in filas) + otros["delta_mercado"]
    tot_oper = sum(r["delta_operado"] for r in filas) + otros["delta_operado"]
    tot_delta = sum(r["delta_total"] for r in filas) + otros["delta_total"]
    tot_prev = sum(r["val_anterior"] for r in filas) + otros["val_anterior"]
    tot_act = sum(r["val_actual"] for r in filas) + otros["val_actual"]

    return {
        "id_cuenta":      id_cuenta,
        "fecha":          fecha,
        "fecha_anterior": fecha_prev,
        "filas":          filas,
        "otros":          {k: (round(v, 2) if isinstance(v, float) else v)
                           for k, v in otros.items()},
        "totales": {
            "val_anterior":  round(tot_prev, 2),
            "val_actual":    round(tot_act, 2),
            "delta_mercado": round(tot_merc, 2),
            "delta_operado": round(tot_oper, 2),
            "delta_total":   round(tot_delta, 2),
        },
    }


def _fn(x):
    """Decimal/num → float, preservando None (base100/tem/tea pueden ser None)."""
    return None if x is None else float(x)


def valuacion_consolidada(filtro_cuenta: str = "todas",
                          scope: tuple[str, ...] | None = None) -> dict:
    """Espejo SQL de valuaciones.valuacion_consolidada — lee `valuaciones.consolidado`
    (cache iterativo escrito por el cron jobs.consolidado_cuentas, dual-write Mongo+SQL).

    Aplica el scope de grupos + el filtro de tipo de cuenta EN PYTHON, idéntico al path
    Mongo. Mismo shape {rows, n, filtro_cuenta}. Si la tabla no existe / está vacía
    (falta correr el cron una vez) → rows: []."""
    try:
        rows = _q(
            "SELECT id_cuenta, cuenta, ultimo_dia, valor_ars, valor_usd, base100_ars, "
            "base100_usd, pnl_acum_ars, pnl_acum_usd, tem_ars, tem_usd, tea_ars, tea_usd "
            "FROM valuaciones.consolidado")
    except Exception:
        return {"rows": [], "n": 0, "filtro_cuenta": filtro_cuenta}

    docs = [{
        "cuenta":       r["cuenta"] or "",
        "id_cuenta":    r["id_cuenta"],
        "ultimo_dia":   r["ultimo_dia"],
        "valor_ars":    _fn(r["valor_ars"]),
        "valor_usd":    _fn(r["valor_usd"]),
        "base100_ars":  _fn(r["base100_ars"]),
        "base100_usd":  _fn(r["base100_usd"]),
        "pnl_acum_ars": _fn(r["pnl_acum_ars"]),
        "pnl_acum_usd": _fn(r["pnl_acum_usd"]),
        "tem_ars":      _fn(r["tem_ars"]),
        "tem_usd":      _fn(r["tem_usd"]),
        "tea_ars":      _fn(r["tea_ars"]),
        "tea_usd":      _fn(r["tea_usd"]),
    } for r in rows]

    if scope is not None:
        permitidas = set(scope)
        docs = [d for d in docs if str(d.get("id_cuenta", "")) in permitidas]

    if filtro_cuenta and filtro_cuenta != "todas":
        from api.services._cuentas_filter import (
            _cuentas_accionistas,
            _ids_cuenta_productores,
        )
        accs = set(_cuentas_accionistas())
        prods = set(_ids_cuenta_productores())

        def _ok(d: dict) -> bool:
            cuenta = d.get("cuenta") or ""
            idc = str(d.get("id_cuenta") or "")
            if filtro_cuenta == "accionistas":
                return cuenta in accs
            if filtro_cuenta == "sin_accionistas":
                return cuenta not in accs
            if filtro_cuenta == "cooperativas":
                return cuenta not in accs and "coop" in cuenta.lower()
            if filtro_cuenta == "productores":
                return idc in prods
            return True

        docs = [d for d in docs if _ok(d)]

    return {"rows": docs, "n": len(docs), "filtro_cuenta": filtro_cuenta}
