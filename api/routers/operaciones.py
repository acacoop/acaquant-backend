"""Router Operaciones: endpoints para MesaAPI (flujo contrapartes), FlujosAPI
(movimientos) y NegocioMovimientos (vista de negocio del día)."""
import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel

from api.auth import get_user_email, require_control_comercial, require_no_invitado
from api.cache import cached
from api.services import cashflow_sql as _cf_sql
from api.services import comercial as _com
from api.services import comercial_sql as _com_sql
from api.services import control_comercial_sql as _cc
from api.services import financiamiento as _fin
from api.services import financiamiento_calc as _fin_calc
from api.services import operaciones_sql as _ops_sql
from api.services._grupos_scope import (
    scope_cuentas,
    verificar_cuenta_str,
    verificar_id_cuenta,
    verificar_id_cuenta_opcional,
)
from api.services.operaciones_view import (
    OPS_MONEDAS as _OPS_MONEDAS,
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
def listar_flujo(
    contraparte: str | None = Query(None, description="Filtrar por contraparte"),
    moneda: str | None = Query(None, description="Filtrar por moneda (ARS/USD)"),
    segmento: str | None = Query(None, description="Filtrar por segmento (sesión de mercado)"),
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
):
    """Flujo de contrapartes — operaciones individuales (drill-down de la vista
    CONTRAPARTES). Lógica + cache en operaciones_sql.flujo_operaciones."""
    return _ops_sql.flujo_operaciones(
        contraparte=contraparte, moneda=moneda, segmento=segmento,
        desde=desde, hasta=hasta,
    )


@router.get("/flujo/resumen")
@cached(ttl=300)
def flujo_resumen(
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
):
    """Resumen del flujo de contrapartes agregado por (día, contraparte, moneda) +
    grupos/monedas disponibles. Lo consume la vista CONTRAPARTES del frontend en
    lugar de bajar 2 años de operaciones crudas (el drill-down por día usa /flujo)."""
    return _ops_sql.flujo_resumen(desde=desde, hasta=hasta)


@router.get("/flujos")
@cached(ttl=300)
def listar_flujos(
    cuenta: str | None = Query(None, description="Filtrar por cuenta (formato [N] NOMBRE)"),
    unidad: str | None = Query(None, description="Filtrar por moneda (ARS/USD)"),
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
):
    # SQL-NATIVE: CashFlow.Movimientos (Mongo) fue migrada → dropeada. Lee SIEMPRE de
    # operaciones.movimientos (SQL) vía cashflow_sql.listar_flujos: comprobante→boleto,
    # total→bruto, fecha(dd/mm/yyyy)→concertacion(iso). El rango de fechas y el orden se
    # resuelven en Python (la fecha está cruda en dd/mm/yyyy, no ordenable). El scope se
    # verifica acá ANTES de delegar (igual que hacía el path Mongo en el if cuenta).
    if cuenta:
        verificar_cuenta_str(cuenta, scope)
    return _cf_sql.listar_flujos(cuenta=cuenta, unidad=unidad, desde=desde,
                                 hasta=hasta, scope=scope)


@router.get("/flujos/resumen")
@cached(ttl=300)
def flujos_resumen(
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
):
    """Resumen de movimientos agregado por (día, cuenta, unidad) con entradas y
    salidas separadas. Lo consume la vista CASHFLOW en vez de bajar los
    movimientos crudos de 2 años."""
    return _cf_sql.flujos_resumen(desde=desde, hasta=hasta, scope=scope)


# ─────────────────────────────────────────────────────────────────────────────
# OPERACIONES (vista MOVIMIENTOS) — lee operaciones.operaciones (fuente: API
# informes), enriquecida con moneda/mercado/operacion en la ingesta
# (jobs.operaciones_informes). Reemplaza a NEGOCIO (consolidados) para
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


# ── CATÁLOGOS DE LOS SELECTORES ──────────────────────────────────────────────
# Los cuatro son un `SELECT DISTINCT` sobre `operaciones` (~490k filas) para
# devolver entre 6 y 11 valores: la query cuesta un scan completo y el payload
# pesa 0,1 KB. Medido 2026-08-19 (`scripts/diag_peso_operaciones`), con TTL de
# 300s los cuatro juntos consumían **96s en 7 días** — más que `/ops/serie` +
# `/ops/resumen` sumados (100s), que son los que traen los datos de verdad.
#
# La causa es que la vista se abre disperso (~106 aperturas/semana): con 300s
# el cache casi nunca pega y cada apertura paga los cuatro scans. `_TTL_TAXO`
# los lleva a 6h porque son TAXONOMÍAS — mercado, segmento, nivel_3 y cartera
# no cambian salvo que aparezca una categoría NUEVA, y lo peor que puede pasar
# es que esa categoría tarde hasta 6h en ofrecerse en el desplegable (los datos
# NO se ven afectados: el filtro solo acota lo que ya se muestra).
#
# `/ops/fechas` y `/ops/cuentas-list` quedan AFUERA a propósito: la primera es
# el ancla del botón ÚLTIMA (un TTL largo retrasaría el día nuevo que acaba de
# ingestar el job) y la segunda es el padrón, donde un alta de cliente sí se
# nota. Ésas se atacan con otra cosa, no con TTL.
_TTL_TAXO = 6 * 3600


@router.get("/ops/mercados")
@cached(ttl=_TTL_TAXO)
def ops_mercados():
    """Mercados distintos (para el selector). Cacheado."""
    return _ops_sql.ops_mercados()


@router.get("/ops/carteras")
@cached(ttl=_TTL_TAXO)
def ops_carteras():
    """Carteras del catálogo de títulos (HD, DL, ARS, FCI…) que aparecen en
    los boletos — selector del filtro de cartera. Cacheado."""
    return _ops_sql.ops_carteras()


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


# ── Intraday (monitor FIFO de renta variable, CSV ad-hoc) ─────────────────────
class _IntradayCSV(BaseModel):
    csv: str
    archivo: str | None = None


@router.post("/intraday/analizar")
def intraday_analizar(payload: _IntradayCSV):
    """Recibe el CSV de boletos del día (formato ROFEX/Aunesa) y devuelve la
    consolidación FIFO por (cuenta, especie): posición neta, precio ponderado,
    PnL realizado/no-realizado (mark live), intereses + IVA → PnL neto.

    Efímero: NO persiste. El cálculo vive en api/services/intraday.py."""
    from api.services import intraday as _intra

    if not (payload.csv or "").strip():
        raise HTTPException(status_code=400, detail="CSV vacío.")
    try:
        return _intra.analizar(payload.csv, archivo=payload.archivo)
    except _intra.IntradayError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


class _IntradayTrade(BaseModel):
    hora: str | None = None
    lado: str
    precio: float
    cantidad: float
    monto: float


class _IntradayPos(BaseModel):
    cuenta: str
    especie: str
    moneda: str = ""
    trades: list[_IntradayTrade] = []


class _IntradayRecalc(BaseModel):
    posiciones: list[_IntradayPos] = []


@router.post("/intraday/recalcular")
def intraday_recalcular(payload: _IntradayRecalc):
    """Re-FIFO con SOLO los trades incluidos (filtrado por-trade del frontend).
    Devuelve {posiciones, totales} con el mismo shape que /analizar."""
    from api.services import intraday as _intra

    items = [p.model_dump() for p in payload.posiciones]
    return _intra.recalcular(items=items)


class _IntradayMarks(BaseModel):
    especies: list[str] = []


@router.post("/intraday/marks")
def intraday_marks(payload: _IntradayMarks):
    """Marks live frescos por especie, para el botón "Actualizar cotizaciones"
    del monitor intradía (refresca precios sin re-subir el CSV).
    Devuelve {marks: {especie: {last, updated_at}}} (sólo las que tienen feed)."""
    from api.services import intraday as _intra

    return {"marks": _intra.marks(especies=payload.especies)}


@router.get("/ops/serie")
@cached(ttl=300)
def ops_serie(
    moneda: str = Query("ARS"),
    mercado: str | None = Query(None, description="Filtra por mercado (vacío = todos)"),
    operacion: str | None = Query(None, description="Filtra el gráfico a una operacion"),
    denominacion: str | None = Query(None, description="Filtra el gráfico a una denominacion"),
    cuenta: str | None = Query(None, description="Filtra a una cuenta (búsqueda)"),
    segmento: str | None = Query(None, description="Filtra por segmento (nivel_1)"),
    nivel_3: str | None = Query(None, description="Filtra por nivel_3 (segmento del boleto)"),
    aca_valores: str | None = Query(None, description="'solo' | 'sin' cuentas del set ACA VALORES"),
    operador: str | None = Query(None, description="Filtra por operador (operador_email)"),
    cartera: str | None = Query(None, description="Filtra por cartera del título (assets)"),
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
                              scope=scope, operador=operador, excluir=_split_excluir(excluir),
                              nivel_3=nivel_3, aca_valores=aca_valores, cartera=cartera)


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
    nivel_3: str | None = Query(None, description="Filtra por nivel_3 (segmento del boleto)"),
    aca_valores: str | None = Query(None, description="'solo' | 'sin' cuentas del set ACA VALORES"),
    operador: str | None = Query(None, description="Filtra por operador (operador_email)"),
    cartera: str | None = Query(None, description="Filtra por cartera del título (assets)"),
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
                                operador=operador, excluir=_split_excluir(excluir),
                                nivel_3=nivel_3, aca_valores=aca_valores, cartera=cartera)


@router.get("/ops/agro")
@cached(ttl=300)
def ops_agro(
    desde: str = Query(..., description="YYYY-MM-DD"),
    hasta: str = Query(..., description="YYYY-MM-DD"),
    agg: str = Query("MENSUAL", description="MENSUAL | DIARIO"),
    commodity: str | None = Query(None, description="SOJA/TRIGO/MAIZ (cross-filter)"),
    cuenta: str | None = Query(None, description="Filtra a una cuenta (denominación exacta)"),
    nivel5: str | None = Query(None, description="Filtra por nivel_5 de Clientes.Comitentes"),
    tipo: str | None = Query(None, description="FUTURO | OPCION (filtra volumen; vacío = ambos)"),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
):
    """Futuros + opciones agropecuarios: Σ TONELADAS por periodo (mes/día) y
    commodity (SOJA/TRIGO/MAIZ). Lógica: tipo 'Futuros'/'Opciones Agropecuarios'
    sin 'Financieros', sin OTC; toneladas = |cantidad| × (10 si 'MIN' en
    instrumento, sino 100). El filtro `tipo` (FUTURO/OPCION) acota el volumen.

    Devuelve: `serie` (Σ por periodo en [desde,hasta], chart de la izq),
    `serie_cuenta` (idem SOLO de la cuenta elegida, chart de la der; vacía sin `cuenta`),
    `serie_share` (% mensual nuestro/mercado por commodity — SOLO futuros),
    `totales` (Σ por commodity), `totales_tipo` + `serie_tipo` (desglose
    FUTURO/OPCION para la tab por tipo), `por_cuenta` y `por_instrumento`."""
    return _ops_sql.ops_agro(desde=desde, hasta=hasta, agg=agg, commodity=commodity,
                             cuenta=cuenta, scope=scope, nivel5=nivel5, tipo=tipo)


@router.get("/ops/dolar-futuro")
@cached(ttl=300)
def ops_dolar_futuro(
    desde: str = Query(..., description="YYYY-MM-DD"),
    hasta: str = Query(..., description="YYYY-MM-DD"),
    agg: str = Query("MENSUAL", description="MENSUAL | DIARIO"),
    tipo: str | None = Query(None, description="Compra | Venta (cross-filter)"),
    cuenta: str | None = Query(None, description="Filtra a una cuenta (denominación exacta)"),
    instrumento: str | None = Query(None, description="Filtra a un vencimiento (instrumento exacto)"),
    nivel5: str | None = Query(None, description="Filtra por nivel_5 de Clientes.Comitentes"),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
):
    """Dólar futuro (DLR, mercado A3): NOCIONAL en USD (1 contrato = USD 1000),
    arancel (ARS) y boletos. Devuelve `por_tipo` (Compra/Venta), `por_cuenta`,
    `por_instrumento` (vencimientos), `serie` (nocional por periodo, split
    Compra/Venta) y `total` (header) — todo acotado a [desde,hasta] con
    cross-filter 3-way por tipo/cuenta/instrumento."""
    return _ops_sql.ops_dolar_futuro(desde=desde, hasta=hasta, agg=agg, tipo=tipo,
                                     cuenta=cuenta, instrumento=instrumento,
                                     scope=scope, nivel5=nivel5)


@router.get("/ops/diferencias-diarias")
@cached(ttl=300)
def ops_diferencias_diarias(
    desde: str = Query(..., description="YYYY-MM-DD"),
    hasta: str = Query(..., description="YYYY-MM-DD"),
    moneda: str = Query("USDL", description="USDL | ARS (no se pueden sumar juntas)"),
    producto: str | None = Query(None, description="Prefijo del instrumento (cross-filter)"),
    cuenta: str | None = Query(None, description="Cuenta exacta (cross-filter)"),
    instrumento: str | None = Query(None, description="Instrumento exacto (cross-filter)"),
    nivel5: str | None = Query(None, description="Filtra por nivel_5 de Clientes.Comitentes"),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
):
    """Diferencias diarias de futuros (liquidación mark-to-market) desde
    operaciones.negocio_movimientos. La métrica es `importe` (± ARS o USDL —
    filtradas por `moneda`, nunca sumadas juntas); NO hay tipo ni instrumento
    nativos (el instrumento se parsea del texto `informacion`). Devuelve
    `por_producto`, `por_cuenta`, `por_instrumento`, `serie` (Σ importe por día)
    y `total` — todo acotado a [desde,hasta] con cross-filter 3-way."""
    return _ops_sql.ops_diferencias_diarias(desde=desde, hasta=hasta, moneda=moneda,
                                             producto=producto, cuenta=cuenta,
                                             instrumento=instrumento, scope=scope, nivel5=nivel5)


@router.get("/ops/diferencias-fechas")
@cached(ttl=300)
def ops_diferencias_fechas(
    moneda: str = Query("USDL", description="USDL | ARS"),
):
    """Fechas (desc) con Diferencias Diarias para la moneda dada — el universo
    de fechas PROPIO de esta vista (no el de operaciones.operaciones). Ancla los
    botones ULTIMA/SEMANA/MES sobre fechas que realmente tienen datos."""
    return _ops_sql.ops_diferencias_fechas(moneda=moneda)


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
    dim: str = Query("nivel3", description="Dimensión de la tabla izquierda: nivel3 | operacion | mercado | operador"),
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
@cached(ttl=_TTL_TAXO)
def ops_segmentos():
    """Segmentos (nivel_1) distintos, para el filtro."""
    return _ops_sql.ops_segmentos()


@router.get("/ops/niveles3")
@cached(ttl=_TTL_TAXO)
def ops_niveles3():
    """Valores distintos de nivel_3 (segmento del boleto), para el filtro OPERACIONES."""
    return _ops_sql.ops_niveles3()


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
# Vista nueva en OPERACIONES. Lógica en api/services/comercial_sql.py.

@router.get("/comercial/operadores")
def comercial_operadores() -> list[dict]:
    """Operadores para el selector (email, nombre, # cuentas)."""
    return _com_sql.listar_operadores_comercial()


@router.get("/comercial/dimensiones")
def comercial_dimensiones() -> dict:
    """Combos (operador, nivel_1, nivel_3) de cuentas activas → pueblan y cruzan
    los 3 filtros madre de la vista OPERADORES."""
    return _com_sql.dimensiones_comercial()


@router.get("/comercial/operador")
def comercial_operador(
    operador: list[str] = Query(default=[], description="operador(es) — multi. Vacío = todos"),
    moneda: str = Query("ARS"),
    nivel_1: list[str] | None = Query(None, description="filtro madre nivel_1 (multi; cruza con los demás)"),
    nivel_2: list[str] | None = Query(None, description="filtro madre nivel_2 (multi)"),
    nivel_3: list[str] | None = Query(None, description="filtro madre nivel_3 (multi)"),
    nivel_4: list[str] | None = Query(None, description="filtro madre nivel_4 (multi)"),
    nivel_5: list[str] | None = Query(None, description="filtro madre nivel_5 (multi)"),
    referido: list[str] | None = Query(None, description="filtro madre referido (multi)"),
    division: list[str] | None = Query(None, description="filtro madre division (multi)"),
    fecha: str | None = Query(None, description="corte = HASTA (ISO). None = hoy"),
    desde: str | None = Query(None, description="inicio del período (ISO). Si viene, MES = [desde, fecha]"),
) -> dict:
    """Resumen (KPIs) + clientes (tabla + ficha) del operador, en una pasada."""
    return _com_sql.operador_comercial(
        operador=operador, moneda=moneda, nivel_1=nivel_1, nivel_2=nivel_2, nivel_3=nivel_3,
        referido=referido, nivel_4=nivel_4, nivel_5=nivel_5, fecha=fecha, desde=desde,
        division=division)


@router.get("/comercial/serie", dependencies=[Depends(verificar_id_cuenta_opcional)])
def comercial_serie(
    operador: list[str] = Query(default=[], description="operador(es) — multi. Vacío = todos"),
    metric: str = Query("volumen", description="volumen | aum"),
    moneda: str = Query("ARS"),
    id_cuenta: str | None = Query(None, description="scope a una sola cuenta (interactivo)"),
    nivel_1: list[str] | None = Query(None, description="filtro madre nivel_1 (multi)"),
    nivel_2: list[str] | None = Query(None, description="filtro madre nivel_2 (multi)"),
    nivel_3: list[str] | None = Query(None, description="filtro madre nivel_3 (multi)"),
    nivel_4: list[str] | None = Query(None, description="filtro madre nivel_4 (multi)"),
    nivel_5: list[str] | None = Query(None, description="filtro madre nivel_5 (multi)"),
    referido: list[str] | None = Query(None, description="filtro madre referido (multi)"),
    division: list[str] | None = Query(None, description="filtro madre division (multi)"),
) -> dict:
    """Serie para el gráfico. Sin id_cuenta → operador; con id_cuenta → cliente."""
    return _com_sql.serie_comercial(
        operador=operador, metric=metric, moneda=moneda, id_cuenta=id_cuenta,
        nivel_1=nivel_1, nivel_2=nivel_2, nivel_3=nivel_3, referido=referido, nivel_4=nivel_4, nivel_5=nivel_5,
        division=division)


@router.get("/comercial/clientes-por-fecha")
def comercial_clientes_por_fecha(
    operador: list[str] = Query(default=[], description="operador(es) — multi. Vacío = todos"),
    desde: str = Query(..., description="ISO YYYY-MM-DD (inicio del período del bar)"),
    hasta: str = Query(..., description="ISO YYYY-MM-DD (fin del período del bar)"),
    moneda: str = Query("ARS"),
    nivel_1: list[str] | None = Query(None, description="filtro madre nivel_1 (multi)"),
    nivel_2: list[str] | None = Query(None, description="filtro madre nivel_2 (multi)"),
    nivel_3: list[str] | None = Query(None, description="filtro madre nivel_3 (multi)"),
    nivel_4: list[str] | None = Query(None, description="filtro madre nivel_4 (multi)"),
    nivel_5: list[str] | None = Query(None, description="filtro madre nivel_5 (multi)"),
    referido: list[str] | None = Query(None, description="filtro madre referido (multi)"),
    division: list[str] | None = Query(None, description="filtro madre division (multi)"),
) -> dict:
    """Clientes que operaron en el rango (click en una barra del chart de volumen)."""
    return _com_sql.clientes_por_fecha(
        operador=operador, desde=desde, hasta=hasta, moneda=moneda,
        nivel_1=nivel_1, nivel_2=nivel_2, nivel_3=nivel_3, referido=referido, nivel_4=nivel_4, nivel_5=nivel_5,
        division=division)


@router.get("/comercial/portafolio", dependencies=[Depends(verificar_id_cuenta)])
def comercial_portafolio(
    id_cuenta: str = Query(..., description="id de la cuenta comitente"),
) -> dict:
    """Tenencia del cliente (posiciones de AuM, último snapshot).

    `verificar_id_cuenta` → 403 si la cuenta está fuera del grupo del usuario.
    No-op para admin y para quien no está en ningún grupo (scope None), así
    que no cambia nada hasta que se pueblen los grupos."""
    return _com_sql.portafolio_cliente(id_cuenta=id_cuenta)


@router.get("/comercial/operaciones", dependencies=[Depends(verificar_id_cuenta)])
def comercial_operaciones(
    id_cuenta: str = Query(..., description="id de la cuenta comitente"),
    limite: int = Query(300, ge=1, le=1000),
) -> dict:
    """Operaciones recientes del cliente (boletos operativos, fecha desc)."""
    return _com_sql.operaciones_cliente(id_cuenta=id_cuenta, limite=limite)


@router.get("/comercial/analisis")
def comercial_analisis(
    operador: list[str] = Query(default=[], description="operador(es) — multi. Vacío = todos"),
    moneda: str = Query("ARS", description="ARS | USD"),
    nivel_1: list[str] | None = Query(None, description="filtro madre nivel_1 (multi)"),
    nivel_2: list[str] | None = Query(None, description="filtro madre nivel_2 (multi)"),
    nivel_3: list[str] | None = Query(None, description="filtro madre nivel_3 (multi)"),
    nivel_4: list[str] | None = Query(None, description="filtro madre nivel_4 (multi)"),
    nivel_5: list[str] | None = Query(None, description="filtro madre nivel_5 (multi)"),
    referido: list[str] | None = Query(None, description="filtro madre referido (multi)"),
    division: list[str] | None = Query(None, description="filtro madre division (multi)"),
    fecha: str | None = Query(None, description="foto al día X = HASTA (ISO). None = hoy"),
    desde: str | None = Query(None, description="inicio del período (ISO). Si viene, opero_mtd = operó en [desde, fecha]"),
) -> dict:
    """Dataset de la vista ANÁLISIS: clientes del operador con estado comercial,
    AuM, última op y niveles de segmentación (estado / churn / distribución).
    `fecha` = modo 'foto al día X': todo se calcula como estaba esa fecha (estado/activas/
    AuM/cuentas por nivel). El cupo queda en valor actual (no histórico aún)."""
    return _com_sql.analisis_comercial(
        operador=operador, moneda=moneda, nivel_1=nivel_1, nivel_2=nivel_2, nivel_3=nivel_3,
        referido=referido, nivel_4=nivel_4, nivel_5=nivel_5, fecha=fecha, desde=desde,
        division=division)


@router.get("/comercial/analisis/detalle", dependencies=[Depends(verificar_id_cuenta)])
def comercial_analisis_detalle(
    id_cuenta: str = Query(..., description="id de la cuenta comitente (fila clickeada)"),
    fecha: str | None = Query(None, description="mismo corte que la tabla (ISO). None = hoy"),
    limite: int = Query(25, ge=1, le=200, description="boletos del historial reciente"),
) -> dict:
    """Auditoría de una fila de ESTADO COMERCIAL: de QUÉ boleto salen los días sin operar.

    Devuelve la última operación que cuenta (con todos los boletos de ese día), el
    historial reciente y los boletos que NO cuentan con su motivo (anulados,
    posteriores al corte). No recalcula nada: usa el mismo predicado que la tabla."""
    return _com_sql.detalle_ultima_op(id_cuenta=id_cuenta, fecha=fecha, limite=limite)


@router.get("/comercial/cobros-futuros")
def comercial_cobros_futuros(
    operador: str = Query(..., description="operador_email o '__todos__'"),
    nivel_1: str | None = Query(None, description="filtro madre nivel_1"),
    nivel_2: str | None = Query(None, description="filtro madre nivel_2"),
    nivel_3: str | None = Query(None, description="filtro madre nivel_3"),
    referido: str | None = Query(None, description="filtro madre referido"),
    desde: str | None = Query(None, description="fecha de cobro DESDE (ISO YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="fecha de cobro HASTA (ISO YYYY-MM-DD)"),
) -> dict:
    """Cobros futuros (acreencias) del scope: serie diaria acumulable + totales por
    cliente. SQL-native (operaciones.acreencias + clientes.comitentes). `desde`/`hasta`
    acotan por fecha de cobro (igual que back-office acreencias)."""
    return _com.cobros_futuros(
        operador=operador, nivel_1=nivel_1, nivel_2=nivel_2, nivel_3=nivel_3,
        referido=referido, desde=desde, hasta=hasta)


@router.get("/comercial/cobros-futuros/cliente", dependencies=[Depends(verificar_id_cuenta)])
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
    + arancel (mes/total). SQL-native (comitentes/negocio_movimientos/tenencia/operaciones)."""
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
    para la comisión de la coop. SQL-native (portafolio.tenencia + comitentes)."""
    return _com.referido_fci(referido=referido, desde=desde, hasta=hasta, moneda=moneda)


# ── INFORME (global, transversal a toda la mesa — no por operador) ───────────

@router.get("/comercial/informe")
@cached(ttl=300)
def comercial_informe(
    moneda: str = Query("ARS", description="ARS | USD"),
    fecha: str | None = Query(None, description="corte = HASTA (ISO): TOTAL hasta corte. None = hoy"),
    desde: str | None = Query(None, description="inicio del período (ISO). Si viene, MES = [desde, fecha] (vol_mes/ar_mes/ctas_ops)"),
    operador: list[str] | None = Query(None, description="filtro madre operador (multi)"),
    nivel_1: list[str] | None = Query(None, description="filtro madre nivel_1 (multi)"),
    nivel_2: list[str] | None = Query(None, description="filtro madre nivel_2 (multi)"),
    nivel_3: list[str] | None = Query(None, description="filtro madre nivel_3 (multi)"),
    nivel_4: list[str] | None = Query(None, description="filtro madre nivel_4 (multi)"),
    nivel_5: list[str] | None = Query(None, description="filtro madre nivel_5 (multi)"),
    referido: list[str] | None = Query(None, description="filtro madre referido (multi)"),
    division: list[str] | None = Query(None, description="filtro madre division (multi)"),
) -> dict:
    """Tablas 2 y 3 del Informe: volumen + aranceles por comercial (ranking) y
    aranceles por segmento. Global (toda la mesa) salvo que se filtre por los
    filtros madre (operador/nivel_1..5/referido), que scopean el informe."""
    return _com_sql.informe_comercial(
        moneda=moneda, fecha=fecha, desde=desde, operador=operador,
        nivel_1=nivel_1, nivel_2=nivel_2, nivel_3=nivel_3,
        nivel_4=nivel_4, nivel_5=nivel_5, referido=referido, division=division)


@router.get("/comercial/informe-segmento")
@cached(ttl=300)
def comercial_informe_segmento(
    hasta: str | None = Query(None, description="mes YYYY-MM (default actual); acumulado a fin de mes"),
    operador: str | None = Query(None, description="opcional: solo cuentas de ese comercial"),
    fecha: str | None = Query(None, description="corte = HASTA exacto (ISO) — pisa `hasta`"),
    desde: str | None = Query(None, description="inicio del período (ISO). Si viene, las Operativas se cuentan en [desde, fecha]"),
    nivel_1: list[str] | None = Query(None, description="filtro madre nivel_1 (multi)"),
    nivel_2: list[str] | None = Query(None, description="filtro madre nivel_2 (multi)"),
    nivel_3: list[str] | None = Query(None, description="filtro madre nivel_3 (multi)"),
    nivel_4: list[str] | None = Query(None, description="filtro madre nivel_4 (multi)"),
    nivel_5: list[str] | None = Query(None, description="filtro madre nivel_5 (multi)"),
    referido: list[str] | None = Query(None, description="filtro madre referido (multi)"),
    division: list[str] | None = Query(None, description="filtro madre division (multi)"),
) -> dict:
    """Tabla 1 del Informe: # cuentas por segmento (nivel_1), acumulado a la fecha de
    corte (`fecha` exacta, o fin del mes `hasta`) por fecha de alta. `operador` opcional."""
    return _com_sql.informe_cuentas_por_segmento(
        hasta=hasta, operador=operador, fecha=fecha, desde=desde,
        nivel_1=nivel_1, nivel_2=nivel_2, nivel_3=nivel_3,
        nivel_4=nivel_4, nivel_5=nivel_5, referido=referido, division=division)


@router.get("/comercial/informe-aranceles-segmento")
@cached(ttl=300)
def comercial_informe_aranceles_segmento(
    operador: str = Query(..., description="operador_email a desglosar"),
    moneda: str = Query("ARS", description="ARS | USD"),
    fecha: str | None = Query(None, description="corte = HASTA (ISO). None = hoy"),
    desde: str | None = Query(None, description="inicio del período (ISO). Si viene, ar_mes = [desde, fecha]"),
    nivel_1: list[str] | None = Query(None, description="filtro madre nivel_1 (multi)"),
    nivel_2: list[str] | None = Query(None, description="filtro madre nivel_2 (multi)"),
    nivel_3: list[str] | None = Query(None, description="filtro madre nivel_3 (multi)"),
    nivel_4: list[str] | None = Query(None, description="filtro madre nivel_4 (multi)"),
    nivel_5: list[str] | None = Query(None, description="filtro madre nivel_5 (multi)"),
    referido: list[str] | None = Query(None, description="filtro madre referido (multi)"),
    division: list[str] | None = Query(None, description="filtro madre division (multi)"),
) -> dict:
    """Q3 re-scopeada a un comercial: aranceles + ticket por segmento, solo de
    sus cuentas."""
    return _com_sql.informe_aranceles_segmento(
        operador=operador, moneda=moneda, fecha=fecha, desde=desde,
        nivel_1=nivel_1, nivel_2=nivel_2, nivel_3=nivel_3,
        nivel_4=nivel_4, nivel_5=nivel_5, referido=referido)


@router.get("/comercial/informe-segmento-detalle")
@cached(ttl=300)
def comercial_informe_segmento_detalle(
    segmento: str = Query("todos", description="nivel_1 a desglosar; 'todos' = todos los segmentos"),
    operador: str | None = Query(None, description="opcional: solo cuentas de ese comercial"),
    moneda: str = Query("ARS", description="ARS | USD"),
    fecha: str | None = Query(None, description="corte = HASTA (ISO). None = hoy"),
    desde: str | None = Query(None, description="inicio del período (ISO). arancel_mes = [desde, fecha]"),
    nivel_2: list[str] | None = Query(None, description="filtro madre nivel_2 (multi)"),
    nivel_3: list[str] | None = Query(None, description="filtro madre nivel_3 (multi)"),
    nivel_4: list[str] | None = Query(None, description="filtro madre nivel_4 (multi)"),
    nivel_5: list[str] | None = Query(None, description="filtro madre nivel_5 (multi)"),
    referido: list[str] | None = Query(None, description="filtro madre referido (multi)"),
    division: list[str] | None = Query(None, description="filtro madre division (multi)"),
    max_ops: int = Query(1000, ge=1, le=20000, description="tope de operaciones en el detalle (payload)"),
) -> dict:
    """Detalle de un segmento (Q4 dinámica): clientes con su arancel +
    operaciones (boletos con arancel) que lo generaron. `segmento='todos'` →
    todos los segmentos (vista por defecto). `operador` opcional. `nivel_1` lo fija
    `segmento`; el resto de los niveles + referido scopean. `max_ops` capea la lista de
    operaciones a las N más recientes (`n_operaciones` trae el total real)."""
    return _com_sql.informe_segmento_detalle(
        segmento=segmento, operador=operador, moneda=moneda, fecha=fecha, desde=desde,
        nivel_2=nivel_2, nivel_3=nivel_3, nivel_4=nivel_4, nivel_5=nivel_5, referido=referido,
        division=division, max_ops=max_ops)


# ── CONTROL COMERCIAL (jefatura) — editor de objetivos (etapa 1) ──────────────
# Vive en la vista Control Comercial (Operadores), NO en Manager. Gateado con el MISMO
# módulo que protege toda la vista (`operaciones`): lo usa quien puede ver Operadores; no
# queda abierto a cualquier usuario autenticado (cubre el hallazgo del security review).
@router.get("/comercial/control/objetivos")
def control_objetivos(
    anio: int = Query(..., description="año de los objetivos"),
    _actor: str = Depends(require_control_comercial),
) -> dict:
    """Objetivos por comercial cargados para un año (alimenta el editor + la Tabla 3)."""
    return _cc.listar_objetivos(anio=anio)


class _ObjetivoIn(BaseModel):
    operador_email: str
    anio: int
    mes: int
    volumen_objetivo: float | None = None
    comisiones_objetivo: float | None = None


@router.patch("/comercial/control/objetivos")
def control_set_objetivo(
    req: _ObjetivoIn,
    actor: str = Depends(require_control_comercial),
) -> dict:
    """Upsert del objetivo de un comercial para (año, mes). Lo edita el jefe in-view → SQL.
    Gateado: require_control_comercial → 403 salvo admin o user con el flag en Manager."""
    return _cc.set_objetivo(
        operador_email=req.operador_email, anio=req.anio, mes=req.mes,
        volumen_objetivo=req.volumen_objetivo, comisiones_objetivo=req.comisiones_objetivo,
        actor=actor or "")


# Filtros madre de la barra de Operadores (multi-select; cruzan con AND). Vacío = sin filtro
# (mesa completa). Van como tupla a los services (datos_totales_alyc está @cached → hashable).
def _filtros_madre(
    operador: list[str] = Query(default=[], description="operador(es) — multi. Vacío = todos"),
    nivel_1: list[str] = Query(default=[], description="filtro madre nivel_1 (multi)"),
    nivel_2: list[str] = Query(default=[], description="filtro madre nivel_2 (multi)"),
    nivel_3: list[str] = Query(default=[], description="filtro madre nivel_3 (multi)"),
    nivel_4: list[str] = Query(default=[], description="filtro madre nivel_4 (multi)"),
    nivel_5: list[str] = Query(default=[], description="filtro madre nivel_5 (multi)"),
    referido: list[str] = Query(default=[], description="filtro madre referido (multi)"),
    division: list[str] = Query(default=[], description="filtro madre division (multi)"),
) -> dict:
    return {"operador": tuple(operador), "nivel_1": tuple(nivel_1), "nivel_2": tuple(nivel_2),
            "nivel_3": tuple(nivel_3), "nivel_4": tuple(nivel_4), "nivel_5": tuple(nivel_5),
            "referido": tuple(referido), "division": tuple(division)}


@router.get("/comercial/control/totales")
def control_totales(
    moneda: str = Query("ARS", description="ARS | USD"),
    filtros: dict = Depends(_filtros_madre),
    _actor: str = Depends(require_control_comercial),
) -> dict:
    """Tabla 1 — totales ALyC por períodos fijos (Día/Semana/Mes/YTD/12M/2025/2024/Total)
    + % vs período anterior. NO depende de Desde/Hasta. Filtros madre acotan el scope."""
    return _cc.datos_totales_alyc(moneda=moneda, **filtros)


@router.get("/comercial/control/por-operador")
def control_por_operador(
    desde: str = Query(..., description="ISO YYYY-MM-DD"),
    hasta: str = Query(..., description="ISO YYYY-MM-DD"),
    moneda: str = Query("ARS", description="ARS | USD"),
    filtros: dict = Depends(_filtros_madre),
    _actor: str = Depends(require_control_comercial),
) -> dict:
    """Tabla 2 — por comercial en [desde, hasta]: activos/inactivos + volumen + comisiones
    (cada uno con % vs el rango anterior de igual largo). Filtros madre acotan el scope."""
    return _cc.datos_por_operador(desde=desde, hasta=hasta, moneda=moneda, **filtros)


@router.get("/comercial/control/objetivos-vs-actual")
def control_objetivos_vs_actual(
    desde: str = Query(..., description="ISO YYYY-MM-DD"),
    hasta: str = Query(..., description="ISO YYYY-MM-DD"),
    moneda: str = Query("ARS", description="ARS | USD"),
    filtros: dict = Depends(_filtros_madre),
    _actor: str = Depends(require_control_comercial),
) -> dict:
    """Tabla 3 — por comercial: Actual (en el rango) vs Objetivo (suma de objetivos mensuales
    del rango) + % alcanzado. Filtros madre acotan el scope."""
    return _cc.objetivos_vs_actual(desde=desde, hasta=hasta, moneda=moneda, **filtros)


@router.get("/financiamiento")
def financiamiento_libro(
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
) -> dict:
    """Vista FINANCIAMIENTO (tab de /operaciones) — libro VIVO de pagarés/cheques.

    Instrumentos con `cartera = FINANCIAMIENTO` y vencimiento HOY o posterior,
    con la CANTIDAD (nominal) que tiene cada cliente y la TASA a la que la
    compró. El bruto no viaja a propósito: estos papeles se compran con
    descuento y la mayoría son dólar-linked liquidados en pesos, así que el
    importe pagado no es comparable entre filas (ver el docstring del service).

    Devuelve el grano cuenta × instrumento; el cruce interactivo de las cuatro
    tablas lo resuelve el front sin refetch.
    """
    return _fin.libro(scope=scope)


# ──────────────────────────────────────────────────────────────────────────────
# FINANCIAMIENTO → CALCULADORA DE DESCUENTO (panel 4 de la tab)
#
# PERMISO: ninguno propio. Todo `/api/operaciones/*` ya está detrás del módulo
# `operaciones` (api/main.py) — quien entra a FINANCIAMIENTO puede tocar los
# datos, que es exactamente lo pedido: la tab DATOS es una tabla de parámetros
# comerciales, no información sensible, y la gracia es que la mesa juegue con
# ella. `require_no_invitado` va igual en las escrituras: defense-in-depth, el
# portal www no escribe NUNCA (REGLA #8).
#
# Fórmulas y por qué el cálculo es server-side: api/services/financiamiento_calc.py
# ──────────────────────────────────────────────────────────────────────────────

class _AvalIn(BaseModel):
    nombre: str
    costo_cheque: float | None = None
    costo_pagare: float | None = None
    nota: str | None = None
    orden: int | None = None


class _ArancelesIn(BaseModel):
    arancel_aca: float | None = None
    derecho_mercado: float | None = None


class _CalcIn(BaseModel):
    monto: float
    tasa_pct: float
    dias: int
    aval: str | None = None
    instrumento: str = "cheque"


@router.get("/financiamiento/datos")
def financiamiento_datos() -> dict:
    """Parámetros de la calculadora: catálogo de SGRs + arancel ACA + derecho de
    mercado. Alimenta la tab DATOS y el selector de aval de la calculadora."""
    return _fin_calc.get_datos()


@router.put("/financiamiento/datos/aval")
def financiamiento_set_aval(
    payload: _AvalIn = Body(...),
    actor: str = Depends(get_user_email),
    _noguest: None = Depends(require_no_invitado),
) -> dict:
    """Alta o edición de una SGR (upsert por nombre)."""
    try:
        return _fin_calc.guardar_aval(
            nombre=payload.nombre, costo_cheque=payload.costo_cheque,
            costo_pagare=payload.costo_pagare, nota=payload.nota,
            orden=payload.orden, actor=actor,
        )
    except _fin_calc.TablasFaltantes as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/financiamiento/datos/aval")
def financiamiento_del_aval(
    nombre: str = Query(..., min_length=1),
    actor: str = Depends(get_user_email),
    _noguest: None = Depends(require_no_invitado),
) -> dict:
    """Baja de una SGR del catálogo."""
    try:
        return _fin_calc.borrar_aval(nombre=nombre, actor=actor)
    except _fin_calc.TablasFaltantes as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.put("/financiamiento/datos/aranceles")
def financiamiento_set_aranceles(
    payload: _ArancelesIn = Body(...),
    actor: str = Depends(get_user_email),
    _noguest: None = Depends(require_no_invitado),
) -> dict:
    """Arancel de ACA Valores + derecho de mercado (fila única)."""
    try:
        return _fin_calc.guardar_aranceles(
            arancel_aca=payload.arancel_aca,
            derecho_mercado=payload.derecho_mercado, actor=actor,
        )
    except _fin_calc.TablasFaltantes as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/financiamiento/calculadora")
def financiamiento_calculadora(payload: _CalcIn = Body(...)) -> dict:
    """Corre la planilla: NETO SIN AVAL + NETO CON AVAL + CFT + flujos.

    NO PERSISTE NADA. Es un simulador — cada usuario corre el suyo y no queda
    rastro. El costo del aval NO viaja en el request (se manda el NOMBRE y el
    backend lo resuelve contra el catálogo) para que nadie pueda cotizar con un
    costo que no es el vigente.
    """
    try:
        return _fin_calc.calcular(
            monto=payload.monto, tasa_pct=payload.tasa_pct, dias=payload.dias,
            aval=payload.aval, instrumento=payload.instrumento,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
