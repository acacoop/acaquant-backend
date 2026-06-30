"""api/services/cashflow_sql.py — lecturas SQL (Supabase) de las colecciones CashFlow
que seguían en Mongo: Movimientos (vista FLUJOS), Acreencias (cobros futuros) y
VolumenMercadoAgro (denominador del share AGRO).

Servicio PURO (sin FastAPI). Cada función devuelve EXACTAMENTE el mismo shape que su
contraparte Mongo (api/routers/operaciones.py::listar_flujos, api/services/acreencias.py,
api/services/comercial.py, api/services/operaciones_sql.py::ops_agro) para que el
resultado sea byte-a-byte.

FLUJOS y ACREENCIAS son SQL-NATIVE desde los cutovers de 2026-06-23/24: /api/operaciones/flujos
+ back-office/acreencias + comercial/cobros-futuros leen SIEMPRE SQL (CashFlow.Movimientos y
CashFlow.Acreencias Mongo dropeadas) — sin flag. Queda un dominio en dual-run por flag:

    VOLUMEN_AGRO_SQL=1    → el denominador del share AGRO sale de SQL (mercado.volumen_mercado_agro)

ACREENCIAS — SQL-NATIVE (cutover 2026-06-23): back-office/acreencias + comercial/
cobros-futuros leen SIEMPRE operaciones.acreencias (SIN flag; CashFlow.Acreencias
Mongo dropeada). Las funciones por_dia/del_dia/del_cliente/acreencias_docs son la
única fuente.

Reglas de traducción Mongo→SQL (verificadas contra el código de los writers):
  * Movimientos.`fecha` es string dd/mm/yyyy CRUDO en Mongo → se guarda tal cual en SQL
    (columna text). El parseo a ISO + el filtro [desde,hasta] + el orden se hacen acá en
    Python, idéntico al path Mongo (la fecha no es ordenable como string).
  * Acreencias: `monto` se SUMA en su moneda nativa (ARS/USD), NO se pesifica. `generado_at`
    no se proyecta (igual que Mongo `{generado_at: 0}`).
  * VolumenMercadoAgro: passthrough — solo se lee periodo/commodity/toneladas.
"""
from __future__ import annotations

import os
import re

from psycopg.rows import dict_row

from api.services.operaciones_view import ddmmyyyy_a_iso as _ddmmyyyy_a_iso
from core.postgres import get_pool

_RE_ID_BRACKET = re.compile(r"^\[(\d+)\]")


# ── selectores de motor (flag por dominio, mismo patrón que operaciones_view.motor) ──
# movimientos_sql_on() / acreencias_sql_on() ELIMINADOS (cutovers 2026-06-23/24):
# FLUJOS y ACREENCIAS leen SQL fijo (sin flag).


def volumen_agro_sql_on() -> bool:
    return os.getenv("VOLUMEN_AGRO_SQL") == "1"


def _q(sql: str, params: dict | None = None) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


def _f(x) -> float:
    return float(x or 0)


# ── MOVIMIENTOS (vista FLUJOS) — espejo de operaciones.py::listar_flujos ─────
def listar_flujos(
    cuenta: str | None = None, unidad: str | None = None,
    desde: str | None = None, hasta: str | None = None,
    scope: tuple[str, ...] | None = None,
) -> list[dict]:
    """Depósitos/extracciones/transferencias (operaciones.movimientos). Mismo shape y
    orden que el path Mongo: comprobante→boleto, total→bruto, fecha(dd/mm/yyyy)→
    concertacion(iso). El rango de fechas y el orden se resuelven en Python (la fecha
    está cruda en dd/mm/yyyy). El router ya verificó el scope sobre `cuenta`."""
    conds: list[str] = []
    p: dict = {}
    if cuenta:
        conds.append("cuenta = %(cuenta)s")
        p["cuenta"] = cuenta
    elif scope is not None:
        # Sin cuenta explícita → restringir al scope por el id bracketed de `cuenta`
        # ("[<id>] NOMBRE"), idéntico al regex Mongo (scope_cuenta_match, campo=None).
        if not scope:
            return []  # scope vacío = no ve nada
        alternation = "|".join(re.escape(s) for s in scope)
        conds.append("cuenta ~ %(scope_re)s")
        p["scope_re"] = rf"^\[({alternation})\]"
    if unidad:
        conds.append("unidad = %(unidad)s")
        p["unidad"] = unidad

    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    rows = _q(
        "SELECT comprobante, cuenta, fecha, informacion, total, unidad "
        f"FROM movimientos{where}", p,
    )
    out = []
    for d in rows:
        iso = _ddmmyyyy_a_iso(d.get("fecha"))
        if desde and (iso is None or iso < desde):
            continue
        if hasta and (iso is None or iso > hasta):
            continue
        out.append({
            "boleto":       d.get("comprobante"),
            "concertacion": iso,
            "cuenta":       d.get("cuenta"),
            "informacion":  d.get("informacion"),
            "bruto":        _f(d.get("total")) if d.get("total") is not None else None,
            "unidad":       d.get("unidad"),
        })
    out.sort(key=lambda r: r["concertacion"] or "")
    return out


# ── ACREENCIAS (cobros futuros) — espejo de acreencias.py (por_dia/del_dia/del_cliente) ──
def por_dia(desde: str | None = None, hasta: str | None = None) -> list[dict]:
    """Agregado por fecha de pago: total por moneda + #clientes + #pagos. Mismo shape
    que acreencias.por_dia (Mongo aggregate)."""
    from datetime import date
    conds = ["fecha_pago >= %(desde)s"]
    p: dict = {"desde": desde or date.today().isoformat()}
    if hasta:
        conds.append("fecha_pago <= %(hasta)s")
        p["hasta"] = hasta
    where = " AND ".join(conds)
    rows = _q(
        "SELECT fecha_pago AS fecha, moneda, SUM(monto) AS monto, "
        "count(DISTINCT id_cuenta) AS n_clientes, count(*) AS n_pagos "
        f"FROM acreencias WHERE {where} GROUP BY fecha_pago, moneda", p,
    )
    # Agrupar por fecha (un row por (fecha, moneda) → consolidar a por_moneda).
    out_map: dict[str, dict] = {}
    for r in rows:
        e = out_map.setdefault(r["fecha"], {
            "fecha": r["fecha"], "por_moneda": {}, "_cuentas": set(), "n_pagos": 0,
        })
        e["por_moneda"][r["moneda"]] = round(_f(r["monto"]), 2)
        e["n_pagos"] += r["n_pagos"]
    # n_clientes: distintos por fecha (no por (fecha,moneda)) → segunda pasada.
    for r in _q(
        "SELECT fecha_pago AS fecha, count(DISTINCT id_cuenta) AS n_clientes "
        f"FROM acreencias WHERE {where} GROUP BY fecha_pago", p,
    ):
        if r["fecha"] in out_map:
            out_map[r["fecha"]]["n_clientes"] = r["n_clientes"]
    out = []
    for f in sorted(out_map):
        e = out_map[f]
        out.append({
            "fecha": e["fecha"], "por_moneda": e["por_moneda"],
            "n_clientes": e.get("n_clientes", 0), "n_pagos": e["n_pagos"],
        })
    return out


def del_dia(fecha: str) -> list[dict]:
    """Quién cobra en una fecha y cuánto (por cliente·ticker), desc por monto. Devuelve
    el doc completo SIN `generado_at` (igual que Mongo {generado_at:0})."""
    rows = _q(
        "SELECT data FROM acreencias WHERE fecha_pago = %(fecha)s ORDER BY monto DESC",
        {"fecha": fecha},
    )
    return [_acr_doc(r["data"]) for r in rows]


def del_cliente(id_cuenta: str, desde: str | None = None) -> list[dict]:
    """Próximos cobros de un cliente, asc por fecha. Doc completo sin `generado_at`."""
    from datetime import date
    rows = _q(
        "SELECT data FROM acreencias WHERE id_cuenta = %(idc)s AND fecha_pago >= %(desde)s "
        "ORDER BY fecha_pago ASC",
        {"idc": id_cuenta, "desde": desde or date.today().isoformat()},
    )
    return [_acr_doc(r["data"]) for r in rows]


def _acr_doc(data: dict | None) -> dict:
    """Doc de acreencia tal como lo emitía Mongo (find con projection {_id:0,
    generado_at:0}). `data` jsonb ya tiene el doc completo menos _id; quitamos
    generado_at por las dudas (el writer lo incluye)."""
    d = dict(data or {})
    d.pop("generado_at", None)
    d.pop("_id", None)
    return d


def acreencias_docs(ids: list[str] | None = None, *, id_cuenta: str | None = None,
                    order_cliente: bool = False,
                    desde: str | None = None, hasta: str | None = None) -> list[dict]:
    """Docs de acreencias filtrados, con el MISMO shape de campos que los aggregates de
    comercial.py (fecha_pago, cliente, id_cuenta, ticker, emisor, moneda, monto). La
    AGREGACIÓN la hace comercial.py sobre estos docs (idéntica al path Mongo) → no se
    duplica la matemática. `order_cliente=True` ordena por (fecha_pago asc, monto desc)
    como el detalle por cliente; sino sin orden (se agrupa igual). `desde`/`hasta`
    (ISO 'YYYY-MM-DD', fecha_pago es text → comparación lexicográfica) acotan el rango."""
    conds: list[str] = []
    p: dict = {}
    if id_cuenta is not None:
        conds.append("id_cuenta = %(idc)s")
        p["idc"] = str(id_cuenta)
    elif ids is not None:
        conds.append("id_cuenta = ANY(%(ids)s)")
        p["ids"] = list(ids)
    if desde:
        conds.append("fecha_pago >= %(desde)s")
        p["desde"] = desde
    if hasta:
        conds.append("fecha_pago <= %(hasta)s")
        p["hasta"] = hasta
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    order = " ORDER BY fecha_pago ASC, monto DESC" if order_cliente else ""
    rows = _q(
        "SELECT fecha_pago, id_cuenta, ticker, moneda, monto, "
        "data->>'cliente' AS cliente, data->>'emisor' AS emisor "
        f"FROM acreencias{where}{order}", p,
    )
    return [
        {"fecha_pago": r["fecha_pago"], "id_cuenta": str(r["id_cuenta"]),
         "cliente": r["cliente"], "ticker": r["ticker"], "emisor": r["emisor"],
         "moneda": r["moneda"], "monto": _f(r["monto"])}
        for r in rows
    ]


# ── VolumenMercadoAgro (denominador del share AGRO) ──────────────────────────
def volumen_mercado_agro() -> dict[str, dict[str, float]]:
    """`{periodo: {commodity: toneladas}}` — denominador del share AGRO. Mismo dato que
    el find Mongo de operaciones_sql.ops_agro (proyección periodo/commodity/toneladas)."""
    out: dict[str, dict[str, float]] = {}
    for r in _q("SELECT periodo, commodity, toneladas FROM volumen_mercado_agro"):
        out.setdefault(r["periodo"], {})[r["commodity"]] = _f(r["toneladas"])
    return out
