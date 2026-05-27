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

# Sentinel del selector: "Todos los operadores" (vista del jefe). Cuando llega
# esto, las agregaciones NO filtran por id_cuenta (evita un $in de ~1770 ids).
TODOS = "__todos__"

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


# ── Unificación de monedas (ARS/USD) ─────────────────────────────────────────
# Valor PESIFICADO (ARS) de un boleto: ARS → |importe|; USD → |importe| × mep
# (el `mep` snapshot del propio boleto). Así el volumen incluye los boletos USD
# (antes se descartaban por el filtro moneda==ARS). En vista USD se divide el
# total ARS por el MEP actual (ver `_factor_usd`).
_PESIF = {
    "$cond": [
        {"$eq": ["$moneda", "ARS"]},
        {"$abs": "$importe"},
        {"$multiply": [{"$abs": "$importe"}, {"$ifNull": ["$mep", 0]}]},
    ],
}


def _factor_usd(moneda: str) -> float | None:
    """Factor de conversión a USD (MEP actual) o None si la vista es ARS / no hay MEP."""
    if (moneda or "ARS").upper() != "USD":
        return None
    from api.services.macro import get_ultimo_mep
    mep = get_ultimo_mep().get("mep")
    return float(mep) if mep else None


def _cv(x: float, factor: float | None) -> float:
    """ARS→USD si hay factor (divide por MEP); redondea a 2."""
    return round(x / factor, 2) if factor else round(float(x), 2)


def _cuentas_de_operador(operador_email: str) -> tuple[str, ...]:
    """ids de cuenta (Comitentes activas) de un operador. `TODOS` → todas."""
    q = {"estado": "Activa"} if operador_email == TODOS else {
        "operador_email": operador_email, "estado": "Activa"}
    docs = get_db_clientes()["Comitentes"].find(q, {"_id": 0, "id_cuenta": 1})
    return tuple(sorted(str(d["id_cuenta"]) for d in docs if d.get("id_cuenta")))


def _aum_por_cuenta(ids: tuple[str, ...], *, todos: bool = False) -> dict[str, float]:
    """AuM (último snapshot) por id_cuenta. `todos` → no filtra por ids (agrega
    todas las cuentas del snapshot, sin `$in`)."""
    if not todos and not ids:
        return {}
    col = get_db_valuaciones()["AuM"]
    snap = col.find_one({}, {"_id": 0, "fecha_snapshot": 1}, sort=[("fecha_snapshot", -1)])
    if not snap:
        return {}
    match: dict[str, Any] = {"fecha_snapshot": snap["fecha_snapshot"]}
    if not todos:
        match["id_cuenta"] = {"$in": list(ids)}
    out: dict[str, float] = {}
    for d in col.aggregate([
        {"$match": match},
        {"$group": {"_id": "$id_cuenta", "aum": {"$sum": "$valuacion"}}},
    ]):
        out[str(d["_id"])] = float(d.get("aum") or 0.0)
    return out


def _match_volumen(ids: tuple[str, ...], fecha_desde: str | None,
                   *, todos: bool = False) -> dict:
    """$match de NegocioMovimientos por cuentas del operador + categorías
    operativas. NO filtra por moneda → entran ARS y USD (el valor se pesifica con
    `_PESIF`). Filtra `id_cuenta` (índice idcuenta_categoria_fecha); `todos` → toda
    la mesa, sin filtro de cuenta."""
    m: dict[str, Any] = {"categoria": {"$in": list(_CATS_VOLUMEN)}}
    if not todos:
        m["id_cuenta"] = {"$in": list(ids)}
    if fecha_desde:
        m["fecha"] = {"$gte": fecha_desde}
    return m


def _volumen_total(ids: tuple[str, ...], fecha_desde: str | None,
                   *, todos: bool = False) -> float:
    """Volumen pesificado (ARS), incluye boletos USD (× mep del boleto)."""
    if not todos and not ids:
        return 0.0
    coll = get_db_cashflow()["NegocioMovimientos"]
    res = list(coll.aggregate([
        {"$match": _match_volumen(ids, fecha_desde, todos=todos)},
        {"$group": {"_id": None, "v": {"$sum": _PESIF}}},
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
    "telefono", "email",
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
    es_todos = operador == TODOS
    ids = _cuentas_de_operador(operador)
    hoy = _hoy_art()
    factor = _factor_usd(moneda)
    aum = _aum_por_cuenta(ids, todos=es_todos)
    mtd_desde = hoy.replace(day=1).isoformat()
    ytd_desde = hoy.replace(month=1, day=1).isoformat()

    # Volumen YTD por cuenta (pesificado, incluye USD); total = suma de cuentas.
    vol_ytd: dict[str, float] = {}
    if ids or es_todos:
        for d in get_db_cashflow()["NegocioMovimientos"].aggregate([
            {"$match": _match_volumen(ids, ytd_desde, todos=es_todos)},
            {"$group": {"_id": "$id_cuenta", "v": {"$sum": _PESIF}}},
        ]):
            if d.get("_id"):
                vol_ytd[str(d["_id"])] = float(d.get("v") or 0.0)

    # Ficha (segmentación) por cuenta — proyección extendida de Comitentes.
    cuentas_q = {"estado": "Activa"} if es_todos else {"operador_email": operador, "estado": "Activa"}
    detalle: dict[str, dict[str, Any]] = {
        str(d["id_cuenta"]): d
        for d in get_db_clientes()["Comitentes"].find(
            cuentas_q,
            {"_id": 0, "id_cuenta": 1, **{f: 1 for f in _FICHA_FIELDS}},
        )
    }

    clientes = [
        {
            "id_cuenta": idc,
            "denominacion": detalle.get(idc, {}).get("denominacion") or "—",
            "aum": _cv(aum.get(idc, 0.0), factor),
            "volumen_ytd": _cv(vol_ytd.get(idc, 0.0), factor),
            "ficha": {k: detalle.get(idc, {}).get(k) for k in _FICHA_FIELDS},
        }
        for idc in ids
    ]
    clientes.sort(key=lambda x: x["aum"], reverse=True)

    return {
        "operador": operador,
        "moneda": moneda,
        "resumen": {
            "aum_gestionado": _cv(sum(aum.get(idc, 0.0) for idc in ids), factor),
            "n_clientes": len(ids),
            "volumen_mtd": _cv(_volumen_total(ids, mtd_desde, todos=es_todos), factor),
            "volumen_ytd": _cv(sum(vol_ytd.values()), factor),
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
    es_todos = (operador == TODOS) and not id_cuenta
    factor = _factor_usd(moneda)
    ids: tuple[str, ...] = (str(id_cuenta),) if id_cuenta else _cuentas_de_operador(operador)
    if not ids and not es_todos:
        return {"operador": operador, "id_cuenta": id_cuenta, "metric": metric, "serie": []}

    if metric == "aum":
        aum_match: dict[str, Any] = {} if es_todos else {"id_cuenta": {"$in": list(ids)}}
        serie = [
            {"fecha": d["_id"], "valor": _cv(float(d.get("v") or 0.0), factor)}
            for d in get_db_valuaciones()["AuM"].aggregate([
                {"$match": aum_match},
                {"$group": {"_id": "$fecha_snapshot", "v": {"$sum": "$valuacion"}}},
                {"$sort": {"_id": 1}},
            ])
        ]
    else:
        serie = [
            {"fecha": d["_id"], "valor": _cv(float(d.get("v") or 0.0), factor)}
            for d in get_db_cashflow()["NegocioMovimientos"].aggregate([
                {"$match": _match_volumen(ids, None, todos=es_todos)},
                {"$group": {"_id": "$fecha", "v": {"$sum": _PESIF}}},
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


_ANALISIS_FIELDS = ("denominacion", "telefono", "nivel_1", "nivel_2", "nivel_3", "nivel_4", "nivel_5")


@cached(ttl=300)
def analisis_comercial(
    *, operador: str, dias_activa: int = 45, dias_dormida: int = 90, moneda: str = "ARS",
) -> dict[str, Any]:
    """Dataset para la vista ANÁLISIS de un operador (un set de queries).

    Una fila por cliente con: AuM + última operación + días sin operar +
    estado comercial (NUEVA/ACTIVA/ENFRIANDOSE/DORMIDA) + niveles de
    segmentación. Alimenta a la vez: estado comercial, riesgo de churn
    (filtrar enfriándose/dormido por AuM desc) y distribución por nivel
    (agrupar client-side). "Operó" = categorías operativas (_CATS_OPERACIONES).
    """
    es_todos = operador == TODOS
    ids = _cuentas_de_operador(operador)
    if not ids and not es_todos:
        return {"operador": operador, "dias_activa": dias_activa,
                "dias_dormida": dias_dormida, "clientes": []}

    hoy = _hoy_art()
    factor = _factor_usd(moneda)
    aum = _aum_por_cuenta(ids, todos=es_todos)
    mov = get_db_cashflow()["NegocioMovimientos"]
    cats = list(_CATS_OPERACIONES)
    year_start = date(hoy.year, 1, 1).isoformat()

    # Última operación (operativa) EVER por cuenta — sin ventana. De acá salen:
    # estado comercial, días reales sin operar y si operó en el año en curso.
    ult_match: dict[str, Any] = {"categoria": {"$in": cats}}
    if not es_todos:
        ult_match["id_cuenta"] = {"$in": list(ids)}
    ult_op: dict[str, str] = {}
    for d in mov.aggregate([
        {"$match": ult_match},
        {"$group": {"_id": "$id_cuenta", "ult": {"$max": "$fecha"}}},
    ]):
        if d.get("_id") and d.get("ult"):
            ult_op[str(d["_id"])] = d["ult"][:10]

    cuentas_q = {"estado": "Activa"} if es_todos else {"operador_email": operador, "estado": "Activa"}
    detalle: dict[str, dict[str, Any]] = {
        str(d["id_cuenta"]): d
        for d in get_db_clientes()["Comitentes"].find(
            cuentas_q,
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
            "aum": _cv(aum.get(idc, 0.0), factor),
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
def resumen_por_operador(*, dias_activa: int = 45, dias_dormida: int = 90) -> dict[str, Any]:
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


# ── Vista INFORME (global, transversal a toda la mesa, no por operador) ───────
# Reporte de la mesa: cuentas por segmento (acumulado por fecha de alta),
# volumen + aranceles por comercial (ranking) y aranceles por segmento.
# Ver docs/TABLERO_COMERCIAL.md [5].


def _fin_de_mes(anio: int, mes: int) -> datetime:
    """Último instante del mes (naive, como se guardan los fecha_alta_legajo)."""
    ini_sig = datetime(anio + 1, 1, 1) if mes == 12 else datetime(anio, mes + 1, 1)
    return ini_sig - timedelta(seconds=1)


@cached(ttl=600)
def informe_cuentas_por_segmento(*, hasta: str | None = None, operador: str | None = None) -> dict[str, Any]:
    """Tabla 1: # cuentas (Activas) por nivel_1, ACUMULADO a fin del mes `hasta`
    (YYYY-MM; default mes actual), por `fecha_alta_legajo`. Incluye `mes_min`
    para acotar el selector del frontend. `operador` opcional → solo sus cuentas."""
    col = get_db_clientes()["Comitentes"]
    hoy = _hoy_art()
    anio, mes = (int(hasta[:4]), int(hasta[5:7])) if hasta else (hoy.year, hoy.month)
    corte = _fin_de_mes(anio, mes)

    match: dict[str, Any] = {"estado": "Activa", "fecha_alta_legajo": {"$lte": corte}}
    if operador:
        match["operador_email"] = operador
    segmentos = [
        {"segmento": d["_id"], "n": d["n"]}
        for d in col.aggregate([
            {"$match": match},
            {"$group": {"_id": {"$ifNull": ["$nivel_1", "(sin segmentar)"]}, "n": {"$sum": 1}}},
            {"$sort": {"n": -1}},
        ])
    ]
    primera = col.find_one(
        {"fecha_alta_legajo": {"$ne": None}},
        {"_id": 0, "fecha_alta_legajo": 1},
        sort=[("fecha_alta_legajo", 1)],
    )
    fa = (primera or {}).get("fecha_alta_legajo")
    mes_min = f"{fa.year:04d}-{fa.month:02d}" if fa else f"{hoy.year:04d}-{hoy.month:02d}"
    return {
        "mes": f"{anio:04d}-{mes:02d}",
        "mes_min": mes_min,
        "mes_actual": f"{hoy.year:04d}-{hoy.month:02d}",
        "total": sum(s["n"] for s in segmentos),
        "segmentos": segmentos,
    }


@cached(ttl=600)
def informe_comercial(*, moneda: str = "ARS") -> dict[str, Any]:
    """Tablas 2 y 3 del Informe (global). Una pasada por NegocioMovimientos
    (volumen pesificado incluyendo USD + arancel, total histórico y mes actual),
    con roll-up por operador (tabla 2, ranking por volumen) y por nivel_1 (tabla 3).
    `moneda='USD'` dolariza al MEP actual."""
    hoy = _hoy_art()
    factor = _factor_usd(moneda)
    mes_start = hoy.replace(day=1).isoformat()
    cats = list(_CATS_VOLUMEN)

    por_cuenta: dict[str, dict] = {}
    for d in get_db_cashflow()["NegocioMovimientos"].aggregate([
        {"$match": {"$or": [{"categoria": {"$in": cats}}, {"arancel": {"$gt": 0}}]}},
        {"$group": {
            "_id": "$id_cuenta",
            "vol_total": {"$sum": {"$cond": [{"$in": ["$categoria", cats]}, _PESIF, 0]}},
            "vol_mes": {"$sum": {"$cond": [
                {"$and": [{"$in": ["$categoria", cats]}, {"$gte": ["$fecha", mes_start]}]},
                _PESIF, 0]}},
            "ar_total": {"$sum": {"$ifNull": ["$arancel", 0]}},
            "ar_mes": {"$sum": {"$cond": [
                {"$gte": ["$fecha", mes_start]}, {"$ifNull": ["$arancel", 0]}, 0]}},
            # # operaciones operativas (cualquier moneda) → ticket promedio.
            "n_ops": {"$sum": {"$cond": [{"$in": ["$categoria", cats]}, 1, 0]}},
        }},
    ]):
        if d.get("_id"):
            por_cuenta[str(d["_id"])] = d

    detalle = {
        str(c["id_cuenta"]): c
        for c in get_db_clientes()["Comitentes"].find(
            {"estado": "Activa"},
            {"_id": 0, "id_cuenta": 1, "operador_email": 1, "operador_nombre": 1, "nivel_1": 1},
        )
    }

    ops: dict[str, dict] = {}
    segs: dict[str, dict] = {}
    for idc, agg in por_cuenta.items():
        info = detalle.get(idc, {})
        key = (info.get("operador_email") or "").strip().lower() or "(sin operador)"
        o = ops.get(key)
        if o is None:
            o = ops[key] = {
                "operador_email": info.get("operador_email"),
                "operador_nombre": info.get("operador_nombre") or info.get("operador_email") or "(sin operador)",
                "vol_total": 0.0, "vol_mes": 0.0, "ar_total": 0.0, "ar_mes": 0.0, "n_ops": 0,
            }
        o["vol_total"] += agg["vol_total"]
        o["vol_mes"] += agg["vol_mes"]
        o["ar_total"] += agg["ar_total"]
        o["ar_mes"] += agg["ar_mes"]
        o["n_ops"] += agg.get("n_ops", 0)

        seg = info.get("nivel_1") or "(sin segmentar)"
        s = segs.get(seg)
        if s is None:
            s = segs[seg] = {"segmento": seg, "ar_total": 0.0, "ar_mes": 0.0,
                             "vol_total": 0.0, "n_ops": 0, "n_cuentas": 0}
        s["ar_total"] += agg["ar_total"]
        s["ar_mes"] += agg["ar_mes"]
        s["vol_total"] += agg["vol_total"]
        s["n_ops"] += agg.get("n_ops", 0)
        if agg["ar_total"] > 0:
            s["n_cuentas"] += 1

    def _ticket(vol: float, n: int) -> float:
        return round(vol / n, 2) if n else 0.0

    # Dolarización (÷ MEP si USD) al cerrar los totales.
    for o in ops.values():
        for k in ("vol_total", "vol_mes", "ar_total", "ar_mes"):
            o[k] = _cv(o[k], factor)
    comerciales = sorted(ops.values(), key=lambda x: x["vol_total"], reverse=True)
    for i, o in enumerate(comerciales, 1):
        o["rank"] = i
        o["ticket_promedio"] = _ticket(o["vol_total"], o["n_ops"])
    for s in segs.values():
        for k in ("ar_total", "ar_mes", "vol_total"):
            s[k] = _cv(s[k], factor)
    segmentos = sorted(segs.values(), key=lambda x: x["ar_total"], reverse=True)
    for s in segmentos:
        s["ticket_promedio"] = _ticket(s["vol_total"], s["n_ops"])
    return {
        "mes_actual": f"{hoy.year:04d}-{hoy.month:02d}",
        "comerciales": comerciales,
        "aranceles_segmento": segmentos,
    }


@cached(ttl=120)
def debug_comercial(
    *, operador: str | None = None, segmento: str | None = None, moneda: str = "ARS",
) -> dict[str, Any]:
    """Auditoría del cálculo del Informe (Manager → Diagnóstico): para un operador
    O un segmento, devuelve el desglose POR CUENTA (# ops, volumen total/mes,
    arancel) + totales + ticket promedio. Volumen pesificado (incluye USD);
    `moneda='USD'` dolariza. Responde "cuántas operaciones reconoce y qué volúmenes"."""
    hoy = _hoy_art()
    factor = _factor_usd(moneda)
    mes_start = hoy.replace(day=1).isoformat()
    cats = list(_CATS_VOLUMEN)

    q: dict[str, Any] = {"estado": "Activa"}
    if operador:
        q["operador_email"] = operador
    if segmento:
        q["nivel_1"] = None if segmento == "(sin segmentar)" else segmento
    cuentas = {
        str(c["id_cuenta"]): c.get("denominacion") or "—"
        for c in get_db_clientes()["Comitentes"].find(
            q, {"_id": 0, "id_cuenta": 1, "denominacion": 1})
    }
    ids = list(cuentas.keys())
    if not ids:
        return {"operador": operador, "segmento": segmento or "todos", "n_cuentas_filtradas": 0,
                "n_cuentas_con_actividad": 0, "totales": {}, "cuentas": []}

    filas = []
    for d in get_db_cashflow()["NegocioMovimientos"].aggregate([
        {"$match": {"id_cuenta": {"$in": ids},
                    "$or": [{"categoria": {"$in": cats}}, {"arancel": {"$gt": 0}}]}},
        {"$group": {
            "_id": "$id_cuenta",
            "n_ops": {"$sum": {"$cond": [{"$in": ["$categoria", cats]}, 1, 0]}},
            "vol_total": {"$sum": {"$cond": [{"$in": ["$categoria", cats]}, _PESIF, 0]}},
            "vol_mes": {"$sum": {"$cond": [
                {"$and": [{"$in": ["$categoria", cats]}, {"$gte": ["$fecha", mes_start]}]},
                _PESIF, 0]}},
            "ar_total": {"$sum": {"$ifNull": ["$arancel", 0]}},
        }},
    ]):
        idc = str(d["_id"])
        filas.append({
            "id_cuenta": idc, "denominacion": cuentas.get(idc, "—"),
            "n_ops": int(d.get("n_ops", 0)),
            "vol_total": _cv(float(d.get("vol_total") or 0.0), factor),
            "vol_mes": _cv(float(d.get("vol_mes") or 0.0), factor),
            "ar_total": _cv(float(d.get("ar_total") or 0.0), factor),
        })
    filas.sort(key=lambda x: x["vol_total"], reverse=True)
    n_ops = sum(f["n_ops"] for f in filas)
    vol_total = round(sum(f["vol_total"] for f in filas), 2)
    return {
        "operador": operador, "segmento": segmento or "todos",
        "n_cuentas_filtradas": len(ids),
        "n_cuentas_con_actividad": len(filas),
        "totales": {
            "n_ops": n_ops,
            "vol_total": vol_total,
            "vol_mes": round(sum(f["vol_mes"] for f in filas), 2),
            "ar_total": round(sum(f["ar_total"] for f in filas), 2),
            "ticket_promedio": round(vol_total / n_ops, 2) if n_ops else 0.0,
        },
        "cuentas": filas[:300],
    }


@cached(ttl=300)
def informe_aranceles_segmento(*, operador: str, moneda: str = "ARS") -> dict[str, Any]:
    """Aranceles + ticket promedio por segmento, SOLO de las cuentas del operador
    (re-scope de la Q3 al tocar un comercial). Mismo shape que `aranceles_segmento`
    de `informe_comercial`. El `$in` es por las cuentas del operador (set chico)."""
    hoy = _hoy_art()
    factor = _factor_usd(moneda)
    mes_start = hoy.replace(day=1).isoformat()
    cats = list(_CATS_VOLUMEN)
    cuentas = {
        str(c["id_cuenta"]): (c.get("nivel_1") or "(sin segmentar)")
        for c in get_db_clientes()["Comitentes"].find(
            {"operador_email": operador, "estado": "Activa"},
            {"_id": 0, "id_cuenta": 1, "nivel_1": 1},
        )
    }
    ids = list(cuentas.keys())
    if not ids:
        return {"operador": operador, "aranceles_segmento": []}

    segs: dict[str, dict] = {}
    for d in get_db_cashflow()["NegocioMovimientos"].aggregate([
        {"$match": {"id_cuenta": {"$in": ids},
                    "$or": [{"categoria": {"$in": cats}}, {"arancel": {"$gt": 0}}]}},
        {"$group": {
            "_id": "$id_cuenta",
            "vol_total": {"$sum": {"$cond": [{"$in": ["$categoria", cats]}, _PESIF, 0]}},
            "ar_total": {"$sum": {"$ifNull": ["$arancel", 0]}},
            "ar_mes": {"$sum": {"$cond": [
                {"$gte": ["$fecha", mes_start]}, {"$ifNull": ["$arancel", 0]}, 0]}},
            "n_ops": {"$sum": {"$cond": [{"$in": ["$categoria", cats]}, 1, 0]}},
        }},
    ]):
        seg = cuentas.get(str(d["_id"]), "(sin segmentar)")
        s = segs.get(seg)
        if s is None:
            s = segs[seg] = {"segmento": seg, "ar_total": 0.0, "ar_mes": 0.0,
                             "vol_total": 0.0, "n_ops": 0, "n_cuentas": 0}
        s["ar_total"] += d.get("ar_total", 0.0)
        s["ar_mes"] += d.get("ar_mes", 0.0)
        s["vol_total"] += d.get("vol_total", 0.0)
        s["n_ops"] += d.get("n_ops", 0)
        if d.get("ar_total", 0.0) > 0:
            s["n_cuentas"] += 1

    out = sorted(segs.values(), key=lambda x: x["ar_total"], reverse=True)
    for s in out:
        for k in ("ar_total", "ar_mes", "vol_total"):
            s[k] = _cv(s[k], factor)
        s["ticket_promedio"] = round(s["vol_total"] / s["n_ops"], 2) if s["n_ops"] else 0.0
    return {"operador": operador, "aranceles_segmento": out}


@cached(ttl=300)
def informe_segmento_detalle(
    *, segmento: str | None = None, operador: str | None = None, moneda: str = "ARS",
) -> dict[str, Any]:
    """Detalle de un segmento (nivel_1) para la Q4 dinámica del Informe.

    Dos vistas del mismo segmento:
      - `clientes`: cuentas del segmento con su arancel (total + mes), desc.
      - `operaciones`: boletos con arancel > 0 de esas cuentas (los que generaron
        el arancel), por fecha desc, acotado.
    `segmento` None / "" / "todos" → TODOS los segmentos (sin filtro de nivel_1,
    vista por defecto). `"(sin segmentar)"` → nivel_1 == null. `operador` opcional
    → solo las cuentas de ese comercial.
    """
    hoy = _hoy_art()
    factor = _factor_usd(moneda)
    mes_start = hoy.replace(day=1).isoformat()
    if not segmento or segmento == "todos":
        match_seg: dict[str, Any] = {}
    elif segmento == "(sin segmentar)":
        match_seg = {"nivel_1": None}
    else:
        match_seg = {"nivel_1": segmento}
    match_cli: dict[str, Any] = {"estado": "Activa", **match_seg}
    if operador:
        match_cli["operador_email"] = operador

    detalle = {
        str(c["id_cuenta"]): c.get("denominacion")
        for c in get_db_clientes()["Comitentes"].find(
            match_cli,
            {"_id": 0, "id_cuenta": 1, "denominacion": 1},
        )
    }
    ids = list(detalle.keys())
    if not ids:
        return {"segmento": segmento or "todos", "n_clientes": 0, "clientes": [], "operaciones": []}

    mov = get_db_cashflow()["NegocioMovimientos"]

    clientes = []
    for d in mov.aggregate([
        {"$match": {"id_cuenta": {"$in": ids}, "arancel": {"$gt": 0}}},
        {"$group": {
            "_id": "$id_cuenta",
            "ar_total": {"$sum": "$arancel"},
            "ar_mes": {"$sum": {"$cond": [{"$gte": ["$fecha", mes_start]}, "$arancel", 0]}},
        }},
    ]):
        idc = str(d["_id"])
        clientes.append({
            "id_cuenta": idc,
            "denominacion": detalle.get(idc) or "—",
            "arancel_total": _cv(float(d.get("ar_total") or 0.0), factor),
            "arancel_mes": _cv(float(d.get("ar_mes") or 0.0), factor),
        })
    clientes.sort(key=lambda x: x["arancel_total"], reverse=True)

    operaciones = []
    for d in mov.find(
        {"id_cuenta": {"$in": ids}, "arancel": {"$gt": 0}},
        {"_id": 0, "fecha": 1, "id_cuenta": 1, "comprobante": 1, "ticker": 1,
         "categoria": 1, "op": 1, "importe": 1, "moneda": 1, "arancel": 1},
    ).sort([("fecha", -1), ("comprobante", -1)]).limit(500):
        d["denominacion"] = detalle.get(str(d.get("id_cuenta"))) or "—"
        d["arancel"] = _cv(float(d.get("arancel") or 0.0), factor)
        operaciones.append(d)

    return {
        "segmento": segmento or "todos",
        "n_clientes": len(clientes),
        "clientes": clientes,
        "operaciones": operaciones,
    }
