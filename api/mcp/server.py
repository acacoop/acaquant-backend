"""FastMCP server para TradingAV.

Expone tools de SOLO LECTURA sobre datos de mercado:
curvas, forwards, breakevens, cauciones, futuros DLR, opciones, REM,
descomposición de retorno, sensibilidad, canje, carry trade, MEP, macro.

NO expone (por diseño):
portfolio, operaciones, cuentas, AuM, manager, intel — son datos
privados del usuario, no de mercado.

Cada tool es un thin wrapper sobre un servicio puro de `api/services/*`.
La lógica vive ahí; este archivo es solo la capa de exposición MCP.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from api.services import analitica as svc_ana
from api.services import canje as svc_canje
from api.services import carry_trade as svc_carry
from api.services import derivados as svc_der
from api.services import descomposicion_retorno as svc_desc
from api.services import macro as svc_macro
from api.services import opciones as svc_opt
from api.services import rem as svc_rem
from api.services import renta_fija as svc_rf
from api.services import repo as svc_repo
from api.services import sensibilidad as svc_sens

# Stateless HTTP: cada request se procesa independiente, sin sesión
# persistente — más simple y compatible con load balancers.
mcp = FastMCP(name="TradingAV", stateless_http=True)


# ─────────────────────────────────────────────────────────────────
# Curvas y bonos
# ─────────────────────────────────────────────────────────────────


@mcp.tool(
    description=(
        "Lista bonos de una curva con su estado vigente: precio, TEA/TEM, "
        "duration, mod_duration, convexity, paridad, vencimiento, volumen "
        "del día. Ordenable por vencimiento, volumen, tea o duration."
    ),
)
def listar_curva(
    curva: str,
    ordenar_por: str = "vencimiento",
    vencimiento_min_meses: float | None = None,
    vencimiento_max_meses: float | None = None,
    limit: int | None = None,
) -> list[dict]:
    """curva ∈ {tasa_fija, cer, soberanos, tamar, dolar_linked}."""
    return svc_rf.listar_curva(
        curva=curva,
        ordenar_por=ordenar_por,
        vencimiento_min_meses=vencimiento_min_meses,
        vencimiento_max_meses=vencimiento_max_meses,
        limit=limit,
    )


@mcp.tool(
    description=(
        "Reconstruye la curva entera (tasa_fija/cer/soberanos/tamar) tal como "
        "cerró un día pasado en formato YYYY-MM-DD. Devuelve los mismos "
        "campos que listar_curva pero usando el último trade de ESE día."
    ),
)
def snapshot_curva_historico(curva: str, fecha: str) -> list[dict]:
    return svc_ana.snapshot_curva_historico(curva=curva, fecha=fecha)


@mcp.tool(
    description=(
        "Pendiente de una curva en bps (largo - corto, según métrica) y "
        "opcionalmente delta vs un día pasado. Métricas: tea, tem, duration."
    ),
)
def pendiente_curva(
    curva: str,
    metrica: str = "tea",
    fecha_comparacion: str | None = None,
) -> dict:
    return svc_ana.calcular_pendiente_curva(
        curva=curva, metrica=metrica, fecha_comparacion=fecha_comparacion,
    )


@mcp.tool(
    description=(
        "Volumen operado del día de un bono vs su promedio de las últimas N "
        "ruedas. Útil para evaluar liquidez antes de operar."
    ),
)
def liquidez_secundario(ticker: str, dias: int = 20) -> dict:
    return svc_ana.liquidez_secundario(ticker=ticker, dias=dias)


@mcp.tool(
    description=(
        "Trades de los últimos 15 días para un instrumento (o todos si se "
        "omite). Cada trade trae precio, size, side, money, TEA/duration "
        "enriquecidas por el motor. Ticker corto ('TX26') o completo."
    ),
)
def historico_trades(instrumento: str | None = None) -> list[dict]:
    return svc_rf.get_historico_trades(instrumento=instrumento)


@mcp.tool(
    description=(
        "Series históricas diarias de cierre para todos los bonos de una "
        "curva (tasa_fija | cer). Una entrada por (fecha, ticker)."
    ),
)
def historico_curva(curva: str) -> list[dict]:
    return svc_rf.get_historico_curva(curva=curva)


# ─────────────────────────────────────────────────────────────────
# Atribución de retorno (Lecap / Boncap)
# ─────────────────────────────────────────────────────────────────


@mcp.tool(
    description=(
        "Atribución ex-post entre dos fechas (YYYY-MM-DD) para Lecap/Boncap: "
        "descompone el retorno total en carry / rolldown / cambio_tasa. "
        "Forma exacta del PDF (composición exponencial), no linealización. "
        "metodo: lineal | cuadratica (interpolación de la curva inicial)."
    ),
)
def descomposicion_retorno(
    desde: str, hasta: str, metodo: str = "lineal",
) -> dict:
    return svc_desc.descomposicion_realizada(
        desde=desde, hasta=hasta, metodo=metodo,
    )


@mcp.tool(
    description=(
        "Atribución prospectiva por Lecap/Boncap a horizonte (días): qué "
        "rinde si la curva no se mueve. Devuelve carry_esperado + "
        "rolldown_esperado por bono, rankeado por total_esperado. Útil para "
        "rankear 'qué Lecap comprar este mes'."
    ),
)
def rolldown_esperado(
    horizonte_dias: int = 30, metodo: str = "lineal",
) -> dict:
    return svc_desc.rolldown_esperado(
        horizonte_dias=horizonte_dias, metodo=metodo,
    )


# ─────────────────────────────────────────────────────────────────
# Sensibilidad
# ─────────────────────────────────────────────────────────────────


@mcp.tool(
    description=(
        "Tabla de sensibilidad de PRECIO por escenarios de TIR para hard "
        "dollar (curva soberanos). tirs es CSV de TIRs en %. modo: "
        "'absoluta' = TIRs finales; 'relativa' = shocks pp sobre la TEA "
        "actual. horizonte_dias proyecta el precio (incluye pull-to-par; "
        "0 = upside instantáneo). tipos opcional: 'globales,bonares' o vacío "
        "= todos."
    ),
)
def sensibilidad_retorno(
    curva: str = "soberanos",
    tirs: str = "4,5,6,7,8,9,10,11",
    horizonte_dias: int = 0,
    modo: str = "absoluta",
    tipos: str | None = None,
) -> list[dict]:
    try:
        tirs_t = tuple(float(t.strip()) / 100 for t in tirs.split(",") if t.strip())
    except ValueError:
        return [{"error": "tirs malformado, esperado CSV de números"}]
    if not tirs_t:
        return [{"error": "tirs vacío"}]
    tipos_t: tuple[str, ...] | None = None
    if tipos:
        tipos_t = tuple(t.strip() for t in tipos.split(",") if t.strip()) or None
    return svc_sens.sensibilidad_retorno_total(
        curva=curva, tirs=tirs_t, horizonte_dias=horizonte_dias,
        modo=modo, tipos=tipos_t,
    )


# ─────────────────────────────────────────────────────────────────
# Forwards y breakevens
# ─────────────────────────────────────────────────────────────────


@mcp.tool(
    description=(
        "Forwards live entre todos los pares de una curva (tasa_fija | cer). "
        "Si no se filtra, devuelve ambas. Forward(A,B) = "
        "((1+TEA_B)^t_B / (1+TEA_A)^t_A)^(1/(t_B − t_A)) − 1."
    ),
)
def forwards_live(curva: str | None = None) -> list[dict]:
    return svc_der.get_forwards(curva=curva)


@mcp.tool(
    description=(
        "Histórico diario de forwards. Filtros opcionales: curva (tasa_fija "
        "| cer), desde, hasta (YYYY-MM-DD)."
    ),
)
def forwards_historico(
    curva: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
) -> list[dict]:
    return svc_der.get_historico_forwards(curva=curva, desde=desde, hasta=hasta)


@mcp.tool(
    description=(
        "Breakevens vivos: pares Lecap-CER de mismo vencimiento con la "
        "inflación mensual implícita en el spread."
    ),
)
def breakevens_live() -> list[dict]:
    return svc_der.get_breakevens()


@mcp.tool(
    description="Histórico diario de breakevens. Rango opcional desde/hasta.",
)
def breakevens_historico(
    desde: str | None = None,
    hasta: str | None = None,
) -> list[dict]:
    return svc_der.get_historico_breakevens(desde=desde, hasta=hasta)


# ─────────────────────────────────────────────────────────────────
# Cauciones, futuros DLR, MEP
# ─────────────────────────────────────────────────────────────────


@mcp.tool(
    description=(
        "Cauciones live (mercado repo). Filtro opcional moneda: ARS | USD; "
        "vacío trae ambas."
    ),
)
def cauciones_live(moneda: str | None = None) -> list[dict]:
    return svc_repo.get_caucion(moneda=moneda)


@mcp.tool(description="Histórico diario de cauciones. Filtros: moneda, desde, hasta.")
def cauciones_historico(
    moneda: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
) -> list[dict]:
    return svc_repo.get_historico_caucion(moneda=moneda, desde=desde, hasta=hasta)


@mcp.tool(description="Futuros DLR (Rofex) vigentes con precio y TEA implícita.")
def futuros_dlr_live() -> list[dict]:
    return svc_der.get_futuros_dlr()


@mcp.tool(
    description=(
        "Histórico de futuros DLR. ticker opcional ('DLR/MMMYY'), "
        "rango opcional desde/hasta."
    ),
)
def futuros_dlr_historico(
    ticker: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
) -> list[dict]:
    return svc_der.get_historico_futuros_dlr(
        ticker=ticker, desde=desde, hasta=hasta,
    )


@mcp.tool(description="Último valor disponible de Dólar MEP.")
def mep_actual() -> dict:
    return svc_macro.get_ultimo_mep()


@mcp.tool(description="Histórico diario de Dólar MEP. Rango opcional desde/hasta.")
def mep_historico(
    desde: str | None = None,
    hasta: str | None = None,
) -> list[dict]:
    return svc_macro.get_historico_mep(desde=desde, hasta=hasta)


# ─────────────────────────────────────────────────────────────────
# Cross-asset
# ─────────────────────────────────────────────────────────────────


@mcp.tool(
    description=(
        "Serie histórica del canje legislación NY vs Argentina para un par "
        "(default AL30). canje = precio_C / precio_D − 1."
    ),
)
def canje(
    par: str = "AL30",
    desde: str | None = None,
    hasta: str | None = None,
) -> list[dict]:
    return svc_canje.serie_canje(par=par, desde=desde, hasta=hasta)


@mcp.tool(
    description=(
        "Carry trade en USD por bono = retorno ARS descontado por var del "
        "dólar (mep | ccl). curva: tasa_fija | cer. Default últimos 180d."
    ),
)
def carry_trade(
    curva: str = "tasa_fija",
    desde: str | None = None,
    hasta: str | None = None,
    dolar: str = "mep",
) -> list[dict]:
    return svc_carry.serie_carry_trade(
        curva=curva, desde=desde, hasta=hasta, dolar=dolar,
    )


# ─────────────────────────────────────────────────────────────────
# Macro (BCRA / INDEC / scrapings)
# ─────────────────────────────────────────────────────────────────


@mcp.tool(
    description=(
        "Serie temporal de una variable macro. Variables soportadas: "
        "tamar, cer, dolar, badlar, mep, ccl, canje, ipc, ipim, "
        "riesgo_pais, repo, rem_inflacion. También admite '<TICKER>.<CAMPO>' "
        "para cualquier doc en colecciones macro. ventana_dias: 1..3650."
    ),
)
def serie_macro(variable: str, ventana_dias: int = 90) -> list[dict]:
    return svc_macro.obtener_serie_macro(
        variable=variable, ventana_dias=ventana_dias,
    )


@mcp.tool(
    description=(
        "Clasifica el nivel actual de una variable macro contra su "
        "distribución en los últimos N días: percentil, z-score, "
        "categoría (alto/medio/bajo)."
    ),
)
def clasificar_nivel(variable: str, ventana_dias: int = 90) -> dict:
    return svc_macro.clasificar_nivel(
        variable=variable, ventana_dias=ventana_dias,
    )


@mcp.tool(
    description=(
        "Expectativas REM del BCRA: IPC mensual proyectado por consultoras. "
        "Filtros opcionales: informe (YYYY-MM o vacío=último), periodo_tipo "
        "(mensual|anual|trimestral), rango periodo_desde/periodo_hasta."
    ),
)
def rem_expectativas(
    informe: str | None = None,
    periodo_tipo: str | None = None,
    periodo_desde: str | None = None,
    periodo_hasta: str | None = None,
) -> list[dict]:
    return svc_rem.expectativas(
        informe=informe, periodo_tipo=periodo_tipo,
        periodo_desde=periodo_desde, periodo_hasta=periodo_hasta,
    )


# ─────────────────────────────────────────────────────────────────
# Opciones
# ─────────────────────────────────────────────────────────────────


@mcp.tool(
    description=(
        "Chain de opciones del OPEX en curso. Filtros opcionales: "
        "instrumento (corto 'GFGC10950A' o completo), tipo (CALL|PUT). "
        "Cada doc trae strike, precio bid/ask/last, IV, griegas, etc."
    ),
)
def opciones_chain(
    instrumento: str | None = None,
    tipo: str | None = None,
) -> list[dict]:
    return svc_opt.get_opciones(instrumento=instrumento, tipo=tipo)


@mcp.tool(
    description=(
        "Metadata de la chain de opciones: subyacente, fecha de vencimiento "
        "del OPEX, tasa libre de riesgo configurada, etc."
    ),
)
def opciones_meta() -> dict:
    return svc_opt.get_opciones_meta()


@mcp.tool(
    description=(
        "Histórico tick-level del OPEX en curso para una opción. Filtros: "
        "instrumento (symbol), tipo (CALL|PUT)."
    ),
)
def opciones_historico(
    instrumento: str | None = None,
    tipo: str | None = None,
) -> list[dict]:
    return svc_opt.get_historico_opciones(instrumento=instrumento, tipo=tipo)
