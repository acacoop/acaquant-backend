"""api/services/comercial_sql.py — vista COMERCIAL leyendo de Postgres (Supabase).

Servicio PURO (sin FastAPI). Sirve los endpoints `/api/operaciones/comercial/*`
leyendo Postgres. Reusa de comercial.py la lógica de presentación: `_cv`
(dolarización al MEP actual), `_factor_usd`, `estado_comercial`, `_hoy_art`, y
las tuplas de categorías. Las agregaciones se hacen en vivo en SQL.

Reglas SQL: `unidad IS DISTINCT FROM 'USDL'` (excluir futuros), pesificación por `mep` del
boleto, AuM "último snapshot" = `max(fecha_snapshot)` GLOBAL, `etapa IS DISTINCT FROM
'solicitud'` para arancel, Decimal→float, date→ISO. Filtro de cuentas del operador por
subquery sobre `comitentes` activas (estado='Activa').

Estado: Chunk 1 (selector + portafolio + operaciones + serie). Resto en progreso.
"""
from __future__ import annotations

from datetime import date, timedelta

from api.services import cashflow_sql as _cf_sql
from api.services._sql import _q
from api.services.comercial import (
    _CATS_OPERACIONES,
    _CATS_VOLUMEN,
    _cv,
    _factor_usd,
    _hoy_art,
    estado_comercial,
)
from config import CUPO_BASE_FECHA

# Ficha embebida en cada cliente (= _FICHA_FIELDS de comercial.py). denominacion sale de
# `cuentas` (no de comitentes); el resto de `comitentes`.
_FICHA = ("denominacion", "operador_nombre", "telefono", "email", "nivel_1", "nivel_2",
          "nivel_3", "nivel_4", "nivel_5", "primer_contacto_comercial", "riesgo_la_ft",
          "division", "adc", "dma", "referido")
_ANALISIS = ("denominacion", "telefono", "nivel_1", "nivel_2", "nivel_3", "nivel_4", "nivel_5")

def _pesif(alias: str = "", col: str = "importe") -> str:
    """Pesifica UN boleto: en ARS va directo; en cualquier otra moneda se multiplica
    por el `mep` DEL PROPIO BOLETO (no el de hoy).

    Sumar brutos de monedas distintas sin esto da un número que **parece plata y no
    lo es**, y no falla nada — por eso la expresión vive en un solo lugar. `col`
    cambia según la tabla: `importe` en `negocio_movimientos`, `bruto` en
    `operaciones.operaciones`."""
    a = f"{alias}." if alias else ""
    return (f"CASE WHEN {a}moneda = 'ARS' THEN abs(COALESCE({a}{col}, 0)) "
            f"ELSE abs(COALESCE({a}{col}, 0)) * COALESCE({a}mep, 0) END")


# Pesificación de un boleto de `negocio_movimientos`. = _PESIF de comercial.py.
_PESIF = _pesif()

# Valor interno para filtrar cuentas sin división (NULL o string vacío).
SIN_CLASIFICAR_DIVISION = "__sin_clasificar__"

# Qué boleto CUENTA como "operación" para DÍAS SIN OPERAR: cualquiera de
# `operaciones.operaciones` que no esté anulado — sin filtro de tipo, de etapa
# ni de categoría. El predicado vive UNA sola vez acá porque lo comparten la
# tabla (analisis_comercial) y su modal de auditoría (detalle_ultima_op): si
# cada uno lo escribiera aparte, el modal podría contradecir a la tabla.
_ULT_OP_WHERE = "anulado_en IS NULL"


def _act_where(alias: str = "") -> str:
    """`_ULT_OP_WHERE` aliasable (`o.anulado_en IS NULL`). Es la MISMA definición de
    "boleto que cuenta como operación" — la usan la tabla de ESTADO COMERCIAL, su
    modal y la tab PROFUNDIDAD DE CLIENTES, que necesita el alias porque joinea
    `operaciones` contra una CTE de meses. Un test congela que las dos digan lo mismo."""
    a = f"{alias}." if alias else ""
    return f"{a}anulado_en IS NULL"


def _arancel_where(alias: str = "") -> str:
    """Qué boleto SUMA arancel, en UN solo lugar: `arancel > 0` y `etapa <> 'solicitud'`
    (la liquidación CL ya cuenta). NO excluye los cierres: el arancel de caución vive
    SOLO en el cierre (ver CLAUDE.md → "El arancel y el bruto NO comparten filtro de
    cierre"). El `anulado_en IS NULL` va aparte porque en algunas queries es condición
    de JOIN y no de WHERE."""
    a = f"{alias}." if alias else ""
    return f"{a}arancel > 0 AND {a}etapa IS DISTINCT FROM 'solicitud'"


def _f(x) -> float:
    return float(x or 0)


def _iso(d):
    return d.isoformat() if d is not None else None


def _comitentes_where(operador, p: dict, nivel_1=None,
                      nivel_3=None, referido=None,
                      alias: str = "", nivel_4=None, nivel_5=None, nivel_2=None,
                      division=None, solo_activas: bool = True) -> str:
    """WHERE de comitentes activas. Cada filtro (operador + nivel_1/2/3/4/5 + referido +
    division) acepta un valor O una LISTA (multi-select): entre filtros se CRUZA con AND;
    dentro de un filtro, OR (`= ANY(array)`). operador str/'__todos__'/lista vacía = sin
    filtro de operador. Muta `p` con los params. `alias` prefija columnas (ej. 'c.') cuando
    hay JOIN."""
    def _lst(v) -> list[str]:
        if v is None:
            return []
        items = [v] if isinstance(v, str) else list(v)
        return [str(x) for x in items if x and str(x) != "__todos__"]
    a = f"{alias}." if alias else ""
    # `solo_activas=False` saca el filtro de estado. Lo usa el histórico de ALTAS, que
    # cuenta ALTAS y no clientes vivos: una cuenta abierta en 2019 y cerrada en 2022 fue
    # un alta de 2019, y filtrarla haría que el pasado se achique cada vez que alguien
    # cierra una cuenta. Default True → nada cambia para los ~15 llamadores existentes.
    conds = [f"{a}estado = 'Activa'"] if solo_activas else []
    for col, val, key in (
        ("operador_email", operador, "ops"),
        ("nivel_1", nivel_1, "n1"), ("nivel_2", nivel_2, "n2"), ("nivel_3", nivel_3, "n3"),
        ("nivel_4", nivel_4, "n4"), ("nivel_5", nivel_5, "n5"),
        ("referido", referido, "ref"),
    ):
        vals = _lst(val)
        if vals:
            p[key] = vals
            conds.append(f"{a}{col} = ANY(%({key})s)")

    # División: además de valores explícitos, permite incluir "sin clasificar"
    # (division NULL o vacía) con un token interno dedicado.
    div_vals = _lst(division)
    if div_vals:
        incluye_sin = SIN_CLASIFICAR_DIVISION in div_vals
        div_norm = [v for v in div_vals if v != SIN_CLASIFICAR_DIVISION]
        parts: list[str] = []
        if div_norm:
            p["div"] = div_norm
            parts.append(f"{a}division = ANY(%(div)s)")
        if incluye_sin:
            parts.append(f"({a}division IS NULL OR btrim({a}division) = '')")
        if parts:
            conds.append("(" + " OR ".join(parts) + ")")
    # Sin condiciones (solo_activas=False y ningún filtro madre) devolver "" haría un
    # `WHERE ` roto. TRUE es explícito y el planner lo descarta.
    return " AND ".join(conds) if conds else "TRUE"


def _madre_activa(operador=None, nivel_1=None, nivel_2=None, nivel_3=None,
                  nivel_4=None, nivel_5=None, referido=None, division=None) -> bool:
    """True si HAY al menos un filtro madre seleccionado (operador/nivel_1..5/referido/
    division). Sirve para que el INFORME solo aplique el scope cuando el usuario filtró — sin
    filtro, las queries quedan IDÉNTICAS al comportamiento global de siempre."""
    def _has(v) -> bool:
        if v is None:
            return False
        if isinstance(v, str):
            return v.strip() not in ("", "__todos__")
        return any(x and str(x) != "__todos__" for x in v)
    return any(_has(v) for v in (operador, nivel_1, nivel_2, nivel_3, nivel_4, nivel_5, referido, division))


def _append_niveles(where: str, p: dict, alias: str, nivel_1=None, nivel_2=None,
                    nivel_3=None, nivel_4=None, nivel_5=None, referido=None,
                    division=None) -> str:
    """Appendéa conds ` AND <col> = ANY(...)` para los filtros madre nivel_1..5 +
    referido + division a un WHERE ya armado (para las queries del INFORME que tienen su
    propio `operador` drill-down). Usa claves de param mn1..mdiv para no chocar. `alias`
    prefija columnas cuando hay JOIN (ej. 'c')."""
    a = f"{alias}." if alias else ""
    def _lst(v) -> list[str]:
        if v is None:
            return []
        items = [v] if isinstance(v, str) else list(v)
        return [str(x) for x in items if x and str(x) != "__todos__"]
    for col, val, key in (
        ("nivel_1", nivel_1, "mn1"), ("nivel_2", nivel_2, "mn2"), ("nivel_3", nivel_3, "mn3"),
        ("nivel_4", nivel_4, "mn4"), ("nivel_5", nivel_5, "mn5"), ("referido", referido, "mref"),
    ):
        vals = _lst(val)
        if vals:
            p[key] = vals
            where += f" AND {a}{col} = ANY(%({key})s)"

    div_vals = _lst(division)
    if div_vals:
        incluye_sin = SIN_CLASIFICAR_DIVISION in div_vals
        div_norm = [v for v in div_vals if v != SIN_CLASIFICAR_DIVISION]
        parts: list[str] = []
        if div_norm:
            p["mdiv"] = div_norm
            parts.append(f"{a}division = ANY(%(mdiv)s)")
        if incluye_sin:
            parts.append(f"({a}division IS NULL OR btrim({a}division) = '')")
        if parts:
            where += " AND (" + " OR ".join(parts) + ")"
    return where


def _scope_cuentas(operador, p: dict, nivel_1=None,
                   nivel_3=None, referido=None,
                   nivel_4=None, nivel_5=None, nivel_2=None, division=None) -> str:
    """Fragmento `id_cuenta IN (SELECT ... FROM comitentes WHERE ...)` scopeado al
    operador + nivel_1/3/4/5/referido/division (intersección). Muta `p` con los params."""
    return (f"id_cuenta IN (SELECT id_cuenta FROM comitentes "
            f"WHERE {_comitentes_where(operador, p, nivel_1, nivel_3, referido, nivel_4=nivel_4, nivel_5=nivel_5, nivel_2=nivel_2, division=division)})")


def _aum_por_cuenta_sql(operador, nivel_1=None,
                        nivel_3=None, referido=None,
                        corte: str | None = None,
                        nivel_4=None, nivel_5=None, nivel_2=None, division=None,
                        ids: list[str] | None = None) -> dict[str, float]:
    """AuM por id_cuenta, scopeado al operador + filtros. Sin `corte` = último snapshot GLOBAL;
    con `corte` (ISO) = el snapshot de `tenencia` más reciente <= corte (foto al día X).
    Con `ids` (lista ya materializada del scope) evita re-evaluar la subquery de
    comitentes — es el camino de operador_comercial/analisis_comercial, que ya
    resolvieron el universo en una pasada (`_universo_comercial`)."""
    if corte is not None:
        snap = _q("SELECT max(fecha) AS f FROM portafolio.tenencia "
                  "WHERE aum = 'si' AND fecha <= %(c)s", {"c": corte})[0]["f"]
    else:
        snap = _q("SELECT max(fecha) AS f FROM portafolio.tenencia WHERE aum = 'si'")[0]["f"]
    if snap is None:
        return {}
    p: dict = {"f": snap}
    if ids is not None:
        p["ids_scope"] = list(ids)
        scope = "id_cuenta = ANY(%(ids_scope)s)"
    else:
        scope = _scope_cuentas(operador, p, nivel_1, nivel_3, referido, nivel_4=nivel_4, nivel_5=nivel_5, nivel_2=nivel_2, division=division)
    return {r["id_cuenta"]: _f(r["aum"]) for r in _q(
        f"SELECT id_cuenta, SUM(valuacion) AS aum FROM portafolio.tenencia "
        f"WHERE fecha = %(f)s AND aum = 'si' AND {scope} GROUP BY id_cuenta", p)}


def _universo_comercial(operador, campos: tuple[str, ...], nivel_1=None,
                        nivel_3=None, referido=None, corte: str | None = None,
                        nivel_4=None, nivel_5=None, nivel_2=None, division=None,
                        extra_cols: tuple[str, ...] = (),
                        ) -> tuple[list[str], list[str], dict[str, dict]]:
    """UNA pasada sobre `comitentes` para todo el request comercial.

    Devuelve (ids_corte, ids_scope, ficha):
      - `ids_scope`: TODAS las cuentas activas del scope (sin corte) — para las
        queries de tenencia/operaciones, que en modo foto NO filtran por alta.
      - `ids_corte`: las que YA existían a la fecha de corte
        (fecha_alta_legajo <= corte); sin corte, == ids_scope.
      - `ficha`: {id_cuenta: {campos + extra_cols}} con denominación (join
        cuentas) y nombre del operador (join operadores).

    Motivo: antes cada request evaluaba el MISMO predicado de comitentes 4-5
    veces (_ids_operador + subquery del AuM + subquery de ult_op + _ficha +
    cupos), con divergencia real latente — la query de cupos omitía
    nivel_2/4/5. Una sola pasada = una sola definición del scope."""
    def _col(c: str) -> str:
        if c == "denominacion":
            return "u.denominacion"
        if c == "operador_nombre":
            return "o.nombre AS operador_nombre"
        return f"c.{c}"
    cols = ", ".join(_col(c) for c in campos + tuple(extra_cols))
    p: dict = {}
    where = _comitentes_where(operador, p, nivel_1, nivel_3, referido, alias="c",
                              nivel_4=nivel_4, nivel_5=nivel_5, nivel_2=nivel_2, division=division)
    rows = _q(f"SELECT c.id_cuenta, c.fecha_alta_legajo, {cols} FROM comitentes c "
              f"LEFT JOIN cuentas u ON u.id_cuenta = c.id_cuenta "
              f"LEFT JOIN operadores o ON o.email = c.operador_email WHERE {where}", p)
    ficha = {r["id_cuenta"]: r for r in rows}
    ids_scope = sorted(ficha.keys())
    if corte is not None:
        d_corte = date.fromisoformat(corte)
        ids_corte = sorted(
            r["id_cuenta"] for r in rows
            if r.get("fecha_alta_legajo") is not None and r["fecha_alta_legajo"] <= d_corte
        )
    else:
        ids_corte = ids_scope
    return ids_corte, ids_scope, ficha


def dimensiones_comercial() -> dict:
    """Combos distintos (operador, nivel_1..5, referido, division) de cuentas activas —
    para poblar y CRUZAR los filtros madre en el frontend."""
    rows = _q(
        "SELECT c.operador_email, o.nombre AS operador_nombre, c.nivel_1, c.nivel_2, c.nivel_3, "
        "c.nivel_4, c.nivel_5, c.referido, "
        "CASE WHEN c.division IS NULL OR btrim(c.division) = '' THEN %(sin)s ELSE c.division END AS division, "
        "count(*) AS n FROM comitentes c "
        "LEFT JOIN operadores o ON o.email = c.operador_email "
        "WHERE c.estado = 'Activa' AND c.operador_email IS NOT NULL "
        "GROUP BY c.operador_email, o.nombre, c.nivel_1, c.nivel_2, c.nivel_3, c.nivel_4, c.nivel_5, c.referido, "
        "CASE WHEN c.division IS NULL OR btrim(c.division) = '' THEN %(sin)s ELSE c.division END",
        {"sin": SIN_CLASIFICAR_DIVISION},
    )
    return {"combos": [
        {"operador_email": r["operador_email"], "operador_nombre": r["operador_nombre"],
         "nivel_1": r["nivel_1"], "nivel_2": r["nivel_2"], "nivel_3": r["nivel_3"],
         "nivel_4": r["nivel_4"], "nivel_5": r["nivel_5"], "referido": r["referido"],
         "division": r["division"], "n_cuentas": r["n"]} for r in rows]}


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
    snap = _q("SELECT max(fecha) AS f FROM portafolio.tenencia WHERE aum = 'si'")[0]["f"]
    if snap is None:
        return {"id_cuenta": str(id_cuenta), "fecha_snapshot": None, "total": 0.0, "posiciones": []}
    rows = _q("SELECT unidad, SUM(valuacion) AS valuacion FROM portafolio.tenencia "
              "WHERE fecha = %(f)s AND aum = 'si' AND id_cuenta = %(idc)s "
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
        "AND unidad IS DISTINCT FROM 'USDL' AND anulado_en IS NULL "
        "ORDER BY fecha DESC, comprobante DESC LIMIT %(lim)s",
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


def serie_comercial(*, operador, metric: str = "volumen", moneda: str = "ARS",
                    id_cuenta: str | None = None, nivel_1=None,
                    nivel_3=None, referido=None,
                    nivel_4=None, nivel_5=None, nivel_2=None, division=None) -> dict:
    factor = _factor_usd(moneda)
    p: dict = {}
    if id_cuenta:
        scope = "id_cuenta = %(idc)s"
        p["idc"] = str(id_cuenta)
    else:
        scope = _scope_cuentas(operador, p, nivel_1, nivel_3, referido, nivel_4=nivel_4, nivel_5=nivel_5, nivel_2=nivel_2, division=division)

    if metric == "aum":
        rows = _q(f"SELECT fecha, SUM(valuacion) AS v FROM portafolio.tenencia "
                  f"WHERE ({scope}) AND aum = 'si' GROUP BY fecha ORDER BY fecha", p)
    else:
        p["cats"] = list(_CATS_VOLUMEN)
        rows = _q(f"SELECT fecha, SUM({_PESIF}) AS v FROM negocio_movimientos "
                  f"WHERE {scope} AND categoria = ANY(%(cats)s) AND unidad IS DISTINCT FROM 'USDL' "
                  f"AND anulado_en IS NULL GROUP BY fecha ORDER BY fecha", p)
    serie = [{"fecha": _iso(r["fecha"]), "valor": _cv(_f(r["v"]), factor)} for r in rows]
    return {"operador": operador, "id_cuenta": id_cuenta, "metric": metric,
            "moneda": moneda, "serie": serie}


def clientes_por_fecha(*, operador, desde: str, hasta: str,
                       moneda: str = "ARS", nivel_1=None,
                       nivel_3=None, referido=None,
                       nivel_4=None, nivel_5=None, nivel_2=None, division=None) -> dict:
    """Clientes que OPERARON en el rango [desde, hasta] con su volumen del período.
    Alimenta la interactividad del chart de volumen (click en barra → tabla del día/semana/mes)."""
    factor = _factor_usd(moneda)
    aum = _aum_por_cuenta_sql(operador, nivel_1, nivel_3, referido, nivel_4=nivel_4, nivel_5=nivel_5, nivel_2=nivel_2, division=division)
    p: dict = {"desde": desde, "hasta": hasta, "cats": list(_CATS_VOLUMEN)}
    scope = _scope_cuentas(operador, p, nivel_1, nivel_3, referido, nivel_4=nivel_4, nivel_5=nivel_5, nivel_2=nivel_2, division=division)
    vol: dict[str, float] = {}
    for r in _q(
        f"SELECT id_cuenta, SUM({_PESIF}) AS v FROM negocio_movimientos "
        f"WHERE {scope} AND categoria = ANY(%(cats)s) AND unidad IS DISTINCT FROM 'USDL' "
        f"AND anulado_en IS NULL "
        f"AND fecha >= %(desde)s AND fecha <= %(hasta)s GROUP BY id_cuenta", p,
    ):
        vol[r["id_cuenta"]] = _f(r["v"])
    ficha = _ficha_por_cuenta(operador, _FICHA, nivel_1, nivel_3, referido, nivel_4=nivel_4, nivel_5=nivel_5, nivel_2=nivel_2, division=division)
    clientes = [{
        "id_cuenta": idc,
        "denominacion": (ficha.get(idc, {}).get("denominacion") or "—"),
        "aum": _cv(aum.get(idc, 0.0), factor),
        "volumen_periodo": _cv(v, factor),
        "ficha": {k: ficha.get(idc, {}).get(k) for k in _FICHA},
    } for idc, v in vol.items() if v]
    clientes.sort(key=lambda x: x["volumen_periodo"], reverse=True)
    return {"operador": operador, "moneda": moneda, "desde": desde, "hasta": hasta,
            "total_volumen": _cv(sum(vol.values()), factor),
            "n_clientes": len(clientes), "clientes": clientes}


def _ficha_por_cuenta(operador, campos: tuple[str, ...], nivel_1=None,
                      nivel_3=None, referido=None,
                      nivel_4=None, nivel_5=None, nivel_2=None, division=None) -> dict[str, dict]:
    """{id_cuenta: {campos}} de comitentes activas (+ denominacion de cuentas, + nombre del
    operador asignado) del scope. `operador_nombre` sale del join a `operadores`."""
    def _col(c: str) -> str:
        if c == "denominacion":
            return "u.denominacion"
        if c == "operador_nombre":
            return "o.nombre AS operador_nombre"
        return f"c.{c}"
    cols = ", ".join(_col(c) for c in campos)
    p: dict = {}
    where = _comitentes_where(operador, p, nivel_1, nivel_3, referido, alias="c",
                              nivel_4=nivel_4, nivel_5=nivel_5, nivel_2=nivel_2, division=division)
    rows = _q(f"SELECT c.id_cuenta, {cols} FROM comitentes c "
              f"LEFT JOIN cuentas u ON u.id_cuenta = c.id_cuenta "
              f"LEFT JOIN operadores o ON o.email = c.operador_email WHERE {where}", p)
    return {r["id_cuenta"]: r for r in rows}


def operador_comercial(*, operador, moneda: str = "ARS", nivel_1=None,
                       nivel_3=None, referido=None,
                       fecha: str | None = None, desde: str | None = None,
                       nivel_4=None, nivel_5=None, nivel_2=None, division=None) -> dict:
    # `fecha` (ISO) = corte = HASTA: TOTAL/YTD hasta corte, volumen capeado a <= corte.
    # `desde` (ISO) = inicio del período: si viene, la métrica "MES" (vol_mtd) pasa a ser
    # la suma de [desde, corte] en vez del mes calendario del corte.
    factor = _factor_usd(moneda)
    corte = date.fromisoformat(fecha) if fecha else _hoy_art()
    corte_iso = corte.isoformat() if fecha else None
    mtd = desde if desde else corte.replace(day=1).isoformat()
    ytd = corte.replace(month=1, day=1).isoformat()
    # UNA pasada sobre comitentes: universo (con y sin corte) + ficha.
    ids, ids_scope, ficha = _universo_comercial(
        operador, _FICHA, nivel_1, nivel_3, referido, corte=corte_iso,
        nivel_4=nivel_4, nivel_5=nivel_5, nivel_2=nivel_2, division=division)
    aum = _aum_por_cuenta_sql(operador, corte=corte_iso, ids=ids_scope)

    # Volumen YTD y MTD por cuenta en una pasada.
    p: dict = {"ytd": ytd, "mtd": mtd, "cats": list(_CATS_VOLUMEN),
               "ids_scope": ids_scope}
    cap = ""
    if fecha:
        cap = " AND fecha <= %(corte)s"
        p["corte"] = corte_iso
    vol_ytd: dict[str, float] = {}
    vol_mtd: dict[str, float] = {}
    for r in _q(
        f"SELECT id_cuenta, "
        f"SUM(CASE WHEN fecha >= %(ytd)s THEN {_PESIF} ELSE 0 END) AS vy, "
        f"SUM(CASE WHEN fecha >= %(mtd)s THEN {_PESIF} ELSE 0 END) AS vm "
        f"FROM negocio_movimientos WHERE id_cuenta = ANY(%(ids_scope)s) "
        f"AND categoria = ANY(%(cats)s) AND anulado_en IS NULL "
        f"AND unidad IS DISTINCT FROM 'USDL'{cap} GROUP BY id_cuenta", p,
    ):
        vol_ytd[r["id_cuenta"]] = _f(r["vy"])
        vol_mtd[r["id_cuenta"]] = _f(r["vm"])
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


def analisis_comercial(*, operador, dias_activa: int = 45, dias_dormida: int = 90,
                       moneda: str = "ARS", nivel_1=None,
                       nivel_3=None, referido=None,
                       fecha: str | None = None, desde: str | None = None,
                       nivel_4=None, nivel_5=None, nivel_2=None, division=None) -> dict:
    # Modo "foto al día X": `fecha` (ISO) = corte → todo se calcula como estaba ese día
    # (universo con alta<=corte, última op<=corte, AuM del snapshot<=corte, días vs corte).
    # `fecha=None` → modo live (hoy). El cupo NO es histórico aún (valor actual) → ver paso 2.
    corte = date.fromisoformat(fecha) if fecha else _hoy_art()
    corte_iso = corte.isoformat() if fecha else None
    # UNA pasada sobre comitentes: universo (con y sin corte) + ficha + cupos —
    # antes el mismo predicado se evaluaba 5 veces por request, y la query de
    # cupos ya había divergido (omitía nivel_2/4/5 del scope).
    ids, ids_scope, ficha = _universo_comercial(
        operador, _ANALISIS, nivel_1, nivel_3, referido, corte=corte_iso,
        nivel_4=nivel_4, nivel_5=nivel_5, nivel_2=nivel_2, division=division,
        extra_cols=("cupo_transaccional_ars", "cupo_usado_ars"))
    if not ids and operador and operador != "__todos__":
        return {"operador": operador, "dias_activa": dias_activa,
                "dias_dormida": dias_dormida, "fecha": fecha, "clientes": []}
    factor = _factor_usd(moneda)
    factor_cupo = _factor_usd("USD")  # cupo SIEMPRE en USD al MEP del día
    aum = _aum_por_cuenta_sql(operador, corte=corte_iso, ids=ids_scope)
    year_start = date(corte.year, 1, 1).isoformat()
    # `desde` (período) pisa el mes calendario para el flag opero_mtd → "operó en [desde, corte]".
    month_start = desde if desde else corte.replace(day=1).isoformat()

    # Última operación por cuenta <= corte (Operaciones, fuente de verdad).
    # El predicado de qué boleto cuenta es `_ULT_OP_WHERE` — el MISMO que audita
    # el modal (`detalle_ultima_op`), para que no puedan divergir.
    p: dict = {"ids_scope": ids_scope}
    ult_sql = (f"SELECT id_cuenta, max(concertacion) AS ult FROM operaciones "
               f"WHERE id_cuenta = ANY(%(ids_scope)s) AND {_ULT_OP_WHERE}")
    if fecha:
        ult_sql += " AND concertacion <= %(corte)s"
        p["corte"] = corte_iso
    ult_sql += " GROUP BY id_cuenta"
    ult_op = {r["id_cuenta"]: _iso(r["ult"]) for r in _q(ult_sql, p) if r["ult"] is not None}

    # CUPO VIVO: `cupo_usado_ars` es la FOTO del 2026-06-01 (config.CUPO_BASE_FECHA)
    # y nada la actualiza. El usado REAL se mueve con la plata que entra y sale del
    # cliente — el mismo flujo que ya muestra la vista CASHFLOW. Se suma al leer
    # (una query cacheada para TODAS las cuentas), no se persiste: así no hay job
    # que pueda doble-contar. `flujo_cupo` viene en ARS, igual que la foto.
    flujo_cupo = _cf_sql.neto_por_cuenta(desde=CUPO_BASE_FECHA)

    clientes = []
    for idc in ids:
        ult = ult_op.get(idc)
        dias = (corte - date.fromisoformat(ult)).days if ult else None
        dias_win = dias if (dias is not None and dias <= dias_dormida) else None
        est = estado_comercial(dias_win, ult is not None, dias_activa, dias_dormida)
        f = ficha.get(idc, {})
        trans, usado = f.get("cupo_transaccional_ars"), f.get("cupo_usado_ars")
        usado_vivo = (float(usado) + flujo_cupo.get(idc, 0.0)) if usado is not None else None
        clientes.append({
            "id_cuenta": idc, "denominacion": f.get("denominacion") or "—",
            "aum": _cv(aum.get(idc, 0.0), factor), "ultima_op": ult, "dias_sin_operar": dias,
            "estado": est, "opero_ytd": bool(ult) and ult >= year_start,
            "opero_mtd": bool(ult) and ult >= month_start,
            "cupo_transaccional_usd": (_cv(float(trans), factor_cupo)
                                       if trans is not None and factor_cupo else None),
            "cupo_usado_usd": (_cv(usado_vivo, factor_cupo)
                               if usado_vivo is not None and factor_cupo else None),
            # El flujo aplicado, para poder auditar la diferencia contra la foto.
            "cupo_flujo_usd": (_cv(flujo_cupo.get(idc, 0.0), factor_cupo)
                               if factor_cupo else None),
            **{n: f.get(n) for n in _ANALISIS if n != "denominacion"},
        })
    clientes.sort(key=lambda x: x["aum"], reverse=True)
    return {"operador": operador, "dias_activa": dias_activa,
            "dias_dormida": dias_dormida, "fecha": fecha, "clientes": clientes}


# ── Auditoría de DÍAS SIN OPERAR (modal de la tabla ESTADO COMERCIAL) ────────
#
# Mismo principio que el detalle por celda de Tesorería: el número que muestra la
# tabla tiene que poder abrirse y mostrar EXACTAMENTE de qué boleto sale. Antes,
# "1 día sin operar" era un número sin respaldo — para saber qué operación lo
# generó había que salir de la vista y buscar a mano en MOVIMIENTOS.
#
# El cálculo NO se rehace acá: se reusa `_ULT_OP_WHERE` (el predicado de la
# tabla) y se listan los boletos crudos, marcando los que NO cuentan (anulados,
# posteriores al corte) con su motivo — igual que los movimientos destildados
# del modal de Tesorería.

# Columnas del boleto que se muestran en la auditoría (crudas, sin derivar).
_COLS_BOLETO = ("boleto, concertacion, operacion, tipo_operacion, instrumento, mercado, "
                "moneda, bruto, arancel, cantidad, etapa, es_cierre, condiciones, "
                "anulado_en, ingestado_en")


def _dmy(d) -> str | None:
    return d.strftime("%d/%m/%Y") if d is not None else None


def _item_boleto(r: dict, *, corte: date, ult) -> dict:
    """Fila de boleto → item del modal, con el motivo si NO cuenta."""
    fecha = r["concertacion"]
    excluido, obs = False, ""
    if r.get("anulado_en") is not None:
        excluido = True
        obs = f"anulado el {_dmy(r['anulado_en'].date())} — no cuenta"
    elif fecha is None:
        excluido = True
        obs = "boleto sin fecha de concertación — no cuenta"
    elif fecha > corte:
        excluido = True
        obs = f"posterior al corte {_dmy(corte)} — no cuenta en la foto"
    return {
        "boleto": r["boleto"], "fecha": _dmy(fecha), "fecha_iso": _iso(fecha),
        "operacion": r["operacion"], "tipo_operacion": r["tipo_operacion"],
        "instrumento": r["instrumento"], "mercado": r["mercado"], "moneda": r["moneda"],
        "bruto": _f(r["bruto"]) if r["bruto"] is not None else None,
        "arancel": _f(r["arancel"]) if r["arancel"] is not None else None,
        "cantidad": _f(r["cantidad"]) if r["cantidad"] is not None else None,
        "etapa": r["etapa"], "es_cierre": bool(r["es_cierre"]),
        "condiciones": r["condiciones"],
        "ingestado_en": r["ingestado_en"].isoformat() if r["ingestado_en"] else None,
        # `es_ultima`: ESTE es el boleto que fija los días sin operar.
        "es_ultima": (not excluido) and ult is not None and fecha == ult,
        "excluido": excluido, "observacion": obs,
    }


def detalle_ultima_op(*, id_cuenta: str, fecha: str | None = None,
                      dias_activa: int = 45, dias_dormida: int = 90,
                      limite: int = 25) -> dict:
    """Auditoría de una fila de ESTADO COMERCIAL: qué boleto fija DÍAS SIN OPERAR.

    Devuelve la última operación que cuenta (con TODOS los boletos de ese día),
    el historial reciente y los boletos que NO cuentan con su motivo. `fecha`
    (ISO) = mismo corte que usa la tabla en modo foto.
    """
    idc = str(id_cuenta)
    corte = date.fromisoformat(fecha) if fecha else _hoy_art()
    p: dict = {"idc": idc, "corte": corte, "lim": int(limite)}

    cab = _q("SELECT u.denominacion, c.operador_email, o.nombre AS operador_nombre, "
             "c.nivel_1, c.nivel_3, c.estado, c.fecha_alta_legajo "
             "FROM cuentas u LEFT JOIN comitentes c ON c.id_cuenta = u.id_cuenta "
             "LEFT JOIN operadores o ON o.email = c.operador_email "
             "WHERE u.id_cuenta = %(idc)s", {"idc": idc})
    cab = cab[0] if cab else {}

    # 1) La última op que CUENTA — misma query que la tabla, para una sola cuenta.
    ult = _q(f"SELECT max(concertacion) AS ult FROM operaciones "
             f"WHERE id_cuenta = %(idc)s AND {_ULT_OP_WHERE} "
             f"AND concertacion <= %(corte)s", p)[0]["ult"]

    # 2) Los boletos de ESE día (pueden ser varios; se muestran todos) + el
    #    historial reciente. Dos queries en vez de una con LIMIT: si la cuenta
    #    tiene muchos boletos anulados posteriores, el LIMIT solo podría dejar
    #    afuera justamente el boleto que fija el número.
    vistos: dict[str, dict] = {}
    if ult is not None:
        for r in _q(f"SELECT {_COLS_BOLETO} FROM operaciones "
                    f"WHERE id_cuenta = %(idc)s AND concertacion = %(ult)s "
                    f"ORDER BY boleto DESC", {"idc": idc, "ult": ult}):
            vistos[str(r["boleto"])] = r
    for r in _q(f"SELECT {_COLS_BOLETO} FROM operaciones "
                f"WHERE id_cuenta = %(idc)s AND concertacion <= %(corte)s "
                f"ORDER BY concertacion DESC NULLS LAST, boleto DESC LIMIT %(lim)s", p):
        vistos.setdefault(str(r["boleto"]), r)

    # 3) Boletos POSTERIORES al corte (solo en modo foto): no cuentan, pero
    #    explican por qué la foto muestra más días que la vista de hoy. Se listan
    #    (capeados) y `count(*) OVER()` da el total real sin una segunda query.
    n_posteriores = 0
    if fecha:
        for r in _q(f"SELECT {_COLS_BOLETO}, count(*) OVER() AS n_total FROM operaciones "
                    f"WHERE id_cuenta = %(idc)s AND concertacion > %(corte)s "
                    f"ORDER BY concertacion, boleto LIMIT %(lim)s", p):
            n_posteriores = int(r["n_total"])
            vistos.setdefault(str(r["boleto"]), r)

    items = [_item_boleto(r, corte=corte, ult=ult) for r in vistos.values()]
    items.sort(key=lambda i: (i["fecha_iso"] or "", i["boleto"] or ""), reverse=True)

    dias = (corte - ult).days if ult is not None else None
    dias_win = dias if (dias is not None and dias <= dias_dormida) else None
    est = estado_comercial(dias_win, ult is not None, dias_activa, dias_dormida)
    ecuacion = (f"{_dmy(corte)} ({'corte' if fecha else 'hoy'}) − {_dmy(ult)} "
                f"(última op) = {dias} día{'s' if dias != 1 else ''}"
                if ult is not None else "la cuenta no registra boletos — nunca operó")

    return {
        "id_cuenta": idc, "denominacion": cab.get("denominacion") or "—",
        "operador_email": cab.get("operador_email"),
        "operador_nombre": cab.get("operador_nombre"),
        "nivel_1": cab.get("nivel_1"), "nivel_3": cab.get("nivel_3"),
        "estado_legal": cab.get("estado"),
        "fecha_alta_legajo": _iso(cab.get("fecha_alta_legajo")),
        "corte": _iso(corte), "fecha": _dmy(corte), "es_foto": bool(fecha),
        "fuente": ("operaciones.operaciones — cuenta CUALQUIER boleto no anulado "
                   "(sin filtro de tipo, mercado ni etapa)"),
        "ultima_op": _iso(ult), "ultima_op_dmy": _dmy(ult),
        "dias_sin_operar": dias, "estado": est, "ecuacion": ecuacion,
        "umbrales": {"activa": dias_activa, "dormida": dias_dormida},
        "n_boletos_ultima_fecha": sum(1 for i in items if i["es_ultima"]),
        "n_excluidos": sum(1 for i in items if i["excluido"]),
        "n_posteriores_corte": n_posteriores,
        "limite": int(limite), "items": items,
    }


# ── INFORME (global, transversal a la mesa) ──────────────────────────────────
def _fin_de_mes(anio: int, mes: int) -> date:
    """Último día del mes (date). fecha_alta_legajo es date en SQL → comparación por día."""
    ini_sig = date(anio + 1, 1, 1) if mes == 12 else date(anio, mes + 1, 1)
    return ini_sig - timedelta(days=1)


def informe_cuentas_por_segmento(*, hasta: str | None = None,
                                 operador: str | None = None,
                                 fecha: str | None = None,
                                 desde: str | None = None,
                                 nivel_1=None, nivel_2=None, nivel_3=None,
                                 nivel_4=None, nivel_5=None, referido=None,
                                 division=None) -> dict:
    hoy = _hoy_art()
    if fecha:
        # Fecha de corte exacta (unificada con el resto de la vista): cuentas con alta <= fecha.
        corte = date.fromisoformat(fecha)
        anio, mes = corte.year, corte.month
    else:
        anio, mes = (int(hasta[:4]), int(hasta[5:7])) if hasta else (hoy.year, hoy.month)
        corte = _fin_de_mes(anio, mes)
    where = "estado = 'Activa' AND fecha_alta_legajo <= %(corte)s"
    p: dict = {"corte": corte}
    if operador:
        where += " AND operador_email = %(op)s"
        p["op"] = operador
    where = _append_niveles(where, p, "", nivel_1, nivel_2, nivel_3, nivel_4, nivel_5, referido, division)
    segmentos = [{"segmento": r["segmento"], "n": r["n"]} for r in _q(
        f"SELECT COALESCE(nivel_1, '(sin segmentar)') AS segmento, count(*) AS n "
        f"FROM comitentes WHERE {where} GROUP BY COALESCE(nivel_1, '(sin segmentar)') "
        f"ORDER BY n DESC", p)]

    # Operativas por segmento: cuentas DISTINTAS que operaron (>=1 boleto) en el MES
    # CALENDARIO del HASTA [1º de ese mes, corte]. Independiente del `desde`.
    #
    # Va contra `operaciones` con `_act_where`, el MISMO predicado que CTAS OPS del
    # ranking y que DÍAS SIN OPERAR. Leía `negocio_movimientos` por categoría, que
    # es de donde las cuentas OTC están excluidas EN LA INGESTA — y las dos mitades
    # de la misma pantalla contaban distinto sin que nada fallara.
    mes_ini = date(anio, mes, 1).isoformat()
    ano_ini = date(anio, 1, 1).isoformat()
    p2: dict = {"mes": mes_ini, "ano": ano_ini, "corte": corte}
    w_op = (f"{_act_where('o')} "
            f"AND o.concertacion >= %(ano)s AND o.concertacion <= %(corte)s")
    if operador:
        w_op += " AND c.operador_email = %(op)s"
        p2["op"] = operador
    w_op = _append_niveles(w_op, p2, "c", nivel_1, nivel_2, nivel_3, nivel_4, nivel_5, referido, division)
    # DOS ventanas en UNA pasada: el MES del corte y el AÑO hasta el corte.
    #
    # El año hacía falta y no existía en ningún lado. Las pills de ANÁLISIS COMERCIAL
    # (ACTIVA/ENFRIÁNDOSE/DORMIDA) miden DÍAS DESDE LA ÚLTIMA OP, que es otra pregunta:
    # una cuenta que operó en marzo y paró figura DORMIDA y sin embargo operó este año.
    # Y la única pill anual era la NEGATIVA ("sin operar en el año"), o sea había que
    # dar vuelta el número a mano y no estaba abierta por segmento.
    #
    # Se scanea el año y el mes sale por FILTER: el mes está contenido en el año, así
    # que pedirlo aparte sería recorrer dos veces lo mismo.
    ops_map = {r["segmento"]: r for r in _q(
        f"SELECT COALESCE(c.nivel_1, '(sin segmentar)') AS segmento, "
        f"count(DISTINCT o.id_cuenta) FILTER (WHERE o.concertacion >= %(mes)s) AS n_mes, "
        f"count(DISTINCT o.id_cuenta) AS n_ano "
        f"FROM operaciones o JOIN comitentes c ON c.id_cuenta = o.id_cuenta "
        f"AND c.estado = 'Activa' WHERE {w_op} "
        f"GROUP BY COALESCE(c.nivel_1, '(sin segmentar)')", p2)}
    for s in segmentos:
        r = ops_map.get(s["segmento"])
        s["ctas_ops"] = int((r or {}).get("n_mes") or 0)
        s["ctas_ops_ano"] = int((r or {}).get("n_ano") or 0)

    fa = _q("SELECT min(fecha_alta_legajo) AS f FROM comitentes "
            "WHERE fecha_alta_legajo IS NOT NULL")[0]["f"]
    mes_min = f"{fa.year:04d}-{fa.month:02d}" if fa else f"{hoy.year:04d}-{hoy.month:02d}"
    return {
        "mes": f"{anio:04d}-{mes:02d}", "mes_min": mes_min,
        "mes_actual": f"{hoy.year:04d}-{hoy.month:02d}",
        "total": sum(s["n"] for s in segmentos),
        "total_ctas_ops": sum(s["ctas_ops"] for s in segmentos),
        "total_ctas_ops_ano": sum(s["ctas_ops_ano"] for s in segmentos),
        "ano": anio,
        "segmentos": segmentos,
    }


def _rollup_por_cuenta(scope: str | None, p: dict,
                       desde: str | None = None,
                       hasta: str | None = None) -> dict[str, dict]:
    """{id_cuenta: {vol_total, vol_mes, n_ops, ar_total, ar_mes, opero_mes}} en vivo.
    vol/n_ops de negocio_movimientos (cats), arancel de operaciones.
    TOTAL (vol_total/ar_total/n_ops) = período elegido [desde, hasta]: sin `desde` no hay
    límite inferior (histórico), sin `hasta` corre hasta hoy. MES (vol_mes/ar_mes)
    = el MES CALENDARIO del HASTA [1º de ese mes, hasta] (si no hay hasta, mes actual);
    independiente del `desde`. Ej: hasta=30/06 → MES = junio; hasta=31/05 → MES = mayo.

    ⚠️ `opero_mes` (= CTAS OPS del informe) NO sale de `negocio_movimientos` como el
    resto: sale de `operaciones.operaciones` con `_ULT_OP_WHERE`, el MISMO predicado
    que DÍAS SIN OPERAR. El porqué, en el bloque QUIÉN OPERÓ EN EL MES de abajo."""
    p["cats"] = list(_CATS_VOLUMEN)
    corte = date.fromisoformat(hasta) if hasta else _hoy_art()
    mes_ini = corte.replace(day=1).isoformat()
    p["mes_ini"] = mes_ini

    # Tope superior = HASTA: aplica a TODO (así el MES del hasta no arrastra meses futuros
    # cuando el corte es una fecha pasada). Sin `hasta` no se acota (no hay datos a futuro).
    ub_vol = ub_ar = ""
    if hasta:
        p["hasta"] = hasta
        ub_vol = " AND fecha <= %(hasta)s"
        ub_ar = " AND concertacion <= %(hasta)s"
    # Límite inferior de la ventana TOTAL = `desde`. Vacío → TRUE = histórico.
    lb_vol = lb_ar = "TRUE"
    if desde:
        p["desde"] = desde
        lb_vol = "fecha >= %(desde)s"
        lb_ar = "concertacion >= %(desde)s"
    # Piso del scan = min(desde, mes_ini): necesitamos filas del TOTAL (>=desde) y del MES
    # (>=mes_ini). Sin `desde` el TOTAL es histórico → no se acota por abajo.
    lo_vol = lo_ar = ""
    if desde:
        lo = min(desde, mes_ini)
        p["lo"] = lo
        lo_vol = " AND fecha >= %(lo)s"
        lo_ar = " AND concertacion >= %(lo)s"

    w_vol = (f"unidad IS DISTINCT FROM 'USDL' AND categoria = ANY(%(cats)s) "
             f"AND anulado_en IS NULL{ub_vol}{lo_vol}")
    w_ar = f"{_arancel_where()} AND anulado_en IS NULL{ub_ar}{lo_ar}"
    if scope:
        w_vol += f" AND {scope}"
        w_ar += f" AND {scope}"
    rows = _q(
        f"WITH vol AS (SELECT id_cuenta, "
        f"  SUM(CASE WHEN {lb_vol} THEN {_PESIF} ELSE 0 END) AS vol_total, "
        f"  SUM(CASE WHEN fecha >= %(mes_ini)s THEN {_PESIF} ELSE 0 END) AS vol_mes, "
        f"  SUM(CASE WHEN {lb_vol} THEN 1 ELSE 0 END) AS n_ops "
        f"  FROM negocio_movimientos WHERE {w_vol} GROUP BY id_cuenta), "
        f"ar AS (SELECT id_cuenta, "
        f"  SUM(CASE WHEN {lb_ar} THEN arancel ELSE 0 END) AS ar_total, "
        f"  SUM(CASE WHEN concertacion >= %(mes_ini)s THEN arancel ELSE 0 END) AS ar_mes "
        f"  FROM operaciones WHERE {w_ar} GROUP BY id_cuenta) "
        f"SELECT COALESCE(v.id_cuenta, a.id_cuenta) AS id_cuenta, "
        f"  COALESCE(v.vol_total,0) AS vol_total, COALESCE(v.vol_mes,0) AS vol_mes, "
        f"  COALESCE(v.n_ops,0) AS n_ops, "
        f"  COALESCE(a.ar_total,0) AS ar_total, COALESCE(a.ar_mes,0) AS ar_mes "
        f"FROM vol v FULL OUTER JOIN ar a ON v.id_cuenta = a.id_cuenta", p)
    out = {r["id_cuenta"]: r for r in rows if r["id_cuenta"]}

    # ── QUIÉN OPERÓ EN EL MES (= CTAS OPS) ────────────────────────────────────
    #
    # Query aparte y contra OTRA tabla, a propósito. `vol` sale de
    # `negocio_movimientos`, y ahí las cuentas OTC NO EXISTEN: la ingesta las tira
    # por substring en el nombre (`aunesa_negocio._excluir`). Contar "cuentas que
    # operaron" sobre esa tabla dejaba al informe contradiciéndose SOLO consigo
    # mismo: la misma fila mostraba arancel cobrado y 0 cuentas operativas, porque
    # el arancel sí sale de `operaciones`. Y si le cobramos arancel, operó — el
    # arancel ES la comisión de un boleto.
    #
    # Predicado: `_ULT_OP_WHERE` (cualquier boleto no anulado), el mismo de DÍAS
    # SIN OPERAR y de la columna ACTIVOS de PROFUNDIDAD. Así queda UNA sola
    # definición de "cuenta operativa" en toda la app, congelada por test.
    #
    # Scopeada al MES (`>= mes_ini`, `<= hasta`) → cae en `ix_ops_concertacion`.
    # No se mete en la CTE `ar` porque esa no acota por abajo cuando no hay
    # `desde`: ensancharle el WHERE la volvería un scan histórico.
    w_act = f"concertacion >= %(mes_ini)s{ub_ar} AND {_ULT_OP_WHERE} AND id_cuenta IS NOT NULL"
    if scope:
        w_act += f" AND {scope}"
    vacio = {"vol_total": 0, "vol_mes": 0, "n_ops": 0, "ar_total": 0, "ar_mes": 0}
    for r in _q(f"SELECT DISTINCT id_cuenta FROM operaciones WHERE {w_act}", p):
        idc = r["id_cuenta"]
        # Una cuenta puede haber operado sin volumen NI arancel (boleto con arancel
        # 0). Igual operó: entra al dict, con los importes en cero.
        out.setdefault(idc, {"id_cuenta": idc, **vacio})["opero_mes"] = True
    for fila in out.values():
        fila.setdefault("opero_mes", False)
    return out


def _ticket(vol: float, n: int) -> float:
    return round(vol / n, 2) if n else 0.0


def informe_comercial(*, moneda: str = "ARS", fecha: str | None = None,
                      desde: str | None = None, operador=None, nivel_1=None,
                      nivel_2=None, nivel_3=None, nivel_4=None, nivel_5=None,
                      referido=None, division=None) -> dict:
    # Columnas TOTAL (vol_total/ar_total/n_ops) = período elegido [desde, hasta] (=`fecha`).
    # Columnas MES (vol_mes/ar_mes) + CTAS OPS = mes calendario EN CURSO, independiente del
    # período. Sin desde/hasta: TOTAL = histórico hasta hoy, MES = mes actual (igual que antes).
    # Filtros madre (operador/nivel_1..5/referido): si HAY alguno, se scopea el rollup a
    # esas cuentas; sin filtro queda global (idéntico a siempre).
    factor = _factor_usd(moneda)
    p_scope: dict = {}
    scope = None
    if _madre_activa(operador, nivel_1, nivel_2, nivel_3, nivel_4, nivel_5, referido, division):
        scope = _scope_cuentas(operador, p_scope, nivel_1, nivel_3, referido,
                               nivel_4=nivel_4, nivel_5=nivel_5, nivel_2=nivel_2, division=division)
    por_cuenta = _rollup_por_cuenta(scope, p_scope, desde=desde, hasta=fecha)

    detalle = {r["id_cuenta"]: r for r in _q(
        "SELECT c.id_cuenta, c.operador_email, o.nombre AS operador_nombre, c.nivel_1 "
        "FROM comitentes c LEFT JOIN operadores o ON o.email = c.operador_email "
        "WHERE c.estado = 'Activa'")}

    ops: dict[str, dict] = {}
    segs: dict[str, dict] = {}
    for idc, agg in por_cuenta.items():
        info = detalle.get(idc)
        # Cuenta que NO figura en Comitentes Activa (no-cliente: propia/inactiva/
        # cancelada) → fuera del informe. Mismo criterio que la versión Mongo
        # (comercial.informe_comercial). `detalle` ya está en memoria → skip gratis.
        if info is None:
            continue
        key = (info.get("operador_email") or "").strip().lower() or "(sin operador)"
        o = ops.get(key)
        if o is None:
            o = ops[key] = {
                "operador_email": info.get("operador_email"),
                "operador_nombre": (info.get("operador_nombre") or info.get("operador_email")
                                    or "(sin operador)"),
                "vol_total": 0.0, "vol_mes": 0.0, "ar_total": 0.0, "ar_mes": 0.0,
                "n_ops": 0, "ctas_ops": 0,
            }
        for k in ("vol_total", "vol_mes", "ar_total", "ar_mes"):
            o[k] += _f(agg[k])
        o["n_ops"] += int(agg["n_ops"] or 0)
        # Ctas Ops = cuentas DISTINTAS que operaron en el mes del corte (≥1 boleto en
        # la ventana [día 1 del mes, corte]). Cada cuenta cuenta como 1, opere 1 vez
        # o mil. El flag lo calcula `_rollup_por_cuenta` sobre `operaciones` — leerlo
        # de `n_ops_mes` (negocio_movimientos) dejaba afuera a las cuentas OTC, que
        # esta MISMA fila muestra cobrando arancel.
        if agg.get("opero_mes"):
            o["ctas_ops"] += 1

        seg = info.get("nivel_1") or "(sin segmentar)"
        s = segs.get(seg)
        if s is None:
            s = segs[seg] = {"segmento": seg, "ar_total": 0.0, "ar_mes": 0.0,
                             "vol_total": 0.0, "n_ops": 0, "n_cuentas": 0}
        s["ar_total"] += _f(agg["ar_total"])
        s["ar_mes"] += _f(agg["ar_mes"])
        s["vol_total"] += _f(agg["vol_total"])
        s["n_ops"] += int(agg["n_ops"] or 0)
        if _f(agg["ar_total"]) > 0:
            s["n_cuentas"] += 1

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
    corte = date.fromisoformat(fecha) if fecha else _hoy_art()
    return {"mes_actual": f"{corte.year:04d}-{corte.month:02d}", "fecha": fecha,
            "comerciales": comerciales, "aranceles_segmento": segmentos}


def informe_cliente_operaciones(*, id_cuenta: str, moneda: str = "ARS",
                                fecha: str | None = None, ventana: str = "mes") -> dict:
    """Los boletos de UN cliente en el MES del corte — el modal de la tabla Detalle.

    Reemplaza a la lista genérica de "todas las operaciones de todos los clientes del
    segmento", que mostraba miles de filas sin dueño y no se podía leer.

    Dos decisiones que tienen que quedar dichas:

    1. **La ventana la elige quien abre** (`ventana`): `mes` = el mes calendario del
       HASTA · `ano` = del 1 de enero al HASTA. Nunca el período `[Desde, Hasta]`.

       Tiene que ser la MISMA con la que el filtro de la tabla dejó pasar esa fila.
       Ya pasó al revés y por eso está parametrizado: el modal estaba clavado al mes,
       se abría desde una fila filtrada por AÑO, y una cuenta que operó en marzo salía
       "Sin boletos en ago 26" — la pantalla desmintiendo su propio filtro.

    2. **Trae CUALQUIER boleto no anulado** (`_ULT_OP_WHERE`), no solo los que cobraron
       arancel. Por lo mismo: el arancel no es lo que define que la cuenta operó, y
       filtrar por él dejaría en blanco justo a las cuentas que operaron sin facturar
       —las que la tabla ahora muestra con arancel en cero—.

    Los totales (volumen, arancel, n) se calculan ACÁ, sobre las mismas filas que se
    devuelven: un total que el navegador suma aparte puede no coincidir con su lista.
    """
    # Import LOCAL: `profundidad_sql` importa de este módulo (`_act_where`, `_f`, …),
    # así que a nivel de módulo sería un ciclo. La etiqueta se reusa en vez de
    # copiarse para que el modal no llame "Caución tomadora" a lo que otra pantalla
    # llama distinto.
    from api.services.profundidad_sql import _op_label

    idc = str(id_cuenta)
    corte = date.fromisoformat(fecha) if fecha else _hoy_art()
    es_ano = str(ventana) == "ano"
    ini = date(corte.year, 1, 1) if es_ano else corte.replace(day=1)
    factor = _factor_usd(moneda)
    p: dict = {"idc": idc, "ini": ini, "fin": corte}

    cab = _q("SELECT u.denominacion, c.nivel_1, o.nombre AS operador_nombre "
             "FROM cuentas u LEFT JOIN comitentes c ON c.id_cuenta = u.id_cuenta "
             "LEFT JOIN operadores o ON o.email = c.operador_email "
             "WHERE u.id_cuenta = %(idc)s", {"idc": idc})
    cab = cab[0] if cab else {}

    filas = _q(f"SELECT concertacion, boleto, operacion, tipo_operacion, instrumento, "
               f"  mercado, moneda, bruto, arancel, cantidad, etapa, es_cierre, mep "
               f"FROM operaciones "
               f"WHERE id_cuenta = %(idc)s AND {_ULT_OP_WHERE} "
               f"  AND concertacion >= %(ini)s AND concertacion <= %(fin)s "
               f"ORDER BY concertacion DESC, boleto DESC", p)

    ops, vol, ar = [], 0.0, 0.0
    for r in filas:
        # Volumen: mismo criterio que el resto del informe — fuera los cierres de
        # caución (doble conteo) y las solicitudes sin liquidar. Se pesifica por el
        # MEP DEL BOLETO, no por el de hoy.
        cuenta_vol = not bool(r["es_cierre"]) and r["etapa"] != "solicitud"
        bruto = _f(r["bruto"]) or 0.0
        bruto_ars = bruto if (r["moneda"] or "ARS") == "ARS" else bruto * (_f(r["mep"]) or 0.0)
        if cuenta_vol:
            vol += abs(bruto_ars)
        if (_f(r["arancel"]) or 0.0) > 0 and r["etapa"] != "solicitud":
            ar += abs(_f(r["arancel"]) or 0.0)
        ops.append({
            "fecha": _iso(r["concertacion"]),
            "fecha_dmy": _dmy(r["concertacion"]),
            "boleto": r["boleto"],
            "operacion": r["operacion"], "operacion_label": _op_label(r["operacion"] or ""),
            "tipo_operacion": r["tipo_operacion"], "instrumento": r["instrumento"],
            "mercado": r["mercado"], "moneda": r["moneda"],
            "bruto": _cv(abs(bruto_ars), factor),
            "arancel": _cv(abs(_f(r["arancel"]) or 0.0), factor),
            "cantidad": _f(r["cantidad"]),
            "etapa": r["etapa"], "es_cierre": bool(r["es_cierre"]),
            "cuenta_volumen": cuenta_vol,
        })

    return {
        "id_cuenta": idc, "denominacion": cab.get("denominacion") or "—",
        "operador_nombre": cab.get("operador_nombre"), "nivel_1": cab.get("nivel_1"),
        "mes": f"{corte.year:04d}-{corte.month:02d}", "ano": corte.year,
        "ventana": "ano" if es_ano else "mes",
        "desde": ini.isoformat(), "hasta": corte.isoformat(),
        "n_boletos": len(ops), "volumen": _cv(vol, factor), "arancel": _cv(ar, factor),
        "operaciones": ops,
    }


def informe_aranceles_segmento(*, operador: str, moneda: str = "ARS",
                               fecha: str | None = None, desde: str | None = None,
                               nivel_1=None, nivel_2=None, nivel_3=None,
                               nivel_4=None, nivel_5=None, referido=None,
                               division=None) -> dict:
    factor = _factor_usd(moneda)
    p_c: dict = {"op": operador}
    w_c = _append_niveles("operador_email = %(op)s AND estado = 'Activa'", p_c, "",
                          nivel_1, nivel_2, nivel_3, nivel_4, nivel_5, referido, division)
    cuentas = {r["id_cuenta"]: (r["nivel_1"] or "(sin segmentar)") for r in _q(
        f"SELECT id_cuenta, nivel_1 FROM comitentes WHERE {w_c}", p_c)}
    if not cuentas:
        return {"operador": operador, "aranceles_segmento": []}
    scope = "id_cuenta = ANY(%(ids)s)"
    por_cuenta = _rollup_por_cuenta(scope, {"ids": list(cuentas)}, desde=desde, hasta=fecha)
    segs: dict[str, dict] = {}
    for idc, agg in por_cuenta.items():
        seg = cuentas.get(idc, "(sin segmentar)")
        s = segs.get(seg)
        if s is None:
            s = segs[seg] = {"segmento": seg, "ar_total": 0.0, "ar_mes": 0.0,
                             "vol_total": 0.0, "n_ops": 0, "n_cuentas": 0}
        s["vol_total"] += _f(agg["vol_total"])
        s["n_ops"] += int(agg["n_ops"] or 0)
        s["ar_total"] += _f(agg["ar_total"])
        s["ar_mes"] += _f(agg["ar_mes"])
        if _f(agg["ar_total"]) > 0:
            s["n_cuentas"] += 1
    out = sorted(segs.values(), key=lambda x: x["ar_total"], reverse=True)
    for s in out:
        for k in ("ar_total", "ar_mes", "vol_total"):
            s[k] = _cv(s[k], factor)
        s["ticket_promedio"] = _ticket(s["vol_total"], s["n_ops"])
    return {"operador": operador, "aranceles_segmento": out}


def informe_segmento_detalle(*, segmento: str | None = None, operador: str | None = None,
                             moneda: str = "ARS", fecha: str | None = None,
                             desde: str | None = None, nivel_1=None, nivel_2=None,
                             nivel_3=None, nivel_4=None, nivel_5=None, referido=None,
                             division=None) -> dict:
    hoy = _hoy_art()
    factor = _factor_usd(moneda)
    corte = date.fromisoformat(fecha) if fecha else hoy
    corte_iso = corte.isoformat() if fecha else None
    # arancel_total = período [desde, hasta]; arancel_mes = el MES CALENDARIO del HASTA.
    mes_ini = corte.replace(day=1).isoformat()
    where = "c.estado = 'Activa'"
    p: dict = {}
    if not segmento or segmento == "todos":
        pass
    elif segmento == "(sin segmentar)":
        where += " AND c.nivel_1 IS NULL"
    else:
        where += " AND c.nivel_1 = %(seg)s"
        p["seg"] = segmento
    if operador:
        where += " AND c.operador_email = %(op)s"
        p["op"] = operador
    # `segmento` ya fija nivel_1 (Q4 es el detalle de UN segmento) → no re-aplico nivel_1
    # madre acá para no contradecirlo; sí el resto de los niveles + referido.
    where = _append_niveles(where, p, "c", None, nivel_2, nivel_3, nivel_4, nivel_5, referido, division)
    detalle = {r["id_cuenta"]: r["denominacion"] for r in _q(
        f"SELECT c.id_cuenta, u.denominacion FROM comitentes c "
        f"LEFT JOIN cuentas u ON u.id_cuenta = c.id_cuenta WHERE {where}", p)}
    ids = list(detalle)
    if not ids:
        return {"segmento": segmento or "todos", "n_clientes": 0,
                "n_operativas": 0, "n_operativas_ano": 0, "clientes": []}

    pa: dict = {"ids": ids, "mes_ini": mes_ini}
    ub = ""                                    # tope superior = HASTA (aplica a todo)
    if corte_iso:
        pa["corte"] = corte_iso
        ub = " AND concertacion <= %(corte)s"
    lb_tot = "TRUE"                            # piso de la ventana TOTAL = DESDE
    if desde:
        pa["desde"] = desde
        lb_tot = "concertacion >= %(desde)s"
    lo = ""                                     # piso del scan = min(desde, mes_ini)
    if desde:
        pa["lo"] = min(desde, mes_ini)
        lo = " AND concertacion >= %(lo)s"
    # QUIÉN OPERÓ EN EL MES, para el filtro "solo operativas" de la tabla. Mismo
    # predicado y misma ventana que la columna CTAS OPS del ranking (`_ULT_OP_WHERE`
    # sobre `operaciones`, mes calendario del HASTA) — el filtro tiene que dejar
    # exactamente las cuentas que ese número cuenta, o la pantalla se contradice.
    # Viaja como FLAG por fila y no como una lista aparte: así el front filtra sin
    # pedir de nuevo y no puede quedar desfasado del dato que ya dibujó.
    # DOS ventanas: el MES del corte y el AÑO hasta el corte. El mes está contenido en
    # el año → se scanea el año una vez y el mes sale por FILTER.
    pa["ano_ini"] = date(corte.year, 1, 1)
    op_win = {r["id_cuenta"]: r for r in _q(
        f"SELECT id_cuenta, bool_or(concertacion >= %(mes_ini)s) AS en_mes "
        f"FROM operaciones "
        f"WHERE id_cuenta = ANY(%(ids)s) AND concertacion >= %(ano_ini)s{ub} "
        f"AND {_ULT_OP_WHERE} GROUP BY id_cuenta", pa)}
    operativas_ano = set(op_win)
    operativas = {i for i, r in op_win.items() if r["en_mes"]}

    clientes = []
    for r in _q(f"SELECT id_cuenta, "
                f"SUM(CASE WHEN {lb_tot} THEN arancel ELSE 0 END) AS ar_total, "
                f"SUM(CASE WHEN concertacion >= %(mes_ini)s THEN arancel ELSE 0 END) AS ar_mes "
                f"FROM operaciones WHERE id_cuenta = ANY(%(ids)s) AND arancel > 0 "
                f"AND anulado_en IS NULL "
                f"AND etapa IS DISTINCT FROM 'solicitud'{ub}{lo} GROUP BY id_cuenta", pa):
        idc = r["id_cuenta"]
        clientes.append({
            "id_cuenta": idc, "denominacion": detalle.get(idc) or "—",
            "arancel_total": _cv(_f(r["ar_total"]), factor),
            "arancel_mes": _cv(_f(r["ar_mes"]), factor),
            "opero_mes": idc in operativas,
            "opero_ano": idc in operativas_ano,
        })
    # Las que OPERARON y no dejaron un peso de arancel en la ventana. Entran igual,
    # con el arancel en cero.
    #
    # Sin esto el filtro "solo operativas" mostraba MENOS cuentas que el CTAS OPS del
    # ranking (medido en pantalla: 30 contra 33) y las dos cifras eran "correctas" —
    # la lista nacía de un WHERE `arancel > 0` y el contador de arriba no. Dos números
    # de la misma pregunta que no coinciden es un bug aunque ninguno esté mal
    # calculado: el que mira no tiene cómo saber cuál creer. Y son justo las filas
    # más interesantes de la tabla — operó y no facturó.
    vistos = {c["id_cuenta"] for c in clientes}
    for idc in sorted(operativas_ano - vistos):
        clientes.append({
            "id_cuenta": idc, "denominacion": detalle.get(idc) or "—",
            "arancel_total": 0.0, "arancel_mes": 0.0,
            "opero_mes": idc in operativas, "opero_ano": True,
        })
    clientes.sort(key=lambda x: x["arancel_total"], reverse=True)

    # El contador del filtro lo cuenta el BACKEND, sobre la misma lista que devuelve:
    # sumarlo en el navegador es cómo un contador termina diciendo algo que la tabla
    # de al lado no dice.
    #
    # Acá vivía además una lista con TODAS las operaciones de TODOS los clientes del
    # scope (capeada a `max_ops`, hasta 20.000 filas serializadas por request). La
    # consumía la tab OPERACIONES de esta tabla, que se dio de baja: miles de boletos
    # sin dueño no contestaban ninguna pregunta. Hoy los boletos se miran POR CLIENTE
    # y a pedido (`informe_cliente_operaciones`), así que la query se fue con ella —
    # dejarla habría sido pagar el viaje más caro del endpoint para nadie.
    return {"segmento": segmento or "todos", "n_clientes": len(clientes),
            "n_operativas": sum(1 for c in clientes if c["opero_mes"]),
            "n_operativas_ano": sum(1 for c in clientes if c["opero_ano"]),
            "clientes": clientes}
