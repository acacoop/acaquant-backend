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
from mcp.server.transport_security import TransportSecuritySettings

from api.services import analitica as svc_ana
from api.services import canje as svc_canje
from api.services import carry_trade as svc_carry
from api.services import day_trading as svc_dt
from api.services import derivados as svc_der
from api.services import descomposicion_retorno as svc_desc
from api.services import fair_value as svc_fv
from api.services import macro as svc_macro
from api.services import opciones as svc_opt
from api.services import opciones_sql as svc_opt_sql
from api.services import order_book as svc_ob
from api.services import rem as svc_rem
from api.services import renta_fija as svc_rf
from api.services import repo as svc_repo
from api.services import scanner as svc_scanner
from api.services import sensibilidad as svc_sens

# Stateless HTTP: cada request se procesa independiente, sin sesión
# persistente — más simple y compatible con load balancers.
# streamable_http_path="/": que el transporte quede en el ROOT del sub-app.
# Si dejamos el default ("/mcp"), al hacer app.mount("/mcp", sub_app) la
# URL real termina en /mcp/mcp/ y no responde a /mcp/.
# transport_security: el SDK default solo acepta localhost (DNS rebinding
# protection). Custom Connector de Claude pega con Host=api.acaquant.com y
# Origin=https://claude.ai → 421 Misdirected Request si no whitelistamos.
mcp = FastMCP(
    name="TradingAV",
    stateless_http=True,
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        allowed_hosts=["api.acaquant.com", "api.acaquant.com:443", "localhost:*", "127.0.0.1:*"],
        allowed_origins=["https://claude.ai", "https://claude.com", "http://localhost:*", "http://127.0.0.1:*"],
    ),
)


# ─────────────────────────────────────────────────────────────────
# Curvas y bonos
# ─────────────────────────────────────────────────────────────────


@mcp.tool(
    description=(
        "Lista bonos de una curva con su estado vigente: precio, TEA/TEM, "
        "duration, mod_duration, convexity, paridad, vencimiento, volumen "
        "del día. Para curva='tasa_fija' agrega tc_breakeven (TC al que el "
        "bono en pesos comprado hoy y mantenido a vto empata contra haber "
        "comprado MEP hoy: MEP × flujo_vto / precio). Ordenable por "
        "vencimiento, volumen, tea o duration."
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
        "opcionalmente delta vs un día pasado. Métricas: tea, tem, duration. "
        "dias_min_corto (default 30) excluye bonos a punto de vencer del anchor "
        "'corto' — sus TEAs son ruidosas e inflan el spread. Bajalo a 0 para "
        "incluir todos los bonos."
    ),
)
def pendiente_curva(
    curva: str,
    metrica: str = "tea",
    fecha_comparacion: str | None = None,
    dias_min_corto: int = 30,
) -> dict:
    return svc_ana.calcular_pendiente_curva(
        curva=curva, metrica=metrica,
        fecha_comparacion=fecha_comparacion,
        dias_min_corto=dias_min_corto,
    )


@mcp.tool(
    description=(
        "Trades de HOY para un instrumento (o todos si se omite). Cada trade trae "
        "hora, precio, size, side, money. Ticker corto ('TX26') o completo."
    ),
)
def historico_trades(instrumento: str | None = None) -> list[dict]:
    import os
    if os.getenv("RENTA_FIJA_SQL") == "1":
        from api.services import renta_fija_sql as _rfs
        return _rfs.get_historico_trades(instrumento=instrumento)
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
# Order Book (LOB live, depth 5, sin histórico)
# ─────────────────────────────────────────────────────────────────


@mcp.tool(
    description=(
        "Order book live (depth 5) de un ticker de renta fija ARG. Devuelve "
        "{ticker, updated_at, book: {bids[5], offers[5]}, metrics: "
        "{last_price, open_price, high_price, low_price, closing_price}}. "
        "Cada nivel del book es {price, size}. Refresh ~1s (lo escribe el "
        "motor MicrostructureEngine sobre Trading.MarketSnapshot). Sin "
        "histórico — siempre el último estado vivo. Acepta ticker corto "
        "('TX26') o completo ('MERV - XMEV - TX26 - 24hs'). Retorna None si "
        "el ticker no está en el universo de Trading.Curvas o nunca recibió "
        "market data."
    ),
)
def order_book(ticker: str) -> dict | None:
    return svc_ob.get_order_book(ticker)


@mcp.tool(
    description=(
        "Order books live (depth 5) de TODOS los tickers de una curva. Útil "
        "para análisis comparativo de liquidez, microestructura o relative "
        "value. curva ∈ {tasa_fija, cer, soberanos, tamar, dolar_linked}. "
        "Cada elemento del array tiene la misma forma que order_book "
        "(ticker, book.bids[5], book.offers[5], metrics, updated_at). "
        "Refresh ~1s, sin histórico. Una sola query a Mongo internamente."
    ),
)
def order_books_curva(curva: str) -> list[dict]:
    return svc_ob.get_order_books_curva(curva=curva)


# ─────────────────────────────────────────────────────────────────
# Atribución de retorno (Lecap / Boncap / Lecer)
# ─────────────────────────────────────────────────────────────────


@mcp.tool(
    description=(
        "Atribución ex-post entre dos fechas (YYYY-MM-DD): descompone el "
        "retorno total en carry / rolldown / cambio_tasa. curva: tasa_fija "
        "(default, sobre precio sucio + TEM) o cer (sobre paridad + TEA "
        "real, suma cer_accrual del período y r_total_ars compuesto). "
        "Forma exacta (composición exponencial), no linealización. "
        "metodo: lineal | cuadratica."
    ),
)
def descomposicion_retorno(
    desde: str, hasta: str,
    metodo: str = "lineal",
    curva: str = "tasa_fija",
) -> dict:
    return svc_desc.descomposicion_realizada(
        desde=desde, hasta=hasta, metodo=metodo, curva=curva,
    )


@mcp.tool(
    description=(
        "Atribución prospectiva a horizonte (días) si la curva no se mueve. "
        "curva: tasa_fija (devuelve carry+rolldown sobre TEM) o cer "
        "(carry+rolldown sobre TEA real más cer_accrual_esperado del REM "
        "y total_esperado_ars compuesto). Rankeado por total esperado. Útil "
        "para 'qué Lecap/Lecer comprar este mes'."
    ),
)
def rolldown_esperado(
    horizonte_dias: int = 30,
    metodo: str = "lineal",
    curva: str = "tasa_fija",
) -> dict:
    return svc_desc.rolldown_esperado(
        horizonte_dias=horizonte_dias, metodo=metodo, curva=curva,
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
        "Coeficientes (media, desvío, n_obs) sobre 30 días hábiles por par "
        "de la matriz de forwards. Para z-scorear el forward live: "
        "z = (forward_live − media) / desvio. Pares con n_obs<20 o "
        "desvío≈0 quedan fuera del payload. Refrescado 1x/día post-cierre."
    ),
)
def forwards_zscore(curva: str | None = None) -> list[dict]:
    return svc_der.get_forwards_zscore(curva=curva)


@mcp.tool(
    description=(
        "Fair value relativo intra-curva: residuo y z-scores de cada bono "
        "vs la curva cuadrática TEA(d) = β₀ + β₁·d + β₂·d². z_estatico = "
        "residuo / σ del universo del día. z_temporal = (residuo_hoy − media_30d) "
        "/ desvio_30d (NULL con n_obs<20). Modo 'live' usa β del último cierre + "
        "TEAs vivas; 'cierre' lee el snapshot persistido. Curvas válidas: "
        "tasa_fija, cer (V1)."
    ),
)
def fair_value(
    curva: str,
    modo: str = "live",
    fecha: str | None = None,
) -> dict:
    if modo == "cierre":
        return svc_fv.get_fair_value_cierre(curva=curva, fecha=fecha)
    return svc_fv.get_fair_value_live(curva=curva)


@mcp.tool(
    description=(
        "Serie diaria del residuo de un bono específico vs su curva fair value "
        "(últimos `dias` cierres). Devuelve {ticker, curva, dias, serie: "
        "[{fecha, residuo_bps, z_temporal, z_estatico, tea_obs, tea_teorica, "
        "duration}]}. USAR para 'cómo viene cotizando T30A7 vs su historia' o "
        "'ver evolución del z-score temporal de X bono'. Ticker debe ser el full "
        "(MERV - XMEV - X - 24hs)."
    ),
)
def fair_value_historico_bono(ticker: str, dias: int = 60) -> dict:
    return svc_fv.get_fair_value_historico_bono(ticker=ticker, dias=dias)


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
        "Serie histórica del canje CCL/MEP intra-bono (mismo bono, especies C "
        "y D distintas). canje = precio_C / precio_D − 1. Mide brecha CCL/MEP "
        "implícita en un único bono. NO es spread legislación (GD30 vs AL30). "
        "Pares disponibles: AL30 (default), GD30."
    ),
)
def canje(
    par: str = "AL30",
    desde: str | None = None,
    hasta: str | None = None,
) -> dict:
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
) -> dict:
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
def serie_macro(variable: str, ventana_dias: int = 90) -> dict:
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
) -> dict:
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


@mcp.tool(
    description=(
        "Evolución diaria de las griegas de un contrato de opción "
        "(delta, gamma, vega, theta, iv) más last/spot — rollup diario de los "
        "últimos ~21 días. Una fila por fecha. Requiere `instrumento` (symbol)."
    ),
)
def opciones_griegas_historico(instrumento: str) -> list[dict]:
    # SQL-NATIVE: Opciones.DataHistorica (Mongo) migrada → dropeada; lee mercado.options_data_hist.
    return svc_opt_sql.get_griegas_historico(instrumento=instrumento)


@mcp.tool(
    description=(
        "Serie diaria del subyacente GGAL (~40 ruedas): precio local en ARS "
        "(LOCAL_Close) y ADR en USD (ADR_Close) por fecha. Para contextualizar "
        "el spot en el análisis de opciones."
    ),
)
def opciones_spot_ggal() -> list[dict]:
    return svc_opt.get_vr_ggal_serie()


# ─────────────────────────────────────────────────────────────────
# Renta variable — Scanner CEDEARs
# ─────────────────────────────────────────────────────────────────


@mcp.tool(
    description=(
        "Lista de CEDEARs argentinos activos con su master categórico "
        "(sector, industria, región, país) y el snapshot live: last, open, "
        "high, low, close, variación intradía, vs 1 día en ARS y en USD "
        "(descontando el CCL), ratio del CEDEAR y timestamp."
    ),
)
def cedears_scanner() -> list[dict]:
    return svc_scanner.get_cedears_scanner()


@mcp.tool(
    description=(
        "Dólar CCL live con su variación vs el cierre del día previo. "
        "Devuelve value, vs_1d_pct y timestamp."
    ),
)
def ccl_live() -> dict:
    return svc_scanner.get_ccl_live()


@mcp.tool(
    description=(
        "Serie de retornos diarios aritméticos del último año (~252 puntos) "
        "de un activo, desde Trading.PreciosAcciones. ticker es el "
        "ticker_corto BYMA (se resuelve el underlying: YPFD → YPF). Para "
        "histogramas de distribución de retornos."
    ),
)
def acciones_retornos(ticker: str) -> dict:
    return svc_scanner.get_ticker_returns(ticker=ticker)


@mcp.tool(
    description=(
        "Stats rolling de un activo sobre el subyacente USD: beta, alpha "
        "anualizada y correlación vs SPY y vs QQQ, más volatilidad realizada "
        "anualizada a 30 y 60 días. ticker es ticker_corto BYMA."
    ),
)
def acciones_quant_stats(ticker: str) -> dict:
    return svc_scanner.get_quant_stats(ticker=ticker)


@mcp.tool(
    description=(
        "TRADE LAB intradía: ranking de CEDEARs para scalping según un "
        "objetivo de captura en % (0.1-5, default 0.5). Por papel: VUELTAS "
        "(movimientos zigzag completos ≥ objetivo que ya hizo HOY, del tape "
        "por minuto), rango del día y posición en él (0=piso, 100=techo), "
        "pata EN CURSO (dir + % recorrido), momentum 15' por reloj, lado del "
        "VWAP, spread bid/offer en %, flujo comprador (% de la plata del día "
        "y de los últimos 30' que fue compra agresora), volumen en cash y "
        "nominales, minutos sin operar, costumbre histórica (vueltas promedio "
        "por rueda, ~20 ruedas) e idea heurística LONG/SHORT con motivo. "
        "Solo tiene datos en horario de rueda (en_rueda=false fuera de hora)."
    ),
)
def day_trading_scanner(objetivo_pct: float = 0.5) -> dict:
    return svc_dt.get_day_trading(objetivo_pct=objetivo_pct)


@mcp.tool(
    description=(
        "Con qué papeles 'se mueve' un CEDEAR: top n más correlacionados "
        "(con) y más anti-correlacionados (contra) por Pearson de cierres "
        "diarios del subyacente USD (ventana 252 ruedas). Para armar pares, "
        "espejos short de un long, o no duplicar la misma apuesta. ticker es "
        "ticker_corto BYMA."
    ),
)
def day_trading_companeros(ticker: str, n: int = 6) -> dict:
    return svc_dt.get_companeros(ticker=ticker, n=n)


@mcp.tool(
    description=(
        "Pivot points Floor Trader en 4 timeframes (diario/semanal/mensual/"
        "anual) del subyacente USD de un CEDEAR: PP, R1-R3, S1-S3 con el "
        "OHLC del período previo. El diario usa solo el día previo; semanal/"
        "mensual/anual agregan toda la ventana. ticker es ticker_corto BYMA."
    ),
)
def pivot_points(ticker: str) -> dict:
    return svc_scanner.get_pivot_points(ticker=ticker)


