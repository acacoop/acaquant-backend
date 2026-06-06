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

from psycopg.rows import dict_row

from api.services.comercial import (
    _CATS_OPERACIONES,
    _CATS_VOLUMEN,
    _cv,
    _factor_usd,
)
from core.postgres import get_pool

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


def _scope_cuentas(operador: str, p: dict) -> str:
    """Fragmento SQL para scopear a las cuentas activas del operador (o todas si __todos__).
    Muta `p` con el param si hace falta. Devuelve el fragmento para un `id_cuenta IN (...)`."""
    if operador == "__todos__":
        return "id_cuenta IN (SELECT id_cuenta FROM comitentes WHERE estado = 'Activa')"
    p["op"] = operador
    return ("id_cuenta IN (SELECT id_cuenta FROM comitentes "
            "WHERE estado = 'Activa' AND operador_email = %(op)s)")


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
                    id_cuenta: str | None = None) -> dict:
    factor = _factor_usd(moneda)
    p: dict = {}
    if id_cuenta:
        scope = "id_cuenta = %(idc)s"
        p["idc"] = str(id_cuenta)
    else:
        scope = _scope_cuentas(operador, p)

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
