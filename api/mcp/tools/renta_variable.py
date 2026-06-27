"""Tools MCP de RENTA VARIABLE — asistente 100% de equities ARG.

Universo CEDEARs/ADRs, live de mercado, time sales intradía, retornos y stats
quant del subyacente USD, pivot points, day-trading lab y la Mesa de Estrategia
(correlación / dimensionado de trades / análisis de book).

Todo SOLO LECTURA y 100% datos de MERCADO. NO toca cuentas, AuM, operaciones ni
nada de la ALyC — esos dominios no viven acá por diseño (REGLA #8).

Ruteo: TODO pasa por `api/services/scanner_sql.py` (fachada SQL-native sobre
`mercado.*`, que además reexporta las piezas Mongo-only vivas: tape `CedearsTimeSales`
y CCL `Valuaciones.Dolar`), `api/services/day_trading.py` y `api/services/rv_motor.py`.
Se evita a propósito `api/services/scanner.py` (path Mongo legacy): sus colecciones
`Trading.{CedearsSnapshot,AdrSnapshot,PreciosAcciones}` fueron dropeadas en el
cutover SQL del 2026-06-24.
"""
from __future__ import annotations

from api.services import day_trading as svc_dt
from api.services import rv_motor as svc_rv
from api.services import scanner_sql as svc


def register(mcp) -> None:
    """Registra las tools de renta variable sobre la instancia FastMCP."""

    # ─────────────────────────────────────────────────────────────────
    # Universo / catálogo
    # ─────────────────────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "Catálogo de CEDEARs argentinos activos (SIN precios). Una fila por "
            "papel con su clasificación: ticker_corto (BYMA, ej 'AAPL'), nombre, "
            "underlying (símbolo US del subyacente), ratio_cedear, sector, rubro "
            "(clasificación de negocio más granular), es_ia (bool, ecosistema IA), "
            "industria, región y país. USAR PRIMERO para descubrir qué papeles "
            "existen y filtrar por sector/rubro/región/IA antes de pedir live, "
            "retornos o quant. Ordenado A→Z por ticker_corto."
        ),
    )
    def rv_universo() -> list[dict]:
        return svc.get_universo()

    # ─────────────────────────────────────────────────────────────────
    # Live de mercado (CEDEAR ARS + ADR USD + CCL)
    # ─────────────────────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "Scanner LIVE de todos los CEDEARs activos. Por papel: master "
            "(ticker_corto, nombre, underlying, ratio, sector/rubro/es_ia/"
            "industria/región/país), CEDEAR en ARS (last, open/high/low/close, "
            "intraday_pct, vs_1d_pct, vs_1d_usd_pct = variación descontando el CCL, "
            "bid/offer/spread/spread_pct, vwap, volume nominal, total_money) y el "
            "ADR del subyacente en USD (adr_last, adr_intraday, adr_vs_1d_pct y "
            "retornos adr_ret_wtd/7d/15r/mtd/ytd_pct, adr_dollar_vol). updated_at por "
            "papel. Refresca cada 1s en rueda (motor_cedears → mercado.cedears_snapshot). "
            "Foto completa del tablero de renta variable en una sola llamada."
        ),
    )
    def cedears_scanner() -> list[dict]:
        return svc.get_cedears_scanner()

    @mcp.tool(
        description=(
            "Dólar CCL live con su variación vs el cierre del día previo. Devuelve "
            "{value, vs_1d_pct, timestamp}. Es el tipo de cambio implícito que separa "
            "el retorno en ARS del retorno en USD de un CEDEAR."
        ),
    )
    def ccl_live() -> dict:
        return svc.get_ccl_live()

    # ─────────────────────────────────────────────────────────────────
    # Time sales / tape intradía
    # ─────────────────────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "TIME & SALES (tape) intradía de un CEDEAR: los trades crudos de HOY, "
            "más recientes primero. Cada trade trae {timestamp (UTC), price, size "
            "(nominales), side ('BUY'|'SELL'|'MID'), money (price×size)}. ticker es "
            "el ticker_corto BYMA ('AAPL'). limite 1-1000 (default 200). Lee "
            "Trading.CedearsTimeSales (se vacía al cierre → solo datos en rueda)."
        ),
    )
    def cedears_tape(ticker: str, limite: int = 200) -> list[dict]:
        return svc.get_cedears_trades(ticker=ticker, limite=limite)

    @mcp.tool(
        description=(
            "Tape de HOY de un CEDEAR AGREGADO por minuto: velas OHLC + volumen por "
            "minuto (UTC), orden ascendente. Para reconstruir el intradía / chart "
            "minuto a minuto de un papel. ticker es el ticker_corto BYMA. Solo hay "
            "datos en horario de rueda (la tape se vacía al cierre)."
        ),
    )
    def cedears_intraday(ticker: str) -> list[dict]:
        return svc.get_cedears_intraday(ticker=ticker)

    # ─────────────────────────────────────────────────────────────────
    # Histórico EOD + quant del subyacente USD
    # ─────────────────────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "Serie de retornos diarios aritméticos del último año (~252 puntos) del "
            "SUBYACENTE USD de un CEDEAR, desde mercado.precios_acciones. Devuelve "
            "{ticker, returns:[float], last_return, last_fecha}. ticker es el "
            "ticker_corto BYMA (resuelve el underlying: YPFD→YPF). Para histogramas "
            "de distribución de retornos y análisis de cola."
        ),
    )
    def acciones_retornos(ticker: str) -> dict:
        return svc.get_ticker_returns(ticker=ticker)

    @mcp.tool(
        description=(
            "Stats rolling del SUBYACENTE USD de un CEDEAR: beta, alpha anualizada y "
            "correlación vs SPY y vs QQQ, más volatilidad realizada anualizada a 30 "
            "y 60 días, y z-score del último retorno. ticker es el ticker_corto BYMA. "
            "Para caracterizar el riesgo de mercado de un papel."
        ),
    )
    def acciones_quant_stats(ticker: str) -> dict:
        return svc.get_quant_stats(ticker=ticker)

    @mcp.tool(
        description=(
            "Pivot points Floor Trader en 4 timeframes (diario/semanal/mensual/"
            "anual) del SUBYACENTE USD de un CEDEAR: PP, R1-R3, S1-S3 con el OHLC "
            "del período previo. El diario usa solo el día previo; semanal/mensual/"
            "anual agregan toda la ventana. El `last` se pisa con el live del ADR si "
            "está disponible. ticker es el ticker_corto BYMA. Para niveles de soporte/"
            "resistencia."
        ),
    )
    def pivot_points(ticker: str) -> dict:
        return svc.get_pivot_points(ticker=ticker)

    # ─────────────────────────────────────────────────────────────────
    # Day-trading lab (intradía, scalping)
    # ─────────────────────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "TRADE LAB intradía: ranking de CEDEARs para scalping según un objetivo "
            "de captura en % (0.1-5, default 0.5). Por papel: VUELTAS (movimientos "
            "zigzag completos ≥ objetivo que ya hizo HOY, del tape por minuto), rango "
            "del día y posición en él (0=piso, 100=techo), pata EN CURSO (dir + % "
            "recorrido), momentum 15' por reloj, lado del VWAP, spread bid/offer en %, "
            "flujo comprador (% de la plata del día y de los últimos 30' que fue compra "
            "agresora), volumen en cash y nominales, minutos sin operar, costumbre "
            "histórica (vueltas promedio por rueda, ~20 ruedas) e idea heurística "
            "LONG/SHORT con motivo. Solo tiene datos en rueda (en_rueda=false fuera "
            "de hora)."
        ),
    )
    def day_trading_scanner(objetivo_pct: float = 0.5) -> dict:
        return svc_dt.get_day_trading(objetivo_pct=objetivo_pct)

    @mcp.tool(
        description=(
            "Con qué papeles 'se mueve' un CEDEAR: top n más correlacionados (con) y "
            "más anti-correlacionados (contra) por Pearson de cierres diarios del "
            "subyacente USD (ventana 252 ruedas). Para armar pares, espejos short de "
            "un long, o no duplicar la misma apuesta. ticker es el ticker_corto BYMA."
        ),
    )
    def day_trading_companeros(ticker: str, n: int = 6) -> dict:
        return svc_dt.get_companeros(ticker=ticker, n=n)

    # ─────────────────────────────────────────────────────────────────
    # Mesa de Estrategia (correlación / dimensionado / book)
    # ─────────────────────────────────────────────────────────────────

    @mcp.tool(
        description=(
            "Matriz de correlación de retornos diarios USD entre CEDEARs + volatilidad "
            "anualizada por papel. tickers: CSV de ticker_corto ('AAPL,MSFT,NVDA') o "
            "vacío = TODO el universo. ventana_dias (default 252). Devuelve {tickers "
            "(efectivamente incluidos, ≥30 obs), excluidos, n_obs, fecha_desde/hasta, "
            "matriz [[float|None]] simétrica con 1.0 en la diagonal, vol_anual:{ticker:"
            "float}}. Base para hedging, pares y diversificación."
        ),
    )
    def correlacion_matriz(tickers: str | None = None, ventana_dias: int = 252) -> dict:
        tickers_t: tuple[str, ...] | None = None
        if tickers:
            tickers_t = tuple(t.strip().upper() for t in tickers.split(",") if t.strip()) or None
        return svc_rv.get_correlation_matrix(tickers=tickers_t, ventana_dias=ventana_dias)

    @mcp.tool(
        description=(
            "Dimensiona y caracteriza un trade HIPOTÉTICO de un CEDEAR (no lee "
            "posiciones reales). Inputs: ticker (ticker_corto BYMA), monto (USD), "
            "direccion ('long'|'short'). Devuelve caracterización (last, vol 30/60d, "
            "beta SPY/QQQ, z-score, VaR 1d 95% en USD y %, peor mes 1σ, exposición de "
            "mercado equivalente), hedge por beta (notional a operar vs SPY/QQQ para "
            "neutralizar mercado) y hedge-finder (universo rankeado por correlación "
            "con hedge_ratio de mínima varianza y reducción de vol esperada). Todo "
            "sobre el precio del subyacente USD."
        ),
    )
    def trade_analysis(ticker: str, monto: float, direccion: str = "long") -> dict:
        return svc_rv.get_trade_analysis(ticker=ticker, monto=monto, direccion=direccion)

    @mcp.tool(
        description=(
            "Analiza el riesgo de un BOOK de CEDEARs que vos describís (no lee "
            "posiciones reales ni AuM). posiciones: CSV 'TICKER:NOTIONAL' separado "
            "por comas, notional en USD, NEGATIVO = short. Ej: 'AAPL:10000,TSLA:-5000'. "
            "Devuelve composición (gross/net/n), exposición por sector y región, "
            "concentración (pct_top5, HHI), riesgo agregado (vol anual del book, VaR "
            "1d 95% USD, exposición de mercado USD vs SPY/QQQ), contribución de riesgo "
            "por papel y excluidos (sin historia suficiente). Todo sobre el subyacente "
            "USD."
        ),
    )
    def book_analysis(posiciones: str) -> dict:
        parsed: list[tuple[str, float]] = []
        for chunk in (posiciones or "").split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if ":" not in chunk:
                return {"error": f"posición malformada: {chunk!r} (esperado 'TICKER:NOTIONAL')"}
            tk, _, notional = chunk.partition(":")
            try:
                parsed.append((tk.strip().upper(), float(notional.strip())))
            except ValueError:
                return {"error": f"notional no numérico en {chunk!r}"}
        if not parsed:
            return {"error": "posiciones vacío (esperado 'TICKER:NOTIONAL,...')"}
        return svc_rv.get_book_analysis(posiciones=tuple(parsed))
