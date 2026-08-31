"""api/services/comercial.py — Tablero Comercial: funciones SQL-native vivas.

⚠️ Decomiso Mongo (2026-06-29): este módulo era el gemelo Mongo del Tablero
Comercial. TODA la vista por operador (operadores / dimensiones / serie /
clientes-por-fecha / portafolio / operaciones / análisis / informes / debug) se
sirve EXCLUSIVAMENTE desde `comercial_sql.py` (el router `operaciones.py` resuelve
vía `_com_motor()`, que SIEMPRE devuelve comercial_sql). Esas funciones Mongo
quedaron muertas y se BORRARON acá.

Lo que sobrevive son las funciones que el router llama directo (`_com.`) y que ya
eran SQL-native (no tenían twin en comercial_sql):
  - cobros_futuros / cobros_futuros_cliente  → SQL operaciones.acreencias (vía cashflow_sql)
  - referido_clientes / referido_fci         → SQL comitentes/negocio_movimientos/tenencia/operaciones
…más los helpers PUROS/SQL que reusan + las constantes `_CATS_VOLUMEN` /
`_CATS_OPERACIONES` que importan `comercial_sql`, `sin_operador` y
`jobs/actividad_mensual`, y `_cuentas_de_operador` (lo usa `carteras.py`). NO borrar.

Diseño completo: docs/CLIENTES.md + `CLAUDE.md` §Tablero Comercial.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from api.cache import cached
from core.postgres import get_pool

# Sentinel del selector: "Todos los operadores" (vista del jefe). Cuando llega
# esto, las agregaciones NO filtran por id_cuenta (evita un $in de ~1770 ids).
TODOS = "__todos__"

# "Volumen operado" = mismo criterio que NEGOCIO: sum(abs(importe)) sobre estas
# categorías de boleto. Lo importan comercial_sql / sin_operador.
_CATS_VOLUMEN = (
    "compra", "venta",
    "suscripcion_fci", "solicitud_suscripcion_fci",
    "caucion_tom_ap", "caucion_col_ap",
)

# Categorías "operativas" para la tab Operaciones del cliente (lo que operó):
# = volumen + rescates de FCI. Excluye comisiones/acreencias/administrativos.
# Lo importa jobs/actividad_mensual.
_CATS_OPERACIONES = (
    *_CATS_VOLUMEN,
    "rescate_fci", "solicitud_rescate_fci",
)


def estado_comercial(
    dias_desde_ult_op: int | None,
    opero_alguna_vez: bool,
    dias_activa: int,
    dias_dormida: int,
) -> str:
    """Estado COMERCIAL (≠ legal), derivado de la última operación. PURO — lo reusa
    `comercial_sql.analisis_comercial`.

    `dias_desde_ult_op`: días desde la última op SI está dentro de la ventana
    reciente (`dias_dormida`), si no → None. `opero_alguna_vez`: si la cuenta
    aparece alguna vez en operaciones.

    NUEVA (nunca operó) · ACTIVA (≤ dias_activa) · ENFRIANDOSE (dias_activa..
    dias_dormida) · DORMIDA (operó alguna vez pero hace > dias_dormida).
    """
    if dias_desde_ult_op is not None:
        return "ACTIVA" if dias_desde_ult_op <= dias_activa else "ENFRIANDOSE"
    return "DORMIDA" if opero_alguna_vez else "NUEVA"


def _hoy_art() -> date:
    """Fecha de hoy en horario Argentina (UTC-3) para MTD/YTD calendario."""
    return (datetime.now(UTC) - timedelta(hours=3)).date()


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


# ── Valuación EN MONEDA DESTINO al MEP del TRADE (unificación ARS/USD) ─────────
# En vez de pesificar todo a ARS al MEP del boleto y después dolarizar el AGREGADO
# al MEP de HOY (round-trip que distorsiona las ops nativas en USD), valuamos CADA
# fila directo en la moneda destino, al MEP de su propio boleto:
#   • Vista ARS: op ARS = importe;         op USD = importe × mep_boleto.
#   • Vista USD: op USD = importe (exacto); op ARS = importe / mep_boleto.
# Así una op de USD 1.000 SIEMPRE son USD 1.000 y el histórico no se mueve con el
# dólar de hoy. `mep_hoy` = fallback SOLO para filas cuyo mep quedó null/0 (no se
# dropean del total). El SQL ya devuelve el valor en la moneda pedida → no hay `_cv`.
def _valor_expr(moneda: str, mep_hoy: float | None = None) -> str:
    """Expresión SQL: valor de un boleto (`importe`/`moneda`/`mep`) en la moneda destino."""
    if (moneda or "ARS").upper() == "USD":
        div = f"COALESCE(NULLIF(mep, 0), {float(mep_hoy)})" if mep_hoy else "NULLIF(mep, 0)"
        return (f"CASE WHEN moneda = 'ARS' THEN abs(COALESCE(importe, 0)) / {div} "
                f"ELSE abs(COALESCE(importe, 0)) END")
    return ("CASE WHEN moneda = 'ARS' THEN abs(COALESCE(importe, 0)) "
            "ELSE abs(COALESCE(importe, 0)) * COALESCE(mep, 0) END")


def _arancel_expr(moneda: str, mep_hoy: float | None = None) -> str:
    """Expresión SQL: `arancel` (SIEMPRE en ARS) en la moneda destino. ARS = arancel;
    USD = arancel / mep del boleto (fallback mep_hoy)."""
    if (moneda or "ARS").upper() == "USD":
        div = f"COALESCE(NULLIF(mep, 0), {float(mep_hoy)})" if mep_hoy else "NULLIF(mep, 0)"
        return f"COALESCE(arancel, 0) / {div}"
    return "COALESCE(arancel, 0)"


# Un filtro de nivel puede llegar como UN valor (`"MAYORISTA"`) o como VARIOS
# (`["MAYORISTA", "MINORISTA"]`, que es como los manda la barra madre multi-select).
# Los dos casos son el mismo predicado: `= ANY(lista)`. Normalizamos acá para que
# ningún caller tenga que decidir entre `=` y `IN` — elegir mal es lo que hace que
# un filtro pase de "achicar" a "no filtrar nada" sin que falle nada.
_Filtro = str | Sequence[str] | None


def _valores(v: _Filtro) -> list[str]:
    """Filtro → lista de valores no vacíos. `None`/`""`/`[]` → `[]` (= sin filtro)."""
    if v is None:
        return []
    if isinstance(v, str):
        return [v] if v else []
    return [x for x in v if x]


def _cuentas_de_operador(operador_email: str, nivel_1: _Filtro = None,
                         nivel_3: _Filtro = None, referido: _Filtro = None,
                         nivel_2: _Filtro = None,
                         nivel_5: _Filtro = None) -> tuple[str, ...]:
    """ids de cuenta (Comitentes activas) del scope. `TODOS` sin filtros → todas.
    Lee SQL clientes.comitentes (Mongo Clientes.Comitentes fue eliminada).

    Cada nivel acepta un valor o varios; dentro de un nivel los valores van con OR
    (`= ANY`) y entre niveles con AND — que es lo que espera un multi-select por
    dimensión. El orden de los parámetros NO se toca: hay callers posicionales.
    """
    conds = ["estado = 'Activa'", "id_cuenta IS NOT NULL"]
    p: dict[str, Any] = {}
    if operador_email != TODOS:
        conds.append("operador_email = %(op)s")
        p["op"] = operador_email
    for campo, clave, valor in (
        ("nivel_1", "n1", nivel_1), ("nivel_2", "n2", nivel_2),
        ("nivel_3", "n3", nivel_3), ("nivel_5", "n5", nivel_5),
        ("referido", "rf", referido),
    ):
        vals = _valores(valor)
        if vals:
            conds.append(f"{campo} = ANY(%({clave})s)")
            p[clave] = vals
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT id_cuenta FROM comitentes WHERE {' AND '.join(conds)}", p)
        return tuple(sorted(str(r[0]) for r in cur.fetchall() if r[0]))


# ── Cobros futuros (acreencias por operador) ─────────────────────────────────
# Tab "Cobros Futuros" de la vista OPERADORES. Cruza las acreencias (cobros
# proyectados por tenencia × calendario contractual, cron jobs.acreencias) con el
# scope del operador (_cuentas_de_operador). El `monto` ya viene en su moneda
# nativa (ARS/USD) — el switch del front elige cuál ver, NO se pesifica (son
# flujos futuros: pesificar con el MEP de hoy distorsiona).
# SQL-NATIVE desde el cutover 2026-06-23: las acreencias salen SIEMPRE de SQL
# operaciones.acreencias (CashFlow.Acreencias Mongo dropeada).


def _acreencias_docs(ids: list[str] | None, desde: str | None = None,
                     hasta: str | None = None) -> list[dict[str, Any]]:
    """Docs de acreencias del scope (ids=None → todas), shape uniforme {fecha_pago,
    cliente, id_cuenta, moneda, monto, ...} desde SQL operaciones.acreencias.
    `desde`/`hasta` (ISO) acotan por fecha_pago."""
    from api.services import cashflow_sql as _cf_sql
    return _cf_sql.acreencias_docs(ids, desde=desde, hasta=hasta)


def _acreencias_docs_cliente(id_cuenta: str) -> list[dict[str, Any]]:
    """Docs de un cliente, asc (fecha_pago, -monto) — para el detalle, desde SQL."""
    from api.services import cashflow_sql as _cf_sql
    return _cf_sql.acreencias_docs(id_cuenta=id_cuenta, order_cliente=True)


@cached(ttl=300)
def cobros_futuros(*, operador: str, nivel_1: str | None = None,
                   nivel_3: str | None = None, referido: str | None = None,
                   nivel_2: str | None = None,
                   desde: str | None = None, hasta: str | None = None) -> dict[str, Any]:
    """Serie diaria (acumulable en el front) + totales por cliente de los cobros
    futuros del scope. El detalle por título va en cobros_futuros_cliente —
    interactivo, no se manda todo el libro en esta llamada. `desde`/`hasta` (ISO)
    acotan por fecha de cobro (mismo filtro por fecha que back-office acreencias)."""
    es_todos = (operador == TODOS and not nivel_1 and not nivel_2 and not nivel_3
                and not referido)
    ids = () if es_todos else _cuentas_de_operador(operador, nivel_1, nivel_3, referido, nivel_2)
    if not es_todos and not ids:
        return {"operador": operador, "serie": [], "clientes": [],
                "total_ars": 0.0, "total_usd": 0.0}

    # Docs de acreencias del scope desde SQL operaciones.acreencias. La AGREGACIÓN
    # (serie por moneda + totales por cliente) se hace acá sobre el shape uniforme.
    docs = _acreencias_docs(None if es_todos else list(ids), desde=desde, hasta=hasta)

    # Serie diaria por moneda (para el gráfico acumulado del scope).
    serie_map: dict[str, dict[str, Any]] = {}
    # Totales por cliente (tabla izquierda). `cliente` = el del PRIMER doc de la cuenta
    # (equivale al $first del aggregate Mongo).
    cli_map: dict[str, dict[str, Any]] = {}
    for d in docs:
        f = d.get("fecha_pago")
        monto = float(d.get("monto") or 0.0)
        es_usd = d.get("moneda") == "USD"
        e = serie_map.setdefault(f, {"fecha": f, "ars": 0.0, "usd": 0.0})
        e["usd" if es_usd else "ars"] += monto
        idc = str(d.get("id_cuenta"))
        c = cli_map.setdefault(idc, {"id_cuenta": idc, "cliente": d.get("cliente"),
                                     "ars": 0.0, "usd": 0.0})
        c["usd" if es_usd else "ars"] += monto
    serie = [serie_map[f] for f in sorted(serie_map)]

    clientes = [
        {"id_cuenta": c["id_cuenta"], "cliente": c["cliente"],
         "total_ars": round(c["ars"], 2), "total_usd": round(c["usd"], 2)}
        for c in cli_map.values()
    ]
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
    docs = _acreencias_docs_cliente(str(id_cuenta))

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


# ── ARANCEL desde SQL operaciones.operaciones (fuente completa) ───────────────
# El arancel incluye futuros y demás tipos que NegocioMovimientos dejaba afuera.
# Filtra etapa="solicitud" (FCI pedido; la liquidación ya cuenta). NO filtra
# es_cierre: el arancel de caución vive SOLO en el cierre y el filtro `arancel>0`
# ya descarta los cierres no-caución (que no tienen fee).


def _aranceles_por_cuenta(
    ids: tuple[str, ...] | list[str] | None, fecha_mes: str,
) -> dict[str, dict[str, float]]:
    """{cuenta: {ar_total, ar_mes}} desde SQL operaciones.operaciones. `ids=None`
    → todas las cuentas. etapa != solicitud (NULL cuenta como no-solicitud)."""
    conds = ["arancel > 0", "etapa IS DISTINCT FROM 'solicitud'", "anulado_en IS NULL"]
    p: dict[str, Any] = {"mes": fecha_mes}
    if ids is not None:
        conds.append("id_cuenta = ANY(%(ids)s)")
        p["ids"] = list(ids)
    out: dict[str, dict[str, float]] = {}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT id_cuenta, SUM(arancel) AS ar_total, "
            f"SUM(CASE WHEN concertacion >= %(mes)s THEN arancel ELSE 0 END) AS ar_mes "
            f"FROM operaciones WHERE {' AND '.join(conds)} GROUP BY id_cuenta", p)
        for idc, ar_total, ar_mes in cur.fetchall():
            if idc:
                out[str(idc)] = {"ar_total": float(ar_total or 0.0),
                                 "ar_mes": float(ar_mes or 0.0)}
    return out


# ── REFERIDOS ────────────────────────────────────────────────────────────────


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
            f"AND anulado_en IS NULL "
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
