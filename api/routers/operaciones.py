"""Router Operaciones: endpoints para MesaAPI (flujo contrapartes), FlujosAPI
(movimientos) y NegocioMovimientos (vista de negocio del día)."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row

from api.cache import cached
from api.db import get_db_cashflow
from api.services import cashflow_sql as _cf_sql
from api.services import comercial as _com
from api.services import comercial_sql as _com_sql
from api.services import operaciones_sql as _ops_sql
from api.services._grupos_scope import (
    aplicar_scope_cuenta,
    scope_cuentas,
    verificar_cuenta_str,
)
from api.services.operaciones_view import (
    OPS_MONEDAS as _OPS_MONEDAS,
)
from api.services.operaciones_view import (
    ddmmyyyy_a_iso as _ddmmyyyy_a_iso,
)
from core.postgres import get_pool

logger = logging.getLogger("api.operaciones")

router = APIRouter(prefix="/api/operaciones", tags=["Operaciones"])


def _split_excluir(excluir: str | None) -> tuple[str, ...] | None:
    """Param `excluir` (denominaciones separadas por '\\n') → tupla, o None si vacío.
    Tupla (no list) para que sea hashable: entra en la key de `@cached`."""
    if not excluir:
        return None
    vals = tuple(x for x in excluir.split("\n") if x.strip())
    return vals or None

@router.get("/flujo")
@cached(ttl=300)
def listar_flujo(
    contraparte: str | None = Query(None, description="Filtrar por contraparte"),
    moneda: str | None = Query(None, description="Filtrar por moneda (ARS/USD)"),
    segmento: str | None = Query(None, description="Filtrar por segmento (sesión de mercado)"),
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
):
    """Flujo de contrapartes — DIRECTO desde CashFlow.Operaciones (match por
    `cuenta` con CashFlow.Contrapartes). Reemplaza la copia intermedia MesaAPI:
    trae `tipoOperacion` y `cuenta` reales (que MesaAPI no tenía) y el `segmento`
    (sesión de mercado) se deriva del tipo_operacion. Excluye Futuros/Opciones."""
    # cuenta (id) → {contraparte, grupo}. SQL clientes.contrapartes. La cuenta es la CLAVE.
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id_cuenta, contraparte, segmento FROM contrapartes "
                    "WHERE id_cuenta IS NOT NULL AND id_cuenta <> ''")
        cp_map = {
            str(idc).strip(): {"contraparte": cp or "", "grupo": seg or ""}
            for idc, cp, seg in cur.fetchall() if idc not in (None, "")
        }
    # Excluye Futuros/Opciones y las Caución COLOCADORA (apertura+cierre): vienen
    # en pares y duplican/ensucian la vista. La caución tomadora se mantiene.
    # SQL operaciones.operaciones (cuenta→id_cuenta). NULL-safe en el NOT regex.
    conds = ["id_cuenta = ANY(%(ids)s)",
             "(tipo_operacion IS NULL OR tipo_operacion !~* 'Futuros|Opciones|colocadora')"]
    p: dict = {"ids": list(cp_map)}
    if moneda:
        conds.append("moneda = %(moneda)s")
        p["moneda"] = moneda
    if desde:
        conds.append("concertacion >= %(desde)s")
        p["desde"] = desde
    if hasta:
        conds.append("concertacion <= %(hasta)s")
        p["hasta"] = hasta

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT boleto, concertacion::text AS concertacion, tipo_operacion, "
            f"id_cuenta, denominacion, instrumento, bruto, moneda "
            f"FROM operaciones WHERE {' AND '.join(conds)} ORDER BY concertacion", p)
        rows = cur.fetchall()

    out = []
    for d in rows:
        cuenta = str(d.get("id_cuenta") or "").strip()
        cp = cp_map.get(cuenta, {})
        tipo = d.get("tipo_operacion") or ""
        seg = tipo.split()[0] if tipo else ""  # sesión de mercado (Concurrencia/SENEBI/…)
        if contraparte and cp.get("contraparte") != contraparte:
            continue
        if segmento and seg != segmento:
            continue
        out.append({
            "boleto":        d.get("boleto"),
            "concertacion":  d.get("concertacion"),
            "tipoOperacion": tipo,
            "cuenta":        cuenta,
            "denominacion":  d.get("denominacion"),
            "unidad":        d.get("instrumento"),
            "bruto":         float(d["bruto"]) if d.get("bruto") is not None else None,
            "segmento":      seg,
            "contraparte":   cp.get("contraparte"),
            "moneda":        d.get("moneda"),
        })
    return out


@router.get("/flujos")
@cached(ttl=300)
def listar_flujos(
    cuenta: str | None = Query(None, description="Filtrar por cuenta (formato [N] NOMBRE)"),
    unidad: str | None = Query(None, description="Filtrar por moneda (ARS/USD)"),
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
):
    # DIRECTO desde CashFlow.Movimientos (sin el espejo OperacionesAPI.FlujosAPI):
    # comprobante→boleto, total→bruto, fecha(dd/mm/yyyy)→concertacion(iso). El
    # rango de fechas y el orden se resuelven en Python porque la fecha está en
    # dd/mm/yyyy (no ordenable como string en Mongo). El set es chico (~12k docs,
    # casi siempre filtrado por cuenta).
    # Dual-run: flag MOVIMIENTOS_SQL=1 → operaciones.movimientos (SQL). El scope se
    # verifica acá ANTES de delegar (igual que el path Mongo lo hace en el if cuenta).
    if cuenta:
        verificar_cuenta_str(cuenta, scope)
    if _cf_sql.movimientos_sql_on():
        return _cf_sql.listar_flujos(cuenta=cuenta, unidad=unidad, desde=desde,
                                     hasta=hasta, scope=scope)
    db = get_db_cashflow()
    filtro: dict = {}
    if cuenta:
        filtro["cuenta"] = cuenta
    else:
        aplicar_scope_cuenta(filtro, scope)
    if unidad:
        filtro["unidad"] = unidad

    proj = {"_id": 0, "comprobante": 1, "cuenta": 1, "fecha": 1,
            "informacion": 1, "total": 1, "unidad": 1}
    out = []
    for d in db["Movimientos"].find(filtro, proj):
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
            "bruto":        d.get("total"),
            "unidad":       d.get("unidad"),
        })
    out.sort(key=lambda r: r["concertacion"] or "")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# OPERACIONES (vista MOVIMIENTOS) — lee CashFlow.Operaciones (fuente: API
# informes), enriquecida con moneda/mercado/operacion por
# scripts/enrich_operaciones.py. Reemplaza a NEGOCIO (consolidados) para
# operaciones de mercado. Importe = |bruto|, agrupado por `operacion`.
# ─────────────────────────────────────────────────────────────────────────────

# ARS/USD = volumen en moneda nativa. USD_DOL = volumen DOLARIZADO (ARS+USD
# convertidos a USD con el mep de cada boleto). USD_DOL vive SOLO en SQL (el
# rollup de Mongo no trae el bruto en USD) → se fuerza el motor SQL para esa opción.
# La serie del gráfico de aranceles se acota por defecto a esta ventana (cubre
# de sobra los botones 1W…1A). El aggregate sobre toda la historia escanea
# ~180k-300k docs (~1-1.5s, medido scripts/diag_perf_aranceles) y aggregation
# NO cubre el $group → el FETCH es inevitable. El botón ALL del front pide
# serie_full=True para traer la historia completa bajo demanda.
# El cierre de caución NO entra a ninguna sumatoria (la apertura ya cuenta el
# volumen). Se filtra por el campo materializado `es_cierre` (ver
# operaciones_informes._aplicar_enrich) → indexable, en vez de `$not /Cierre/`
# que forzaba un COLLSCAN. Requiere el backfill de es_cierre en los docs viejos.


@router.get("/ops/mercados")
@cached(ttl=300)
def ops_mercados():
    """Mercados distintos (para el selector). Cacheado."""
    return _ops_sql.ops_mercados()


@router.get("/ops/fechas")
@cached(ttl=120)
def ops_fechas():
    """Fechas con operaciones (desc) + count, para el selector de fecha."""
    return _ops_sql.ops_fechas()


@router.get("/ops/meta")
@cached(ttl=120)
def ops_meta(fecha: str = Query(..., description="YYYY-MM-DD")):
    """Metadata del día: # boletos + última ingesta + # mercados."""
    return _ops_sql.ops_meta(fecha=fecha)


@router.get("/ops/serie")
@cached(ttl=300)
def ops_serie(
    moneda: str = Query("ARS"),
    mercado: str | None = Query(None, description="Filtra por mercado (vacío = todos)"),
    operacion: str | None = Query(None, description="Filtra el gráfico a una operacion"),
    denominacion: str | None = Query(None, description="Filtra el gráfico a una denominacion"),
    cuenta: str | None = Query(None, description="Filtra a una cuenta (búsqueda)"),
    segmento: str | None = Query(None, description="Filtra por segmento (nivel_1)"),
    operador: str | None = Query(None, description="Filtra por operador (operador_email)"),
    excluir: str | None = Query(None, description="Cuentas a ocultar (denominaciones separadas por \\n)"),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
):
    """Serie diaria: Σ bruto por fecha (las barras del gráfico).

    Caso común (sin filtro de alta cardinalidad ni scope) → lee el rollup
    OpsSerieDiaria (jobs/ops_rollup) en vez de escanear toda Operaciones. Con
    denominacion/cuenta/scoped/operador → live (ya filtra por índice, no escanea)."""
    if moneda not in _OPS_MONEDAS:
        raise HTTPException(status_code=400, detail=f"moneda inválida: {moneda!r}")
    return _ops_sql.ops_serie(moneda=moneda, mercado=mercado, operacion=operacion,
                              denominacion=denominacion, cuenta=cuenta, segmento=segmento,
                              scope=scope, operador=operador, excluir=_split_excluir(excluir))


@router.get("/ops/resumen")
@cached(ttl=300)
def ops_resumen(
    moneda: str = Query("ARS"),
    mercado: str | None = Query(None),
    desde: str = Query(..., description="YYYY-MM-DD"),
    hasta: str = Query(..., description="YYYY-MM-DD"),
    operacion: str | None = Query(None, description="Selección de operacion (cross-filter)"),
    denominacion: str | None = Query(None, description="Selección de denominacion (cross-filter)"),
    instrumento: str | None = Query(None, description="Selección de instrumento/título (cross-filter)"),
    cuenta: str | None = Query(None, description="Filtra a una cuenta (búsqueda)"),
    segmento: str | None = Query(None, description="Filtra por segmento (nivel_1)"),
    operador: str | None = Query(None, description="Filtra por operador (operador_email)"),
    excluir: str | None = Query(None, description="Cuentas a ocultar (denominaciones separadas por \\n)"),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
):
    """Scope [desde,hasta]: Σ bruto por operacion, por denominacion (cuentas) y por
    instrumento (títulos).

    Cross-filter 3-way: cada tabla aplica las selecciones de las OTRAS dos
    (operacion ↔ denominacion ↔ instrumento). `cuenta`/`segmento`/`operador` filtran las tres.
    """
    if moneda not in _OPS_MONEDAS:
        raise HTTPException(status_code=400, detail=f"moneda inválida: {moneda!r}")
    return _ops_sql.ops_resumen(moneda=moneda, mercado=mercado, desde=desde, hasta=hasta,
                                operacion=operacion, denominacion=denominacion, cuenta=cuenta,
                                segmento=segmento, scope=scope, instrumento=instrumento,
                                operador=operador, excluir=_split_excluir(excluir))


@router.get("/ops/agro")
@cached(ttl=300)
def ops_agro(
    desde: str = Query(..., description="YYYY-MM-DD"),
    hasta: str = Query(..., description="YYYY-MM-DD"),
    agg: str = Query("MENSUAL", description="MENSUAL | DIARIO"),
    commodity: str | None = Query(None, description="SOJA/TRIGO/MAIZ (cross-filter)"),
    cuenta: str | None = Query(None, description="Filtra a una cuenta (denominación exacta)"),
    nivel5: str | None = Query(None, description="Filtra por nivel_5 de Clientes.Comitentes"),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
):
    """Futuros agropecuarios: Σ TONELADAS por periodo (mes/día) y commodity
    (SOJA/TRIGO/MAIZ). Lógica: tipo 'Futuros' sin 'Financieros', sin OTC;
    toneladas = |cantidad| × (10 si 'MIN' en instrumento, sino 100).

    Devuelve: `serie` (Σ por periodo en [desde,hasta], chart de la izq),
    `serie_cuenta` (idem SOLO de la cuenta elegida, chart de la der; vacía sin `cuenta`),
    `serie_share` (% mensual nuestro/mercado por commodity, tab "Share de
    mercado"; lee CashFlow.VolumenMercadoAgro), `totales` (Σ por commodity),
    `por_cuenta` y `por_instrumento` (acotados al rango [desde,hasta])."""
    return _ops_sql.ops_agro(desde=desde, hasta=hasta, agg=agg, commodity=commodity,
                             cuenta=cuenta, scope=scope, nivel5=nivel5)


@router.get("/ops/aranceles")
@cached(ttl=300)
def ops_aranceles(
    moneda: str = Query("ARS"),
    desde: str = Query(..., description="YYYY-MM-DD"),
    hasta: str = Query(..., description="YYYY-MM-DD"),
    agg: str = Query("MENSUAL", description="MENSUAL | DIARIO"),
    cuenta: str | None = Query(None, description="Cross-filter: denominación seleccionada"),
    instrumento: str | None = Query(None, description="Cross-filter: instrumento seleccionado"),
    sel_dim: str | None = Query(None, description="Cross-filter: valor seleccionado de la dim izquierda"),
    segmento: str | None = Query(None, description="Filtra por segmento (nivel_1)"),
    operador: str | None = Query(None, description="Filtra madre por operador (operador_email)"),
    dim: str = Query("nivel3", description="Dimensión de la tabla izquierda: nivel3 | operacion | operador"),
    serie_full: bool = Query(False, description="True = serie histórica completa (botón ALL); default ~18m"),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
):
    """Σ aranceles por periodo (gráfico), por nivel_3 (izq) y por cliente (der).

    La SERIE (histórica, antes escaneaba todo) sale del rollup OpsSerieDiaria
    cuando no hay scope ni operador; las TABLAS por_nivel3/por_cuenta son
    date-bounded → live (ya usan índice)."""
    if moneda not in _OPS_MONEDAS:
        raise HTTPException(status_code=400, detail=f"moneda inválida: {moneda!r}")
    return _ops_sql.ops_aranceles(moneda=moneda, desde=desde, hasta=hasta, agg=agg,
                                  cuenta=cuenta, instrumento=instrumento, sel_dim=sel_dim,
                                  segmento=segmento, dim=dim, serie_full=serie_full,
                                  scope=scope, operador=operador)


@router.get("/ops/cuentas-list")
@cached(ttl=3600)
def ops_cuentas_list(scope: tuple[str, ...] | None = Depends(scope_cuentas)):
    """Denominaciones (+ cuenta) distintas — fuente del buscador."""
    return _ops_sql.ops_cuentas_list(scope=scope)


@router.get("/ops/segmentos")
@cached(ttl=600)
def ops_segmentos():
    """Segmentos (nivel_1) distintos, para el filtro."""
    return _ops_sql.ops_segmentos()


@router.get("/ops/niveles5")
@cached(ttl=600)
def ops_niveles5():
    """Valores distintos de nivel_5 (clientes.comitentes SQL), para el filtro AGRO."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT nivel_5 FROM comitentes "
                    "WHERE nivel_5 IS NOT NULL AND nivel_5 <> ''")
        vals = [r[0] for r in cur.fetchall()]
    return {"niveles5": sorted(str(v) for v in vals if v)}


# ── COMERCIAL (lente por operador, estilo NEGOCIO) ───────────────────────────
# Vista nueva en OPERACIONES. Lógica en api/services/comercial.py.

def _com_motor(_engine: str | None):
    """Comercial es SIEMPRE SQL (comercial_sql). El path Mongo (comercial.py) leía
    Comitentes/NegocioMovimientos/ComercialCache — todas DROPEADAS → era código muerto
    (decommission 2026-06-23). El `_engine` queda sin efecto (compat)."""
    return _com_sql


@router.get("/comercial/operadores")
def comercial_operadores(_engine: str | None = Query(None, include_in_schema=False)) -> list[dict]:
    """Operadores para el selector (email, nombre, # cuentas)."""
    return _com_motor(_engine).listar_operadores_comercial()


@router.get("/comercial/dimensiones")
def comercial_dimensiones(_engine: str | None = Query(None, include_in_schema=False)) -> dict:
    """Combos (operador, nivel_1, nivel_3) de cuentas activas → pueblan y cruzan
    los 3 filtros madre de la vista OPERADORES."""
    return _com_motor(_engine).dimensiones_comercial()


@router.get("/comercial/operador")
def comercial_operador(
    operador: str = Query(..., description="operador_email"),
    moneda: str = Query("ARS"),
    nivel_1: str | None = Query(None, description="filtro madre nivel_1 (cruza con operador/nivel_3)"),
    nivel_3: str | None = Query(None, description="filtro madre nivel_3 (cruza con operador/nivel_1)"),
    referido: str | None = Query(None, description="filtro madre referido (cruza con los demás)"),
    _engine: str | None = Query(None, include_in_schema=False),
) -> dict:
    """Resumen (KPIs) + clientes (tabla + ficha) del operador, en una pasada."""
    return _com_motor(_engine).operador_comercial(
        operador=operador, moneda=moneda, nivel_1=nivel_1, nivel_3=nivel_3, referido=referido)


@router.get("/comercial/serie")
def comercial_serie(
    operador: str = Query(..., description="operador_email"),
    metric: str = Query("volumen", description="volumen | aum"),
    moneda: str = Query("ARS"),
    id_cuenta: str | None = Query(None, description="scope a una sola cuenta (interactivo)"),
    nivel_1: str | None = Query(None, description="filtro madre nivel_1"),
    nivel_3: str | None = Query(None, description="filtro madre nivel_3"),
    referido: str | None = Query(None, description="filtro madre referido"),
    _engine: str | None = Query(None, include_in_schema=False),
) -> dict:
    """Serie para el gráfico. Sin id_cuenta → operador; con id_cuenta → cliente."""
    return _com_motor(_engine).serie_comercial(
        operador=operador, metric=metric, moneda=moneda, id_cuenta=id_cuenta,
        nivel_1=nivel_1, nivel_3=nivel_3, referido=referido)


@router.get("/comercial/clientes-por-fecha")
def comercial_clientes_por_fecha(
    operador: str = Query(..., description="operador_email"),
    desde: str = Query(..., description="ISO YYYY-MM-DD (inicio del período del bar)"),
    hasta: str = Query(..., description="ISO YYYY-MM-DD (fin del período del bar)"),
    moneda: str = Query("ARS"),
    nivel_1: str | None = Query(None, description="filtro madre nivel_1"),
    nivel_3: str | None = Query(None, description="filtro madre nivel_3"),
    referido: str | None = Query(None, description="filtro madre referido"),
    _engine: str | None = Query(None, include_in_schema=False),
) -> dict:
    """Clientes que operaron en el rango (click en una barra del chart de volumen)."""
    return _com_motor(_engine).clientes_por_fecha(
        operador=operador, desde=desde, hasta=hasta, moneda=moneda,
        nivel_1=nivel_1, nivel_3=nivel_3, referido=referido)


@router.get("/comercial/portafolio")
def comercial_portafolio(
    id_cuenta: str = Query(..., description="id de la cuenta comitente"),
    _engine: str | None = Query(None, include_in_schema=False),
) -> dict:
    """Tenencia del cliente (posiciones de AuM, último snapshot)."""
    return _com_motor(_engine).portafolio_cliente(id_cuenta=id_cuenta)


@router.get("/comercial/operaciones")
def comercial_operaciones(
    id_cuenta: str = Query(..., description="id de la cuenta comitente"),
    limite: int = Query(300, ge=1, le=1000),
    _engine: str | None = Query(None, include_in_schema=False),
) -> dict:
    """Operaciones recientes del cliente (boletos operativos, fecha desc)."""
    return _com_motor(_engine).operaciones_cliente(id_cuenta=id_cuenta, limite=limite)


@router.get("/comercial/analisis")
def comercial_analisis(
    operador: str = Query(..., description="operador_email"),
    moneda: str = Query("ARS", description="ARS | USD"),
    nivel_1: str | None = Query(None, description="filtro madre nivel_1"),
    nivel_3: str | None = Query(None, description="filtro madre nivel_3"),
    referido: str | None = Query(None, description="filtro madre referido"),
    fecha: str | None = Query(None, description="foto al día X (ISO YYYY-MM-DD). None = hoy"),
    _engine: str | None = Query(None, include_in_schema=False),
) -> dict:
    """Dataset de la vista ANÁLISIS: clientes del operador con estado comercial,
    AuM, última op y niveles de segmentación (estado / churn / distribución).
    `fecha` = modo 'foto al día X': todo se calcula como estaba esa fecha (estado/activas/
    AuM/cuentas por nivel). El cupo queda en valor actual (no histórico aún)."""
    return _com_motor(_engine).analisis_comercial(
        operador=operador, moneda=moneda, nivel_1=nivel_1, nivel_3=nivel_3, referido=referido,
        fecha=fecha)


@router.get("/comercial/cobros-futuros")
def comercial_cobros_futuros(
    operador: str = Query(..., description="operador_email o '__todos__'"),
    nivel_1: str | None = Query(None, description="filtro madre nivel_1"),
    nivel_3: str | None = Query(None, description="filtro madre nivel_3"),
    referido: str | None = Query(None, description="filtro madre referido"),
) -> dict:
    """Cobros futuros (acreencias) del scope: serie diaria acumulable + totales por
    cliente. Mongo-only (CashFlow.Acreencias no tiene espejo SQL)."""
    return _com.cobros_futuros(
        operador=operador, nivel_1=nivel_1, nivel_3=nivel_3, referido=referido)


@router.get("/comercial/cobros-futuros/cliente")
def comercial_cobros_futuros_cliente(
    id_cuenta: str = Query(..., description="id de la cuenta comitente"),
) -> dict:
    """Detalle de cobros futuros de un cliente: serie acumulable + títulos que cobra."""
    return _com.cobros_futuros_cliente(id_cuenta=id_cuenta)


@router.get("/comercial/referido-clientes")
def comercial_referido_clientes(
    referido: str = Query(..., description="nombre del referido (empresa referidora)"),
    moneda: str = Query("ARS"),
) -> dict:
    """Vista REFERIDOS: cuentas referidas por una empresa con AuM + volumen (mes/año)
    + arancel (mes/total). Mongo-only (reusa los helpers del tablero comercial)."""
    return _com.referido_clientes(referido=referido, moneda=moneda)


@router.get("/comercial/referido-fci")
def comercial_referido_fci(
    referido: str = Query(..., description="nombre del referido (empresa referidora)"),
    desde: str = Query(..., description="YYYY-MM-DD inclusive"),
    hasta: str = Query(..., description="YYYY-MM-DD inclusive"),
    moneda: str = Query("ARS"),
) -> dict:
    """Vista REFERIDOS — tabla FCI: dinero en cartera FCI (saldo promedio diario
    del rango) de las cuentas del referido, abierto por sociedad gerente. Base
    para la comisión de la coop. Mongo-only (reusa helpers del tablero comercial)."""
    return _com.referido_fci(referido=referido, desde=desde, hasta=hasta, moneda=moneda)


# ── INFORME (global, transversal a toda la mesa — no por operador) ───────────

@router.get("/comercial/informe")
def comercial_informe(
    moneda: str = Query("ARS", description="ARS | USD"),
    _engine: str | None = Query(None, include_in_schema=False),
) -> dict:
    """Tablas 2 y 3 del Informe: volumen + aranceles por comercial (ranking) y
    aranceles por segmento. Global (toda la mesa)."""
    return _com_motor(_engine).informe_comercial(moneda=moneda)


@router.get("/comercial/informe-segmento")
def comercial_informe_segmento(
    hasta: str | None = Query(None, description="mes YYYY-MM (default actual); acumulado a fin de mes"),
    operador: str | None = Query(None, description="opcional: solo cuentas de ese comercial"),
    _engine: str | None = Query(None, include_in_schema=False),
) -> dict:
    """Tabla 1 del Informe: # cuentas por segmento (nivel_1), acumulado a fin del
    mes `hasta` por fecha de alta. `operador` opcional re-scopea a ese comercial."""
    return _com_motor(_engine).informe_cuentas_por_segmento(hasta=hasta, operador=operador)


@router.get("/comercial/informe-aranceles-segmento")
def comercial_informe_aranceles_segmento(
    operador: str = Query(..., description="operador_email a desglosar"),
    moneda: str = Query("ARS", description="ARS | USD"),
    _engine: str | None = Query(None, include_in_schema=False),
) -> dict:
    """Q3 re-scopeada a un comercial: aranceles + ticket por segmento, solo de
    sus cuentas."""
    return _com_motor(_engine).informe_aranceles_segmento(operador=operador, moneda=moneda)


@router.get("/comercial/informe-segmento-detalle")
def comercial_informe_segmento_detalle(
    segmento: str = Query("todos", description="nivel_1 a desglosar; 'todos' = todos los segmentos"),
    operador: str | None = Query(None, description="opcional: solo cuentas de ese comercial"),
    moneda: str = Query("ARS", description="ARS | USD"),
    _engine: str | None = Query(None, include_in_schema=False),
) -> dict:
    """Detalle de un segmento (Q4 dinámica): clientes con su arancel +
    operaciones (boletos con arancel) que lo generaron. `segmento='todos'` →
    todos los segmentos (vista por defecto). `operador` opcional."""
    return _com_motor(_engine).informe_segmento_detalle(
        segmento=segmento, operador=operador, moneda=moneda)
