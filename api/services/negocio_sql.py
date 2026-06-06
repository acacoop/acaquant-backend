"""api/services/negocio_sql.py — vista NEGOCIO leyendo de Postgres (Supabase).

Servicio PURO (sin FastAPI). Espejo SQL de los endpoints `/api/operaciones/negocio/*` que
hoy leen Mongo (CashFlow.NegocioMovimientos). Mismo shape de salida → dual-run + comparación
(scripts/compare_negocio_sql_vs_mongo.py). Ver docs/MIGRACION_MONGO_SUPABASE.md.

Reglas replicadas EXACTO del router:
  * importe convertido a la moneda destino con el `mep` snapshot de CADA boleto (no el de hoy).
  * excluir futuros DLR: `unidad IS DISTINCT FROM 'USDL'` (= $nin de Mongo, matchea NULL).
  * filtro de cuenta: todas/accionistas/sin_accionistas/cooperativas/productores.
  * scope por `id_cuenta` (en NEGOCIO el scope va por id_cuenta, no por el string cuenta).
"""
from __future__ import annotations

from psycopg.rows import dict_row

from core.postgres import get_pool

_CATS = ("compra", "venta", "suscripcion_fci", "solicitud_suscripcion_fci",
         "caucion_tom_ap", "caucion_col_ap")
_UI_MAP = {
    "compra": ["compra"],
    "venta": ["venta"],
    "suscripciones": ["suscripcion_fci", "solicitud_suscripcion_fci"],
    "cauc_tom": ["caucion_tom_ap"],
    "cauc_col": ["caucion_col_ap"],
}


def _q(sql: str, params: dict | None = None) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


def _f(x) -> float:
    return float(x or 0)


def _iso(d):
    return d.isoformat() if d is not None else None


def _iso_naive(d):
    from datetime import UTC
    if d is None:
        return None
    if d.tzinfo is not None:
        d = d.astimezone(UTC).replace(tzinfo=None)
    return d.isoformat()


def _conv(moneda: str) -> str:
    """Expr SQL: |importe| convertido a `moneda` con el mep del boleto (misma moneda →
    directo; cross → ×mep si destino ARS, /mep si destino USD y mep>0, sino 0)."""
    if (moneda or "ARS").upper() == "USD":
        return ("CASE WHEN moneda = 'USD' THEN abs(COALESCE(importe, 0)) "
                "ELSE (CASE WHEN COALESCE(mep, 0) > 0 "
                "THEN abs(COALESCE(importe, 0)) / mep ELSE 0 END) END")
    return ("CASE WHEN moneda = 'ARS' THEN abs(COALESCE(importe, 0)) "
            "ELSE abs(COALESCE(importe, 0)) * COALESCE(mep, 0) END")


def _cuenta_filter_sql(filtro: str) -> tuple[str, dict]:
    if filtro == "productores":
        return ("id_cuenta IN (SELECT id_cuenta FROM comitentes WHERE nivel_1 = 'PRODUCTORES')", {})
    if filtro == "accionistas":
        return ("cuenta IN (SELECT cuenta FROM accionistas)", {})
    if filtro == "sin_accionistas":
        return ("cuenta NOT IN (SELECT cuenta FROM accionistas)", {})
    if filtro == "cooperativas":
        return (r"cuenta NOT IN (SELECT cuenta FROM accionistas) AND cuenta ~* '\ycoop'", {})
    return ("", {})


def _where(cuenta_filter=None, cuenta=None, scope=None, cats=None,
           fecha=None, desde=None, hasta=None) -> tuple[str, dict]:
    conds = ["unidad IS DISTINCT FROM 'USDL'"]
    p: dict = {}
    if cats is not None:
        conds.append("categoria = ANY(%(cats)s)")
        p["cats"] = list(cats)
    if fecha is not None:
        conds.append("fecha = %(fecha)s")
        p["fecha"] = fecha
    if desde is not None:
        conds.append("fecha >= %(desde)s")
        p["desde"] = desde
    if hasta is not None:
        conds.append("fecha <= %(hasta)s")
        p["hasta"] = hasta
    if cuenta:
        conds.append("cuenta = %(cuenta)s")
        p["cuenta"] = cuenta
    elif cuenta_filter and cuenta_filter != "todas":
        frag, fp = _cuenta_filter_sql(cuenta_filter)
        if frag:
            conds.append(frag)
            p.update(fp)
    if scope is not None:
        conds.append("id_cuenta = ANY(%(scope)s)")
        p["scope"] = list(scope)
    return " AND ".join(conds), p


# ── buckets de categoría (para serie / matrix) ───────────────────────────────
def _buckets(conv: str) -> str:
    return (
        f"SUM(CASE WHEN categoria='compra' THEN {conv} ELSE 0 END) AS compra, "
        f"SUM(CASE WHEN categoria='venta' THEN {conv} ELSE 0 END) AS venta, "
        f"SUM(CASE WHEN categoria IN ('suscripcion_fci','solicitud_suscripcion_fci') "
        f"THEN {conv} ELSE 0 END) AS suscripciones, "
        f"SUM(CASE WHEN categoria='caucion_tom_ap' THEN {conv} ELSE 0 END) AS cauc_tom, "
        f"SUM(CASE WHEN categoria='caucion_col_ap' THEN {conv} ELSE 0 END) AS cauc_col"
    )


def negocio_fechas() -> dict:
    rows = _q("SELECT fecha, count(*) AS n FROM negocio_movimientos "
              "WHERE fecha IS NOT NULL GROUP BY fecha ORDER BY fecha DESC")
    return {"fechas": [{"fecha": _iso(r["fecha"]), "n": r["n"]} for r in rows]}


def negocio_cuentas_list(scope: tuple[str, ...] | None = None) -> dict:
    where = "cuenta IS NOT NULL"
    p: dict = {}
    if scope is not None:
        where += " AND id_cuenta = ANY(%(scope)s)"
        p["scope"] = list(scope)
    rows = _q(f"SELECT DISTINCT cuenta FROM negocio_movimientos WHERE {where} ORDER BY cuenta", p)
    cuentas = [r["cuenta"] for r in rows]
    return {"cuentas": cuentas, "n": len(cuentas)}


def negocio(fecha: str) -> dict:
    r = _q("SELECT count(*) AS n, max(ingestado_en) AS ultima, "
           "count(DISTINCT categoria) AS ncat FROM negocio_movimientos WHERE fecha = %(f)s",
           {"f": fecha})[0]
    return {"meta": {
        "fecha": fecha, "n_boletos": r["n"] or 0, "n_categorias": r["ncat"] or 0,
        "ultima_ingesta": _iso_naive(r["ultima"]),
    }}


def negocio_serie(moneda: str = "ARS", cuenta_filter: str = "todas",
                  cuenta: str | None = None, scope: tuple[str, ...] | None = None) -> dict:
    where, p = _where(cuenta_filter=cuenta_filter, cuenta=cuenta, scope=scope, cats=_CATS)
    rows = _q(f"SELECT fecha, {_buckets(_conv(moneda))} FROM negocio_movimientos "
              f"WHERE {where} GROUP BY fecha ORDER BY fecha", p)
    serie = [{
        "fecha": _iso(r["fecha"]), "compra": round(_f(r["compra"]), 2),
        "venta": round(_f(r["venta"]), 2), "suscripciones": round(_f(r["suscripciones"]), 2),
        "cauc_tom": round(_f(r["cauc_tom"]), 2), "cauc_col": round(_f(r["cauc_col"]), 2),
    } for r in rows]
    return {"moneda": moneda, "cuenta_filter": cuenta_filter, "cuenta": cuenta, "serie": serie}


def negocio_cuentas(moneda: str = "ARS", cuenta_filter: str = "todas", categoria: str = "",
                    desde: str = "", hasta: str = "", cuenta: str | None = None,
                    scope: tuple[str, ...] | None = None) -> dict:
    where, p = _where(cuenta_filter=cuenta_filter, cuenta=cuenta, scope=scope,
                      cats=_UI_MAP[categoria], desde=desde, hasta=hasta)
    rows = _q(f"SELECT COALESCE(cuenta,'(sin cuenta)') AS cuenta, SUM({_conv(moneda)}) AS importe_abs, "
              f"count(*) AS n FROM negocio_movimientos WHERE {where} "
              f"GROUP BY cuenta ORDER BY importe_abs DESC", p)
    cuentas = [{"cuenta": r["cuenta"], "importe_abs": round(_f(r["importe_abs"]), 2), "n": r["n"]}
               for r in rows]
    return {
        "moneda": moneda, "cuenta_filter": cuenta_filter, "categoria": categoria,
        "desde": desde, "hasta": hasta, "cuenta": cuenta, "cuentas": cuentas,
        "total_abs": round(sum(c["importe_abs"] for c in cuentas), 2),
        "n_total": sum(c["n"] for c in cuentas),
    }


def negocio_cuentas_matrix(moneda: str = "ARS", cuenta_filter: str = "todas", desde: str = "",
                           hasta: str = "", cuenta: str | None = None,
                           scope: tuple[str, ...] | None = None) -> dict:
    where, p = _where(cuenta_filter=cuenta_filter, cuenta=cuenta, scope=scope,
                      cats=_CATS, desde=desde, hasta=hasta)
    rows = _q(f"SELECT COALESCE(cuenta,'(sin cuenta)') AS cuenta, {_buckets(_conv(moneda))}, "
              f"count(*) AS n FROM negocio_movimientos WHERE {where} GROUP BY cuenta", p)
    out = []
    for r in rows:
        compra, venta = round(_f(r["compra"]), 2), round(_f(r["venta"]), 2)
        susc = round(_f(r["suscripciones"]), 2)
        ct, cc = round(_f(r["cauc_tom"]), 2), round(_f(r["cauc_col"]), 2)
        out.append({
            "cuenta": r["cuenta"], "compra": compra, "venta": venta, "suscripciones": susc,
            "cauc_tom": ct, "cauc_col": cc, "n": r["n"],
            "total": round(compra + venta + susc + ct + cc, 2),
        })
    out.sort(key=lambda x: x["total"], reverse=True)
    return {
        "moneda": moneda, "cuenta_filter": cuenta_filter, "desde": desde, "hasta": hasta,
        "cuenta": cuenta, "cuentas": out, "n_cuentas": len(out),
        "total": round(sum(r["total"] for r in out), 2), "n_total": sum(r["n"] for r in out),
    }


def negocio_boletos(fecha: str = "", cuenta: str = "", moneda: str = "ARS",
                    categoria: str | None = None, scope: tuple[str, ...] | None = None) -> dict:
    cats = _UI_MAP[categoria] if categoria is not None else None
    where, p = _where(cuenta=cuenta, scope=scope, fecha=fecha, cats=cats)
    rows = _q(f"SELECT comprobante, categoria, op, ticker, cantidad, precio, importe, moneda, "
              f"mep, plazo, lugar, estado, informacion FROM negocio_movimientos "
              f"WHERE {where} ORDER BY comprobante", p)
    tgt_usd = moneda.upper() == "USD"
    boletos = []
    for r in rows:
        imp = r["importe"]
        mep = r["mep"] or 0
        if imp is not None and r["moneda"] != moneda:
            imp = (float(imp) / float(mep)) if (tgt_usd and mep) else (
                None if tgt_usd else float(imp) * float(mep))
        boletos.append({
            "comprobante": r["comprobante"], "categoria": r["categoria"], "op": r["op"],
            "ticker": r["ticker"],
            "cantidad": _f(r["cantidad"]) if r["cantidad"] is not None else None,
            "precio": _f(r["precio"]) if r["precio"] is not None else None,
            "importe": float(imp) if imp is not None else None,
            "moneda": r["moneda"], "plazo": r["plazo"], "lugar": r["lugar"],
            "estado": r["estado"], "informacion": r["informacion"],
        })
    return {"fecha": fecha, "cuenta": cuenta, "moneda": moneda, "categoria": categoria,
            "boletos": boletos, "n": len(boletos)}
