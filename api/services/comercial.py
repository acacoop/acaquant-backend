"""api/services/comercial.py — Tablero Comercial (lente por operador).

Cruza, todo por `id_cuenta`:
  - QUIÉN   → `Clientes.Comitentes` (operador asignado, segmentación nivel_1).
  - ACTIVIDAD (operó/última op/estado) → `CashFlow.Operaciones` (fuente de
    verdad de operaciones de mercado; join por `cuenta` == id_cuenta).
  - ARANCEL → `CashFlow.Operaciones` (fuente completa; join por `cuenta` == id_cuenta).
  - VOLUMEN → `CashFlow.NegocioMovimientos` (pendiente migrar a Operaciones: falta
    estampar el `mep` del día en Operaciones para pesificar igual que hoy).
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
)
from api.services._negocio_futuros import match_no_futuros
from core.postgres import get_pool

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
    aparece alguna vez en CashFlow.Operaciones.

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


def _comitentes_match(operador_email: str, nivel_1: str | None = None,
                      nivel_3: str | None = None, referido: str | None = None) -> dict[str, Any]:
    """$match de Comitentes activas para el scope comercial: operador (o TODOS) +
    filtros opcionales nivel_1 / nivel_3 / referido. Todos se CRUZAN (intersección)
    — es el comportamiento de los filtros madre de la vista OPERADORES."""
    q: dict[str, Any] = {"estado": "Activa"}
    if operador_email != TODOS:
        q["operador_email"] = operador_email
    if nivel_1:
        q["nivel_1"] = nivel_1
    if nivel_3:
        q["nivel_3"] = nivel_3
    if referido:
        q["referido"] = referido
    return q


def _cuentas_de_operador(operador_email: str, nivel_1: str | None = None,
                         nivel_3: str | None = None, referido: str | None = None) -> tuple[str, ...]:
    """ids de cuenta (Comitentes activas) del scope. `TODOS` sin filtros → todas.
    Lee SQL clientes.comitentes (Mongo Clientes.Comitentes fue eliminada)."""
    conds = ["estado = 'Activa'", "id_cuenta IS NOT NULL"]
    p: dict[str, Any] = {}
    if operador_email != TODOS:
        conds.append("operador_email = %(op)s")
        p["op"] = operador_email
    if nivel_1:
        conds.append("nivel_1 = %(n1)s")
        p["n1"] = nivel_1
    if nivel_3:
        conds.append("nivel_3 = %(n3)s")
        p["n3"] = nivel_3
    if referido:
        conds.append("referido = %(rf)s")
        p["rf"] = referido
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT id_cuenta FROM comitentes WHERE {' AND '.join(conds)}", p)
        return tuple(sorted(str(r[0]) for r in cur.fetchall() if r[0]))


def dimensiones_comercial() -> dict[str, Any]:
    """Combos distintos (operador, nivel_1, nivel_3, referido) de las cuentas activas —
    para poblar y CRUZAR los filtros madre en el frontend (cada uno achica los otros)."""
    combos = [
        {"operador_email": d["_id"]["op"], "operador_nombre": d.get("nombre"),
         "nivel_1": d["_id"].get("n1"), "nivel_3": d["_id"].get("n3"),
         "referido": d["_id"].get("ref"), "n_cuentas": d["n"]}
        for d in get_db_clientes()["Comitentes"].aggregate([
            {"$match": {"estado": "Activa", "operador_email": {"$ne": None}}},
            {"$group": {"_id": {"op": "$operador_email", "n1": "$nivel_1",
                                "n3": "$nivel_3", "ref": "$referido"},
                        "nombre": {"$first": "$operador_nombre"}, "n": {"$sum": 1}}},
        ])
    ]
    return {"combos": combos}


# ── Cobros futuros (acreencias por operador) ─────────────────────────────────
# Tab "Cobros Futuros" de la vista OPERADORES. Cruza CashFlow.Acreencias (cobros
# proyectados por tenencia × calendario contractual, cron jobs.acreencias) con el
# scope del operador (_cuentas_de_operador). El `monto` ya viene en su moneda
# nativa (ARS/USD) — el switch del front elige cuál ver, NO se pesifica (son
# flujos futuros: pesificar con el MEP de hoy distorsiona).

def _acreencias_col():
    return get_db_cashflow()["Acreencias"]


@cached(ttl=300)
def cobros_futuros(*, operador: str, nivel_1: str | None = None,
                   nivel_3: str | None = None, referido: str | None = None) -> dict[str, Any]:
    """Serie diaria (acumulable en el front) + totales por cliente de los cobros
    futuros del scope. El detalle por título va en cobros_futuros_cliente —
    interactivo, no se manda todo el libro en esta llamada."""
    es_todos = operador == TODOS and not nivel_1 and not nivel_3 and not referido
    ids = () if es_todos else _cuentas_de_operador(operador, nivel_1, nivel_3, referido)
    if not es_todos and not ids:
        return {"operador": operador, "serie": [], "clientes": [],
                "total_ars": 0.0, "total_usd": 0.0}

    match: dict[str, Any] = {} if es_todos else {"id_cuenta": {"$in": list(ids)}}
    col = _acreencias_col()

    # Serie diaria por moneda (para el gráfico acumulado del scope).
    serie_map: dict[str, dict[str, Any]] = {}
    for d in col.aggregate([
        {"$match": match},
        {"$group": {"_id": {"fecha": "$fecha_pago", "moneda": "$moneda"},
                    "monto": {"$sum": "$monto"}}},
    ]):
        f = d["_id"]["fecha"]
        e = serie_map.setdefault(f, {"fecha": f, "ars": 0.0, "usd": 0.0})
        if d["_id"].get("moneda") == "USD":
            e["usd"] += float(d.get("monto") or 0.0)
        else:
            e["ars"] += float(d.get("monto") or 0.0)
    serie = [serie_map[f] for f in sorted(serie_map)]

    # Totales por cliente (tabla izquierda). Orden por monto combinado (relevancia);
    # el front re-ordena por la moneda elegida.
    clientes: list[dict[str, Any]] = []
    for d in col.aggregate([
        {"$match": match},
        {"$group": {"_id": "$id_cuenta",
                    "cliente": {"$first": "$cliente"},
                    "ars": {"$sum": {"$cond": [{"$eq": ["$moneda", "USD"]}, 0, "$monto"]}},
                    "usd": {"$sum": {"$cond": [{"$eq": ["$moneda", "USD"]}, "$monto", 0]}}}},
    ]):
        clientes.append({
            "id_cuenta": str(d["_id"]),
            "cliente":   d.get("cliente"),
            "total_ars": round(float(d.get("ars") or 0.0), 2),
            "total_usd": round(float(d.get("usd") or 0.0), 2),
        })
    clientes.sort(key=lambda c: c["total_ars"] + c["total_usd"], reverse=True)

    return {
        "operador":  operador,
        "serie":     serie,
        "clientes":  clientes,
        "total_ars": round(sum(c["total_ars"] for c in clientes), 2),
        "total_usd": round(sum(c["total_usd"] for c in clientes), 2),
    }


@cached(ttl=300)
def cobros_futuros_cliente(*, id_cuenta: str) -> dict[str, Any]:
    """Detalle de un cliente: serie diaria (acumulable) + títulos que cobra."""
    docs = list(_acreencias_col().find(
        {"id_cuenta": str(id_cuenta)},
        {"_id": 0, "fecha_pago": 1, "cliente": 1, "ticker": 1,
         "emisor": 1, "moneda": 1, "monto": 1},
    ).sort([("fecha_pago", 1), ("monto", -1)]))

    serie_map: dict[str, dict[str, Any]] = {}
    titulos: list[dict[str, Any]] = []
    cliente: str | None = None
    for d in docs:
        cliente = cliente or d.get("cliente")
        monto = float(d.get("monto") or 0.0)
        f = d.get("fecha_pago")
        e = serie_map.setdefault(f, {"fecha": f, "ars": 0.0, "usd": 0.0})
        if d.get("moneda") == "USD":
            e["usd"] += monto
        else:
            e["ars"] += monto
        titulos.append({
            "fecha_pago": f, "ticker": d.get("ticker"), "emisor": d.get("emisor"),
            "moneda": d.get("moneda"), "monto": round(monto, 2),
        })
    serie = [serie_map[f] for f in sorted(serie_map)]
    return {
        "id_cuenta": str(id_cuenta),
        "cliente":   cliente,
        "serie":     serie,
        "titulos":   titulos,
        "total_ars": round(sum(e["ars"] for e in serie), 2),
        "total_usd": round(sum(e["usd"] for e in serie), 2),
    }


def _aum_por_cuenta(ids: tuple[str, ...], *, todos: bool = False) -> dict[str, float]:
    """AuM (último snapshot) por id_cuenta. `todos` → no filtra por ids (agrega
    todas las cuentas del snapshot, sin `$in`)."""
    if not todos and not ids:
        return {}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(fecha) FROM portafolio.tenencia WHERE aum = 'si'")
        f = cur.fetchone()[0]
        if not f:
            return {}
        if todos:
            cur.execute("SELECT id_cuenta, SUM(valuacion) FROM portafolio.tenencia "
                        "WHERE fecha = %s AND aum = 'si' GROUP BY id_cuenta", (f,))
        else:
            cur.execute("SELECT id_cuenta, SUM(valuacion) FROM portafolio.tenencia "
                        "WHERE fecha = %s AND aum = 'si' AND id_cuenta = ANY(%s) "
                        "GROUP BY id_cuenta", (f, list(ids)))
        return {str(r[0]): float(r[1] or 0.0) for r in cur.fetchall()}


def _match_volumen(ids: tuple[str, ...], fecha_desde: str | None,
                   *, todos: bool = False) -> dict:
    """$match de NegocioMovimientos por cuentas del operador + categorías
    operativas. NO filtra por moneda → entran ARS y USD (el valor se pesifica con
    `_PESIF`). Filtra `id_cuenta` (índice idcuenta_categoria_fecha); `todos` → toda
    la mesa, sin filtro de cuenta."""
    # Futuros DLR (unidad="USDL") no son arancelables → no entran al volumen
    # comercial. Se filtran acá una vez para todos los consumidores del helper.
    m: dict[str, Any] = {
        "categoria": {"$in": list(_CATS_VOLUMEN)},
        **match_no_futuros(),
    }
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


# ── ARANCEL desde CashFlow.Operaciones (fuente completa) ─────────────────────
# El arancel migró a Operaciones: incluye futuros y demás tipos que
# NegocioMovimientos dejaba afuera. Join por `cuenta` (== id_cuenta). Filtra
# etapa="solicitud" (FCI pedido; la liquidación ya cuenta). NO filtra es_cierre:
# el arancel de caución vive SOLO en el cierre (es_cierre=True) y el filtro
# `arancel>0` ya descarta los cierres no-caución (que no tienen fee). Ver
# diag_aranceles_caucion. El VOLUMEN sigue en NegocioMov hasta estampar el `mep`.


def _aranceles_por_cuenta(
    ids: tuple[str, ...] | list[str] | None, fecha_mes: str,
) -> dict[str, dict[str, float]]:
    """{cuenta: {ar_total, ar_mes}} desde Operaciones. `ids=None` → todas las cuentas."""
    match: dict[str, Any] = {
        "arancel": {"$gt": 0}, "etapa": {"$ne": "solicitud"},
    }
    if ids is not None:
        match["cuenta"] = {"$in": list(ids)}
    out: dict[str, dict[str, float]] = {}
    for d in get_db_cashflow()["Operaciones"].aggregate([
        {"$match": match},
        {"$group": {
            "_id": "$cuenta",
            "ar_total": {"$sum": "$arancel"},
            "ar_mes": {"$sum": {"$cond": [{"$gte": ["$concertacion", fecha_mes]}, "$arancel", 0]}},
        }},
    ]):
        if d.get("_id"):
            out[str(d["_id"])] = {"ar_total": float(d.get("ar_total") or 0.0),
                                  "ar_mes": float(d.get("ar_mes") or 0.0)}
    return out


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
    "primer_contacto_comercial", "riesgo_la_ft", "division", "adc", "dma", "referido",
)


@cached(ttl=300)
def operador_comercial(*, operador: str, moneda: str = "ARS", nivel_1: str | None = None,
                       nivel_3: str | None = None, referido: str | None = None) -> dict[str, Any]:
    """Resumen (KPIs) + clientes (tabla + ficha) del operador en UNA pasada.

    Optimización: el lookup de cuentas y el AuM se resuelven 1 vez c/u (antes
    `resumen_comercial` y `clientes_comercial` los repetían). El Volumen YTD
    por cuenta y el total salen del mismo `$group`; MTD total es el único
    agregado extra. La FICHA (segmentación de Comitentes) viaja embebida en
    cada fila → seleccionar un cliente no dispara otra query.

    `nivel_1`/`nivel_3` cruzan con el operador (intersección): el resumen y la
    tabla quedan acotados a las cuentas que cumplen los 3 filtros.
    """
    # Con filtro de nivel ya NO es "toda la mesa" aunque operador==TODOS: hay un
    # set concreto de ids → se usa el $in (no el atajo `todos`).
    es_todos = operador == TODOS and not nivel_1 and not nivel_3 and not referido
    ids = _cuentas_de_operador(operador, nivel_1, nivel_3, referido)
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
    detalle: dict[str, dict[str, Any]] = {
        str(d["id_cuenta"]): d
        for d in get_db_clientes()["Comitentes"].find(
            _comitentes_match(operador, nivel_1, nivel_3, referido),
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


# ── REFERIDOS (lente por empresa referidora) ─────────────────────────────────
# Al referido (la empresa) solo le interesan SUS cuentas: cuánto operan, AuM,
# aranceles que generaron. NO operador/segmento/ficha. Reusa los mismos helpers
# del tablero comercial (vol pesificado, arancel desde Operaciones).

@cached(ttl=300)
def referido_clientes(*, referido: str, moneda: str = "ARS") -> dict[str, Any]:
    """Clientes referidos por una empresa: por cuenta AuM + volumen (mes/año) +
    arancel (mes/total). Vista REFERIDOS. Valores en `moneda` (USD = ÷ MEP)."""
    ids = _cuentas_de_operador(TODOS, None, None, referido)
    if not ids:
        return {"referido": referido, "moneda": moneda, "clientes": [],
                "resumen": {"n_clientes": 0, "aum_total": 0.0, "vol_mes": 0.0,
                            "vol_ano": 0.0, "arancel_mes": 0.0, "arancel_total": 0.0}}

    hoy = _hoy_art()
    factor = _factor_usd(moneda)
    mtd_desde = hoy.replace(day=1).isoformat()
    ytd_desde = hoy.replace(month=1, day=1).isoformat()
    aum = _aum_por_cuenta(ids)

    # Volumen por cuenta: año (ytd) y mes (mtd) en una sola pasada — SQL
    # operaciones.negocio_movimientos. Pesifica ARS in-place; excluye futuros DLR.
    vol_mes: dict[str, float] = {}
    vol_ano: dict[str, float] = {}
    _pesif = ("CASE WHEN moneda = 'ARS' THEN abs(COALESCE(importe, 0)) "
              "ELSE abs(COALESCE(importe, 0)) * COALESCE(mep, 0) END")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT id_cuenta, SUM({_pesif}) AS ano, "
            f"SUM(CASE WHEN fecha >= %(mtd)s THEN {_pesif} ELSE 0 END) AS mes "
            f"FROM negocio_movimientos "
            f"WHERE categoria = ANY(%(cats)s) AND unidad IS DISTINCT FROM 'USDL' "
            f"AND id_cuenta = ANY(%(ids)s) AND fecha >= %(ytd)s GROUP BY id_cuenta",
            {"cats": list(_CATS_VOLUMEN), "ids": list(ids),
             "ytd": ytd_desde, "mtd": mtd_desde})
        for idc, ano, mes in cur.fetchall():
            if idc:
                vol_ano[str(idc)] = float(ano or 0.0)
                vol_mes[str(idc)] = float(mes or 0.0)

    aranceles = _aranceles_por_cuenta(ids, mtd_desde)
    # Denominación por cuenta desde SQL (clientes.comitentes activas del referido
    # ⋈ clientes.cuentas para el nombre).
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT c.id_cuenta, u.denominacion FROM comitentes c "
            "LEFT JOIN cuentas u ON u.id_cuenta = c.id_cuenta "
            "WHERE c.estado = 'Activa' AND c.referido = %(rf)s AND c.id_cuenta IS NOT NULL",
            {"rf": referido})
        denom = {str(r[0]): r[1] for r in cur.fetchall() if r[0]}

    clientes = []
    for idc in ids:
        ar = aranceles.get(idc, {})
        clientes.append({
            "id_cuenta":     idc,
            "denominacion":  denom.get(idc) or "—",
            "aum":           _cv(aum.get(idc, 0.0), factor),
            "vol_mes":       _cv(vol_mes.get(idc, 0.0), factor),
            "vol_ano":       _cv(vol_ano.get(idc, 0.0), factor),
            "arancel_mes":   _cv(ar.get("ar_mes", 0.0), factor),
            "arancel_total": _cv(ar.get("ar_total", 0.0), factor),
        })
    clientes.sort(key=lambda c: c["aum"], reverse=True)

    # Posición AGREGADA del referido (todas sus cuentas) por título — para el panel
    # derecho cuando no hay cliente elegido (títulos + valuación de cada uno).
    posiciones: list[dict[str, Any]] = []
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(fecha) FROM portafolio.tenencia WHERE aum = 'si'")
        f = cur.fetchone()[0]
        if f:
            cur.execute("SELECT unidad, SUM(valuacion) AS v FROM portafolio.tenencia "
                        "WHERE fecha = %s AND aum = 'si' AND id_cuenta = ANY(%s) "
                        "GROUP BY unidad ORDER BY v DESC", (f, list(ids)))
            rows_pos = cur.fetchall()
            tot = sum(float(r[1] or 0.0) for r in rows_pos) or 1.0
            posiciones = [
                {"unidad": r[0],
                 "valuacion": _cv(float(r[1] or 0.0), factor),
                 "pct": round(float(r[1] or 0.0) / tot * 100, 1)}
                for r in rows_pos if r[0]
            ]

    return {
        "referido": referido,
        "moneda": moneda,
        "resumen": {
            "n_clientes":    len(ids),
            "aum_total":     round(sum(c["aum"] for c in clientes), 2),
            "vol_mes":       round(sum(c["vol_mes"] for c in clientes), 2),
            "vol_ano":       round(sum(c["vol_ano"] for c in clientes), 2),
            "arancel_mes":   round(sum(c["arancel_mes"] for c in clientes), 2),
            "arancel_total": round(sum(c["arancel_total"] for c in clientes), 2),
        },
        "clientes": clientes,
        "posiciones": posiciones,
    }


def referido_fci(*, referido: str, desde: str, hasta: str,
                 moneda: str = "ARS") -> dict[str, Any]:
    """Vista REFERIDOS — tabla FCI con la COMISIÓN a la coop, POR FONDO.

    Por cada fondo (unidad) que las cuentas del referido tuvieron en [desde, hasta]:
      - `saldo`    = saldo promedio diario = Σ(valuación de los días con foto) / nº
                     de días con snapshot en el rango (días sin tenencia ponderan 0).
      - `fee`      = honorario ANUAL del fondo (Assets.FEE_ADMIN, fracción; varía por
                     fondo) — None si todavía no se cargó.
      - `comision` = saldo × fee × (días_corridos_del_período / 365). El fee es anual:
                     se prorratea por los días del rango (≈ días del mes).

    El front agrupa por sociedad gerente (`emisor`). Money en `moneda` (USD = ÷ MEP);
    el `fee` es una tasa, NO se convierte. `_fci_assets_map` (portfolio) es la única
    fuente de unidades FCI + emisor + fee."""
    vacio = {"referido": referido, "moneda": moneda, "desde": desde, "hasta": hasta,
             "n_dias": 0, "dias_periodo": 0, "total_saldo": 0.0, "total_comision": 0.0,
             "fondos": []}
    ids = _cuentas_de_operador(TODOS, None, None, referido)
    if not ids:
        return vacio

    from api.services.portfolio import _fci_assets_map
    fci = _fci_assets_map()                       # unidad → {emisor, ticker, fee}
    if not fci:
        return vacio

    # Días CORRIDOS del período para prorratear el fee anual (el fee corre todos
    # los días; el saldo promedio es sobre días hábiles → buena aproximación).
    try:
        dias_periodo = (date.fromisoformat(hasta) - date.fromisoformat(desde)).days + 1
    except ValueError:
        dias_periodo = 0

    factor = _factor_usd(moneda)
    fondos: list[dict[str, Any]] = []
    with get_pool().connection() as conn, conn.cursor() as cur:
        # Denominador del promedio: días con foto en el rango.
        cur.execute("SELECT count(DISTINCT fecha) FROM portafolio.tenencia "
                    "WHERE aum = 'si' AND fecha BETWEEN %s AND %s", (desde, hasta))
        n_dias = cur.fetchone()[0] or 1
        if not dias_periodo:
            dias_periodo = n_dias
        cur.execute(
            "SELECT unidad, SUM(valuacion) AS val FROM portafolio.tenencia "
            "WHERE aum = 'si' AND fecha BETWEEN %s AND %s AND id_cuenta = ANY(%s) "
            "AND unidad = ANY(%s) GROUP BY unidad",
            (desde, hasta, list(ids), list(fci)))
        rows_fci = cur.fetchall()
    for unidad, val in rows_fci:
        info = fci.get(unidad) or {}
        saldo = float(val or 0.0) / n_dias
        fee = info.get("fee")
        comision = saldo * fee * dias_periodo / 365 if fee else None
        fondos.append({
            "unidad":   unidad,
            "emisor":   info.get("emisor") or "—",
            "saldo":    _cv(saldo, factor),
            "fee":      fee,                                   # fracción anual o None
            "comision": _cv(comision, factor) if comision is not None else None,
        })
    fondos.sort(key=lambda x: (x["emisor"], -x["saldo"]))
    return {
        "referido": referido, "moneda": moneda, "desde": desde, "hasta": hasta,
        "n_dias": n_dias, "dias_periodo": dias_periodo,
        "total_saldo":    round(sum(f["saldo"] for f in fondos), 2),
        "total_comision": round(sum(f["comision"] or 0.0 for f in fondos), 2),
        "fondos": fondos,
    }


def clientes_por_fecha(*, operador: str, desde: str, hasta: str,
                       moneda: str = "ARS", nivel_1: str | None = None,
                       nivel_3: str | None = None, referido: str | None = None) -> dict[str, Any]:
    """Clientes que OPERARON en [desde, hasta] con su volumen del período.
    Alimenta la interactividad del chart (click en barra → tabla del día/semana/mes)."""
    es_todos = operador == TODOS and not nivel_1 and not nivel_3 and not referido
    ids = _cuentas_de_operador(operador, nivel_1, nivel_3, referido)
    factor = _factor_usd(moneda)
    aum = _aum_por_cuenta(ids, todos=es_todos)
    match = _match_volumen(ids, None, todos=es_todos)
    match["fecha"] = {"$gte": desde, "$lte": hasta}

    vol: dict[str, float] = {}
    if ids or es_todos:
        for d in get_db_cashflow()["NegocioMovimientos"].aggregate([
            {"$match": match},
            {"$group": {"_id": "$id_cuenta", "v": {"$sum": _PESIF}}},
        ]):
            if d.get("_id"):
                vol[str(d["_id"])] = float(d.get("v") or 0.0)

    detalle = {
        str(d["id_cuenta"]): d
        for d in get_db_clientes()["Comitentes"].find(
            _comitentes_match(operador, nivel_1, nivel_3, referido),
            {"_id": 0, "id_cuenta": 1, **{f: 1 for f in _FICHA_FIELDS}})
    }
    clientes = [{
        "id_cuenta": idc,
        "denominacion": detalle.get(idc, {}).get("denominacion") or "—",
        "aum": _cv(aum.get(idc, 0.0), factor),
        "volumen_periodo": _cv(v, factor),
        "ficha": {k: detalle.get(idc, {}).get(k) for k in _FICHA_FIELDS},
    } for idc, v in vol.items() if v]
    clientes.sort(key=lambda x: x["volumen_periodo"], reverse=True)
    return {"operador": operador, "moneda": moneda, "desde": desde, "hasta": hasta,
            "total_volumen": _cv(sum(vol.values()), factor),
            "n_clientes": len(clientes), "clientes": clientes}


@cached(ttl=300)
def serie_comercial(
    *, operador: str, metric: str = "volumen", moneda: str = "ARS",
    id_cuenta: str | None = None, nivel_1: str | None = None, nivel_3: str | None = None,
    referido: str | None = None,
) -> dict[str, Any]:
    """Serie temporal para el gráfico de líneas.

    Sin `id_cuenta` → toda la cartera del operador (acotada por nivel_1/nivel_3
    si se pasan). Con `id_cuenta` → esa sola cuenta (los niveles se ignoran).
    metric='volumen' → sum(abs(importe)) diario (NegocioMovimientos).
    metric='aum'     → AuM por fecha_snapshot (Valuaciones.AuM, ARS)."""
    es_todos = (operador == TODOS) and not id_cuenta and not nivel_1 and not nivel_3 and not referido
    factor = _factor_usd(moneda)
    ids: tuple[str, ...] = (str(id_cuenta),) if id_cuenta else _cuentas_de_operador(
        operador, nivel_1, nivel_3, referido)
    if not ids and not es_todos:
        return {"operador": operador, "id_cuenta": id_cuenta, "metric": metric, "serie": []}

    if metric == "aum":
        cond = "aum = 'si'" + ("" if es_todos else " AND id_cuenta = ANY(%s)")
        params = () if es_todos else (list(ids),)
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT fecha, SUM(valuacion) AS v FROM portafolio.tenencia "
                        f"WHERE {cond} GROUP BY fecha ORDER BY fecha", params)
            serie = [{"fecha": r[0].isoformat(), "valor": _cv(float(r[1] or 0.0), factor)}
                     for r in cur.fetchall()]
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
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(fecha) FROM portafolio.tenencia WHERE aum = 'si'")
        f = cur.fetchone()[0]
        if not f:
            return {"id_cuenta": str(id_cuenta), "fecha_snapshot": None,
                    "total": 0.0, "posiciones": []}
        cur.execute("SELECT unidad, SUM(valuacion) AS v FROM portafolio.tenencia "
                    "WHERE fecha = %s AND aum = 'si' AND id_cuenta = %s "
                    "GROUP BY unidad ORDER BY v DESC", (f, str(id_cuenta)))
        rows = cur.fetchall()
    snap = {"fecha_snapshot": f.isoformat()}
    total = sum(float(r[1] or 0.0) for r in rows)
    posiciones = [
        {
            "unidad": r[0],
            "valuacion": round(float(r[1] or 0.0), 2),
            "pct": round(100.0 * float(r[1] or 0.0) / total, 2) if total else 0.0,
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
        **match_no_futuros(),
    }
    cf = get_db_cashflow()
    rows = list(
        cf["NegocioMovimientos"]
        .find(match, _OP_PROJ)
        .sort([("fecha", -1), ("comprobante", -1)])
        .limit(int(limite))
    )
    # Arancel por boleto desde CashFlow.Operaciones (fuente autoritativa del fee;
    # NegocioMovimientos no lo trae). Join por boleto == comprobante, una sola query.
    boletos = [str(r["comprobante"]).strip() for r in rows if r.get("comprobante")]
    ar_map: dict[str, float] = {}
    if boletos:
        for o in cf["Operaciones"].find(
            {"boleto": {"$in": boletos}}, {"_id": 0, "boleto": 1, "arancel": 1}
        ):
            if o.get("boleto") is not None:
                ar_map[str(o["boleto"]).strip()] = float(o.get("arancel") or 0.0)
    for r in rows:
        r["arancel"] = ar_map.get(str(r.get("comprobante") or "").strip())
    return {"id_cuenta": str(id_cuenta), "n": len(rows), "operaciones": rows}


_ANALISIS_FIELDS = ("denominacion", "telefono", "nivel_1", "nivel_2", "nivel_3", "nivel_4", "nivel_5")


@cached(ttl=300)
def analisis_comercial(
    *, operador: str, dias_activa: int = 45, dias_dormida: int = 90, moneda: str = "ARS",
    nivel_1: str | None = None, nivel_3: str | None = None, referido: str | None = None,
) -> dict[str, Any]:
    """Dataset para la vista ANÁLISIS de un operador (un set de queries).

    Una fila por cliente con: AuM + última operación + días sin operar +
    estado comercial (NUEVA/ACTIVA/ENFRIANDOSE/DORMIDA) + niveles de
    segmentación. Alimenta a la vez: estado comercial, riesgo de churn
    (filtrar enfriándose/dormido por AuM desc) y distribución por nivel
    (agrupar client-side). "Operó" = categorías operativas (_CATS_OPERACIONES).

    `nivel_1`/`nivel_3` cruzan con el operador (intersección de cuentas).
    """
    es_todos = operador == TODOS and not nivel_1 and not nivel_3 and not referido
    ids = _cuentas_de_operador(operador, nivel_1, nivel_3, referido)
    if not ids and not es_todos:
        return {"operador": operador, "dias_activa": dias_activa,
                "dias_dormida": dias_dormida, "clientes": []}

    hoy = _hoy_art()
    factor = _factor_usd(moneda)
    # Cupo se reporta SIEMPRE en USD al MEP del día (decisión de producto), sin
    # importar el toggle ARS/USD global. Si no hay MEP, queda en None y el
    # frontend muestra "—".
    factor_cupo = _factor_usd("USD")
    aum = _aum_por_cuenta(ids, todos=es_todos)
    year_start = date(hoy.year, 1, 1).isoformat()
    month_start = hoy.replace(day=1).isoformat()  # "activa del mes" = operó en el mes calendario

    # ACTIVIDAD: última operación EVER por cuenta desde CashFlow.Operaciones
    # (fuente de verdad de operaciones de mercado, MÁS COMPLETA que
    # NegocioMovimientos —que filtraba por categoría y dejaba operaciones afuera—).
    # "Operó" = existe ≥1 boleto en Operaciones para esa cuenta; join por `cuenta`
    # (== id_cuenta); fecha = `concertacion` (YYYY-MM-DD). Índice cuenta_concertacion
    # cubre el group. De acá salen: estado comercial, días reales sin operar y YTD/MTD.
    ops_coll = get_db_cashflow()["Operaciones"]
    ult_match: dict[str, Any] = {}
    if not es_todos:
        ult_match["cuenta"] = {"$in": list(ids)}
    ult_op: dict[str, str] = {}
    for d in ops_coll.aggregate([
        {"$match": ult_match},
        {"$group": {"_id": "$cuenta", "ult": {"$max": "$concertacion"}}},
    ]):
        if d.get("_id") and d.get("ult"):
            ult_op[str(d["_id"])] = str(d["ult"])[:10]

    detalle: dict[str, dict[str, Any]] = {
        str(d["id_cuenta"]): d
        for d in get_db_clientes()["Comitentes"].find(
            _comitentes_match(operador, nivel_1, nivel_3, referido),
            {
                "_id": 0, "id_cuenta": 1,
                **{f: 1 for f in _ANALISIS_FIELDS},
                "cupo.transaccional_ars": 1, "cupo.usado_ars": 1,
            },
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
        cupo = f.get("cupo") or {}
        trans_ars = cupo.get("transaccional_ars")
        usado_ars = cupo.get("usado_ars")
        clientes.append({
            "id_cuenta": idc,
            "denominacion": f.get("denominacion") or "—",
            "aum": _cv(aum.get(idc, 0.0), factor),
            "ultima_op": ult,
            "dias_sin_operar": dias,
            "estado": est,
            "opero_ytd": bool(ult) and ult >= year_start,
            "opero_mtd": bool(ult) and ult >= month_start,
            "cupo_transaccional_usd": (
                _cv(float(trans_ars), factor_cupo)
                if trans_ars is not None and factor_cupo else None
            ),
            "cupo_usado_usd": (
                _cv(float(usado_ars), factor_cupo)
                if usado_ars is not None and factor_cupo else None
            ),
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
def actividad_historica(
    *, operador: str, desde: str | None = None, hasta: str | None = None, moneda: str = "ARS",
) -> dict[str, Any]:
    """Serie mensual de CUENTAS ACTIVAS, desde el snapshot `Clientes.ActividadMensual`.

    "Activa en el mes M" = la cuenta operó (≥1 boleto operativo) en el mes
    calendario M. El snapshot lo precalcula `jobs/actividad_mensual.py` (1 doc
    por mes×cuenta, con operador/segmento CONGELADOS al momento del cómputo →
    point-in-time). Acá solo se agrega y se scopea.

    `operador == TODOS` → toda la mesa (sin filtro por operador). `desde`/`hasta`
    son meses "YYYY-MM" inclusive. Devuelve serie ordenada por mes ascendente.
    """
    factor = _factor_usd(moneda)
    match: dict[str, Any] = {}
    if operador != TODOS:
        match["operador_email"] = operador
    if desde or hasta:
        ym: dict[str, str] = {}
        if desde:
            ym["$gte"] = desde
        if hasta:
            ym["$lte"] = hasta
        match["year_month"] = ym

    serie = [
        {
            "year_month": d["_id"],
            "n_activas": d["n_activas"],
            "volumen": _cv(d.get("volumen_ars", 0.0), factor),
        }
        for d in get_db_clientes()["ActividadMensual"].aggregate([
            {"$match": match},
            {"$group": {
                "_id": "$year_month",
                "n_activas": {"$sum": 1},  # 1 doc = 1 cuenta activa (clave única mes×cuenta)
                "volumen_ars": {"$sum": "$volumen_ars"},
            }},
            {"$sort": {"_id": 1}},
        ])
    ]
    return {"operador": operador, "serie": serie}


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


def _por_cuenta_cache(mes_start: str) -> dict[str, dict] | None:
    """Roll-up {id_cuenta: {vol_total, vol_mes, ar_total, ar_mes, n_ops}} desde el
    precompute Clientes.ComercialCache (jobs/comercial_rollup). ~58k filas indexadas
    → barato. None si el cache está vacío (no construido) → el caller cae a live."""
    por: dict[str, dict] = {}
    for d in get_db_clientes()["ComercialCache"].aggregate([
        {"$group": {
            "_id": "$id_cuenta",
            "vol_total": {"$sum": "$vol"},
            "vol_mes": {"$sum": {"$cond": [{"$gte": ["$fecha", mes_start]}, "$vol", 0]}},
            "ar_total": {"$sum": "$arancel"},
            "ar_mes": {"$sum": {"$cond": [{"$gte": ["$fecha", mes_start]}, "$arancel", 0]}},
            "n_ops": {"$sum": "$n_ops"},
        }},
    ], allowDiskUse=True):
        if d.get("_id"):
            por[str(d["_id"])] = d
    return por or None


def _por_cuenta_live(mes_start: str, cats: list[str]) -> dict[str, dict]:
    """Fallback LIVE (C1/C4): pasada por NegocioMovimientos (vol) + arancel de
    Operaciones. Caro (COLLSCAN) — sólo si ComercialCache está vacío."""
    por_cuenta: dict[str, dict] = {}
    for d in get_db_cashflow()["NegocioMovimientos"].aggregate([
        {"$match": {**match_no_futuros(),
                    "$or": [{"categoria": {"$in": cats}}, {"arancel": {"$gt": 0}}]}},
        {"$group": {
            "_id": "$id_cuenta",
            "vol_total": {"$sum": {"$cond": [{"$in": ["$categoria", cats]}, _PESIF, 0]}},
            "vol_mes": {"$sum": {"$cond": [
                {"$and": [{"$in": ["$categoria", cats]}, {"$gte": ["$fecha", mes_start]}]},
                _PESIF, 0]}},
            "ar_total": {"$sum": {"$ifNull": ["$arancel", 0]}},
            "ar_mes": {"$sum": {"$cond": [
                {"$gte": ["$fecha", mes_start]}, {"$ifNull": ["$arancel", 0]}, 0]}},
            "n_ops": {"$sum": {"$cond": [{"$in": ["$categoria", cats]}, 1, 0]}},
        }},
    ]):
        if d.get("_id"):
            por_cuenta[str(d["_id"])] = d
    # Arancel desde Operaciones (completo): pisa el de NegocioMov.
    ar_ops = _aranceles_por_cuenta(None, mes_start)
    for d in por_cuenta.values():
        d["ar_total"] = 0.0
        d["ar_mes"] = 0.0
    for idc, a in ar_ops.items():
        d = por_cuenta.get(idc)
        if d is None:
            d = por_cuenta[idc] = {"_id": idc, "vol_total": 0.0, "vol_mes": 0.0,
                                   "ar_total": 0.0, "ar_mes": 0.0, "n_ops": 0}
        d["ar_total"] = a["ar_total"]
        d["ar_mes"] = a["ar_mes"]
    return por_cuenta


@cached(ttl=600)
def informe_comercial(*, moneda: str = "ARS") -> dict[str, Any]:
    """Tablas 2 y 3 del Informe (global). Roll-up por operador (ranking) y por
    nivel_1 (segmento), de volumen pesificado + arancel (total histórico y mes).
    Lee el precompute Clientes.ComercialCache (rápido); fallback a live si vacío.
    `moneda='USD'` dolariza al MEP actual."""
    hoy = _hoy_art()
    factor = _factor_usd(moneda)
    mes_start = hoy.replace(day=1).isoformat()
    cats = list(_CATS_VOLUMEN)

    por_cuenta = _por_cuenta_cache(mes_start) or _por_cuenta_live(mes_start, cats)

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
        info = detalle.get(idc)
        # Cuenta que NO figura en Comitentes Activa (no-cliente: propia/inactiva/
        # cancelada) → fuera del informe. No tiene operador real para rankear y
        # ensuciaba el ranking con un bucket "(sin operador)" gigante. `detalle`
        # ya está en memoria → el skip es gratis (incluso hace menos trabajo).
        if info is None:
            continue
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

    ar_ops = _aranceles_por_cuenta(ids, mes_start)  # arancel completo (Operaciones)
    filas = []
    vistos: set[str] = set()
    for d in get_db_cashflow()["NegocioMovimientos"].aggregate([
        {"$match": {"id_cuenta": {"$in": ids}, **match_no_futuros(),
                    "$or": [{"categoria": {"$in": cats}}, {"arancel": {"$gt": 0}}]}},
        {"$group": {
            "_id": "$id_cuenta",
            "n_ops": {"$sum": {"$cond": [{"$in": ["$categoria", cats]}, 1, 0]}},
            "vol_total": {"$sum": {"$cond": [{"$in": ["$categoria", cats]}, _PESIF, 0]}},
            "vol_mes": {"$sum": {"$cond": [
                {"$and": [{"$in": ["$categoria", cats]}, {"$gte": ["$fecha", mes_start]}]},
                _PESIF, 0]}},
        }},
    ]):
        idc = str(d["_id"])
        vistos.add(idc)
        filas.append({
            "id_cuenta": idc, "denominacion": cuentas.get(idc, "—"),
            "n_ops": int(d.get("n_ops", 0)),
            "vol_total": _cv(float(d.get("vol_total") or 0.0), factor),
            "vol_mes": _cv(float(d.get("vol_mes") or 0.0), factor),
            "ar_total": _cv(ar_ops.get(idc, {}).get("ar_total", 0.0), factor),
        })
    # cuentas con arancel en Operaciones pero sin volumen en NegocioMov.
    for idc, a in ar_ops.items():
        if idc not in vistos:
            filas.append({
                "id_cuenta": idc, "denominacion": cuentas.get(idc, "—"),
                "n_ops": 0, "vol_total": 0.0, "vol_mes": 0.0,
                "ar_total": _cv(a["ar_total"], factor),
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

    ar_ops = _aranceles_por_cuenta(ids, mes_start)  # arancel completo (Operaciones)
    # vol/n_ops por cuenta desde NegocioMov (categorías operativas).
    vol_cuenta: dict[str, dict] = {}
    for d in get_db_cashflow()["NegocioMovimientos"].aggregate([
        {"$match": {"id_cuenta": {"$in": ids}, **match_no_futuros(), "categoria": {"$in": cats}}},
        {"$group": {"_id": "$id_cuenta", "vol_total": {"$sum": _PESIF}, "n_ops": {"$sum": 1}}},
    ]):
        vol_cuenta[str(d["_id"])] = d

    segs: dict[str, dict] = {}
    for idc in set(vol_cuenta) | set(ar_ops):
        seg = cuentas.get(idc, "(sin segmentar)")
        s = segs.get(seg)
        if s is None:
            s = segs[seg] = {"segmento": seg, "ar_total": 0.0, "ar_mes": 0.0,
                             "vol_total": 0.0, "n_ops": 0, "n_cuentas": 0}
        v = vol_cuenta.get(idc, {})
        a = ar_ops.get(idc, {})
        s["vol_total"] += float(v.get("vol_total", 0.0) or 0.0)
        s["n_ops"] += int(v.get("n_ops", 0) or 0)
        s["ar_total"] += a.get("ar_total", 0.0)
        s["ar_mes"] += a.get("ar_mes", 0.0)
        if a.get("ar_total", 0.0) > 0:
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

    ops_coll = get_db_cashflow()["Operaciones"]
    ops_match: dict[str, Any] = {
        "cuenta": {"$in": ids}, "arancel": {"$gt": 0},
        "etapa": {"$ne": "solicitud"},   # sin es_cierre: incluye el cierre de caución (donde está el fee)
    }

    clientes = []
    for d in ops_coll.aggregate([
        {"$match": ops_match},
        {"$group": {
            "_id": "$cuenta",
            "ar_total": {"$sum": "$arancel"},
            "ar_mes": {"$sum": {"$cond": [{"$gte": ["$concertacion", mes_start]}, "$arancel", 0]}},
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

    # Boletos con arancel (Operaciones), mapeados a los keys que espera el front
    # (fecha←concertacion, comprobante←boleto, ticker←instrumento, op←tipo_operacion…).
    operaciones = []
    for d in ops_coll.find(
        ops_match,
        {"_id": 0, "concertacion": 1, "cuenta": 1, "boleto": 1, "instrumento": 1,
         "operacion": 1, "tipo_operacion": 1, "bruto": 1, "moneda": 1, "arancel": 1},
    ).sort([("concertacion", -1), ("boleto", -1)]).limit(500):
        idc = str(d.get("cuenta"))
        operaciones.append({
            "fecha":        d.get("concertacion"),
            "id_cuenta":    idc,
            "denominacion": detalle.get(idc) or "—",
            "comprobante":  d.get("boleto"),
            "ticker":       d.get("instrumento"),
            "categoria":    d.get("operacion"),
            "op":           d.get("tipo_operacion"),
            "importe":      d.get("bruto"),
            "moneda":       d.get("moneda"),
            "arancel":      _cv(float(d.get("arancel") or 0.0), factor),
        })

    return {
        "segmento": segmento or "todos",
        "n_clientes": len(clientes),
        "clientes": clientes,
        "operaciones": operaciones,
    }
