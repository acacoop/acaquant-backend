"""api/services/operaciones_sql.py — vista OPERACIONES leyendo de Postgres (Supabase).

Servicio PURO (sin FastAPI). Sirve los endpoints `/api/operaciones/ops/*`
(`api/routers/operaciones.py`) leyendo Postgres. Agrega EN VIVO (sin rollup):
Postgres agrega 490k filas con índice en milisegundos. Ver docs/SQL.md.

Reglas de traducción de campos blindadas (verificadas contra los datos reales):
  * etapa: `IS DISTINCT FROM 'solicitud'` (98% de los docs tienen etapa NULL; `<> 'solicitud'`
    NO matchea NULL en SQL → perdería todo). Equivale al `$ne` de Mongo.
  * es_cierre: `COALESCE(es_cierre, false) = false`. NULL = NO es cierre (ej. FCI
    bilateral, que lo escribe jobs/fci_bilateral sin setear el campo) → se INCLUYE.
    El viejo `= false` mimicaba Mongo `{es_cierre:false}` y perdía todos los NULL,
    ocultando las operaciones bilaterales de la vista (fix 2026-07-08).
  * fechas: concertacion es `date` → se formatea a 'YYYY-MM-DD' a la salida.
  * Decimal→float, $ifNull→COALESCE, $abs→ABS, substr 0-based→to_char.

El denominador del share de /ops/agro sale de `mercado.volumen_mercado_agro` (SQL,
carga manual) vía `cashflow_sql.volumen_mercado_agro`.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from api.cache import cached
from api.services._sql import _q
from core.postgres import get_pool

_SERIE_VENTANA_DIAS = 550  # ~18 meses (igual que operaciones.py)
# Valor especial del filtro de cartera: los títulos que NO están en el
# catálogo de assets o no tienen cartera cargada (~1% del volumen, medido).
_CARTERA_SIN = "(SIN CARTERA)"
_TON = (
    "ABS(COALESCE(cantidad, 0)) * "
    "(CASE WHEN COALESCE(instrumento, '') ~* 'MIN' THEN 10 ELSE 100 END)"
)


# ── infra ────────────────────────────────────────────────────────────────────
def _f(x) -> float:
    return float(x or 0)


def _iso(d):
    return d.isoformat() if d is not None else None


def _iso_naive(d):
    """datetime tz-aware (timestamptz de PG) → ISO naive en UTC, igual que Mongo
    (que guarda el datetime sin tz). Mismo instante, sin el sufijo '+00:00'."""
    if d is None:
        return None
    if d.tzinfo is not None:
        d = d.astimezone(UTC).replace(tzinfo=None)
    return d.isoformat()


# Valor de `moneda` que pide el VOLUMEN DOLARIZADO (todo → USD con el mep del
# boleto). Opción EXTRA del selector, además de ARS/USD nativos. Solo SQL (el
# rollup de Mongo no trae el bruto en USD).
_DOLARIZAR = "USD_DOL"

# TC de la CONCERTACIÓN cuando el boleto NO trae su snapshot `mep` (mismo criterio
# que core.dolar_sql.mep_para_fecha: último mep > 0 con timestamp <= fin-de-día ART,
# arrastrando el último día con dato). Existe porque no todo boleto se estampa: los
# FCI bilateral que escribe jobs/fci_bilateral nunca tuvieron `mep`, y con el viejo
# `ELSE 0` su volumen ARS DESAPARECÍA del dolarizado en vez de convertirse (bug
# 2026-08-10: FCI Bilateral mostraba ~0 en USD con volumen ARS real).
# Va DENTRO de una rama de CASE → Postgres solo lo evalúa en las filas sin `mep`.
_MEP_DIA = (
    "(SELECT d.mep FROM valuaciones.dolar d "
    " WHERE d.mep IS NOT NULL AND d.mep > 0 "
    "   AND d.timestamp < ((operaciones.concertacion + 1)::timestamp "
    "                      AT TIME ZONE 'America/Argentina/Buenos_Aires') "
    " ORDER BY d.timestamp DESC LIMIT 1)"
)
# TC de la fila: snapshot del boleto y, si falta, el del día. NULL si no hay ninguno
# (fecha anterior al inicio del feed) → las divisiones dan NULL y SUM las ignora.
_MEP_ROW = f"NULLIF(COALESCE(NULLIF(mep, 0), {_MEP_DIA}), 0)"


# ── FLUJO CONTRAPARTES — resumen agregado (vista /operaciones → CONTRAPARTES) ─
@cached(ttl=300)
def flujo_operaciones(contraparte: str | None = None, moneda: str | None = None,
                      segmento: str | None = None, desde: str | None = None,
                      hasta: str | None = None) -> list[dict]:
    """Flujo de contrapartes — operaciones individuales (drill-down de la vista
    CONTRAPARTES; el agregado por día es `flujo_resumen`). Match por id_cuenta
    contra clientes.contrapartes. Excluye Futuros/Opciones y las Caución
    COLOCADORA (vienen en pares y duplican la vista; la tomadora se mantiene).

    Todos los filtros bajan a SQL: `contraparte` reduce la lista de ids ANTES
    de la query y `segmento` filtra por split_part(tipo_operacion) en el WHERE
    — antes se traía TODO el scope y se descartaba en Python, y como el cache
    es por combinación de parámetros, cada filtro re-ejecutaba el fetch
    completo (el filtro multiplicaba el costo en vez de reducirlo)."""
    # cuenta (id) → {contraparte, grupo}. La cuenta es la CLAVE.
    cp_rows = _q("SELECT id_cuenta, contraparte, segmento FROM contrapartes "
                 "WHERE id_cuenta IS NOT NULL AND id_cuenta <> ''")
    cp_map = {
        str(r["id_cuenta"]).strip(): {"contraparte": r["contraparte"] or "",
                                      "grupo": r["segmento"] or ""}
        for r in cp_rows if r["id_cuenta"] not in (None, "")
    }
    ids = list(cp_map)
    if contraparte:
        ids = [idc for idc, cp in cp_map.items() if cp["contraparte"] == contraparte]
    if not ids:
        return []

    conds = ["id_cuenta = ANY(%(ids)s)", "anulado_en IS NULL",
             "(tipo_operacion IS NULL OR tipo_operacion !~* 'Futuros|Opciones|colocadora')"]
    p: dict = {"ids": ids}
    if moneda:
        conds.append("moneda = %(moneda)s")
        p["moneda"] = moneda
    if segmento:
        conds.append("split_part(COALESCE(tipo_operacion, ''), ' ', 1) = %(seg)s")
        p["seg"] = segmento
    if desde:
        conds.append("concertacion >= %(desde)s")
        p["desde"] = desde
    if hasta:
        conds.append("concertacion <= %(hasta)s")
        p["hasta"] = hasta

    rows = _q(
        f"SELECT boleto, concertacion::text AS concertacion, tipo_operacion, "
        f"id_cuenta, denominacion, instrumento, bruto, moneda "
        f"FROM operaciones WHERE {' AND '.join(conds)} ORDER BY concertacion", p)

    out = []
    for d in rows:
        cuenta = str(d.get("id_cuenta") or "").strip()
        cp = cp_map.get(cuenta, {})
        tipo = d.get("tipo_operacion") or ""
        out.append({
            "boleto":        d.get("boleto"),
            "concertacion":  d.get("concertacion"),
            "tipoOperacion": tipo,
            "cuenta":        cuenta,
            "denominacion":  d.get("denominacion"),
            "unidad":        d.get("instrumento"),
            "bruto":         float(d["bruto"]) if d.get("bruto") is not None else None,
            "segmento":      tipo.split()[0] if tipo else "",
            "contraparte":   cp.get("contraparte"),
            "moneda":        d.get("moneda"),
        })
    return out


def flujo_resumen(desde: str | None = None, hasta: str | None = None) -> dict:
    """Agregado por (día, contraparte, moneda) del flujo de contrapartes.

    Reemplaza el patrón "mandar 2 años de operaciones crudas al browser y agrupar
    en React": Postgres agrega y viaja UNA fila por (día, contraparte, moneda)
    con el grupo ya joineado. El drill-down de un día puntual sigue usando
    /flujo?desde=dia&hasta=dia (operaciones individuales).

    Mismo universo que listar_flujo (router operaciones): solo cuentas de
    clientes.contrapartes, excluye Futuros/Opciones/caución colocadora."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id_cuenta, contraparte, segmento FROM contrapartes "
                    "WHERE id_cuenta IS NOT NULL AND id_cuenta <> ''")
        cp_map = {str(idc).strip(): (cp or "", seg or "")
                  for idc, cp, seg in cur.fetchall() if idc not in (None, "")}
    conds = ["id_cuenta = ANY(%(ids)s)", "anulado_en IS NULL",
             "(tipo_operacion IS NULL OR tipo_operacion !~* 'Futuros|Opciones|colocadora')"]
    p: dict = {"ids": list(cp_map)}
    if desde:
        conds.append("concertacion >= %(desde)s")
        p["desde"] = desde
    if hasta:
        conds.append("concertacion <= %(hasta)s")
        p["hasta"] = hasta
    rows = _q(f"SELECT concertacion::text AS dia, id_cuenta, moneda, "
              f"COALESCE(SUM(bruto), 0) AS bruto, COUNT(*) AS n "
              f"FROM operaciones WHERE {' AND '.join(conds)} "
              f"GROUP BY concertacion, id_cuenta, moneda", p)
    # Re-agrupar por nombre de contraparte (varias cuentas pueden compartirla).
    agg: dict[tuple, dict] = {}
    for r in rows:
        cpn, grupo = cp_map.get(str(r["id_cuenta"]).strip(), ("", ""))
        e = agg.setdefault((r["dia"], cpn, r["moneda"]), {
            "dia": r["dia"], "contraparte": cpn, "grupo": grupo,
            "moneda": r["moneda"], "bruto": 0.0, "n": 0})
        e["bruto"] += _f(r["bruto"])
        e["n"] += int(r["n"])
    filas = sorted(agg.values(), key=lambda x: (x["dia"], x["contraparte"] or ""))
    for f in filas:
        f["bruto"] = round(f["bruto"], 2)
    return {
        "filas": filas,
        "grupos": sorted({seg for _, seg in cp_map.values() if seg}),
        "monedas": sorted({f["moneda"] for f in filas if f["moneda"]}),
    }


# ── WHERE compartido (equivale a _ops_match / _arancel_match) ─────────────────
def _multi(v: str | None) -> list[str]:
    """'A,B,C' → ['A','B','C'] — los filtros de la vista OPERACIONES aceptan
    SELECCIÓN MÚLTIPLE y la mandan separada por comas. Un valor solo sigue
    funcionando igual (lista de 1), así que es compatible hacia atrás.
    Descarta vacíos y los comodines 'todos'/'todas'.

    Solo se usa en campos tipo enum (segmento, nivel_3, cartera, mercado, operador):
    valores cortos y sin comas. NO aplicar a denominación/cuenta, que sí las tienen.
    """
    if not v:
        return []
    partes = [x.strip() for x in str(v).split(",") if x.strip()]
    return [x for x in partes if x.lower() not in ("todos", "todas")]


def _ops_where(
    moneda: str | None = None, mercado: str | None = None, operacion: str | None = None,
    denominacion: str | None = None, cuenta: str | None = None, segmento: str | None = None,
    scope: tuple[str, ...] | None = None, *, arancel: bool = False, operador: str | None = None,
    excluir: tuple[str, ...] | None = None, nivel_3: str | None = None,
    aca_valores: str | None = None, cartera: str | None = None,
) -> tuple[str, dict]:
    """Devuelve (where_sql, params). `arancel=True` → sin filtro de moneda, incluye los
    cierres con arancel (caución), igual que _arancel_match."""
    conds: list[str] = ["anulado_en IS NULL"]  # boletos que Aunesa anuló: fuera de toda vista
    p: dict = {}
    # es_cierre NULL (ej. FCI bilateral) = NO es cierre → COALESCE para no perderlos
    # (NULL = false en SQL da NULL, no TRUE, y descartaba esas filas). Ver docstring.
    if arancel:
        conds.append("(COALESCE(es_cierre, false) = false OR (es_cierre = true AND arancel <> 0))")
    elif moneda == _DOLARIZAR:
        # Dolarizado: entran ARS y USD (sin filtro de moneda); cada boleto se
        # convierte a USD con su mep en la suma del volumen (ver _bruto_expr).
        conds.append("COALESCE(es_cierre, false) = false")
    else:
        conds.append("moneda = %(moneda)s")
        conds.append("COALESCE(es_cierre, false) = false")
        p["moneda"] = moneda
    # FCI bilateral: contar UNA vez — suscripción por su SOLICITUD (día del pedido),
    # rescate por su LIQUIDACIÓN. Excluye suscripción+liquidación y rescate+solicitud.
    # Equivale al $nor de _ops_match. COALESCE evita que los NULL propaguen a NULL.
    conds.append(
        "NOT ((COALESCE(operacion,'') = 'Suscripción' AND COALESCE(etapa,'') = 'liquidacion') "
        "OR (COALESCE(operacion,'') = 'Rescate' AND COALESCE(etapa,'') = 'solicitud'))")
    if (mm := _multi(mercado)):
        conds.append("mercado = ANY(%(mercado)s)")
        p["mercado"] = mm
    if operacion:
        conds.append("operacion = %(operacion)s")
        p["operacion"] = operacion
    if denominacion:
        conds.append("denominacion = %(denominacion)s")
        p["denominacion"] = denominacion
    if cuenta:
        conds.append("id_cuenta = %(cuenta)s")
        p["cuenta"] = cuenta
    if (ss := _multi(segmento)):
        conds.append("segmento = ANY(%(segmento)s)")
        p["segmento"] = ss
    if (n3 := _multi(nivel_3)):
        # nivel_3 congelado en el boleto (columna propia de operaciones.operaciones),
        # NO el vigente del comitente — es el segmento al momento de la operación.
        conds.append("nivel_3 = ANY(%(nivel_3)s)")
        p["nivel_3"] = n3
    # CARTERA del título (pedido user 2026-07-21). El catálogo es
    # portafolio.assets y la clave es `unidad` = `operaciones.instrumento`:
    # MEDIDO con scripts/diag_ops_cartera → 99.0% del volumen y 879
    # instrumentos (ticker: 12.9% / assets.instrumento: 0%). Se usa EXISTS y
    # NO un JOIN: `unidad` es PK así que no duplicaría, pero con EXISTS el
    # filtro no puede alterar jamás las sumas aunque el catálogo cambie.
    # 'SIN' = el ~1% que no está en el catálogo o no tiene cartera cargada.
    # Multi-selección: 'SIN CARTERA' y las carteras reales se combinan con OR
    # (elegir HD + SIN CARTERA trae las dos cosas, no cero filas).
    if (cc := _multi(cartera)):
        sin = [c for c in cc if c.upper() == _CARTERA_SIN]
        reales = [c for c in cc if c.upper() != _CARTERA_SIN]
        ors = []
        if sin:
            ors.append("NOT EXISTS (SELECT 1 FROM portafolio.assets a "
                       "WHERE a.unidad = operaciones.instrumento AND a.cartera IS NOT NULL)")
        if reales:
            ors.append("EXISTS (SELECT 1 FROM portafolio.assets a "
                       "WHERE a.unidad = operaciones.instrumento "
                       "AND a.cartera = ANY(%(cartera)s))")
            p["cartera"] = reales
        conds.append(f"({' OR '.join(ors)})")
    if aca_valores == "solo":
        conds.append("id_cuenta IN (SELECT id_cuenta FROM clientes.aca_valores)")
    elif aca_valores == "sin":
        conds.append("id_cuenta NOT IN (SELECT id_cuenta FROM clientes.aca_valores)")
    if (oo := _multi(operador)):
        conds.append("id_cuenta IN (SELECT id_cuenta FROM comitentes "
                     "WHERE operador_email = ANY(%(operador)s))")
        p["operador"] = oo
    if excluir:
        # Ocultar cuentas elegidas por el usuario. Compara contra la denominación tal
        # como se muestra ('(sin)' para NULL/'') → coincide con lo que llega del front.
        conds.append("COALESCE(NULLIF(denominacion, ''), '(sin)') <> ALL(%(excluir)s)")
        p["excluir"] = list(excluir)
    if scope is not None:
        conds.append("id_cuenta = ANY(%(scope)s)")
        p["scope"] = list(scope)
    return " AND ".join(conds), p


# ── selectores (RAW, sin filtros de negocio — igual que en Mongo) ─────────────
def ops_mercados() -> dict:
    rows = _q("SELECT DISTINCT mercado FROM operaciones "
              "WHERE mercado IS NOT NULL AND mercado <> '' ORDER BY mercado")
    return {"mercados": [r["mercado"] for r in rows]}


def ops_segmentos() -> dict:
    rows = _q("SELECT DISTINCT segmento FROM operaciones "
              "WHERE segmento IS NOT NULL AND segmento <> '' ORDER BY segmento")
    return {"segmentos": [r["segmento"] for r in rows]}


def ops_niveles3() -> dict:
    """Valores distintos de nivel_3 (segmento congelado en el boleto) presentes en
    operaciones.operaciones — catálogo para el filtro nivel_3 de la vista OPERACIONES."""
    rows = _q("SELECT DISTINCT nivel_3 FROM operaciones "
              "WHERE nivel_3 IS NOT NULL AND nivel_3 <> '' ORDER BY nivel_3")
    return {"niveles3": [r["nivel_3"] for r in rows]}


def ops_carteras() -> dict:
    """Carteras (portafolio.assets.cartera) que REALMENTE aparecen en los
    boletos — catálogo del filtro nuevo de Operaciones. Se listan solo las que
    tienen operaciones para no ofrecer filtros que devuelven vacío. Suma
    '(SIN CARTERA)' si hay volumen de títulos fuera del catálogo (~1%)."""
    rows = _q(
        "SELECT DISTINCT a.cartera FROM portafolio.assets a "
        "WHERE a.cartera IS NOT NULL AND a.cartera <> '' "
        "  AND EXISTS (SELECT 1 FROM operaciones o WHERE o.instrumento = a.unidad) "
        "ORDER BY a.cartera")
    carteras = [r["cartera"] for r in rows]
    huerfanos = _q(
        "SELECT 1 AS x FROM operaciones o WHERE o.instrumento IS NOT NULL "
        "  AND NOT EXISTS (SELECT 1 FROM portafolio.assets a "
        "                  WHERE a.unidad = o.instrumento AND a.cartera IS NOT NULL) "
        "LIMIT 1")
    if huerfanos:
        carteras.append(_CARTERA_SIN)
    return {"carteras": carteras}


def ops_fechas() -> dict:
    rows = _q("SELECT concertacion AS fecha, count(*) AS n FROM operaciones "
              "WHERE concertacion IS NOT NULL AND anulado_en IS NULL "
              "GROUP BY concertacion ORDER BY concertacion DESC")
    return {"fechas": [{"fecha": _iso(r["fecha"]), "n": r["n"]} for r in rows]}


def ops_cuentas_list(scope: tuple[str, ...] | None = None) -> dict:
    """Denominaciones (+ id) distintas — fuente del buscador de la vista MOVIMIENTOS.

    APLICA el scope de grupos. Antes lo recibía y lo descartaba (heredado del
    port desde Mongo), así que cualquier usuario con el módulo `operaciones`
    se llevaba el padrón COMPLETO de comitentes con su denominación — los
    nombres de todos los clientes de la mesa. `scope=None` (admin o usuario
    sin grupo) sigue viendo todo; un tuple vacío no devuelve nada, que es la
    semántica de "está en un grupo sin cuentas" (docs/GRUPOS.md).

    `id_cuenta` acá es el id pelado, mismo namespace que `manager.grupos`.
    """
    sql = ("SELECT id_cuenta AS cuenta, max(denominacion) AS denominacion "
           "FROM operaciones WHERE id_cuenta IS NOT NULL AND anulado_en IS NULL")
    params: dict = {}
    if scope is not None:
        sql += " AND id_cuenta = ANY(%(scope)s)"
        params["scope"] = list(scope)
    sql += " GROUP BY id_cuenta ORDER BY denominacion"
    rows = _q(sql, params) if params else _q(sql)
    return {"cuentas": [{"cuenta": r["cuenta"], "denominacion": r["denominacion"]} for r in rows]}


def ops_meta(fecha: str) -> dict:
    # RAW (sin _ops_match): cuenta TODOS los boletos del día, igual que Mongo.
    rows = _q(
        "SELECT count(*) AS n, max(ingestado_en) AS ultima, "
        "count(DISTINCT mercado) FILTER (WHERE mercado IS NOT NULL AND mercado <> '') AS ncat "
        "FROM operaciones WHERE concertacion = %(fecha)s AND anulado_en IS NULL",
        {"fecha": fecha},
    )
    r = rows[0]
    return {"meta": {
        "fecha": fecha,
        "n_boletos": r["n"] or 0,
        "n_categorias": r["ncat"] or 0,
        "ultima_ingesta": _iso_naive(r["ultima"]),
    }}


# ── serie / resumen / boletos (VOLUMEN, _ops_match) ──────────────────────────
def _arancel_expr(moneda: str) -> str:
    """Expr SQL agregada del arancel (ABS). El arancel se guarda SIEMPRE en pesos →
    en USD/USD_DOL se convierte con el mep del boleto (y si falta, con el del día:
    `_MEP_ROW`). En ARS queda en pesos. Mismo criterio que _bruto_expr."""
    if moneda in (_DOLARIZAR, "USD"):
        return ("SUM(CASE WHEN COALESCE(arancel, 0) <> 0 "
                f"THEN ABS(arancel) / {_MEP_ROW} ELSE 0 END)")
    return "SUM(ABS(COALESCE(arancel, 0)))"


def _bruto_expr(moneda: str) -> str:
    """Expr SQL agregada del volumen. 'USD_DOL' → suma cada boleto convertido a USD
    con su mep (USD directo; ARS / mep, y si el boleto no trae mep, con el del día →
    `_MEP_ROW`). ARS/USD nativos → bruto tal cual (el filtro de moneda lo pone
    _ops_where)."""
    if moneda == _DOLARIZAR:
        return ("SUM(CASE WHEN moneda = 'USD' THEN COALESCE(bruto, 0) "
                "WHEN moneda = 'ARS' AND COALESCE(bruto, 0) <> 0 "
                f"THEN bruto / {_MEP_ROW} "
                "ELSE 0 END)")
    return "SUM(COALESCE(bruto, 0))"


def _peso_bruto_row(moneda: str) -> str:
    """Volumen POR FILA (el mismo de `_bruto_expr` pero SIN el SUM externo) — es el
    peso para ponderar la tasa. Así la tasa ponderada usa exactamente la misma medida
    de volumen que la columna BRUTO que se muestra al lado."""
    if moneda == _DOLARIZAR:
        return ("CASE WHEN moneda = 'USD' THEN COALESCE(bruto, 0) "
                "WHEN moneda = 'ARS' AND COALESCE(bruto, 0) <> 0 "
                f"THEN bruto / {_MEP_ROW} "
                "ELSE 0 END")
    return "COALESCE(bruto, 0)"


def _tasa_pond_expr(moneda: str) -> str:
    """Tasa PONDERADA por volumen (bruto) de un grupo. En PORCENTAJE (6 = 6%), soporta
    negativas. Cálculo hecho acá (no en el front): Σ(tasa·bruto) / Σ(bruto).

    - Ignora las filas con `tasa` NULL (FILTER): no aportan ni al numerador ni al
      peso, así una fila sin tasa no diluye el promedio hacia cero.
    - NULL si NINGUNA fila del grupo tiene tasa (ej. mercado ≠ MAV) → la columna
      queda vacía, que es justo lo pedido.
    - NULLIF(...,0) evita división por cero si el volumen ponderable es 0."""
    w = _peso_bruto_row(moneda)
    return (f"SUM(tasa * ({w})) FILTER (WHERE tasa IS NOT NULL) "
            f"/ NULLIF(SUM(({w})) FILTER (WHERE tasa IS NOT NULL), 0)")


def ops_serie(
    moneda: str = "ARS", mercado: str | None = None, operacion: str | None = None,
    denominacion: str | None = None, cuenta: str | None = None, segmento: str | None = None,
    scope: tuple[str, ...] | None = None, operador: str | None = None,
    excluir: tuple[str, ...] | None = None, nivel_3: str | None = None,
    aca_valores: str | None = None, cartera: str | None = None,
) -> dict:
    # HOT/COLD (decisión user 2026-08-04): el caso SIN filtros — el default de la
    # vista, que agregaba TODA la historia en cada carga fría y crece con la
    # tabla — lee los días cerrados del agregado (jobs/ops_agregado, drift-proof
    # por recómputo de días sucios) y solo lo posterior al agregado EN VIVO.
    # Cualquier filtro → 100% en vivo, igual que siempre (index-bounded).
    sin_filtros = (
        not any([mercado and mercado.lower() != "todos", operacion, denominacion,
                 cuenta, segmento and segmento.lower() != "todos",
                 nivel_3 and nivel_3.lower() != "todos",
                 cartera and cartera.lower() not in ("todas", ""),
                 operador, excluir, aca_valores in ("solo", "sin")])
        and scope is None
        and moneda in ("ARS", "USD", _DOLARIZAR)
    )
    serie_fria: list[dict] = []
    corte = None
    if sin_filtros:
        try:
            frias = _q(
                "SELECT fecha, bruto FROM operaciones.ops_agregado_diario "
                "WHERE moneda_calc = %(m)s ORDER BY fecha",
                {"m": moneda},
            )
            if frias:
                corte = frias[-1]["fecha"]
                serie_fria = [{"fecha": _iso(r["fecha"]), "bruto": round(_f(r["bruto"]), 2)}
                              for r in frias]
        except Exception:
            # tabla ausente / job nunca corrido → fallback silencioso al camino vivo
            serie_fria, corte = [], None

    where, p = _ops_where(moneda, mercado, operacion, denominacion, cuenta, segmento, scope,
                          operador=operador, excluir=excluir, nivel_3=nivel_3, cartera=cartera,
                          aca_valores=aca_valores)
    if corte is not None:
        where += " AND concertacion > %(corte_agg)s"
        p["corte_agg"] = corte
    rows = _q(
        f"SELECT concertacion AS fecha, {_bruto_expr(moneda)} AS bruto "
        f"FROM operaciones WHERE {where} GROUP BY concertacion ORDER BY concertacion",
        p,
    )
    serie = serie_fria + [
        {"fecha": _iso(r["fecha"]), "bruto": round(_f(r["bruto"]), 2)} for r in rows
    ]
    return {"moneda": moneda, "mercado": mercado, "serie": serie}


def ops_resumen(
    moneda: str = "ARS", mercado: str | None = None, desde: str = "", hasta: str = "",
    operacion: str | None = None, denominacion: str | None = None, cuenta: str | None = None,
    segmento: str | None = None, scope: tuple[str, ...] | None = None,
    instrumento: str | None = None, operador: str | None = None,
    excluir: tuple[str, ...] | None = None, nivel_3: str | None = None,
    aca_valores: str | None = None, cartera: str | None = None,
) -> dict:
    base, p = _ops_where(moneda, mercado, cuenta=cuenta, segmento=segmento, scope=scope,
                         operador=operador, excluir=excluir, nivel_3=nivel_3,
                         aca_valores=aca_valores, cartera=cartera)
    p.update({"desde": desde, "hasta": hasta})
    base = f"{base} AND concertacion >= %(desde)s AND concertacion <= %(hasta)s"
    bexpr = _bruto_expr(moneda)

    def _xf(*, denom=False, op=False, instr=False) -> tuple[str, dict]:
        """WHERE base + las selecciones cruzadas pedidas (cada tabla aplica las de las otras)."""
        w, pp = base, dict(p)
        if denom and denominacion:
            w += " AND denominacion = %(f_denom)s"; pp["f_denom"] = denominacion
        if op and operacion:
            w += " AND operacion = %(f_op)s"; pp["f_op"] = operacion
        if instr and instrumento:
            w += " AND instrumento = %(f_instr)s"; pp["f_instr"] = instrumento
        return w, pp

    # arancel POR FILA: se suma en la MISMA query que el bruto (mismo WHERE: moneda +
    # es_cierre=false + cross-filters) → es el arancel exacto de las operaciones mostradas.
    # Sigue la MISMA dolarización que el bruto (USD/USD_DOL → /mep). El arancel de caución
    # (cierres, es_cierre=true) NO entra acá — ese vive en la tab ARANCELES dedicada.
    aexpr = _arancel_expr(moneda)
    # TASA ponderada por volumen (bruto). NULL salvo mercado MAV (allí `tasa` está
    # poblada por jobs.ops_tasa_mav). Se calcula server-side para que el front
    # NO tenga que ponderar (evita el cálculo en el front, pedido explícito).
    texpr = _tasa_pond_expr(moneda)

    def _rt(v) -> float | None:
        """Redondea la tasa a 4 decimales preservando None (vacío = sin tasa)."""
        return None if v is None else round(float(v), 4)

    # por_operacion: cruzada por denominacion + instrumento, HAVING bruto<>0.
    w_op, p_op = _xf(denom=True, instr=True)
    por_operacion = [
        {"operacion": r["operacion"] or "(sin)", "bruto": round(_f(r["bruto"]), 2),
         "arancel": round(_f(r["ar"]), 2), "n": r["n"], "tasa_pond": _rt(r["tasa_pond"])}
        for r in _q(f"SELECT operacion, {bexpr} AS bruto, {aexpr} AS ar, count(*) AS n, "
                    f"{texpr} AS tasa_pond FROM operaciones "
                    f"WHERE {w_op} GROUP BY operacion HAVING {bexpr} <> 0 ORDER BY bruto DESC", p_op)
    ]
    # por_denominacion: cruzada por operacion + instrumento.
    w_dn, p_dn = _xf(op=True, instr=True)
    por_denominacion = [
        {"denominacion": r["denominacion"] or "(sin)", "bruto": round(_f(r["bruto"]), 2),
         "arancel": round(_f(r["ar"]), 2), "n": r["n"], "tasa_pond": _rt(r["tasa_pond"])}
        for r in _q(f"SELECT denominacion, {bexpr} AS bruto, {aexpr} AS ar, count(*) AS n, "
                    f"{texpr} AS tasa_pond FROM operaciones "
                    f"WHERE {w_dn} GROUP BY denominacion ORDER BY bruto DESC", p_dn)
    ]
    # por_instrumento (títulos): cruzada por operacion + denominacion, HAVING bruto<>0.
    # Acá `tasa_pond` es la tasa del TÍTULO: un título es un instrumento y sus boletos
    # comparten tasa, así que la ponderada colapsa a ese único valor (si difirieran,
    # es el promedio honesto por volumen). Es la columna del pedido "POR TÍTULO".
    w_in, p_in = _xf(op=True, denom=True)
    por_instrumento = [
        {"instrumento": r["instrumento"] or "(sin)", "bruto": round(_f(r["bruto"]), 2),
         "arancel": round(_f(r["ar"]), 2), "n": r["n"], "tasa_pond": _rt(r["tasa_pond"])}
        for r in _q(f"SELECT instrumento, {bexpr} AS bruto, {aexpr} AS ar, count(*) AS n, "
                    f"{texpr} AS tasa_pond FROM operaciones "
                    f"WHERE {w_in} GROUP BY instrumento HAVING {bexpr} <> 0 ORDER BY bruto DESC", p_in)
    ]
    total = round(sum(r["bruto"] for r in (por_denominacion if denominacion else por_operacion)), 2)
    return {
        "moneda": moneda, "mercado": mercado, "desde": desde, "hasta": hasta,
        "por_operacion": por_operacion, "por_denominacion": por_denominacion,
        "por_instrumento": por_instrumento, "total": total,
    }


# ── Consolidado por dimensión (lo consume el ASISTENTE DE NEGOCIO, P7) ──────
# Whitelist de dimensiones → columna real. El LLM elige la CLAVE, jamás
# compone SQL (la jaula: cualquier valor fuera de acá se rechaza).
_DIMENSIONES_CONSOLIDADO: dict[str, str] = {
    "mercado": "mercado",
    "operacion": "operacion",
    "segmento": "segmento",     # nivel 1 congelado en el boleto
    "nivel_3": "nivel_3",       # segmento fino del boleto
    "instrumento": "instrumento",
    # CLIENTE: la dimensión que faltaba. Sin ella el asistente podía filtrar a
    # un cliente pero NUNCA rankear ("los 10 que más operaron") — la forma de
    # pregunta #1 del negocio (auditoría 2026-07-21). OJO: la clave es el
    # NOMBRE del cliente → quien la use tiene que ficharla antes de devolverla.
    "cliente": "denominacion",
    # cartera del TÍTULO (assets.unidad = instrumento): subconsulta correlada,
    # no columna — se resuelve aparte en la query (ver _CLAVE_CARTERA).
    "cartera": None,
    # MES: la serie del negocio ("¿cómo viene mes a mes?"), otra pregunta que
    # era incontestable. Se resuelve con to_char, no es columna.
    "mes": None,
}
_CLAVE_MES = "to_char(concertacion, 'YYYY-MM')"


_CLAVE_CARTERA = (
    "COALESCE((SELECT a.cartera FROM portafolio.assets a "
    " WHERE a.unidad = operaciones.instrumento), '(SIN CARTERA)')"
)


def ops_consolidado(
    metrica: str, desde: str, hasta: str, por: str = "mercado",
    moneda: str = "ARS", mercado: str | None = None,
    excluir_segmento: str | None = None, operador_sel: str | None = None,
    denominacion: str | None = None, cartera_filtro: str | None = None,
    top: int = 25,
) -> dict:
    """Consolidado de VOLUMEN ('bruto') o ARANCELES ('arancel') agrupado por
    una dimensión, en [desde, hasta]. MISMAS reglas que la vista Operaciones
    (via _ops_where): volumen excluye cierres; el arancel INCLUYE los cierres
    con arancel (caución) y va SIEMPRE en pesos (ABS). `excluir_segmento`
    saca un segmento nivel 1 (ej. consolidado sin agro). `por='operador'`
    agrupa por el operador del comitente (join, mismo criterio que la vista
    Aranceles); `operador_sel` filtra a las cuentas de UN operador;
    `denominacion` filtra a UNA cuenta de cliente (es lo que responde
    "¿cuánto operó tal cliente?" — que NO es su patrimonio)."""
    por_operador = por == "operador"
    por_cartera = por == "cartera"
    por_mes = por == "mes"
    col = _DIMENSIONES_CONSOLIDADO.get(por)
    if col is None and not por_operador and not por_cartera and not por_mes:
        return {"error": f"dimension invalida: {por!r} (validas: "
                         f"{sorted([*_DIMENSIONES_CONSOLIDADO, 'operador'])})"}
    if metrica == "arancel":
        where, p = _ops_where(mercado=mercado, arancel=True, cartera=cartera_filtro)
        # el arancel se guarda en ARS; para USD se convierte con el mep DE CADA
        # BOLETO (mismo criterio que la vista) — no con una cotización de hoy
        expr = _arancel_expr(moneda)
    else:
        where, p = _ops_where(moneda=moneda, mercado=mercado, cartera=cartera_filtro)
        expr = _bruto_expr(moneda)
    p.update({"desde": desde, "hasta": hasta})
    where = f"{where} AND concertacion >= %(desde)s AND concertacion <= %(hasta)s"
    if excluir_segmento:
        where += " AND COALESCE(segmento, '') <> %(excl_seg)s"
        p["excl_seg"] = excluir_segmento
    if denominacion:
        where += " AND denominacion = %(f_den)s"
        p["f_den"] = denominacion
    if operador_sel:
        frag, fp = _op_pred(operador_sel)
        # calificado: con el join de por='operador', `id_cuenta` a secas sería
        # ambiguo (operaciones y comitentes lo tienen)
        frag = frag.replace("id_cuenta ", "operaciones.id_cuenta ", 1)
        where += f" AND {frag}"
        p.update(fp)
    if por_operador:
        # mismo criterio que la vista Aranceles (dim=operador): el operador
        # VIGENTE del comitente, '(sin operador)' para cuentas huérfanas
        clave_expr = "COALESCE(o.nombre, o.email, '(sin operador)')"
        from_sql = ("operaciones LEFT JOIN comitentes c ON c.id_cuenta = operaciones.id_cuenta "
                    "LEFT JOIN operadores o ON o.email = c.operador_email")
    elif por_cartera:
        clave_expr = _CLAVE_CARTERA
        from_sql = "operaciones"
    elif por_mes:
        clave_expr = _CLAVE_MES
        from_sql = "operaciones"
    else:
        clave_expr = f"COALESCE(NULLIF({col}, ''), '(sin)')"
        from_sql = "operaciones"
    rows = _q(
        f"SELECT {clave_expr} AS clave, {expr} AS valor, "
        f"count(*) AS n FROM {from_sql} WHERE {where} "
        # el mes se ordena cronológicamente (es una serie, no un ranking)
        f"GROUP BY 1 HAVING {expr} <> 0 "
        f"ORDER BY {'clave' if por_mes else 'valor DESC'} LIMIT %(top)s",
        {**p, "top": max(1, min(int(top), 100))},
    )
    return {
        "metrica": metrica, "por": por, "desde": desde, "hasta": hasta,
        "moneda": "ARS" if metrica == "arancel" else moneda,
        "filas": [{"clave": r["clave"], "valor": round(_f(r["valor"]), 2), "n": r["n"]}
                  for r in rows],
        "total": round(sum(_f(r["valor"]) for r in rows), 2),
    }


# ── ARANCELES (_arancel_match; el arancel se GUARDA siempre en ARS) ──────────
# La vista puede pedirlo en USD: se convierte con el `mep` snapshot DE CADA BOLETO
# vía `_arancel_expr`. Es la misma expresión agregada, así que sale en la MISMA
# pasada — sin queries ni joins extra (`mep` ya es columna de `operaciones`).


@cached(ttl=300)
def _operador_map() -> dict[str, str]:
    """Mapa id_cuenta→operador. Dato casi estático (alta/edición de comitentes):
    sin cache se escaneaba comitentes⋈operadores en CADA request de aranceles."""
    rows = _q("SELECT c.id_cuenta, COALESCE(o.nombre, o.email, '(sin operador)') AS op "
              "FROM comitentes c LEFT JOIN operadores o ON o.email = c.operador_email")
    return {str(r["id_cuenta"]): r["op"] for r in rows}


def _op_pred(sel: str) -> tuple[str, dict]:
    """Predicado WHERE para filtrar operaciones por operador == sel (dim=operador)."""
    if sel == "(sin operador)":
        return ("id_cuenta NOT IN "
                "(SELECT id_cuenta FROM comitentes WHERE operador_email IS NOT NULL)", {})
    return ("id_cuenta IN (SELECT c.id_cuenta FROM comitentes c "
            "LEFT JOIN operadores o ON o.email = c.operador_email "
            "WHERE COALESCE(o.nombre, o.email) = %(sel_op)s)", {"sel_op": sel})


# Dimensión de la tabla izquierda de ARANCELES → columna de `operaciones`.
# `operador` queda afuera a propósito: no es una columna, se resuelve por subquery
# a comitentes (`_op_pred`). Lo que no esté acá cae a nivel_3 (el default).
_ARANCEL_DIM_COL = {"nivel3": "nivel_3", "operacion": "operacion", "mercado": "mercado"}


def ops_aranceles(
    moneda: str = "ARS", desde: str = "", hasta: str = "", agg: str = "MENSUAL",
    cuenta: str | None = None, instrumento: str | None = None, sel_dim: str | None = None,
    segmento: str | None = None, dim: str = "nivel3", serie_full: bool = False,
    scope: tuple[str, ...] | None = None, operador: str | None = None,
) -> dict:
    fmt = "YYYY-MM" if agg.upper() == "MENSUAL" else "YYYY-MM-DD"
    base, bp = _ops_where(segmento=segmento, scope=scope, arancel=True)
    # Filtro madre por operador: scopea TODO (serie + tablas) a las cuentas de ese
    # operador (subquery a comitentes). Se mete en `base` → aplica uniforme.
    if operador:
        base = (f"{base} AND id_cuenta IN "
                f"(SELECT id_cuenta FROM comitentes WHERE operador_email = %(f_op)s)")
        bp["f_op"] = operador

    # SERIE (histórica, ventana ~18m salvo serie_full). En vivo, sin rollup.
    sp = dict(bp)
    serie_where = base
    if not serie_full:
        sp["cutoff"] = (datetime.now(UTC) - timedelta(hours=3)
                        - timedelta(days=_SERIE_VENTANA_DIAS)).date().isoformat()
        serie_where = f"{base} AND concertacion >= %(cutoff)s"
    aexpr = _arancel_expr(moneda)   # ARS tal cual; USD → arancel/mep por boleto
    serie = [
        {"periodo": r["periodo"], "arancel": round(_f(r["ar"]), 2)}
        for r in _q(
            f"SELECT to_char(concertacion, %(fmt)s) AS periodo, {aexpr} AS ar "
            f"FROM operaciones WHERE {serie_where} GROUP BY periodo ORDER BY periodo",
            {**sp, "fmt": fmt},
        )
    ]

    # TABLAS acotadas a [desde,hasta]. Cross-filter 3-way: cada tabla aplica las
    # selecciones de las OTRAS dos.
    tp = {**bp, "desde": desde, "hasta": hasta}
    date_w = f"{base} AND concertacion >= %(desde)s AND concertacion <= %(hasta)s"
    m_cuenta = ("denominacion = %(f_cuenta)s", {"f_cuenta": cuenta}) if cuenta else (None, {})
    m_instr = ("instrumento = %(f_instr)s", {"f_instr": instrumento}) if instrumento else (None, {})
    if sel_dim and dim == "operador":
        m_dim = _op_pred(sel_dim)
    elif sel_dim and dim in _ARANCEL_DIM_COL:
        m_dim = (f"{_ARANCEL_DIM_COL[dim]} = %(f_dim)s", {"f_dim": sel_dim})
    elif sel_dim:
        m_dim = ("nivel_3 = %(f_dim)s", {"f_dim": sel_dim})
    else:
        m_dim = (None, {})

    def _tabla(group_expr: str, key: str, *subs: tuple[str | None, dict]) -> list[dict]:
        conds = [date_w]
        p = dict(tp)
        for frag, fp in subs:
            if frag:
                conds.append(frag)
                p.update(fp)
        where = " AND ".join(conds)
        return [
            {key: r[key], "arancel": round(_f(r["ar"]), 2), "n": r["n"]}
            for r in _q(
                f"SELECT COALESCE({group_expr}, '(sin)') AS {key}, {aexpr} AS ar, "
                f"count(*) AS n FROM operaciones WHERE {where} "
                f"GROUP BY {group_expr} HAVING {aexpr} > 0 ORDER BY ar DESC", p,
            )
        ]

    # IZQUIERDA (por_dim): filtrada por cuenta + instrumento (no por sí misma).
    if dim == "operador":
        det = _operador_map()
        conds = [date_w]
        p = dict(tp)
        for frag, fp in (m_cuenta, m_instr):
            if frag:
                conds.append(frag)
                p.update(fp)
        acc: dict[str, dict] = {}
        for r in _q(
            f"SELECT id_cuenta, {aexpr} AS ar, count(*) AS n FROM operaciones "
            f"WHERE {' AND '.join(conds)} GROUP BY id_cuenta", p,
        ):
            op = det.get(str(r["id_cuenta"]), "(sin operador)")
            a = acc.setdefault(op, {"ar": 0.0, "n": 0})
            a["ar"] += _f(r["ar"])
            a["n"] += r["n"]
        por_dim = sorted(
            ({"clave": k, "arancel": round(v["ar"], 2), "n": v["n"]}
             for k, v in acc.items() if v["ar"] > 0),
            key=lambda x: x["arancel"], reverse=True,
        )
    else:
        field = _ARANCEL_DIM_COL.get(dim, "nivel_3")
        por_dim = _tabla(field, "clave", m_cuenta, m_instr)

    por_cuenta = _tabla("denominacion", "denominacion", m_dim, m_instr)
    por_instrumento = _tabla("instrumento", "instrumento", m_dim, m_cuenta)

    return {
        "moneda": moneda, "desde": desde, "hasta": hasta, "agg": agg, "dim": dim,
        "serie": serie, "por_dim": por_dim, "por_cuenta": por_cuenta,
        "por_instrumento": por_instrumento,
        "total": round(sum(r["arancel"] for r in por_dim), 2),
    }


# ── AGRO (toneladas; share contra mercado.volumen_mercado_agro) ──────────────
def _agro_serie(rows: list[dict]) -> list[dict]:
    out: dict[str, dict] = {}
    for r in rows:
        d = out.setdefault(r["p"], {"periodo": r["p"], "SOJA": 0.0, "TRIGO": 0.0, "MAIZ": 0.0})
        d[r["c"]] = round(_f(r["ton"]), 0)
    return [out[p] for p in sorted(out)]


def _agro_base(scope: tuple[str, ...] | None,
               nivel5: str | None) -> tuple[str, dict]:
    """El WHERE común a TODAS las queries de agro: los 3 commodities, sin anular,
    más el scope de cuentas y el filtro de nivel_5.

    Vive en un solo lugar a propósito. Antes estaba escrito inline en `ops_agro`;
    al sacar `_agro_nuestro_mensual` a su propia función quedaba duplicado, y dos
    copias de un filtro son dos copias que se desincronizan — con la particularidad
    de que acá la divergencia no rompería nada: devolvería números distintos entre
    el share y el resto de la vista, sin ningún error.
    """
    base = "commodity IN ('SOJA', 'TRIGO', 'MAIZ') AND anulado_en IS NULL"
    p: dict = {}
    if scope is not None:
        base += " AND id_cuenta = ANY(%(scope)s)"
        p["scope"] = list(scope)
    if nivel5:
        base += " AND id_cuenta IN (SELECT id_cuenta FROM comitentes WHERE nivel_5 = %(nivel5)s)"
        p["nivel5"] = nivel5
    return base, p


@cached(ttl=300)
def _agro_nuestro_mensual(*, scope: tuple[str, ...] | None = None,
                          nivel5: str | None = None) -> dict[str, dict]:
    """Toneladas propias por mes × commodity, histórico COMPLETO — numerador del
    market-share (`serie_share`).

    **Por qué está aparte y cacheado** (medido en el Droplet el 2026-08-11): es la
    única de las ocho queries de `ops_agro` **sin filtro de fecha**, así que agrega
    todo el histórico de agro en cada llamada: **314 ms de los 1103 ms** que suman
    las queries del endpoint, el 29%.

    Y ese trabajo se repetía al pedo. El resultado depende SOLO de `scope` y
    `nivel5` — no de `desde`, `hasta`, `agg`, `commodity`, `cuenta` ni `tipo`, que
    es todo lo que el usuario toca en la vista. Midiendo la tab AGRO se contaron
    **22 llamadas en un minuto** con distintas combinaciones de filtros: las 22
    recalculaban este mismo histórico para obtener exactamente el mismo número.

    El TTL de 300s no puede cambiar lo que se ve: la serie es MENSUAL y su fuente
    (`jobs.operaciones_informes`) escribe una vez por hora. Lo único que podría
    quedar hasta 5 minutos atrás es el mes en curso.

    SOLO FUTUROS: el share se calcula contra el volumen de futuros del mercado
    (las opciones no están en `volumen_mercado_agro`) → se excluye OPCION.
    """
    base, bp = _agro_base(scope, nivel5)
    out: dict[str, dict] = {}
    for r in _q(f"SELECT to_char(concertacion, 'YYYY-MM') AS p, commodity AS c, SUM({_TON}) AS ton "
                f"FROM operaciones WHERE {base} AND tipo_agro IS DISTINCT FROM 'OPCION' "
                f"GROUP BY p, commodity", bp):
        out.setdefault(r["p"], {})[r["c"]] = _f(r["ton"])
    return out


def ops_agro(
    desde: str = "", hasta: str = "", agg: str = "MENSUAL", commodity: str | None = None,
    cuenta: str | None = None, scope: tuple[str, ...] | None = None, nivel5: str | None = None,
    tipo: str | None = None,
) -> dict:
    fmt = "YYYY-MM" if agg.upper() == "MENSUAL" else "YYYY-MM-DD"
    base, bp = _agro_base(scope, nivel5)
    # Filtro FUTURO/OPCION: aplica a las vistas de VOLUMEN (serie/tablas), NO a
    # las vistas "por tipo" (que siempre muestran ambos) ni al share (futuros).
    tipo_up = (tipo or "").upper()
    tipo_clause = ""
    if tipo_up in ("FUTURO", "OPCION"):
        tipo_clause = " AND tipo_agro = %(tipo)s"
        bp["tipo"] = tipo_up
    date_w = f"{base} AND concertacion >= %(desde)s AND concertacion <= %(hasta)s"
    dp = {**bp, "desde": desde, "hasta": hasta}
    vol_w = date_w + tipo_clause  # volumen = date range + filtro tipo

    # serie (global, por periodo×commodity).
    serie = _agro_serie(_q(
        f"SELECT to_char(concertacion, %(fmt)s) AS p, commodity AS c, SUM({_TON}) AS ton "
        f"FROM operaciones WHERE {vol_w} GROUP BY p, commodity", {**dp, "fmt": fmt},
    ))
    # serie_cuenta (solo si hay cuenta elegida).
    serie_cuenta: list[dict] = []
    if cuenta:
        serie_cuenta = _agro_serie(_q(
            f"SELECT to_char(concertacion, %(fmt)s) AS p, commodity AS c, SUM({_TON}) AS ton "
            f"FROM operaciones WHERE {vol_w} AND denominacion = %(cuenta)s "
            f"GROUP BY p, commodity", {**dp, "fmt": fmt, "cuenta": cuenta},
        ))
    # totales por commodity (cross-filter: cuenta → filtra commodities).
    w_comm, p_comm = vol_w, dict(dp)
    if cuenta:
        w_comm += " AND denominacion = %(cuenta)s"
        p_comm["cuenta"] = cuenta
    tot = {"SOJA": 0.0, "TRIGO": 0.0, "MAIZ": 0.0}
    for r in _q(f"SELECT commodity AS c, SUM({_TON}) AS ton FROM operaciones "
                f"WHERE {w_comm} GROUP BY commodity", p_comm):
        tot[r["c"]] = round(_f(r["ton"]), 0)
    # por_cuenta (cross-filter: commodity → filtra cuentas).
    w_cta, p_cta = vol_w, dict(dp)
    if commodity:
        w_cta += " AND commodity = %(commodity)s"
        p_cta["commodity"] = commodity
    por_cuenta = [
        {"denominacion": r["d"] or "(sin)", "toneladas": round(_f(r["ton"]), 0), "n": r["n"]}
        for r in _q(f"SELECT denominacion AS d, SUM({_TON}) AS ton, count(*) AS n "
                    f"FROM operaciones WHERE {w_cta} GROUP BY denominacion ORDER BY ton DESC",
                    p_cta)
    ]
    # por_instrumento (cross-filter: cuenta + commodity).
    w_ins, p_ins = vol_w, dict(dp)
    if cuenta:
        w_ins += " AND denominacion = %(cuenta)s"
        p_ins["cuenta"] = cuenta
    if commodity:
        w_ins += " AND commodity = %(commodity)s"
        p_ins["commodity"] = commodity
    por_instrumento = [
        {"instrumento": r["i"] or "(sin)", "toneladas": round(_f(r["ton"]), 0), "n": r["n"]}
        for r in _q(f"SELECT instrumento AS i, SUM({_TON}) AS ton, count(*) AS n "
                    f"FROM operaciones WHERE {w_ins} GROUP BY instrumento ORDER BY ton DESC",
                    p_ins)
    ]
    # POR TIPO (tab nueva): commodity × {FUTURO, OPCION} — SIEMPRE ambos (ignora
    # el filtro tipo), acotado a [desde,hasta] + cross-filter de cuenta.
    w_tipo, p_tipo = date_w, dict(dp)
    if cuenta:
        w_tipo += " AND denominacion = %(cuenta)s"
        p_tipo["cuenta"] = cuenta
    totales_tipo = {c: {"FUTURO": 0.0, "OPCION": 0.0} for c in ("SOJA", "TRIGO", "MAIZ")}
    for r in _q(f"SELECT commodity AS c, COALESCE(tipo_agro, 'FUTURO') AS t, SUM({_TON}) AS ton "
                f"FROM operaciones WHERE {w_tipo} "
                f"GROUP BY commodity, COALESCE(tipo_agro, 'FUTURO')", p_tipo):
        if r["c"] in totales_tipo and r["t"] in ("FUTURO", "OPCION"):
            totales_tipo[r["c"]][r["t"]] = round(_f(r["ton"]), 0)
    # serie_tipo: por periodo × {FUTURO, OPCION} (chart de la tab por tipo).
    serie_tipo_map: dict[str, dict] = {}
    for r in _q(f"SELECT to_char(concertacion, %(fmt)s) AS p, "
                f"COALESCE(tipo_agro, 'FUTURO') AS t, SUM({_TON}) AS ton "
                f"FROM operaciones WHERE {w_tipo} "
                f"GROUP BY p, COALESCE(tipo_agro, 'FUTURO')",
                {**p_tipo, "fmt": fmt}):
        d = serie_tipo_map.setdefault(r["p"], {"periodo": r["p"], "FUTURO": 0.0, "OPCION": 0.0})
        if r["t"] in ("FUTURO", "OPCION"):
            d[r["t"]] = round(_f(r["ton"]), 0)
    serie_tipo = [serie_tipo_map[p] for p in sorted(serie_tipo_map)]

    nuestro_m = _agro_nuestro_mensual(scope=scope, nivel5=nivel5)

    # share: denominador del market-share desde mercado.volumen_mercado_agro.
    # `{periodo: {commodity: toneladas}}`.
    from api.services import cashflow_sql as _cf_sql
    mercado = _cf_sql.volumen_mercado_agro()
    serie_share = []
    for p in sorted(mercado):
        nm = mercado[p]
        ours = nuestro_m.get(p, {})
        row: dict = {"periodo": p}
        for c in ("SOJA", "TRIGO", "MAIZ"):
            mkt = nm.get(c) or 0
            row[c] = round(100 * (ours.get(c) or 0) / mkt, 2) if mkt else None
            row[f"{c}_nuestro"] = round(ours.get(c) or 0, 0)
            row[f"{c}_mercado"] = round(mkt, 0)
        serie_share.append(row)

    return {
        "desde": desde, "hasta": hasta, "agg": agg,
        "serie": serie, "serie_cuenta": serie_cuenta, "serie_share": serie_share,
        "totales": tot, "por_cuenta": por_cuenta, "por_instrumento": por_instrumento,
        "totales_tipo": totales_tipo, "serie_tipo": serie_tipo,
    }


# ── DÓLAR FUTURO (nocional en USD; 1 contrato = USD 1000) ─────────────────────
# Verificado sobre prod (2026-07-28): instrumento '[DLRmmYYYY]',
# mercado 'A3', tipo_operacion 'Futuros Financieros - Compra/Venta', es_cierre
# SIEMPRE false, etapa SIEMPRE NULL, bruto SIEMPRE 0 (inútil). `cantidad` es el
# nº de contratos (entero, sin fraccionarios) → NOCIONAL (USD) = |cantidad| × 1000.
# `arancel` va en ARS. No hay cierres ni liquidaciones que doble-contar, así que
# el filtro es sólo el instrumento DLR (a diferencia de la vista Operaciones).
_DLR_NOCIONAL = "ABS(COALESCE(cantidad, 0)) * 1000"
_DLR_TIPO = "CASE WHEN strpos(COALESCE(tipo_operacion, ''), 'Compra') > 0 THEN 'Compra' ELSE 'Venta' END"
_DLR_METRIC = (
    f"SUM({_DLR_NOCIONAL}) AS noc, SUM(ABS(COALESCE(arancel, 0))) AS ar, count(*) AS n"
)


def ops_dolar_futuro(
    desde: str = "", hasta: str = "", agg: str = "MENSUAL",
    tipo: str | None = None, cuenta: str | None = None, instrumento: str | None = None,
    scope: tuple[str, ...] | None = None, nivel5: str | None = None,
) -> dict:
    """Dólar futuro (DLR): NOCIONAL (USD) + arancel (ARS) + boletos, agregado en
    vivo sobre operaciones.operaciones. Devuelve `por_tipo` (Compra/Venta),
    `por_cuenta`, `por_instrumento` (vencimientos) y `serie` (nocional por
    periodo, split Compra/Venta). Cross-filter 3-way: cada tabla aplica las
    selecciones de las OTRAS (la serie no se filtra por `tipo`, lo separa en
    series). Todo acotado a [desde, hasta]."""
    fmt = "YYYY-MM" if agg.upper() == "MENSUAL" else "YYYY-MM-DD"
    # Literal inline (no param): el planner solo usa el índice parcial ix_ops_dlr_concert
    # si ve el predicado idéntico al del índice. Constante nuestra, no input de usuario.
    base = "instrumento ILIKE '%%DLR%%' AND anulado_en IS NULL"
    bp: dict = {}
    if scope is not None:
        base += " AND id_cuenta = ANY(%(scope)s)"
        bp["scope"] = list(scope)
    if nivel5:
        base += " AND id_cuenta IN (SELECT id_cuenta FROM comitentes WHERE nivel_5 = %(nivel5)s)"
        bp["nivel5"] = nivel5
    date_w = f"{base} AND concertacion >= %(desde)s AND concertacion <= %(hasta)s"
    dp = {**bp, "desde": desde, "hasta": hasta}

    # Fragmentos de cross-filter (WHERE parcial, params).
    f_tipo = (f" AND {_DLR_TIPO} = %(f_tipo)s", {"f_tipo": tipo}) if tipo else ("", {})
    f_cta = (" AND denominacion = %(f_cta)s", {"f_cta": cuenta}) if cuenta else ("", {})
    f_ins = (" AND instrumento = %(f_ins)s", {"f_ins": instrumento}) if instrumento else ("", {})

    # por_tipo (Compra/Venta) — aplica cuenta + instrumento.
    w = date_w + f_cta[0] + f_ins[0]
    por_tipo = [
        {"tipo": r["t"], "nocional": _f(r["noc"]), "arancel": round(_f(r["ar"]), 2), "n": r["n"]}
        for r in _q(f"SELECT {_DLR_TIPO} AS t, {_DLR_METRIC} FROM operaciones "
                    f"WHERE {w} GROUP BY t ORDER BY t", {**dp, **f_cta[1], **f_ins[1]})
    ]
    # por_cuenta — aplica tipo + instrumento.
    w = date_w + f_tipo[0] + f_ins[0]
    por_cuenta = [
        {"denominacion": r["d"] or "(sin)", "nocional": _f(r["noc"]),
         "arancel": round(_f(r["ar"]), 2), "n": r["n"]}
        for r in _q(f"SELECT denominacion AS d, {_DLR_METRIC} FROM operaciones "
                    f"WHERE {w} GROUP BY denominacion ORDER BY noc DESC NULLS LAST",
                    {**dp, **f_tipo[1], **f_ins[1]})
    ]
    # por_instrumento (vencimientos) — aplica tipo + cuenta.
    w = date_w + f_tipo[0] + f_cta[0]
    por_instrumento = [
        {"instrumento": r["i"] or "(sin)", "nocional": _f(r["noc"]),
         "arancel": round(_f(r["ar"]), 2), "n": r["n"]}
        for r in _q(f"SELECT instrumento AS i, {_DLR_METRIC} FROM operaciones "
                    f"WHERE {w} GROUP BY instrumento ORDER BY noc DESC NULLS LAST",
                    {**dp, **f_tipo[1], **f_cta[1]})
    ]
    # serie (nocional por periodo, split Compra/Venta) — aplica cuenta + instrumento.
    w = date_w + f_cta[0] + f_ins[0]
    serie_map: dict[str, dict] = {}
    for r in _q(f"SELECT to_char(concertacion, %(fmt)s) AS per, {_DLR_TIPO} AS t, "
                f"SUM({_DLR_NOCIONAL}) AS noc FROM operaciones WHERE {w} GROUP BY per, t",
                {**dp, **f_cta[1], **f_ins[1], "fmt": fmt}):
        d = serie_map.setdefault(r["per"], {"periodo": r["per"], "Compra": 0.0, "Venta": 0.0})
        d[r["t"]] = round(_f(r["noc"]), 0)
    serie = [serie_map[k] for k in sorted(serie_map)]
    # total (header) — aplica los 3 filtros.
    w = date_w + f_tipo[0] + f_cta[0] + f_ins[0]
    tr = _q(f"SELECT {_DLR_METRIC} FROM operaciones WHERE {w}",
            {**dp, **f_tipo[1], **f_cta[1], **f_ins[1]})
    total = ({"nocional": _f(tr[0]["noc"]), "arancel": round(_f(tr[0]["ar"]), 2), "n": tr[0]["n"]}
             if tr else {"nocional": 0.0, "arancel": 0.0, "n": 0})

    return {
        "desde": desde, "hasta": hasta, "agg": agg, "total": total,
        "por_tipo": por_tipo, "por_cuenta": por_cuenta,
        "por_instrumento": por_instrumento, "serie": serie,
    }
