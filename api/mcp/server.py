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
from api.services import derivados as svc_der
from api.services import descomposicion_retorno as svc_desc
from api.services import fair_value as svc_fv
from api.services import macro as svc_macro
from api.services import mm_microstructure as svc_mm
from api.services import opciones as svc_opt
from api.services import order_book as svc_ob
from api.services import order_book_historico as svc_obh
from api.services import rem as svc_rem
from api.services import renta_fija as svc_rf
from api.services import repo as svc_repo
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


@mcp.tool(
    description=(
        "Order book L2 HISTÓRICO de un ticker entre dos timestamps. A "
        "diferencia de order_book (último estado vivo), esta tool devuelve "
        "la serie completa de cambios del book — cada vez que bids u offers "
        "cambiaron en algún nivel. Útil para análisis de microestructura: "
        "evolución del spread, depth, imbalance, velocidad de updates, etc. "
        "Cobertura: solo tickers en config.TICKERS_BOOK_FULL (hoy: AL30 CI). "
        "Usar listar_tickers_orderbook_l2 para confirmar qué hay disponible. "
        "ticker debe ser COMPLETO ('MERV - XMEV - AL30 - CI'). desde/hasta "
        "ISO datetime opcionales (defaults: última hora). limit max 10000 "
        "docs ordenados ascendente por ts."
    ),
)
def order_book_historico(
    ticker: str,
    desde: str | None = None,
    hasta: str | None = None,
    limit: int = 1000,
) -> list[dict]:
    return svc_obh.get_orderbook_historico(
        ticker=ticker, desde=desde, hasta=hasta, limit=limit,
    )


@mcp.tool(
    description=(
        "Lista de tickers que tienen captura L2 del book histórico activa "
        "en Trading.OrderBookL2. Útil para saber qué activos podés pasar "
        "a order_book_historico antes de pedir data."
    ),
)
def listar_tickers_orderbook_l2() -> list[str]:
    return svc_obh.listar_tickers_disponibles()


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


# ─────────────────────────────────────────────────────────────────
# MM Microstructure (Cartea cap 1-4) — derivado de OrderBookL2 + TimeSales
# Cobertura: solo tickers en config.TICKERS_BOOK_FULL (hoy AL30 - CI).
# ─────────────────────────────────────────────────────────────────


@mcp.tool(
    description=(
        "MM Microstructure cap 1 — snapshot LIVE del estado del ticker. "
        "Devuelve {ticker, ts_book, book: {bids, offers}, metrics: {best_bid, "
        "best_ask, mid, microprice, obi, quoted_spread, qs_bps}, last_trade}. "
        "El microprice es el midprice ponderado por order book imbalance — "
        "mejor predictor del próximo precio que el midprice simple. "
        "OBI ∈ [-1, +1]: positivo = más volumen del lado bid (presión "
        "compradora); negativo = más volumen ask (presión vendedora). "
        "ticker default 'MERV - XMEV - AL30 - CI' (único capturado hoy)."
    ),
)
def mm_live(ticker: str = svc_mm.DEFAULT_TICKER) -> dict:
    return svc_mm.get_live(ticker=ticker)


@mcp.tool(
    description=(
        "MM Microstructure cap 4 — tape de trades enriquecido. Cada trade "
        "viene con: effective spread (es, en bps via es_bps), Lee-Ready side "
        "(BUY/SELL/MID según pos vs mid), lee_ready_matches_side (cross-check "
        "vs el side reportado por el motor pyRofex; false = mid stale al "
        "momento del trade), walking flag (size > top_size del lado), mid del "
        "momento. Default últimos 30 min. Hard cap limit trades. Útil para "
        "ver costos efectivos, identificar trades agresivos (walking) y "
        "validar Lee-Ready vs ground truth."
    ),
)
def mm_tape(
    ticker: str = svc_mm.DEFAULT_TICKER,
    desde: str | None = None,
    hasta: str | None = None,
    ventana_min: int = 30,
    limit: int = 500,
) -> dict:
    return svc_mm.get_tape(
        ticker=ticker, desde=desde, hasta=hasta,
        ventana_min=ventana_min, limit=limit,
    )


@mcp.tool(
    description=(
        "MM Microstructure cap 4 — buckets intradía (default 1-min) sobre un "
        "día con métricas agregadas: NOF (net order flow Lee-Ready, signed VN), "
        "qES (quantity-weighted effective spread), walking_pct (%, walking "
        "incidence), realized_vol (en bps, stdev de retornos sobre mid del "
        "bucket), volume, n_trades, mid_close. Output ordenado cronológicamente. "
        "Útil para identificar momentos de toxicidad alta (qES alto), zonas "
        "de presión direccional (NOF acumulado), patrones intradía."
    ),
)
def mm_intraday(
    ticker: str = svc_mm.DEFAULT_TICKER,
    fecha: str | None = None,
    bucket_min: int = 1,
) -> dict:
    return svc_mm.get_intraday(ticker=ticker, fecha=fecha, bucket_min=bucket_min)


@mcp.tool(
    description=(
        "MM Microstructure cap 4 — estimación empírica de impacto de precio. "
        "permanent_impact.b: ΔS_n = b · π_n + ε (Δmid del bucket vs NOF). "
        "Es la versión multi-tick del λ de Kyle. Mayor b → mercado menos "
        "líquido / más informacional. temporary_impact.k: |price - mid| = "
        "k · Q + ε per trade. Es el costo de consumir liquidez. Mayor k → "
        "menor profundidad. OLS sin intercepto + winsorización 1%; reporta "
        "R² y n. Pendiente: Huber/RLM para robustez total."
    ),
)
def mm_impact(
    ticker: str = svc_mm.DEFAULT_TICKER,
    desde: str | None = None,
    hasta: str | None = None,
    dias: int = 5,
    bucket_min: int = 1,
) -> dict:
    return svc_mm.get_impact(
        ticker=ticker, desde=desde, hasta=hasta, dias=dias, bucket_min=bucket_min,
    )


@mcp.tool(
    description=(
        "MM Microstructure cap 4 — smile intradiario (forma U). Para cada "
        "bucket de bucket_min minutos del día, promedia volumen + vol "
        "realizada (en bps) sobre los últimos `dias` con trades. El patrón "
        "esperado es U: pico apertura, mínimo mediodía, pico mayor cierre. "
        "Aparece universalmente en cualquier mercado. Base teórica de "
        "algoritmos VWAP."
    ),
)
def mm_smile(
    ticker: str = svc_mm.DEFAULT_TICKER,
    dias: int = 5,
    bucket_min: int = 30,
) -> dict:
    return svc_mm.get_smile(ticker=ticker, dias=dias, bucket_min=bucket_min)


@mcp.tool(
    description=(
        "MM Microstructure cap 3 — stylized facts de los retornos del activo: "
        "kurtosis, skewness, ACF lag-1 sobre mid (≈0 esperado, eficiencia "
        "direccional), ACF lag-1 sobre last (negativa esperada, bid-ask "
        "bounce), persistencia de ACF de |r| sobre 20 lags (>5 lags > 0.05 "
        "= volatility clustering), Jarque-Bera con p-value. Output incluye "
        "`interpretacion` con lecturas en español de los números. Retornos "
        "calculados sobre buckets de bucket_min sobre últimos `dias` con "
        "actividad."
    ),
)
def mm_stylized_facts(
    ticker: str = svc_mm.DEFAULT_TICKER,
    desde: str | None = None,
    hasta: str | None = None,
    dias: int = 5,
    bucket_min: int = 1,
) -> dict:
    return svc_mm.get_stylized_facts(
        ticker=ticker, desde=desde, hasta=hasta, dias=dias, bucket_min=bucket_min,
    )
