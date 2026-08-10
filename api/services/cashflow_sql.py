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

from api.cache import cached
from api.services._mep import get_mep_for_date
from api.services._sql import _q
from api.services.operaciones_view import ddmmyyyy_a_iso as _ddmmyyyy_a_iso

_RE_ID_BRACKET = re.compile(r"^\[(\d+)\]")


# ── selectores de motor (flag por dominio, mismo patrón que operaciones_view.motor) ──
# movimientos_sql_on() / acreencias_sql_on() ELIMINADOS (cutovers 2026-06-23/24):
# FLUJOS y ACREENCIAS leen SQL fijo (sin flag).


def volumen_agro_sql_on() -> bool:
    return os.getenv("VOLUMEN_AGRO_SQL") == "1"


def _f(x) -> float:
    return float(x or 0)


# Categorías de `negocio_movimientos` que SON un flujo de fondos. Son las mismas
# tres palabras que usa el writer viejo (jobs/cashflow.py::PALABRAS_CLAVE), pero
# ya resueltas a categoría por api/services/aunesa_negocio.py::categorizar.
_CATS_FLUJO = ("deposito", "transferencia", "extraccion")


# ── MOVIMIENTOS (vista FLUJOS) ───────────────────────────────────────────────
def listar_flujos(
    cuenta: str | None = None, unidad: str | None = None,
    desde: str | None = None, hasta: str | None = None,
    scope: tuple[str, ...] | None = None,
) -> list[dict]:
    """Depósitos/extracciones/transferencias. Shape estable: comprobante→boleto,
    importe→bruto, fecha→concertacion(iso). El router ya verificó el scope.

    DOS FUENTES, y no es por gusto (2026-08-10). Las dos salen del MISMO endpoint
    de Aunesa (`consolidadosGenerales`), pero las ingiere gente distinta:

      * `operaciones.negocio_movimientos` — la ingiere `jobs/negocio_movimientos`
        cada 30' CON LOOKBACK de 2 días hábiles y PK (fecha, comprobante).
      * `operaciones.movimientos` — la ingiere `jobs/cashflow` 1×/día a las 23 ART,
        mira SOLO ese día y no vuelve nunca, con PK `comprobante` a secas.

    Medido el 2026-08-10: el 06/08 Aunesa tenía 66 comprobantes para ese día y el
    job de cashflow, cuando corrió, vio 58 → en la tabla quedaron 57. Los ~15
    ausentes de esos dos días eran TODOS depósitos (e-cheque, cheques, común) por
    ~22.400 millones de ARS, y encima daban vuelta el signo del día: la vista
    mostraba −10.902 M donde el flujo real era +7.919 M. La cobertura de depósitos
    de `movimientos` viene en 61-67% desde abril; la de `negocio_movimientos`, 100%.

    Por eso `negocio_movimientos` es la fuente PRINCIPAL y `movimientos` queda de
    COMPLEMENTO: se le suma únicamente lo que tenga un `comprobante` que la
    principal no traiga. Así el cambio no puede mostrar MENOS que antes — solo más
    — y no hace falta backfillear ni tocar un dato. Cuando el complemento deje de
    aportar filas (verificable con un COUNT), se borra `jobs/cashflow.py` + la tabla.
    """
    principal = _flujos_negocio(cuenta, unidad, desde, hasta, scope)
    vistos = {r["boleto"] for r in principal}
    complemento = [r for r in _flujos_legacy(cuenta, unidad, desde, hasta, scope)
                   if r["boleto"] not in vistos]
    out = principal + complemento
    out.sort(key=lambda r: r["concertacion"] or "")
    return out


def _flujos_negocio(
    cuenta: str | None, unidad: str | None, desde: str | None, hasta: str | None,
    scope: tuple[str, ...] | None,
) -> list[dict]:
    """Fuente PRINCIPAL: `operaciones.negocio_movimientos` (lookback + PK completa).

    `fecha` acá es un `date` de verdad (no el text dd/mm/yyyy de la tabla vieja),
    así que el rango va en SQL sin regex. El scope filtra por `id_cuenta`, que está
    materializado e indexado — no hace falta el regex sobre el string `cuenta`.
    `anulado_en IS NULL` descarta los boletos que Aunesa dio de baja (la tabla vieja
    no tiene ese concepto: un anulado se queda ahí para siempre)."""
    conds = ["categoria = ANY(%(cats)s)", "anulado_en IS NULL"]
    p: dict = {"cats": list(_CATS_FLUJO)}
    if cuenta:
        conds.append("cuenta = %(cuenta)s")
        p["cuenta"] = cuenta
    elif scope is not None:
        if not scope:
            return []  # scope vacío = no ve nada
        conds.append("id_cuenta = ANY(%(scope)s)")
        p["scope"] = [str(s) for s in scope]
    if unidad:
        conds.append("moneda = %(unidad)s")
        p["unidad"] = unidad
    if desde:
        conds.append("fecha >= %(f_desde)s")
        p["f_desde"] = desde
    if hasta:
        conds.append("fecha <= %(f_hasta)s")
        p["f_hasta"] = hasta

    rows = _q(
        "SELECT comprobante, cuenta, fecha, informacion, importe, moneda "
        "  FROM negocio_movimientos WHERE " + " AND ".join(conds), p,
    )
    out = []
    for d in rows:
        f = d.get("fecha")
        out.append({
            "boleto":       d.get("comprobante"),
            "concertacion": f.isoformat() if hasattr(f, "isoformat") else (str(f) if f else None),
            "cuenta":       d.get("cuenta"),
            "informacion":  d.get("informacion"),
            "bruto":        _f(d.get("importe")) if d.get("importe") is not None else None,
            "unidad":       d.get("moneda"),
        })
    return out


def _flujos_legacy(
    cuenta: str | None, unidad: str | None, desde: str | None, hasta: str | None,
    scope: tuple[str, ...] | None,
) -> list[dict]:
    """Fuente de COMPLEMENTO: `operaciones.movimientos` (la tabla vieja). Se conserva
    tal cual estaba para no perder nada que la principal no traiga — ver el docstring
    de `listar_flujos`."""
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

    # RANGO DE FECHAS EN SQL (2026-07-22): antes se traía la tabla ENTERA
    # (~2 años de movimientos) y se descartaba en Python — un full scan + la
    # transferencia completa en cada llamada, en el mismo pool que sirve la web.
    # El regex blinda el to_date contra fechas malformadas (que quedan afuera
    # igual que antes: `iso is None` se descartaba). El filtro de Python sigue
    # abajo como cinturón, ahora sobre un conjunto ya acotado.
    if desde or hasta:
        conds.append(r"fecha ~ '^[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}$'")
        if desde:
            conds.append("to_date(fecha, 'DD/MM/YYYY') >= %(f_desde)s")
            p["f_desde"] = desde
        if hasta:
            conds.append("to_date(fecha, 'DD/MM/YYYY') <= %(f_hasta)s")
            p["f_hasta"] = hasta

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


@cached(ttl=300)
def neto_por_cuenta(desde: str) -> dict[str, float]:
    """`{id_cuenta: neto en ARS}` — la plata que entró menos la que salió de cada
    cliente desde `desde`, con la MISMA fuente que la vista CASHFLOW.

    Lo consume el CUPO TRANSACCIONAL: `cupo_usado_ars` es una foto y esto es lo
    que se le suma para tener el usado real (ver config.CUPO_BASE_FECHA).

    Dos detalles que no son inferibles:
      * `total` ya viene con el signo invertido por el writer (entrada +), así
        que el neto es la suma directa — no hay que separar entradas de salidas.
      * `operaciones.movimientos` NO tiene snapshot de MEP (a diferencia de
        negocio_movimientos), así que lo que está en USD se pesifica con la
        cotización del día del movimiento. Un movimiento en USD sin cotización
        para su fecha se DESCARTA en vez de contarse como si fueran pesos.
    """
    filas = listar_flujos(desde=desde)
    mep_cache: dict[str, float | None] = {}
    neto: dict[str, float] = {}
    for f in filas:
        m = _RE_ID_BRACKET.match(f.get("cuenta") or "")
        bruto = f.get("bruto")
        if not m or bruto is None:
            continue
        if (f.get("unidad") or "ARS").upper() == "ARS":
            monto = float(bruto)
        else:
            fecha = f.get("concertacion")
            if not fecha:
                continue
            if fecha not in mep_cache:
                mep_cache[fecha] = get_mep_for_date(fecha)
            tc = mep_cache[fecha]
            if not tc:
                continue
            monto = float(bruto) * float(tc)
        neto[m.group(1)] = neto.get(m.group(1), 0.0) + monto
    return neto


def flujos_resumen(
    desde: str | None = None, hasta: str | None = None,
    scope: tuple[str, ...] | None = None,
) -> dict:
    """Resumen agregado de la vista CASHFLOW: una fila por (día, cuenta, unidad)
    con entradas (Σ brutos ≥0) y salidas (Σ brutos <0) separadas — la vista las
    muestra por separado, un neto solo no alcanza.

    Reemplaza bajar 2 años de movimientos crudos al browser. La fecha se parsea
    en Python (dd/mm/yyyy crudo, igual que listar_flujos) — un to_date en SQL
    reventaría con una fecha malformada."""
    rows = listar_flujos(desde=desde, hasta=hasta, scope=scope)
    agg: dict[tuple, dict] = {}
    for r in rows:
        if not r["concertacion"]:
            continue
        e = agg.setdefault((r["concertacion"], r["cuenta"] or "", r["unidad"] or ""), {
            "dia": r["concertacion"], "cuenta": r["cuenta"] or "",
            "unidad": r["unidad"] or "", "entradas": 0.0, "salidas": 0.0, "n": 0})
        b = r["bruto"] or 0.0
        if b >= 0:
            e["entradas"] += b
        else:
            e["salidas"] += b
        e["n"] += 1
    filas = sorted(agg.values(), key=lambda x: (x["dia"], x["cuenta"]))
    for f in filas:
        f["entradas"] = round(f["entradas"], 2)
        f["salidas"] = round(f["salidas"], 2)
    return {"filas": filas}


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
