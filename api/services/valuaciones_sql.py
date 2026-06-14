"""api/services/valuaciones_sql.py — espejo SQL de api/services/valuaciones.py.

Migración progresiva de la vista Portfolio (/valuaciones, dentro de CARTERAS) a SQL.
Lee `portafolio.tenencia` (fechas corregidas) filtrando `aum = 'si'` (mismas exclusiones
que el AuM real). Enriquece con la tabla `assets` (igual que el path Mongo con Assets).

Dual-run: el router elige Mongo o SQL por `?_engine=sql` / flag `VALUACIONES_SQL`.
Default Mongo → la vista en vivo NO cambia hasta probar con ?_engine=sql. Mismo shape de
salida que valuaciones.py para que el frontend no cambie.

Estado: posiciones_actuales (tabla de posiciones). Pendientes: serie, mensual, variación,
consolidado (se agregan de a una, cada una verificada antes de prender el flag global).
"""
from __future__ import annotations

from psycopg.rows import dict_row

from core.postgres import get_pool


def _q(sql: str, params: dict | None = None) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


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
        "FROM portafolio.tenencia v LEFT JOIN assets a ON a.unidad = v.unidad "
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
