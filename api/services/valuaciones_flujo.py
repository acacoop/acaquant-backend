"""api/services/valuaciones_flujo.py — flujo directo de la cartera por cuenta (SQL).

NEGOCIO → Valuaciones (admin + asistente_comercial). Flujo neto de la cuenta
desde los boletos de títulos (tabla SQL `negocio_movimientos`, espejo de
CashFlow.NegocioMovimientos), por mes y categoría, para TODA la cartera. Evita el
ruido de depósitos/extracciones administrativas.

EFICIENTE: el RESUMEN se agrega EN POSTGRES (GROUP BY → matriz chica, no miles de
filas). El DETALLE se pide bajo demanda por (categoría, mes). Acotado por desde/hasta.

5 columnas: compra→COMPRAS, venta→VENTAS, rescate_fci→RESCATES,
suscripcion_fci→SUSCRIPCIONES, acreencia→OTROS.

Las EXCLUSIONES (sí/no) se guardan POR CUENTA en Mongo CashFlow.ValuacionFlujoExcluidos
(set de comprobantes; default = todos incluidos) y se aplican en el WHERE del SQL.

Nombre FCI: negocio.ticker para FCI es el código CAFCI → se resuelve al ticker
limpio con LEFT JOIN portafolio.assets ON portafolio.assets.cafci = nm.ticker.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from psycopg.rows import dict_row

from core.mongo import get_mongo_client, get_mongo_client_read
from core.postgres import get_pool

# categoria del boleto → columna. Mismo universo que el PnL Títulos.
_COL_TO_CAT: dict[str, str] = {
    "COMPRAS":       "compra",
    "VENTAS":        "venta",
    "RESCATES":      "rescate_fci",
    "SUSCRIPCIONES": "suscripcion_fci",
    "OTROS":         "acreencia",
}
COLUMNAS: tuple[str, ...] = ("COMPRAS", "VENTAS", "RESCATES", "SUSCRIPCIONES", "OTROS")
_CATS: list[str] = list(_COL_TO_CAT.values())

_SEL_DB, _SEL_COL = "CashFlow", "ValuacionFlujoExcluidos"

# Pesificación a ARS (SIGNADO, sin abs → para que el NETO tenga sentido):
# misma moneda → directo; cross → ×mep.
_CONV = ("CASE WHEN moneda = 'ARS' THEN COALESCE(importe, 0) "
         "ELSE COALESCE(importe, 0) * COALESCE(mep, 0) END")


def _f(x) -> float:
    return float(x or 0)


def _q(sql: str, params: dict | None = None) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


def _excluidos(id_cuenta: str) -> list[str]:
    doc = get_mongo_client_read()[_SEL_DB][_SEL_COL].find_one(
        {"id_cuenta": str(id_cuenta)}, {"_id": 0, "excluidos": 1})
    return [str(c) for c in (doc.get("excluidos") or [])] if doc else []


def _rango(desde: str | None, hasta: str | None) -> tuple[str, str]:
    """Default = año en curso (1-ene → hoy) si no se pasa rango."""
    h = hasta or date.today().isoformat()
    d = desde or f"{h[:4]}-01-01"
    return d, h


def _cart_cond(cartera: str | None, col: str) -> tuple[str, dict]:
    """Filtro SQL por CARTERA: el ticker del movimiento ∈ los tickers/cafci de esa
    cartera en `assets` (para FCI el negocio.ticker es el código CAFCI). Vacío /
    None / TODAS → sin filtro."""
    if not cartera or cartera.upper() == "TODAS":
        return "", {}
    frag = (f"{col} IN ("
            "SELECT ticker FROM portafolio.assets WHERE cartera = %(cart)s AND ticker IS NOT NULL "
            "UNION SELECT cafci FROM portafolio.assets WHERE cartera = %(cart)s AND cafci IS NOT NULL)")
    return frag, {"cart": cartera}


def get_carteras() -> dict:
    """Valores de CARTERA disponibles (para el filtro maestro de la vista)."""
    rows = _q("SELECT DISTINCT cartera FROM portafolio.assets "
              "WHERE cartera IS NOT NULL AND cartera <> '' ORDER BY cartera")
    return {"carteras": [r["cartera"] for r in rows]}


def get_resumen(id_cuenta: str, desde: str | None = None, hasta: str | None = None,
                cartera: str | None = None) -> dict:
    """Matriz mes × categoría agregada en Postgres (solo movimientos incluidos)."""
    d, h = _rango(desde, hasta)
    excl = _excluidos(id_cuenta)
    cfrag, cp = _cart_cond(cartera, "ticker")
    rows = _q(
        f"""SELECT to_char(fecha, 'YYYY-MM') AS mes,
              SUM(CASE WHEN categoria='compra'          THEN {_CONV} ELSE 0 END) AS compras,
              SUM(CASE WHEN categoria='venta'           THEN {_CONV} ELSE 0 END) AS ventas,
              SUM(CASE WHEN categoria='rescate_fci'     THEN {_CONV} ELSE 0 END) AS rescates,
              SUM(CASE WHEN categoria='suscripcion_fci' THEN {_CONV} ELSE 0 END) AS suscripciones,
              SUM(CASE WHEN categoria='acreencia'       THEN {_CONV} ELSE 0 END) AS otros
            FROM negocio_movimientos
            WHERE id_cuenta = %(id)s AND categoria = ANY(%(cats)s)
              AND fecha >= %(d)s AND fecha <= %(h)s
              AND unidad IS DISTINCT FROM 'USDL'
              AND comprobante <> ALL(%(excl)s)
              {f"AND {cfrag}" if cfrag else ""}
            GROUP BY mes ORDER BY mes DESC""",
        {"id": str(id_cuenta), "cats": _CATS, "d": d, "h": h, "excl": excl, **cp},
    )
    filas: list[dict] = []
    tot = {c: 0.0 for c in COLUMNAS}
    key = {"COMPRAS": "compras", "VENTAS": "ventas", "RESCATES": "rescates",
           "SUSCRIPCIONES": "suscripciones", "OTROS": "otros"}
    for r in rows:
        cols = {c: _f(r[key[c]]) for c in COLUMNAS}
        for c in COLUMNAS:
            tot[c] += cols[c]
        filas.append({"mes": r["mes"],
                      **{c: round(cols[c], 2) for c in COLUMNAS},
                      "neto": round(sum(cols.values()), 2)})
    return {
        "id_cuenta":  str(id_cuenta),
        "desde":      d,
        "hasta":      h,
        "columnas":   list(COLUMNAS),
        "filas":      filas,
        "totales":    {c: round(tot[c], 2) for c in COLUMNAS},
        "neto_total": round(sum(tot.values()), 2),
    }


def get_movimientos(id_cuenta: str, categoria: str, desde: str | None = None,
                    hasta: str | None = None, mes: str | None = None,
                    cartera: str | None = None) -> dict:
    """Detalle (bajo demanda) de una categoría, para el panel derecho 50%."""
    cat = _COL_TO_CAT.get((categoria or "").upper())
    if not cat:
        return {"categoria": categoria, "movimientos": [], "n": 0}
    d, h = _rango(desde, hasta)
    conds = ["nm.id_cuenta = %(id)s", "nm.categoria = %(cat)s",
             "nm.fecha >= %(d)s", "nm.fecha <= %(h)s",
             "nm.unidad IS DISTINCT FROM 'USDL'"]
    p: dict = {"id": str(id_cuenta), "cat": cat, "d": d, "h": h}
    if mes:
        conds.append("to_char(nm.fecha, 'YYYY-MM') = %(mes)s")
        p["mes"] = mes
    cfrag, cp = _cart_cond(cartera, "nm.ticker")
    if cfrag:
        conds.append(cfrag)
        p.update(cp)
    rows = _q(
        f"""SELECT nm.comprobante, nm.fecha, nm.op, nm.moneda, nm.importe, nm.mep,
              COALESCE(a.ticker, nm.ticker) AS ticker,
              ({_CONV}) AS importe_ars
            FROM negocio_movimientos nm
            LEFT JOIN portafolio.assets a ON a.cafci = nm.ticker
            WHERE {' AND '.join(conds)}
            ORDER BY nm.fecha, nm.comprobante""",
        p,
    )
    excl = set(_excluidos(id_cuenta))
    movimientos = [{
        "comprobante": str(r["comprobante"]),
        "fecha":       r["fecha"].isoformat() if r["fecha"] else None,
        "categoria":   categoria.upper(),
        "op":          r["op"],
        "ticker":      r["ticker"],
        "importe":     _f(r["importe"]),
        "moneda":      r["moneda"],
        "importe_ars": round(_f(r["importe_ars"]), 2),
        "incluido":    str(r["comprobante"]) not in excl,
    } for r in rows]
    return {"id_cuenta": str(id_cuenta), "categoria": categoria.upper(),
            "movimientos": movimientos, "n": len(movimientos)}


# Signo del flujo para el XIRR (confirmado por la mesa): compra/suscripción
# AGREGAN posición (depósito +); venta/rescate la SACAN (extracción −);
# acreencia = cobro que sale del portfolio (−).
_SIGNO: dict[str, int] = {
    "compra": +1, "suscripcion_fci": +1,
    "venta": -1, "rescate_fci": -1, "acreencia": -1,
}


def get_mensual(id_cuenta: str, cartera: str | None = None) -> dict:
    """Tabla mensual estilo Carteras (Cierre AuM, Flujo neto, Δ valor, PnL acum,
    TEM, TEA en ARS y USD) — REUSA api.services.valuaciones.valuacion_mensual,
    pero alimentada con el FLUJO = neto de los boletos de títulos incluidos
    (en vez de depósitos/extracciones). XIRR/TEM/TEA idéntico a Carteras.

    El flujo se construye signado (ver _SIGNO) y excluye los comprobantes
    marcados 'NO'. Sin filtro de fecha: la tabla cubre toda la historia (el
    front muestra los últimos N meses).
    """
    excl = _excluidos(id_cuenta)
    cfrag, cp = _cart_cond(cartera, "ticker")
    rows = _q(
        f"""SELECT fecha, categoria, ABS({_CONV}) AS mag
            FROM negocio_movimientos
            WHERE id_cuenta = %(id)s AND categoria = ANY(%(cats)s)
              AND unidad IS DISTINCT FROM 'USDL'
              AND comprobante <> ALL(%(excl)s)
              {f"AND {cfrag}" if cfrag else ""}
            ORDER BY fecha""",
        {"id": str(id_cuenta), "cats": _CATS, "excl": excl, **cp},
    )
    flujos: dict[str, dict] = {}
    for r in rows:
        if not r["fecha"]:
            continue
        signo = _SIGNO.get(r["categoria"], 0)
        if signo == 0:
            continue
        imp = signo * _f(r["mag"])
        mes = r["fecha"].isoformat()[:7]
        b = flujos.setdefault(mes, {"depositos": 0.0, "extracciones": 0.0, "items": []})
        if imp != 0:
            b["items"].append((r["fecha"].isoformat(), imp))
        if imp > 0:
            b["depositos"] += imp
        else:
            b["extracciones"] += imp

    # Versión SIN cache (el flujo es un dict no hasheable → rompe @cached).
    # `cartera` también filtra el CIERRE (AuM) por su campo CARTERA, así el
    # rendimiento queda consistente con el flujo filtrado.
    from api.services.valuaciones import _valuacion_mensual
    # Devuelve {id_cuenta, meses:[MensualRow], n_meses} igual que Carteras.
    return _valuacion_mensual(id_cuenta=id_cuenta, flujos_override=flujos,
                              cartera=cartera or None)


def get_tenencias(id_cuenta: str, fecha: str | None = None,
                  cartera: str | None = None) -> dict:
    """Tenencias (posiciones) de la cuenta a una fecha de cierre — reusa el
    snapshot de Carteras (panel derecho inferior 50%), filtrable por CARTERA."""
    from api.services.valuaciones import posiciones_actuales
    return posiciones_actuales(id_cuenta=id_cuenta, fecha=fecha, cartera=cartera or None)


def set_incluido(id_cuenta: str, comprobante, incluido: bool, actor: str) -> dict:
    """Incluye/excluye un comprobante para la cuenta (guardado compartido).
    Guarda SOLO las exclusiones (default = incluido). Idempotente."""
    comp = str(comprobante)
    col = get_mongo_client()[_SEL_DB][_SEL_COL]
    cambio = ({"$pull": {"excluidos": comp}} if incluido
              else {"$addToSet": {"excluidos": comp}})
    cambio["$set"] = {"actualizado_por": actor, "actualizado_at": datetime.now(UTC)}
    col.update_one({"id_cuenta": str(id_cuenta)}, cambio, upsert=True)
    return {"id_cuenta": str(id_cuenta), "comprobante": comp, "incluido": incluido}
