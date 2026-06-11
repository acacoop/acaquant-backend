"""operaciones_view.py — lógica PURA de la vista Operaciones (negocio + ops).

Constructores de match/agregación Mongo, selector de motor SQL/Mongo y las
series con live-fallback sobre el rollup. Antes vivían como helpers privados
dentro del router `api/routers/operaciones.py` (1.737 líneas) — al moverlos
acá: (1) se pueden testear sin levantar FastAPI, (2) el router vuelve a ser
thin HTTP plumbing, (3) el MCP / futuros jobs pueden reusarlos. El router los
importa de vuelta con sus nombres `_*` originales (alias) → cero cambios en
los call sites. AUDITORIA A2.

Reglas de dominio NO inferibles (ver CLAUDE.md "Operaciones — rollup, NO
escanear"): el cierre de caución (`es_cierre`) separa VOLUMEN de ARANCEL —
volumen lo excluye (la apertura ya cuenta el nocional), arancel lo INCLUYE
(el fee de caución vive solo en el cierre). `etapa=solicitud/liquidacion`
desdobla el FCI bilateral para no doblar el volumen.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from api.db import get_db_clientes

# La serie del gráfico de aranceles se acota por defecto a esta ventana (cubre
# de sobra los botones 1W…1A). El aggregate sobre toda la historia escanea
# ~180k-300k docs y el FETCH es inevitable. El botón ALL del front pide
# serie_full=True para traer la historia completa bajo demanda.
SERIE_VENTANA_DIAS = 550  # ~18 meses
OPS_MONEDAS = ("ARS", "USD", "USD_DOL")


def ddmmyyyy_a_iso(raw: str | None) -> str | None:
    """'02/07/2025' (dd/mm/yyyy) → '2025-07-02'. None si no parsea.
    CashFlow.Movimientos guarda la fecha en dd/mm/yyyy; la API la sirve en ISO."""
    try:
        return datetime.strptime((raw or "").strip(), "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def motor(req: str | None, env: str = "OPERACIONES_SQL") -> str:
    """Motor de datos para una vista. Override por request `?_engine=sql|mongo`
    (A/B en prod); si no, el global `env`=1 → 'sql', sino 'mongo'. Mongo es el
    default hasta el cutover. Cada vista tiene su flag → cutover independiente.
    La salida SQL == Mongo (validado por scripts/compare_*)."""
    if req in ("sql", "mongo"):
        return req
    return "sql" if os.getenv(env) == "1" else "mongo"


def importe_convertido(moneda: str) -> dict:
    """|importe| convertido a la moneda destino con el `mep` snapshot de CADA
    boleto (conversión histórica exacta — NO al MEP de hoy). Misma moneda →
    directo; ARS→USD → /mep; USD→ARS → ×mep. Si a un boleto de otra moneda le
    falta `mep`, aporta 0 (no se puede convertir sin su mep del día)."""
    abs_imp = {"$abs": {"$ifNull": ["$importe", 0]}}
    mep = {"$ifNull": ["$mep", 0]}
    if (moneda or "ARS").upper() == "USD":
        return {"$cond": [
            {"$eq": ["$moneda", "USD"]},
            abs_imp,
            {"$cond": [{"$gt": [mep, 0]}, {"$divide": [abs_imp, "$mep"]}, 0]},
        ]}
    return {"$cond": [
        {"$eq": ["$moneda", "ARS"]},
        abs_imp,
        {"$multiply": [abs_imp, mep]},
    ]}


def valor_si_categoria(target: str, conv: dict) -> dict:
    """`conv` (importe ya convertido a la moneda destino) si categoria ==
    target, sino 0. Para los $group por categoría de la serie / matrix."""
    return {"$cond": [{"$eq": ["$categoria", target]}, conv, 0]}


def ops_match(
    moneda: str,
    mercado: str | None,
    operacion: str | None = None,
    denominacion: str | None = None,
    cuenta: str | None = None,
    segmento: str | None = None,
) -> dict:
    # FCI bilateral aparece 2 veces (solicitud DOC + liquidación CL). Para NO doblar
    # el volumen se cuenta UNA sola vez, en el día "correcto":
    #   · SUSCRIPCIÓN → su SOLICITUD (día del pedido; así se ve en el día y no recién
    #     al liquidar T+1). Se EXCLUYE su liquidación.
    #   · RESCATE     → su LIQUIDACIÓN (la solicitud del rescate viene en 0). Se
    #     EXCLUYE su solicitud.
    # Todo lo demás (boletos normales SIN etapa, liquidaciones de rescate) pasa.
    # OJO: una suscripción CL muy vieja SIN su DOC quedaría excluida (riesgo asumido).
    m: dict = {"moneda": moneda, "es_cierre": False,
               "$nor": [
                   {"operacion": "Suscripción", "etapa": "liquidacion"},
                   {"operacion": "Rescate", "etapa": "solicitud"},
               ]}
    if mercado and mercado.lower() != "todos":
        m["mercado"] = mercado
    if operacion:
        m["operacion"] = operacion
    if denominacion:
        m["denominacion"] = denominacion
    if cuenta:
        m["cuenta"] = cuenta
    if segmento and segmento.lower() != "todos":
        m["segmento"] = segmento
    return m


def arancel_match(
    moneda: str,
    mercado: str | None = None,
    *,
    segmento: str | None = None,
    denominacion: str | None = None,
    cuenta: str | None = None,
) -> dict:
    """Como `ops_match` pero para sumar ARANCEL: incluye los CIERRES con arancel.

    El arancel de caución vive SOLO en el cierre (es_cierre=True); las aperturas
    NO traen arancel (verificado con scripts/diag_aranceles_caucion: $121,6M en el
    cierre, 0 en la apertura). El filtro de volumen (ops_match → es_cierre=False)
    los excluía y los PERDÍA. Acá los incluimos sin arrastrar compras/ventas: los
    cierres que NO son caución no tienen arancel, así que el `$or` sólo suma fees.
    """
    m = ops_match(moneda, mercado, denominacion=denominacion, cuenta=cuenta, segmento=segmento)
    del m["es_cierre"]
    m["$or"] = [{"es_cierre": False}, {"es_cierre": True, "arancel": {"$ne": 0}}]
    return m


def serie_bruto_rollup(cf, moneda, mercado, operacion, segmento) -> list[dict] | None:
    """Serie Σ bruto por fecha desde el rollup CashFlow.OpsSerieDiaria (histórico
    CERRADO, fecha < hoy) + el día de HOY en vivo (live-fallback). Devuelve None si
    el rollup está vacío (no construido) → el caller cae a la query live completa.

    Solo para el caso común (sin filtro de alta cardinalidad ni scope). El rollup
    ya pre-filtra es_cierre/solicitud igual que ops_match → equivalente."""
    hoy = (datetime.now(UTC) - timedelta(hours=3)).date().isoformat()  # ART
    m: dict = {"moneda": moneda, "fecha": {"$lt": hoy}}
    if mercado and mercado.lower() != "todos":
        m["mercado"] = mercado
    if operacion:
        m["operacion"] = operacion
    if segmento and segmento.lower() != "todos":
        m["segmento"] = segmento
    rollup = list(cf["OpsSerieDiaria"].aggregate([
        {"$match": m},
        {"$group": {"_id": "$fecha", "bruto": {"$sum": "$bruto"}}},
    ]))
    if not rollup:
        return None  # rollup no construido para esta moneda → live
    serie = {r["_id"]: r["bruto"] for r in rollup}
    # Hoy en vivo (un solo día → índice concertacion, barato). El rollup llega a ayer.
    hoy_match = ops_match(moneda, mercado, operacion, None, None, segmento)
    hoy_match["concertacion"] = hoy
    hoy_doc = next(iter(cf["Operaciones"].aggregate([
        {"$match": hoy_match},
        {"$group": {"_id": None, "bruto": {"$sum": {"$ifNull": ["$bruto", 0]}}}},
    ])), None)
    if hoy_doc and hoy_doc.get("bruto"):
        serie[hoy] = hoy_doc["bruto"]
    return [{"fecha": f, "bruto": round(serie[f], 2)} for f in sorted(serie)]


def serie_arancel_rollup(cf, segmento, plen, serie_full) -> list[dict] | None:
    """Serie Σ arancel (PESOS, todas las monedas) por periodo desde OpsSerieDiaria
    (histórico < hoy) + hoy en vivo. El arancel es siempre en pesos → NO se filtra
    por moneda. None si el rollup está vacío → live. plen=7 mensual / 10 diario."""
    hoy = (datetime.now(UTC) - timedelta(hours=3)).date().isoformat()  # ART
    m: dict = {"fecha": {"$lt": hoy}}
    if segmento and segmento.lower() != "todos":
        m["segmento"] = segmento
    if not serie_full:
        cutoff = (datetime.now(UTC) - timedelta(hours=3)
                  - timedelta(days=SERIE_VENTANA_DIAS)).date().isoformat()
        m["fecha"] = {"$gte": cutoff, "$lt": hoy}
    rollup = list(cf["OpsSerieDiaria"].aggregate([
        {"$match": m},
        {"$group": {"_id": {"$substr": ["$fecha", 0, plen]}, "ar": {"$sum": "$arancel"}}},
    ]))
    if not rollup:
        return None
    serie = {r["_id"]: r["ar"] for r in rollup}
    # Hoy en vivo (un día → índice concertacion). abs(arancel), sin filtro de moneda.
    # arancel_match: incluye los cierres de caución (es donde está el arancel).
    hoy_match = arancel_match("ARS", segmento=segmento)
    hoy_match.pop("moneda", None)
    hoy_match["concertacion"] = hoy
    hoy_doc = next(iter(cf["Operaciones"].aggregate([
        {"$match": hoy_match},
        {"$group": {"_id": None, "ar": {"$sum": {"$abs": {"$ifNull": ["$arancel", 0]}}}}},
    ])), None)
    if hoy_doc and hoy_doc.get("ar"):
        p = hoy[:plen]
        serie[p] = serie.get(p, 0) + hoy_doc["ar"]
    return [{"periodo": p, "arancel": round(serie[p], 2)} for p in sorted(serie)]


def op_cuentas(operador: str | None, scope: tuple[str, ...] | None) -> list[str] | None:
    """operador_email → id_cuentas de ese operador (intersecta con scope si lo
    hay). None si no se filtra por operador. En Operaciones el campo `cuenta`
    guarda el id_cuenta, así que se aplica como `match['cuenta'] = {$in: ...}`."""
    if not operador:
        return None
    cuentas = [str(c["id_cuenta"]) for c in get_db_clientes()["Comitentes"].find(
        {"operador_email": operador}, {"_id": 0, "id_cuenta": 1}) if c.get("id_cuenta")]
    if scope is not None:
        scope_set = set(scope)
        cuentas = [c for c in cuentas if c in scope_set]
    return cuentas


# ─────────────────────────────────────────────────────────────────────────────
# Cuerpos Mongo de los endpoints pesados (AUDITORIA A2, parte 2)
#
# El router valida params / despacha SQL / maneja HTTP; acá vive el CÓMPUTO.
# Mismo código que tenían los endpoints — move, no rewrite.
# ─────────────────────────────────────────────────────────────────────────────

# Categorías de boleto que entran al chart de la vista gerencial. Algunas
# se combinan en una sola serie para el front (suscripciones = susc + sol_susc).
NEGOCIO_SERIE_BOLETO_CATS = (
    "compra", "venta",
    "suscripcion_fci", "solicitud_suscripcion_fci",
    "caucion_tom_ap", "caucion_col_ap",
)
NEGOCIO_MONEDAS_VALIDAS = ("ARS", "USD")


def negocio_serie_mongo(
    *, moneda: str, cuenta_filter: str, cuenta: str | None,
    scope: tuple[str, ...] | None,
) -> dict:
    """Serie diaria del importe absoluto por categoría (bar chart de
    /operaciones/negocio). Params ya validados por el router; `cuenta` ya
    verificada contra el scope (verificar_cuenta_str)."""
    from api.db import get_db_cashflow
    from api.services._cuentas_filter import match_cuenta_filter
    from api.services._grupos_scope import aplicar_scope_cuenta
    from api.services._negocio_futuros import match_no_futuros

    coll = get_db_cashflow()["NegocioMovimientos"]
    # NO filtra por moneda: entran ARS y USD, y cada boleto se convierte a
    # la moneda destino con su propio mep (ver importe_convertido). Así el
    # "volumen operado" es el total dolarizado/pesificado, no solo una moneda.
    match_doc: dict = {
        "categoria": {"$in": list(NEGOCIO_SERIE_BOLETO_CATS)},
        # Futuros DLR (unidad="USDL"): no entran al gráfico de NEGOCIO.
        **match_no_futuros(),
    }
    if cuenta:
        # Match exacto sobre cuenta — override total del cuenta_filter.
        match_doc["cuenta"] = cuenta
    else:
        match_doc.update(match_cuenta_filter(cuenta_filter))
    aplicar_scope_cuenta(match_doc, scope, campo="id_cuenta")
    conv = importe_convertido(moneda)
    pipeline = [
        {"$match": match_doc},
        {"$group": {
            "_id":         "$fecha",
            "compra":      {"$sum": valor_si_categoria("compra", conv)},
            "venta":       {"$sum": valor_si_categoria("venta", conv)},
            "_susc":       {"$sum": valor_si_categoria("suscripcion_fci", conv)},
            "_sol_susc":   {"$sum": valor_si_categoria("solicitud_suscripcion_fci", conv)},
            "cauc_tom":    {"$sum": valor_si_categoria("caucion_tom_ap", conv)},
            "cauc_col":    {"$sum": valor_si_categoria("caucion_col_ap", conv)},
        }},
        {"$sort": {"_id": 1}},
        {"$project": {
            "_id":          0,
            "fecha":        "$_id",
            "compra":       {"$round": ["$compra", 2]},
            "venta":        {"$round": ["$venta", 2]},
            "suscripciones": {"$round": [{"$add": ["$_susc", "$_sol_susc"]}, 2]},
            "cauc_tom":     {"$round": ["$cauc_tom", 2]},
            "cauc_col":     {"$round": ["$cauc_col", 2]},
        }},
    ]
    serie = list(coll.aggregate(pipeline))
    return {
        "moneda":        moneda,
        "cuenta_filter": cuenta_filter,
        "cuenta":        cuenta,
        "serie":         serie,
    }


def ops_serie_mongo(
    *, moneda: str, mercado: str | None, operacion: str | None,
    denominacion: str | None, cuenta: str | None, segmento: str | None,
    operador: str | None, scope: tuple[str, ...] | None,
) -> dict:
    """Serie diaria: Σ bruto por fecha (las barras del gráfico).

    Caso común (sin filtro de alta cardinalidad ni scope) → lee el rollup
    OpsSerieDiaria (jobs/ops_rollup) en vez de escanear toda Operaciones. Con
    denominacion/cuenta/scoped/operador → live (ya filtra por índice)."""
    from api.db import get_db_cashflow
    from api.services._grupos_scope import aplicar_scope_cuenta

    cuentas_operador = op_cuentas(operador, scope)
    cf = get_db_cashflow()
    serie: list[dict] | None = None
    if not denominacion and not cuenta and scope is None and cuentas_operador is None:
        serie = serie_bruto_rollup(cf, moneda, mercado, operacion, segmento)
    if serie is None:
        match = ops_match(moneda, mercado, operacion, denominacion, cuenta, segmento)
        aplicar_scope_cuenta(match, scope, campo="cuenta")
        if cuentas_operador is not None:
            match["cuenta"] = {"$in": cuentas_operador}
        serie = list(cf["Operaciones"].aggregate([
            {"$match": match},
            {"$group": {"_id": "$concertacion", "bruto": {"$sum": {"$ifNull": ["$bruto", 0]}}}},
            {"$sort": {"_id": 1}},
            {"$project": {"_id": 0, "fecha": "$_id", "bruto": {"$round": ["$bruto", 2]}}},
        ]))
    return {"moneda": moneda, "mercado": mercado, "serie": serie}


def ops_resumen_mongo(
    *, moneda: str, mercado: str | None, desde: str, hasta: str,
    operacion: str | None, denominacion: str | None, instrumento: str | None,
    cuenta: str | None, segmento: str | None, operador: str | None,
    scope: tuple[str, ...] | None,
) -> dict:
    """Scope [desde,hasta]: Σ bruto por operacion / denominacion / instrumento.
    Cross-filter 3-way: cada tabla aplica las selecciones de las OTRAS dos."""
    from api.db import get_db_cashflow
    from api.services._grupos_scope import aplicar_scope_cuenta

    cuentas_operador = op_cuentas(operador, scope)
    db = get_db_cashflow()["Operaciones"]
    base = ops_match(moneda, mercado, cuenta=cuenta, segmento=segmento)
    base["concertacion"] = {"$gte": desde, "$lte": hasta}
    aplicar_scope_cuenta(base, scope, campo="cuenta")
    if cuentas_operador is not None:
        base["cuenta"] = {"$in": cuentas_operador}

    # Cross-filter 3-way: cada faceta aplica las selecciones de las OTRAS dos dims.
    def _xf(*, denom=False, op=False, instr=False) -> list[dict]:
        m: dict = {}
        if denom and denominacion:
            m["denominacion"] = denominacion
        if op and operacion:
            m["operacion"] = operacion
        if instr and instrumento:
            m["instrumento"] = instrumento
        return [{"$match": m}] if m else [{"$match": {}}]

    def _grupo(campo: str, key: str, having_nz: bool) -> list[dict]:
        etapas = [
            {"$group": {"_id": campo, "bruto": {"$sum": {"$ifNull": ["$bruto", 0]}}, "n": {"$sum": 1}}},
        ]
        if having_nz:
            etapas.append({"$match": {"bruto": {"$ne": 0}}})
        etapas += [
            {"$sort": {"bruto": -1}},
            {"$project": {"_id": 0, key: {"$ifNull": ["$_id", "(sin)"]},
                          "bruto": {"$round": ["$bruto", 2]}, "n": 1}},
        ]
        return etapas

    facet = list(db.aggregate([
        {"$match": base},
        {"$facet": {
            "por_operacion":    [*_xf(denom=True, instr=True), *_grupo("$operacion", "operacion", True)],
            "por_denominacion": [*_xf(op=True, instr=True), *_grupo("$denominacion", "denominacion", False)],
            "por_instrumento":  [*_xf(op=True, denom=True), *_grupo("$instrumento", "instrumento", True)],
        }},
    ]))
    f = facet[0] if facet else {}
    por_op = f.get("por_operacion", [])
    por_denom = f.get("por_denominacion", [])
    por_instr = f.get("por_instrumento", [])
    # Total = el de la dimensión filtrada (si hay selección) o el global.
    total = round(sum(r["bruto"] for r in (por_denom if denominacion else por_op)), 2)
    return {
        "moneda": moneda, "mercado": mercado, "desde": desde, "hasta": hasta,
        "por_operacion": por_op, "por_denominacion": por_denom,
        "por_instrumento": por_instr, "total": total,
    }


def ops_agro_mongo(
    *, desde: str, hasta: str, agg: str, commodity: str | None,
    cuenta: str | None, nivel5: str | None, scope: tuple[str, ...] | None,
) -> dict:
    """Futuros agropecuarios: Σ TONELADAS por periodo (mes/día) y commodity
    (SOJA/TRIGO/MAIZ). Lógica: tipo 'Futuros' sin 'Financieros', sin OTC;
    toneladas = |cantidad| × (10 si 'MIN' en instrumento, sino 100).

    Devuelve: `serie` (Σ por periodo en [desde,hasta], chart de la izq),
    `serie_cuenta` (idem SOLO de la cuenta elegida; vacía sin `cuenta`),
    `serie_share` (% mensual nuestro/mercado por commodity — lee
    CashFlow.VolumenMercadoAgro), `totales`, `por_cuenta`, `por_instrumento`."""
    from api.db import get_db_cashflow
    from api.services._grupos_scope import aplicar_scope_cuenta

    dbcf = get_db_cashflow()
    db = dbcf["Operaciones"]
    plen = 7 if agg.upper() == "MENSUAL" else 10
    inst = {"$ifNull": ["$instrumento", ""]}
    # `commodity` (SOJA/TRIGO/MAIZ) se materializa en la ingesta — ver
    # operaciones_informes.clasificar_commodity. Match indexado (índice parcial
    # commodity_concertacion) → no escanea la colección con regex. Backfill de
    # docs viejos: scripts/backfill_commodity_operaciones.py.
    # serie/serie_cuenta (gráficos de VOLUMEN) y las tablas se acotan al rango
    # [desde,hasta] (date_m) → el toolbar Desde/Hasta maneja el gráfico (su "ALL"
    # = el rango elegido). El share mensual (nuestro_mensual) SÍ sigue histórico.
    match: dict = {"commodity": {"$in": ["SOJA", "TRIGO", "MAIZ"]}}
    aplicar_scope_cuenta(match, scope, campo="cuenta")
    # Filtro por nivel_5 (Comitentes) → cuentas de ese nivel_5 (intersecta scope).
    if nivel5:
        n5_cuentas = [str(c["id_cuenta"]) for c in get_db_clientes()["Comitentes"].find(
            {"nivel_5": nivel5}, {"_id": 0, "id_cuenta": 1}) if c.get("id_cuenta")]
        if scope is not None:
            n5_cuentas = [c for c in n5_cuentas if c in set(scope)]
        match["cuenta"] = {"$in": n5_cuentas}
    date_m = {"$match": {"concertacion": {"$gte": desde, "$lte": hasta}}}
    addf = {
        "toneladas": {"$multiply": [
            {"$abs": {"$ifNull": ["$cantidad", 0]}},
            {"$cond": [{"$regexMatch": {"input": inst, "regex": "MIN", "options": "i"}}, 10, 100]},
        ]},
        "periodo": {"$substr": ["$concertacion", 0, plen]},
    }
    # Cross-filter independiente: `cuenta` filtra commodities + instrumentos;
    # `commodity` filtra cuentas + instrumentos. El gráfico global queda FIJO; el
    # de cuenta sólo existe cuando hay `cuenta` elegida.
    f_comm = [{"$match": {"denominacion": cuenta}}] if cuenta else []   # cuenta → filtra commodities
    f_cta = [{"$match": {"commodity": commodity}}] if commodity else []  # commodity → filtra cuentas
    serie_grp = {"$group": {"_id": {"p": "$periodo", "c": "$commodity"}, "ton": {"$sum": "$toneladas"}}}
    facet_spec: dict = {
        "serie": [date_m, serie_grp],
        "por_commodity": [date_m, *f_comm,
                          {"$group": {"_id": "$commodity", "ton": {"$sum": "$toneladas"}}}],
        "por_cuenta": [date_m, *f_cta,
                       {"$group": {"_id": "$denominacion", "ton": {"$sum": "$toneladas"}, "n": {"$sum": 1}}},
                       {"$sort": {"ton": -1}}],
        "por_instrumento": [date_m, *f_comm, *f_cta,
                            {"$group": {"_id": "$instrumento", "ton": {"$sum": "$toneladas"}, "n": {"$sum": 1}}},
                            {"$sort": {"ton": -1}}],
        # Nuestro volumen agregado a MES (histórico) → numerador del market share.
        "nuestro_mensual": [{"$group": {
            "_id": {"p": {"$substr": ["$concertacion", 0, 7]}, "c": "$commodity"},
            "ton": {"$sum": "$toneladas"}}}],
    }
    if cuenta:
        facet_spec["serie_cuenta"] = [{"$match": {"denominacion": cuenta}}, date_m, serie_grp]
    facet = list(db.aggregate([
        {"$match": match},
        {"$addFields": addf},
        {"$facet": facet_spec},
    ]))
    f = facet[0] if facet else {}

    def _serie(rows) -> list[dict]:
        out: dict[str, dict] = {}
        for r in rows:
            p, c = r["_id"]["p"], r["_id"]["c"]
            d = out.setdefault(p, {"periodo": p, "SOJA": 0.0, "TRIGO": 0.0, "MAIZ": 0.0})
            d[c] = round(r["ton"], 0)
        return [out[p] for p in sorted(out)]

    tot = {"SOJA": 0.0, "TRIGO": 0.0, "MAIZ": 0.0}
    for r in f.get("por_commodity", []):
        tot[r["_id"]] = round(r["ton"], 0)
    por_cuenta = [{"denominacion": r["_id"] or "(sin)", "toneladas": round(r["ton"], 0), "n": r["n"]}
                  for r in f.get("por_cuenta", [])]
    por_instrumento = [{"instrumento": r["_id"] or "(sin)", "toneladas": round(r["ton"], 0), "n": r["n"]}
                       for r in f.get("por_instrumento", [])]

    # Market share MENSUAL: nuestro_mensual / volumen de mercado. Sólo para los
    # meses con dato de mercado cargado (CashFlow.VolumenMercadoAgro, manual).
    # share = None si falta el mercado de ese commodity → el front lo muestra como —.
    nuestro_m: dict[str, dict] = {}
    for r in f.get("nuestro_mensual", []):
        nuestro_m.setdefault(r["_id"]["p"], {})[r["_id"]["c"]] = r["ton"]
    mercado: dict[str, dict] = {}
    for d in dbcf["VolumenMercadoAgro"].find(
        {}, {"_id": 0, "periodo": 1, "commodity": 1, "toneladas": 1}
    ):
        mercado.setdefault(d["periodo"], {})[d["commodity"]] = d.get("toneladas") or 0
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
        "serie": _serie(f.get("serie", [])),
        "serie_cuenta": _serie(f.get("serie_cuenta", [])),
        "serie_share": serie_share,
        "totales": tot, "por_cuenta": por_cuenta, "por_instrumento": por_instrumento,
    }


def ops_aranceles_mongo(
    *, moneda: str, desde: str, hasta: str, agg: str, cuenta: str | None,
    instrumento: str | None, sel_dim: str | None, segmento: str | None,
    operador: str | None, dim: str, serie_full: bool,
    scope: tuple[str, ...] | None,
) -> dict:
    """Σ aranceles por periodo (gráfico), por dim izquierda y por cliente.

    La SERIE (histórica) sale del rollup OpsSerieDiaria cuando no hay scope
    ni operador; las TABLAS son date-bounded → live (ya usan índice)."""
    from api.db import get_db_cashflow
    from api.services._grupos_scope import aplicar_scope_cuenta

    cf = get_db_cashflow()
    db = cf["Operaciones"]
    plen = 7 if agg.upper() == "MENSUAL" else 10
    # El arancel es SIEMPRE en pesos y hay UN solo valor (aunesa_aranceles guarda
    # aranceles["ARS"]). NO existe "arancel en USD" → no se filtra ni convierte por
    # moneda; el toggle de moneda no aplica a esta vista.
    arancel = {"$abs": {"$ifNull": ["$arancel", 0]}}

    # Filtro madre por operador → cuentas de ese operador (intersecta con scope).
    cuentas_operador = op_cuentas(operador, scope)

    # SERIE: rollup (sumando TODAS las monedas) + hoy live; live si scoped o por
    # operador (el rollup no tiene operador → cae a live con el filtro de cuentas).
    serie: list[dict] | None = None
    if scope is None and cuentas_operador is None:
        serie = serie_arancel_rollup(cf, segmento, plen, serie_full)
    if serie is None:
        match_s = arancel_match("ARS", segmento=segmento)
        match_s.pop("moneda", None)
        aplicar_scope_cuenta(match_s, scope, campo="cuenta")
        if cuentas_operador is not None:
            match_s["cuenta"] = {"$in": cuentas_operador}
        if serie_full:
            serie_ventana: list[dict] = []
        else:
            cutoff = (datetime.now(UTC) - timedelta(hours=3)
                      - timedelta(days=SERIE_VENTANA_DIAS)).date().isoformat()
            serie_ventana = [{"$match": {"concertacion": {"$gte": cutoff}}}]
        serie = list(db.aggregate([
            {"$match": match_s},
            *serie_ventana,
            {"$group": {"_id": {"$substr": ["$concertacion", 0, plen]}, "ar": {"$sum": arancel}}},
            {"$sort": {"_id": 1}},
            {"$project": {"_id": 0, "periodo": "$_id", "arancel": {"$round": ["$ar", 2]}}},
        ]))

    # TABLAS (acotadas a [desde,hasta] → rápidas por índice). CROSS-FILTER COMPLETO:
    # las 3 tablas (dim izq · cuentas · instrumentos) se filtran entre sí — cada una
    # aplica las selecciones de las OTRAS dos, no la propia.
    match_t = arancel_match("ARS", segmento=segmento)
    match_t.pop("moneda", None)   # un solo arancel en pesos → sin filtro de moneda
    aplicar_scope_cuenta(match_t, scope, campo="cuenta")
    if cuentas_operador is not None:
        match_t["cuenta"] = {"$in": cuentas_operador}
    date_m = {"$match": {"concertacion": {"$gte": desde, "$lte": hasta}}}

    # Mapa operador (lazy): id_cuenta → operador (nombre/email/"(sin operador)").
    _op_map: dict[str, str] = {}

    def _operador_map() -> dict[str, str]:
        if not _op_map:
            _op_map.update({
                str(c["id_cuenta"]): (c.get("operador_nombre") or c.get("operador_email") or "(sin operador)")
                for c in get_db_clientes()["Comitentes"].find(
                    {}, {"_id": 0, "id_cuenta": 1, "operador_email": 1, "operador_nombre": 1})
            })
        return _op_map

    def _match_operador(valor: str) -> dict:
        m = _operador_map()
        if valor == "(sin operador)":
            return {"cuenta": {"$nin": [k for k, v in m.items() if v != "(sin operador)"]}}
        return {"cuenta": {"$in": [k for k, v in m.items() if v == valor]}}

    # Sub-match de cada selección (None si no hay).
    if sel_dim and dim == "operador":
        m_dim: dict | None = _match_operador(sel_dim)
    elif sel_dim and dim == "operacion":
        m_dim = {"operacion": sel_dim}
    elif sel_dim:
        m_dim = {"nivel_3": sel_dim}
    else:
        m_dim = None
    m_cuenta = {"denominacion": cuenta} if cuenta else None
    m_instr = {"instrumento": instrumento} if instrumento else None

    def _tabla(campo: str, key: str, *subs: dict | None) -> list[dict]:
        etapas = [{"$match": s} for s in subs if s]
        return list(db.aggregate([
            {"$match": match_t}, date_m, *etapas,
            {"$group": {"_id": campo, "ar": {"$sum": arancel}, "n": {"$sum": 1}}},
            {"$match": {"ar": {"$gt": 0}}},
            {"$sort": {"ar": -1}},
            {"$project": {"_id": 0, key: {"$ifNull": ["$_id", "(sin)"]},
                          "arancel": {"$round": ["$ar", 2]}, "n": 1}},
        ]))

    # IZQUIERDA (por_dim): filtrada por cuenta + instrumento (no por sí misma).
    if dim == "operador":
        det = _operador_map()
        acc: dict[str, dict] = {}
        etapas = [{"$match": s} for s in (m_cuenta, m_instr) if s]
        for r in db.aggregate([{"$match": match_t}, date_m, *etapas,
                {"$group": {"_id": "$cuenta", "ar": {"$sum": arancel}, "n": {"$sum": 1}}}]):
            op = det.get(str(r["_id"]), "(sin operador)")
            a = acc.setdefault(op, {"ar": 0.0, "n": 0})
            a["ar"] += r["ar"]
            a["n"] += r["n"]
        por_dim = sorted(
            ({"clave": k, "arancel": round(v["ar"], 2), "n": v["n"]}
             for k, v in acc.items() if v["ar"] > 0),
            key=lambda x: x["arancel"], reverse=True,
        )
    else:
        field = "$operacion" if dim == "operacion" else "$nivel_3"
        por_dim = _tabla(field, "clave", m_cuenta, m_instr)

    # DERECHA ARRIBA (por_cuenta): filtrada por dim + instrumento.
    por_cuenta = _tabla("$denominacion", "denominacion", m_dim, m_instr)
    # DERECHA ABAJO (por_instrumento): filtrada por dim + cuenta.
    por_instrumento = _tabla("$instrumento", "instrumento", m_dim, m_cuenta)

    return {
        "moneda": moneda, "desde": desde, "hasta": hasta, "agg": agg, "dim": dim,
        "serie": serie,
        "por_dim": por_dim,
        "por_cuenta": por_cuenta,
        "por_instrumento": por_instrumento,
        "total": round(sum(r["arancel"] for r in por_dim), 2),
    }
