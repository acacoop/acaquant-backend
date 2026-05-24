"""api/services/comercial.py — Tablero Comercial (lente por operador).

Cruza, todo por `id_cuenta`:
  - QUIÉN   → `Clientes.Comitentes` (operador asignado, segmentación nivel_1).
  - ACTIVIDAD → `CashFlow.NegocioMovimientos` (última operación).
  - TAMAÑO  → `Valuaciones.AuM` (último snapshot).
  - operador ↔ usuario → `Manager.Users` (para detectar cuentas huérfanas).

Devuelve el resumen por operador para la vista COMERCIAL (v1, solo manager).
On-the-fly cacheado (TTL); si pesa, mover a precompute `Clientes.ComercialCache`.
Diseño completo: docs/TABLERO_COMERCIAL.md [5].
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from api.cache import cached
from api.db import (
    get_db_cashflow,
    get_db_clientes,
    get_db_manager,
    get_db_valuaciones,
)

# Las queries por cuenta filtran `id_cuenta` (denormalizado en la ingesta de
# NegocioMovimientos, indexado) — sin regex sobre `cuenta`. Backfill de docs
# viejos: scripts/backfill_id_cuenta_negocio.py.

_BUCKET = {
    "ACTIVA": "n_activas",
    "ENFRIANDOSE": "n_enfriandose",
    "DORMIDA": "n_dormidas",
    "NUEVA": "n_nuevas",
}


def estado_comercial(
    dias_desde_ult_op: int | None,
    opero_alguna_vez: bool,
    dias_activa: int,
    dias_dormida: int,
) -> str:
    """Estado COMERCIAL (≠ legal), derivado de la última operación.

    `dias_desde_ult_op`: días desde la última op SI está dentro de la ventana
    reciente (`dias_dormida`), si no → None. `opero_alguna_vez`: si la cuenta
    aparece alguna vez en NegocioMovimientos.

    NUEVA (nunca operó) · ACTIVA (≤ dias_activa) · ENFRIANDOSE (dias_activa..
    dias_dormida) · DORMIDA (operó alguna vez pero hace > dias_dormida).
    """
    if dias_desde_ult_op is not None:
        return "ACTIVA" if dias_desde_ult_op <= dias_activa else "ENFRIANDOSE"
    return "DORMIDA" if opero_alguna_vez else "NUEVA"


def _nuevo_operador(email: str | None, nombre: str | None) -> dict[str, Any]:
    return {
        "operador_email": email,
        "operador_nombre": nombre,
        "n_cuentas": 0,
        "n_activas": 0,
        "n_enfriandose": 0,
        "n_dormidas": 0,
        "n_nuevas": 0,
        "n_sin_segmentar": 0,
        "aum_total": 0.0,
        "huerfana": False,
    }


# ── Vista COMERCIAL en OPERACIONES (lente por operador, estilo NEGOCIO) ──────
# "Volumen operado" = mismo criterio que NEGOCIO: sum(abs(importe)) sobre estas
# categorías de boleto. AuM = Valuaciones.AuM (ARS). Todo por cuenta del operador.

_CATS_VOLUMEN = (
    "compra", "venta",
    "suscripcion_fci", "solicitud_suscripcion_fci",
    "caucion_tom_ap", "caucion_col_ap",
)

# Categorías "operativas" para la tab Operaciones del cliente (lo que operó):
# = volumen + rescates de FCI. Excluye comisiones/acreencias/administrativos.
_CATS_OPERACIONES = (
    *_CATS_VOLUMEN,
    "rescate_fci", "solicitud_rescate_fci",
)


def _hoy_art() -> date:
    """Fecha de hoy en horario Argentina (UTC-3) para MTD/YTD calendario."""
    return (datetime.now(UTC) - timedelta(hours=3)).date()


def _cuentas_de_operador(operador_email: str) -> tuple[str, ...]:
    """ids de cuenta (Comitentes activas) asignadas a un operador."""
    docs = get_db_clientes()["Comitentes"].find(
        {"operador_email": operador_email, "estado": "Activa"},
        {"_id": 0, "id_cuenta": 1},
    )
    return tuple(sorted(str(d["id_cuenta"]) for d in docs if d.get("id_cuenta")))


def _aum_por_cuenta(ids: tuple[str, ...]) -> dict[str, float]:
    """AuM (último snapshot) por id_cuenta, restringido a `ids`."""
    if not ids:
        return {}
    col = get_db_valuaciones()["AuM"]
    snap = col.find_one({}, {"_id": 0, "fecha_snapshot": 1}, sort=[("fecha_snapshot", -1)])
    if not snap:
        return {}
    out: dict[str, float] = {}
    for d in col.aggregate([
        {"$match": {"fecha_snapshot": snap["fecha_snapshot"], "id_cuenta": {"$in": list(ids)}}},
        {"$group": {"_id": "$id_cuenta", "aum": {"$sum": "$valuacion"}}},
    ]):
        out[str(d["_id"])] = float(d.get("aum") or 0.0)
    return out


def _match_volumen(ids: tuple[str, ...], moneda: str, fecha_desde: str | None) -> dict:
    """$match de NegocioMovimientos por cuentas del operador + categorías + moneda.
    Filtra `id_cuenta` (índice idcuenta_categoria_fecha) — sin regex."""
    m: dict[str, Any] = {
        "moneda": moneda,
        "categoria": {"$in": list(_CATS_VOLUMEN)},
        "id_cuenta": {"$in": list(ids)},
    }
    if fecha_desde:
        m["fecha"] = {"$gte": fecha_desde}
    return m


def _volumen_total(ids: tuple[str, ...], moneda: str, fecha_desde: str | None) -> float:
    if not ids:
        return 0.0
    coll = get_db_cashflow()["NegocioMovimientos"]
    res = list(coll.aggregate([
        {"$match": _match_volumen(ids, moneda, fecha_desde)},
        {"$group": {"_id": None, "v": {"$sum": {"$abs": "$importe"}}}},
    ]))
    return round(float(res[0]["v"]), 2) if res else 0.0


@cached(ttl=300)
def listar_operadores_comercial() -> list[dict[str, Any]]:
    """Operadores para el selector: email, nombre, # cuentas activas."""
    pipeline = [
        {"$match": {"estado": "Activa", "operador_email": {"$ne": None}}},
        {"$group": {
            "_id": "$operador_email",
            "nombre": {"$first": "$operador_nombre"},
            "n_cuentas": {"$sum": 1},
        }},
        {"$sort": {"n_cuentas": -1}},
    ]
    return [
        {"operador_email": d["_id"], "operador_nombre": d.get("nombre"), "n_cuentas": d["n_cuentas"]}
        for d in get_db_clientes()["Comitentes"].aggregate(pipeline)
    ]


# Campos de `Clientes.Comitentes` que enriquecen la FICHA del cliente (tab
# "Datos" del panel izquierdo). Viajan embebidos en cada fila → el front los
# lee del array ya cargado, sin pegar otra query al seleccionar un cliente.
# `denominacion` solo para el header; el resto se renderiza en grilla.
_FICHA_FIELDS = (
    "denominacion",
    "nivel_1", "nivel_2", "nivel_3", "nivel_4", "nivel_5",
    "primer_contacto_comercial", "riesgo_la_ft", "division", "adc", "dma",
)


@cached(ttl=300)
def operador_comercial(*, operador: str, moneda: str = "ARS") -> dict[str, Any]:
    """Resumen (KPIs) + clientes (tabla + ficha) del operador en UNA pasada.

    Optimización: el lookup de cuentas y el AuM se resuelven 1 vez c/u (antes
    `resumen_comercial` y `clientes_comercial` los repetían). El Volumen YTD
    por cuenta y el total salen del mismo `$group`; MTD total es el único
    agregado extra. La FICHA (segmentación de Comitentes) viaja embebida en
    cada fila → seleccionar un cliente no dispara otra query.
    """
    ids = _cuentas_de_operador(operador)
    hoy = _hoy_art()
    aum = _aum_por_cuenta(ids)
    mtd_desde = hoy.replace(day=1).isoformat()
    ytd_desde = hoy.replace(month=1, day=1).isoformat()

    # Volumen YTD por cuenta (un group); el total YTD = suma de las cuentas.
    vol_ytd: dict[str, float] = {}
    if ids:
        for d in get_db_cashflow()["NegocioMovimientos"].aggregate([
            {"$match": _match_volumen(ids, moneda, ytd_desde)},
            {"$group": {"_id": "$id_cuenta", "v": {"$sum": {"$abs": "$importe"}}}},
        ]):
            if d.get("_id"):
                vol_ytd[str(d["_id"])] = round(float(d.get("v") or 0.0), 2)

    # Ficha (segmentación) por cuenta — proyección extendida de Comitentes.
    detalle: dict[str, dict[str, Any]] = {
        str(d["id_cuenta"]): d
        for d in get_db_clientes()["Comitentes"].find(
            {"operador_email": operador, "estado": "Activa"},
            {"_id": 0, "id_cuenta": 1, **{f: 1 for f in _FICHA_FIELDS}},
        )
    }

    clientes = [
        {
            "id_cuenta": idc,
            "denominacion": detalle.get(idc, {}).get("denominacion") or "—",
            "aum": round(aum.get(idc, 0.0), 2),
            "volumen_ytd": vol_ytd.get(idc, 0.0),
            "ficha": {k: detalle.get(idc, {}).get(k) for k in _FICHA_FIELDS},
        }
        for idc in ids
    ]
    clientes.sort(key=lambda x: x["aum"], reverse=True)

    return {
        "operador": operador,
        "moneda": moneda,
        "resumen": {
            "aum_gestionado": round(sum(aum.values()), 2),
            "n_clientes": len(ids),
            "volumen_mtd": _volumen_total(ids, moneda, mtd_desde),
            "volumen_ytd": round(sum(vol_ytd.values()), 2),
        },
        "clientes": clientes,
    }


@cached(ttl=300)
def serie_comercial(
    *, operador: str, metric: str = "volumen", moneda: str = "ARS",
    id_cuenta: str | None = None,
) -> dict[str, Any]:
    """Serie temporal para el gráfico de líneas.

    Sin `id_cuenta` → toda la cartera del operador. Con `id_cuenta` → esa sola
    cuenta (la vista se vuelve interactiva al seleccionar un cliente).
    metric='volumen' → sum(abs(importe)) diario (NegocioMovimientos).
    metric='aum'     → AuM por fecha_snapshot (Valuaciones.AuM, ARS)."""
    ids: tuple[str, ...] = (str(id_cuenta),) if id_cuenta else _cuentas_de_operador(operador)
    if not ids:
        return {"operador": operador, "id_cuenta": id_cuenta, "metric": metric, "serie": []}

    if metric == "aum":
        serie = [
            {"fecha": d["_id"], "valor": round(float(d.get("v") or 0.0), 2)}
            for d in get_db_valuaciones()["AuM"].aggregate([
                {"$match": {"id_cuenta": {"$in": list(ids)}}},
                {"$group": {"_id": "$fecha_snapshot", "v": {"$sum": "$valuacion"}}},
                {"$sort": {"_id": 1}},
            ])
        ]
    else:
        serie = [
            {"fecha": d["_id"], "valor": round(float(d.get("v") or 0.0), 2)}
            for d in get_db_cashflow()["NegocioMovimientos"].aggregate([
                {"$match": _match_volumen(ids, moneda, None)},
                {"$group": {"_id": "$fecha", "v": {"$sum": {"$abs": "$importe"}}}},
                {"$sort": {"_id": 1}},
            ])
        ]
    return {
        "operador": operador, "id_cuenta": id_cuenta,
        "metric": metric, "moneda": moneda, "serie": serie,
    }


@cached(ttl=300)
def portafolio_cliente(*, id_cuenta: str) -> dict[str, Any]:
    """Tenencia del cliente: posiciones de `Valuaciones.AuM` (último snapshot).

    Master-detail estilo AUM: cada posición es una `unidad` con su valuación
    (ARS) y su % sobre el total del cliente. Ordenadas por valuación desc.
    """
    col = get_db_valuaciones()["AuM"]
    snap = col.find_one({}, {"_id": 0, "fecha_snapshot": 1}, sort=[("fecha_snapshot", -1)])
    if not snap:
        return {"id_cuenta": str(id_cuenta), "fecha_snapshot": None, "total": 0.0, "posiciones": []}
    rows = list(col.aggregate([
        {"$match": {"fecha_snapshot": snap["fecha_snapshot"], "id_cuenta": str(id_cuenta)}},
        {"$group": {"_id": "$unidad", "valuacion": {"$sum": "$valuacion"}}},
        {"$sort": {"valuacion": -1}},
    ]))
    total = sum(float(r.get("valuacion") or 0.0) for r in rows)
    posiciones = [
        {
            "unidad": r["_id"],
            "valuacion": round(float(r.get("valuacion") or 0.0), 2),
            "pct": round(100.0 * float(r.get("valuacion") or 0.0) / total, 2) if total else 0.0,
        }
        for r in rows
    ]
    return {
        "id_cuenta": str(id_cuenta),
        "fecha_snapshot": snap["fecha_snapshot"],
        "total": round(total, 2),
        "posiciones": posiciones,
    }


_OP_PROJ = {
    "_id": 0, "fecha": 1, "comprobante": 1, "categoria": 1, "op": 1,
    "ticker": 1, "cantidad": 1, "precio": 1, "importe": 1, "moneda": 1, "plazo": 1,
}


@cached(ttl=300)
def operaciones_cliente(*, id_cuenta: str, limite: int = 300) -> dict[str, Any]:
    """Operaciones del cliente (boletos operativos), recientes primero.

    Misma fuente que el volumen (`CashFlow.NegocioMovimientos`), filtrada por
    `id_cuenta` (índice). Solo categorías de `_CATS_OPERACIONES`
    (compra/venta/FCI/cauciones). Límite por defecto 300, orden fecha desc.
    """
    match: dict[str, Any] = {
        "id_cuenta": str(id_cuenta),
        "categoria": {"$in": list(_CATS_OPERACIONES)},
    }
    rows = list(
        get_db_cashflow()["NegocioMovimientos"]
        .find(match, _OP_PROJ)
        .sort([("fecha", -1), ("comprobante", -1)])
        .limit(int(limite))
    )
    return {"id_cuenta": str(id_cuenta), "n": len(rows), "operaciones": rows}


_ANALISIS_FIELDS = ("denominacion", "nivel_1", "nivel_2", "nivel_3", "nivel_4", "nivel_5")


@cached(ttl=300)
def analisis_comercial(
    *, operador: str, dias_activa: int = 30, dias_dormida: int = 90,
) -> dict[str, Any]:
    """Dataset para la vista ANÁLISIS de un operador (un set de queries).

    Una fila por cliente con: AuM + última operación + días sin operar +
    estado comercial (NUEVA/ACTIVA/ENFRIANDOSE/DORMIDA) + niveles de
    segmentación. Alimenta a la vez: estado comercial, riesgo de churn
    (filtrar enfriándose/dormido por AuM desc) y distribución por nivel
    (agrupar client-side). "Operó" = categorías operativas (_CATS_OPERACIONES).
    """
    ids = _cuentas_de_operador(operador)
    if not ids:
        return {"operador": operador, "dias_activa": dias_activa,
                "dias_dormida": dias_dormida, "clientes": []}

    hoy = _hoy_art()
    aum = _aum_por_cuenta(ids)
    mov = get_db_cashflow()["NegocioMovimientos"]
    cats = list(_CATS_OPERACIONES)
    year_start = date(hoy.year, 1, 1).isoformat()

    # Última operación (operativa) EVER por cuenta — sin ventana. De acá salen:
    # estado comercial, días reales sin operar y si operó en el año en curso.
    ult_op: dict[str, str] = {}
    for d in mov.aggregate([
        {"$match": {"id_cuenta": {"$in": list(ids)}, "categoria": {"$in": cats}}},
        {"$group": {"_id": "$id_cuenta", "ult": {"$max": "$fecha"}}},
    ]):
        if d.get("_id") and d.get("ult"):
            ult_op[str(d["_id"])] = d["ult"][:10]

    detalle: dict[str, dict[str, Any]] = {
        str(d["id_cuenta"]): d
        for d in get_db_clientes()["Comitentes"].find(
            {"operador_email": operador, "estado": "Activa"},
            {"_id": 0, "id_cuenta": 1, **{f: 1 for f in _ANALISIS_FIELDS}},
        )
    }

    clientes = []
    for idc in ids:
        ult = ult_op.get(idc)
        dias = (hoy - date.fromisoformat(ult)).days if ult else None
        # estado usa la ventana: días si entra en dias_dormida, si no None (→ DORMIDA).
        dias_win = dias if (dias is not None and dias <= dias_dormida) else None
        est = estado_comercial(dias_win, ult is not None, dias_activa, dias_dormida)
        f = detalle.get(idc, {})
        clientes.append({
            "id_cuenta": idc,
            "denominacion": f.get("denominacion") or "—",
            "aum": round(aum.get(idc, 0.0), 2),
            "ultima_op": ult,
            "dias_sin_operar": dias,
            "estado": est,
            "opero_ytd": bool(ult) and ult >= year_start,
            **{n: f.get(n) for n in _ANALISIS_FIELDS if n != "denominacion"},
        })
    clientes.sort(key=lambda x: x["aum"], reverse=True)
    return {
        "operador": operador,
        "dias_activa": dias_activa,
        "dias_dormida": dias_dormida,
        "clientes": clientes,
    }


@cached(ttl=300)
def resumen_por_operador(*, dias_activa: int = 30, dias_dormida: int = 90) -> dict[str, Any]:
    """Resumen comercial agrupado por operador. Ver módulo."""
    hoy = datetime.now(UTC).date()

    # 1) Master: cuentas comitentes activas (legal) con operador + segmento.
    cuentas = list(
        get_db_clientes()["Comitentes"].find(
            {"estado": "Activa"},
            {"_id": 0, "id_cuenta": 1, "operador_email": 1,
             "operador_nombre": 1, "nivel_1": 1},
        )
    )

    # 2) AuM por id_cuenta (último snapshot).
    aum_col = get_db_valuaciones()["AuM"]
    ult_snap = aum_col.find_one({}, {"_id": 0, "fecha_snapshot": 1}, sort=[("fecha_snapshot", -1)])
    aum_por_cuenta: dict[str, float] = {}
    if ult_snap:
        for d in aum_col.aggregate([
            {"$match": {"fecha_snapshot": ult_snap["fecha_snapshot"]}},
            {"$group": {"_id": "$id_cuenta", "aum": {"$sum": "$valuacion"}}},
        ]):
            aum_por_cuenta[str(d["_id"])] = float(d.get("aum") or 0.0)

    # 3) Actividad: última op por cuenta dentro de la ventana + set "operó alguna vez".
    mov = get_db_cashflow()["NegocioMovimientos"]
    desde = (hoy - timedelta(days=dias_dormida)).isoformat()
    dias_ult_op: dict[str, int] = {}
    for d in mov.aggregate([
        {"$match": {"fecha": {"$gte": desde}, "id_cuenta": {"$ne": None}}},
        {"$group": {"_id": "$id_cuenta", "ult": {"$max": "$fecha"}}},
    ]):
        idc = d.get("_id")
        if not idc or not d.get("ult"):
            continue
        try:
            dias_ult_op[str(idc)] = (hoy - date.fromisoformat(d["ult"][:10])).days
        except ValueError:
            continue
    opero_alguna_vez: set[str] = {str(c) for c in mov.distinct("id_cuenta") if c}

    # 4) Emails de usuarios reales (para flag de cuentas huérfanas).
    emails_users = {
        str(e).lower().strip()
        for e in get_db_manager()["Users"].distinct("email")
        if e
    }

    # 5) Agregar por operador.
    ops: dict[str, dict] = {}
    for c in cuentas:
        idc = str(c.get("id_cuenta"))
        email = (c.get("operador_email") or "").strip()
        key = email.lower() or "(sin operador)"
        o = ops.get(key)
        if o is None:
            o = ops[key] = _nuevo_operador(email or None, c.get("operador_nombre"))
        o["n_cuentas"] += 1
        o["aum_total"] += aum_por_cuenta.get(idc, 0.0)
        if not c.get("nivel_1"):
            o["n_sin_segmentar"] += 1
        est = estado_comercial(
            dias_ult_op.get(idc), idc in opero_alguna_vez, dias_activa, dias_dormida,
        )
        o[_BUCKET[est]] += 1

    for o in ops.values():
        em = (o["operador_email"] or "").lower()
        o["huerfana"] = bool(em) and em not in emails_users

    operadores = sorted(ops.values(), key=lambda x: x["aum_total"], reverse=True)
    return {
        "operadores": operadores,
        "dias_activa": dias_activa,
        "dias_dormida": dias_dormida,
        "snapshot_aum": (ult_snap or {}).get("fecha_snapshot"),
        "total_cuentas": sum(o["n_cuentas"] for o in operadores),
        "total_aum": sum(o["aum_total"] for o in operadores),
    }
