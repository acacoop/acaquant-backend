"""Capa de servicio — portfolio / AuM / FCI.

Lógica pura (sin FastAPI) sobre `Valuaciones.AuM` (directo, sin espejo) y
`TitulosAPI`. El router `api/routers/carteras.py` es un thin wrapper que
parsea query params y delega acá. Beneficios del corte:

- Tests deterministas sin levantar uvicorn.
- Agente puede dispatchear directo si en el futuro se relajan los
  `BLOCKED_PATH_PREFIXES`.
- Caché compartido entre router y eventual dispatch interno.

Regla de valuación centralizada en `_valuacion_api`. Fórmulas — ver
`docs/ARCHITECTURE.md` §9 / CLAUDE.md §1.
"""
from __future__ import annotations

import logging
from datetime import datetime

from api.cache import cached
from api.db import get_db_valuaciones
from api.services._cuentas_filter import match_cuenta_filter
from api.services._mep import get_mep_for_date
from api.services.assets_sql import assets_rows

logger = logging.getLogger("api.portfolio")

# Se lee DIRECTO de Valuaciones.AuM (sin el espejo PortfolioAPI.AumAPI). La fuente
# guarda la fecha como `fecha_snapshot` (string 'YYYY-MM-DD'); el espejo la servía
# como `fecha` (datetime). _fecha_dt preserva ese contrato en listar_aum.
_PROJ_AUM = {
    "_id": 0, "fecha_snapshot": 1, "id_cuenta": 1, "unidad": 1,
    "cantidad": 1, "cuenta": 1, "precio": 1, "valuacion": 1,
}


def _fecha_dt(s: str | None) -> datetime | None:
    try:
        return datetime.strptime(s, "%Y-%m-%d") if s else None
    except (ValueError, TypeError):
        return None

# Cuentas que se EXCLUYEN de la vista AuM (chart, KPIs, leaderboard, FCI
# breakdown) pero se siguen capturando en `Valuaciones.AuM` por
# `jobs/aum.py`. Sirven para otras vistas como `/aum → VALUACIONES` que
# pueden mostrarlas individualmente.
#
# 255 = ACA VALORES S.A. - INTERMEDIACION (cuenta de trading propia: las
# posiciones se mueven mucho intra-día y rompen los totales del AuM real).
_EXCLUDED_FROM_AUM_VIEW: frozenset[str] = frozenset({"255"})


# ─────────────────────────────────────────────────────────────────────────────
# Helpers internos
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=600)
def _fci_assets_map() -> dict[str, dict]:
    """Mapea unidad → {emisor, ticker} para unidades con CARTERA=CARTERA FCI.

    Lee `Valuaciones.Assets` UPPERCASE (rama Mongo legacy; la rama SQL usa
    `portafolio.assets`). El set de unidades FCI sale de CARTERA=FCI — antes
    leíamos `TitulosAPI.AssetsAPI` (lowercase, copia
    derivada) y se desincronizaban cuando se editaba el master sin correr
    el sync. Cacheado 10 min — los assets FCI cambian como mucho mensualmente.
    """
    return {
        a["unidad"]: {"emisor": a["EMISOR"], "ticker": a["TICKER"],
                      "fee": a["FEE_ADMIN"]}   # honorario anual (fracción), para REFERIDOS
        # tolera el rename de cartera: 'FCI' (nuevo) y 'CARTERA FCI' (legacy).
        for a in assets_rows(["CARTERA", "EMISOR", "TICKER", "FEE_ADMIN"])
        if a["unidad"] and a["CARTERA"] in ("FCI", "CARTERA FCI")
    }


@cached(ttl=600)
def _assets_enrich_map() -> dict[str, dict]:
    """unidad → {cartera, clase_activo} desde Valuaciones.Assets UPPERCASE
    (fuente de verdad — ver `_fci_assets_map` para la motivación)."""
    return {
        a["unidad"]: {
            "cartera": a["CARTERA"] or "OTROS",
            "clase_activo": a["CLASE_ACTIVO"],
        }
        for a in assets_rows(["CARTERA", "CLASE_ACTIVO"])
        if a["unidad"]
    }


def _valuacion_api(cant: float, px: float, cartera: str, clase_activo: str) -> float:
    """Regla de valuación: FCI o clase OTROS → P×Q directo, resto → P×Q/100."""
    if clase_activo == "OTROS" or "FCI" in cartera:
        return cant * px
    return cant * px / 100


def _scope_match(scope: tuple[str, ...] | None) -> dict:
    """Sub-doc `$match` Mongo que restringe `id_cuenta` al scope de grupos
    (ver `api/services/_grupos_scope.py`). `{}` si `scope` es None (sin
    restricción). Tuple vacío → `{$in: []}` → no matchea nada (correcto:
    usuario en un grupo sin cuentas no ve nada). Para campos `id_cuenta`
    que YA tienen un filtro (ej. `$nin`), usar `_apply_scope`."""
    return {} if scope is None else {"id_cuenta": {"$in": list(scope)}}


def _apply_scope(match: dict, scope: tuple[str, ...] | None) -> None:
    """Agrega la restricción de scope a un `$match` que puede ya tener un
    filtro sobre `id_cuenta` (ej. el `$nin` de cuentas excluidas del AuM).
    Mongo combina `$in` y `$nin` sobre el mismo campo."""
    if scope is None:
        return
    cond = match.get("id_cuenta")
    if isinstance(cond, dict):
        cond["$in"] = list(scope)
    else:
        match["id_cuenta"] = {"$in": list(scope)}


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints raw: /aum
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=300)
def listar_aum(
    id_cuenta: str | None = None,
    unidad: str | None = None,
    cuenta: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    ultimo: bool = False,
    scope: tuple[str, ...] | None = None,
) -> list:
    """Snapshots AuM filtrables por cuenta/unidad/rango. `ultimo=True` ignora rango.

    `scope` restringe a las cuentas del grupo del usuario (None = sin
    restricción). Si se pasa `id_cuenta` explícito el router ya verificó
    que esté dentro del scope."""
    db = get_db_valuaciones()
    filtro: dict = {}

    if ultimo:
        last = db["AuM"].find_one(
            {}, {"fecha_snapshot": 1, "_id": 0}, sort=[("fecha_snapshot", -1)],
        )
        if not last:
            return []
        filtro["fecha_snapshot"] = last["fecha_snapshot"]

    if id_cuenta:
        filtro["id_cuenta"] = id_cuenta
    elif scope is not None:
        filtro["id_cuenta"] = {"$in": list(scope)}
    if unidad:
        filtro["unidad"] = unidad
    if cuenta:
        filtro["cuenta"] = cuenta
    if not ultimo and (desde or hasta):
        # fecha_snapshot es string ISO 'YYYY-MM-DD' → compara como string (sin strptime).
        rango: dict = {}
        if desde:
            rango["$gte"] = desde
        if hasta:
            rango["$lte"] = hasta
        filtro["fecha_snapshot"] = rango

    # fecha_snapshot (string) → fecha (datetime): mismo shape que servía AumAPI.
    return [
        {"fecha": _fecha_dt(d.pop("fecha_snapshot", None)), **d}
        for d in db["AuM"].find(filtro, _PROJ_AUM)
    ]


# ─────────────────────────────────────────────────────────────────────────────
# /resumen y /detalle
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# /tasa-fija y /cer — ELIMINADAS (2026-06-07): vistas sin uso (decisión del user).
# Renta fija / CER de portfolio ya no se sirven. flujos_instrumentos sigue vivo en
# el módulo renta-fija (api/routers/titulos.py), no acá.
# ─────────────────────────────────────────────────────────────────────────────


# (funciones tasa_fija_snapshot y cer_snapshot eliminadas — sin uso)


# ─────────────────────────────────────────────────────────────────────────────
# FCI (/fci-serie, /fci-snapshot)
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=300)
def fci_serie(
    desde: str | None = None,
    hasta: str | None = None,
    cuenta_filter: str = "todas",
    scope: tuple[str, ...] | None = None,
) -> list:
    """Serie histórica FCI: total por fecha + desglose por emisor.

    Sin filtro de cuenta (default "todas") ni scope: lee
    `Valuaciones.AuMResumenFCI` (rollup 1 doc/fecha — barato, ya pre-agregado).

    Con filtro de cuenta o con `scope` de grupos: el rollup no soporta
    breakdown por cuenta, así que cae a `Valuaciones.AuM` raw + $match +
    $group por (fecha_snapshot, unidad). Mismo set de unidades FCI que el
    rollup (definido por `_fci_assets_map`, fuente Valuaciones.Assets).
    """
    db_v = get_db_valuaciones()
    assets_map = _fci_assets_map()

    if (cuenta_filter and cuenta_filter != "todas") or scope is not None:
        # Camino raw — paga la performance del filter.
        unidades_fci = list(assets_map.keys())
        if not unidades_fci:
            return []
        match: dict = {
            "unidad": {"$in": unidades_fci},
            "id_cuenta": {"$nin": list(_EXCLUDED_FROM_AUM_VIEW)},
        }
        match.update(match_cuenta_filter(cuenta_filter))
        _apply_scope(match, scope)
        if desde or hasta:
            rango: dict = {}
            if desde:
                rango["$gte"] = desde
            if hasta:
                rango["$lte"] = hasta
            match["fecha_snapshot"] = rango
        pipeline = [
            {"$match": match},
            {"$group": {
                "_id": {"fecha": "$fecha_snapshot", "unidad": "$unidad"},
                "valuacion_total": {"$sum": "$valuacion"},
            }},
            {"$sort": {"_id.fecha": 1}},
        ]
        rows = list(db_v["AuM"].aggregate(pipeline))
        bucket: dict[str, dict] = {}
        for r in rows:
            fecha_str = str(r["_id"]["fecha"])[:10]
            unidad = r["_id"]["unidad"]
            val = float(r.get("valuacion_total") or 0)
            emisor = assets_map.get(unidad, {}).get("emisor", "") or "SIN EMISOR"
            b = bucket.setdefault(fecha_str, {"total": 0.0, "por_emisor": {}})
            b["total"] += val
            b["por_emisor"][emisor] = b["por_emisor"].get(emisor, 0.0) + val
        return [
            {"fecha": f, "total": v["total"], "por_emisor": v["por_emisor"]}
            for f, v in sorted(bucket.items())
        ]

    # Camino default — rollup pre-agregado.
    filtro: dict = {}
    if desde or hasta:
        rango = {}
        if desde:
            rango["$gte"] = desde
        if hasta:
            rango["$lte"] = hasta
        filtro["fecha_snapshot"] = rango

    cursor = db_v["AuMResumenFCI"].find(filtro, {"_id": 0}).sort("fecha_snapshot", 1).limit(730)

    out = []
    for doc in cursor:
        fecha = doc.get("fecha_snapshot")
        fecha_str = fecha.strftime("%Y-%m-%d") if isinstance(fecha, datetime) else str(fecha)[:10]

        por_emisor: dict[str, float] = {}
        total = 0.0
        for u in doc.get("unidades", []):
            unidad = u.get("unidad", "")
            val = float(u.get("valuacion_total") or 0)
            total += val
            emisor = assets_map.get(unidad, {}).get("emisor", "") or "SIN EMISOR"
            por_emisor[emisor] = por_emisor.get(emisor, 0.0) + val

        out.append({"fecha": fecha_str, "total": total, "por_emisor": por_emisor})

    return out


@cached(ttl=300)
def fci_snapshot(
    fecha: str,
    cuenta_filter: str = "todas",
    scope: tuple[str, ...] | None = None,
) -> list:
    """Snapshot FCI en una fecha: detalle por unidad/emisor/cuenta.

    Lee `Valuaciones.AuM` (fuente de verdad). Mismo set de unidades FCI
    que `fci_serie` (definido por `_fci_assets_map`). `scope` restringe a
    las cuentas del grupo del usuario (None = sin restricción).
    """
    db_v = get_db_valuaciones()
    assets_map = _fci_assets_map()
    unidades_fci = list(assets_map.keys())

    if not unidades_fci:
        return []

    match: dict = {
        "fecha_snapshot": fecha,
        "unidad": {"$in": unidades_fci},
        "id_cuenta": {"$nin": list(_EXCLUDED_FROM_AUM_VIEW)},
    }
    match.update(match_cuenta_filter(cuenta_filter))
    _apply_scope(match, scope)

    docs = db_v["AuM"].find(
        match,
        {"_id": 0, "unidad": 1, "cuenta": 1, "id_cuenta": 1,
         "valuacion": 1, "cantidad": 1},
    )

    out = []
    for d in docs:
        unidad = d.get("unidad", "")
        meta = assets_map.get(unidad, {})
        out.append({
            "unidad": unidad,
            "emisor": meta.get("emisor", "SIN EMISOR") or "SIN EMISOR",
            "ticker": meta.get("ticker", unidad),
            "cuenta": d.get("cuenta", ""),
            "id_cuenta": d.get("id_cuenta", ""),
            "valuacion": float(d.get("valuacion") or 0),
            "cantidad": float(d.get("cantidad") or 0),
        })

    return out


# ─────────────────────────────────────────────────────────────────────────────
# TOTAL (/total-serie, /total-snapshot) — agregado por CARTERA, no por emisor.
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=300)
def listar_cuentas(scope: tuple[str, ...] | None = None) -> list[dict]:
    """Cuentas distintas presentes en el último snapshot de Valuaciones.AuM.

    Devuelve `[{id_cuenta, cuenta}, ...]` ordenado por id_cuenta. Filtra por
    el último `fecha_snapshot` para evitar arrastrar cuentas viejas que ya
    no operan. Lo consume el selector de cuenta de la vista Valuaciones.

    `scope` restringe a las cuentas del grupo del usuario (None = sin
    restricción). El cron (`pnl_todas_cuentas_compute`) lo llama sin scope.
    """
    db_v = get_db_valuaciones()
    last = db_v["AuM"].find_one(
        {}, {"_id": 0, "fecha_snapshot": 1}, sort=[("fecha_snapshot", -1)],
    )
    if not last:
        return []
    pipeline = [
        {"$match": {"fecha_snapshot": last["fecha_snapshot"], **_scope_match(scope)}},
        {"$group": {"_id": "$id_cuenta", "cuenta": {"$first": "$cuenta"}}},
        {"$sort": {"_id": 1}},
        {"$project": {"_id": 0, "id_cuenta": "$_id", "cuenta": 1}},
    ]
    return list(db_v["AuM"].aggregate(pipeline))


@cached(ttl=300)
def total_serie(
    desde: str | None = None,
    hasta: str | None = None,
    cuenta_filter: str = "todas",
    moneda: str = "ARS",
    scope: tuple[str, ...] | None = None,
) -> dict:
    """Serie histórica del AuM total agrupado por CARTERA.

    Lee `Valuaciones.AuM` raw y enriquece cada unidad con `cartera` desde
    `Valuaciones.Assets` UPPERCASE.

    Si `moneda == "USD"`: convierte cada doc dividiendo por el MEP de la
    fecha del snapshot (todo el AuM se persiste en ARS, incluso unidades
    USD/USDC). Las fechas para las que no hay MEP se reportan en
    `fechas_sin_mep` y conservan el valor en ARS sin convertir, para que
    el caller pueda decidir cómo mostrarlas.

    Returns:
        {
          serie: [{fecha, total, por_cartera, mep_used (si USD)}, ...],
          moneda: "ARS" | "USD",
          fechas_sin_mep: [<list>]  (siempre [], salvo moneda=USD)
        }
    """
    db_v = get_db_valuaciones()
    enrich = _assets_enrich_map()

    match: dict = {"id_cuenta": {"$nin": list(_EXCLUDED_FROM_AUM_VIEW)}}
    match.update(match_cuenta_filter(cuenta_filter))
    _apply_scope(match, scope)
    if desde or hasta:
        rango: dict = {}
        if desde:
            rango["$gte"] = desde
        if hasta:
            rango["$lte"] = hasta
        match["fecha_snapshot"] = rango

    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": {"fecha": "$fecha_snapshot", "unidad": "$unidad"},
            "valuacion_total": {"$sum": "$valuacion"},
        }},
        {"$sort": {"_id.fecha": 1}},
    ]

    rows = list(db_v["AuM"].aggregate(pipeline))

    # Cache MEP por fecha — varias unidades de la misma fecha lo comparten.
    mep_cache: dict[str, float | None] = {}
    fechas_sin_mep: set[str] = set()

    def _mep(f: str) -> float | None:
        if f not in mep_cache:
            mep_cache[f] = get_mep_for_date(f)
        return mep_cache[f]

    bucket: dict[str, dict] = {}
    for r in rows:
        fecha_str = str(r["_id"]["fecha"])[:10]
        unidad = r["_id"]["unidad"]
        val = float(r.get("valuacion_total") or 0)
        if moneda == "USD":
            mep = _mep(fecha_str)
            if mep:
                val = val / mep
            else:
                fechas_sin_mep.add(fecha_str)
        cartera = enrich.get(unidad, {}).get("cartera") or "OTROS"
        b = bucket.setdefault(fecha_str, {"total": 0.0, "por_cartera": {}})
        b["total"] += val
        b["por_cartera"][cartera] = b["por_cartera"].get(cartera, 0.0) + val

    serie = []
    for f, v in sorted(bucket.items()):
        row = {"fecha": f, "total": v["total"], "por_cartera": v["por_cartera"]}
        if moneda == "USD":
            row["mep_used"] = mep_cache.get(f)
        serie.append(row)

    return {
        "serie": serie,
        "moneda": moneda,
        "fechas_sin_mep": sorted(fechas_sin_mep),
    }


def _resolve_fecha_snapshot(db_v, fecha_pedida: str) -> str | None:
    """Devuelve el último `fecha_snapshot` <= `fecha_pedida` que existe en
    Valuaciones.AuM. None si no hay ninguno anterior. Permite que el caller
    pase fechas convencionales (1° de mes, hoy, etc) sin requerir match
    exacto — cae al snapshot disponible más cercano."""
    doc = db_v["AuM"].find_one(
        {"fecha_snapshot": {"$lte": fecha_pedida}},
        {"_id": 0, "fecha_snapshot": 1},
        sort=[("fecha_snapshot", -1)],
    )
    return doc.get("fecha_snapshot") if doc else None


@cached(ttl=300)
def total_diff(
    fecha_actual: str,
    fecha_anterior: str,
    moneda: str = "ARS",
    cuenta_filter: str = "todas",
    scope: tuple[str, ...] | None = None,
) -> dict:
    """Diferencia de saldo por cuenta entre dos fechas snapshot.

    Para cada cuenta computa `saldo_actual` (sum valuacion del fecha_actual),
    `saldo_anterior` y `diff = actual - anterior`. Marca `es_nueva` (no
    existía en `fecha_anterior`) y `es_cerrada` (no existe en `fecha_actual`).
    Lista ordenada por |diff| desc por default.

    Si `moneda=="USD"`: cada saldo se divide por el MEP de SU fecha. Si
    falta MEP para alguna, se setea el flag correspondiente y el saldo
    queda en ARS sin convertir.

    Si las fechas pedidas no existen exactas en AuM, se cae al último
    snapshot <= la fecha pedida (campos `fecha_actual_resuelta` /
    `fecha_anterior_resuelta` reportan qué se usó realmente).
    """
    db_v = get_db_valuaciones()

    fecha_act = _resolve_fecha_snapshot(db_v, fecha_actual) or fecha_actual
    fecha_ant = _resolve_fecha_snapshot(db_v, fecha_anterior) or fecha_anterior

    base_match: dict = {"id_cuenta": {"$nin": list(_EXCLUDED_FROM_AUM_VIEW)}}
    base_match.update(match_cuenta_filter(cuenta_filter))
    _apply_scope(base_match, scope)

    def _agg(fecha: str) -> dict[str, dict]:
        match = {**base_match, "fecha_snapshot": fecha}
        rows = list(db_v["AuM"].aggregate([
            {"$match": match},
            {"$group": {
                "_id":    "$id_cuenta",
                "cuenta": {"$first": "$cuenta"},
                "saldo":  {"$sum": "$valuacion"},
            }},
        ]))
        return {str(r["_id"]): r for r in rows}

    map_act = _agg(fecha_act)
    map_ant = _agg(fecha_ant)

    # Conversión a USD: dividir cada saldo por el MEP de SU fecha.
    mep_act: float | None = None
    mep_ant: float | None = None
    mep_missing_act = False
    mep_missing_ant = False
    if moneda == "USD":
        mep_act = get_mep_for_date(fecha_act)
        mep_ant = get_mep_for_date(fecha_ant)
        if mep_act:
            for r in map_act.values():
                r["saldo"] = float(r["saldo"]) / mep_act
        else:
            mep_missing_act = True
        if mep_ant:
            for r in map_ant.values():
                r["saldo"] = float(r["saldo"]) / mep_ant
        else:
            mep_missing_ant = True

    all_ids = set(map_act) | set(map_ant)
    filas: list[dict] = []
    for cid in all_ids:
        a = map_act.get(cid)
        n = map_ant.get(cid)
        saldo_act = float(a["saldo"]) if a else None
        saldo_ant = float(n["saldo"]) if n else None
        diff = (saldo_act or 0.0) - (saldo_ant or 0.0)
        cuenta = (a or n or {}).get("cuenta", "") or ""
        filas.append({
            "id_cuenta":      cid,
            "cuenta":         cuenta,
            "saldo_actual":   saldo_act,
            "saldo_anterior": saldo_ant,
            "diff":           diff,
            "es_nueva":       saldo_ant is None,
            "es_cerrada":     saldo_act is None,
        })
    filas.sort(key=lambda r: abs(r["diff"]), reverse=True)

    return {
        "fecha_actual_pedida":    fecha_actual,
        "fecha_anterior_pedida":  fecha_anterior,
        "fecha_actual_resuelta":  fecha_act,
        "fecha_anterior_resuelta": fecha_ant,
        "moneda":                 moneda,
        "mep_actual":             mep_act,
        "mep_anterior":           mep_ant,
        "mep_missing_actual":     mep_missing_act,
        "mep_missing_anterior":   mep_missing_ant,
        "filas":                  filas,
        "total_diff":             sum(f["diff"] for f in filas),
        "n_total":                len(filas),
        "n_nuevas":               sum(1 for f in filas if f["es_nueva"]),
        "n_cerradas":             sum(1 for f in filas if f["es_cerrada"]),
    }


@cached(ttl=300)
def total_snapshot(
    fecha: str,
    cuenta_filter: str = "todas",
    moneda: str = "ARS",
    scope: tuple[str, ...] | None = None,
) -> dict:
    """Snapshot del AuM total en una fecha: detalle por unidad/cartera/cuenta.

    Si `moneda == "USD"`: divide cada `valuacion` por el MEP de `fecha`. Si
    no hay MEP disponible para esa fecha, devuelve los valores en ARS y
    setea `mep_missing=True` para que el frontend muestre un aviso.
    """
    db_v = get_db_valuaciones()
    enrich = _assets_enrich_map()

    match: dict = {
        "fecha_snapshot": fecha,
        "id_cuenta": {"$nin": list(_EXCLUDED_FROM_AUM_VIEW)},
    }
    match.update(match_cuenta_filter(cuenta_filter))
    _apply_scope(match, scope)

    docs = list(db_v["AuM"].find(
        match,
        {"_id": 0, "unidad": 1, "cuenta": 1, "id_cuenta": 1,
         "valuacion": 1, "cantidad": 1, "tipoTitulo": 1},
    ))

    mep: float | None = None
    mep_missing = False
    if moneda == "USD":
        mep = get_mep_for_date(fecha)
        if mep is None:
            mep_missing = True

    out = []
    for d in docs:
        unidad = d.get("unidad", "")
        meta = enrich.get(unidad, {})
        cartera = meta.get("cartera") or "OTROS"
        val = float(d.get("valuacion") or 0)
        if moneda == "USD" and mep:
            val = val / mep
        out.append({
            "unidad": unidad,
            "cartera": cartera,
            "tipo": d.get("tipoTitulo") or "",
            "cuenta": d.get("cuenta", ""),
            "id_cuenta": d.get("id_cuenta", ""),
            "valuacion": val,
            "cantidad": float(d.get("cantidad") or 0),
        })

    return {
        "docs": out,
        "moneda": moneda,
        "mep_used": mep,
        "mep_missing": mep_missing,
    }
