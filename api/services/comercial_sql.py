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

from datetime import date

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
          "nivel_5", "primer_contacto_comercial", "riesgo_la_ft", "division", "adc", "dma")
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


def _scope_cuentas(operador: str, p: dict) -> str:
    """Fragmento SQL para scopear a las cuentas activas del operador (o todas si __todos__).
    Muta `p` con el param si hace falta. Devuelve el fragmento para un `id_cuenta IN (...)`."""
    if operador == "__todos__":
        return "id_cuenta IN (SELECT id_cuenta FROM comitentes WHERE estado = 'Activa')"
    p["op"] = operador
    return ("id_cuenta IN (SELECT id_cuenta FROM comitentes "
            "WHERE estado = 'Activa' AND operador_email = %(op)s)")


def _ids_operador(operador: str) -> list[str]:
    """ids de cuenta activas del operador (o todas si __todos__), igual que _cuentas_de_operador."""
    if operador == "__todos__":
        rows = _q("SELECT id_cuenta FROM comitentes WHERE estado = 'Activa'")
    else:
        rows = _q("SELECT id_cuenta FROM comitentes WHERE estado = 'Activa' "
                  "AND operador_email = %(op)s", {"op": operador})
    return sorted(r["id_cuenta"] for r in rows)


def _aum_por_cuenta_sql(operador: str) -> dict[str, float]:
    """AuM (último snapshot GLOBAL) por id_cuenta, scopeado al operador."""
    snap = _q("SELECT max(fecha_snapshot) AS f FROM aum")[0]["f"]
    if snap is None:
        return {}
    p: dict = {"f": snap}
    scope = _scope_cuentas(operador, p)
    return {r["id_cuenta"]: _f(r["aum"]) for r in _q(
        f"SELECT id_cuenta, SUM(valuacion) AS aum FROM aum "
        f"WHERE fecha_snapshot = %(f)s AND {scope} GROUP BY id_cuenta", p)}


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


def _ficha_por_cuenta(operador: str, campos: tuple[str, ...]) -> dict[str, dict]:
    """{id_cuenta: {campos}} de comitentes activas (+ denominacion de cuentas) del operador."""
    cols = ", ".join(f"c.{c}" if c != "denominacion" else "u.denominacion" for c in campos)
    p: dict = {}
    where = "c.estado = 'Activa'"
    if operador != "__todos__":
        where += " AND c.operador_email = %(op)s"
        p["op"] = operador
    rows = _q(f"SELECT c.id_cuenta, {cols} FROM comitentes c "
              f"LEFT JOIN cuentas u ON u.id_cuenta = c.id_cuenta WHERE {where}", p)
    return {r["id_cuenta"]: r for r in rows}


def operador_comercial(*, operador: str, moneda: str = "ARS") -> dict:
    factor = _factor_usd(moneda)
    hoy = _hoy_art()
    mtd, ytd = hoy.replace(day=1).isoformat(), hoy.replace(month=1, day=1).isoformat()
    ids = _ids_operador(operador)
    aum = _aum_por_cuenta_sql(operador)

    # Volumen YTD y MTD por cuenta en una pasada.
    p: dict = {"ytd": ytd, "mtd": mtd, "cats": list(_CATS_VOLUMEN)}
    scope = _scope_cuentas(operador, p)
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

    ficha = _ficha_por_cuenta(operador, _FICHA)
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
                       moneda: str = "ARS") -> dict:
    ids = _ids_operador(operador)
    if not ids and operador != "__todos__":
        return {"operador": operador, "dias_activa": dias_activa,
                "dias_dormida": dias_dormida, "clientes": []}
    hoy = _hoy_art()
    factor = _factor_usd(moneda)
    factor_cupo = _factor_usd("USD")  # cupo SIEMPRE en USD al MEP del día
    aum = _aum_por_cuenta_sql(operador)
    year_start = date(hoy.year, 1, 1).isoformat()
    month_start = hoy.replace(day=1).isoformat()

    # Última operación EVER por cuenta (Operaciones, fuente de verdad).
    p: dict = {}
    scope = _scope_cuentas(operador, p)
    ult_op = {r["id_cuenta"]: _iso(r["ult"]) for r in _q(
        f"SELECT id_cuenta, max(concertacion) AS ult FROM operaciones WHERE {scope} "
        f"GROUP BY id_cuenta", p) if r["ult"] is not None}

    ficha = _ficha_por_cuenta(operador, _ANALISIS)
    cupos = {r["id_cuenta"]: r for r in _q(
        "SELECT id_cuenta, cupo_transaccional_ars, cupo_usado_ars FROM comitentes "
        "WHERE estado = 'Activa'" + ("" if operador == "__todos__"
                                     else " AND operador_email = %(op)s"),
        {} if operador == "__todos__" else {"op": operador})}

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
