"""copiloto/registro.py — el registro VISTAS: ensambla cada vista (fetch,
extras, columnas, reglas, chips, módulo RBAC) importando su módulo. Fuente única
de qué vistas existen; lo consumen motor y derivacion."""
from __future__ import annotations

from .agro import _REGLAS_AGRO, _extras_agro, _fetch_agro
from .home import _REGLAS_HOME, _extras_home, _fetch_home
from .ons import _REGLAS_ONS, _extras_ons, _fetch_ons
from .opciones import _REGLAS_OPCIONES, _extras_opciones, _fetch_opciones
from .renta_fija import _REGLAS_RENTA_FIJA, _extras_renta_fija, _fetch_renta_fija
from .renta_variable import (
    _CHIPS_RENTA_VARIABLE,
    _REGLAS_RENTA_VARIABLE,
    _enriquecer_cedears,
    _extras_renta_variable,
    _fetch_cedears,
)
from .reuters import _REGLAS_REUTERS, _fetch_reuters
from .trading import _REGLAS_TRADING, _extras_trading, _fetch_trading

VISTAS: dict[str, dict] = {
    "home": {
        "titulo": "Home",
        "modulo": "home",
        "dominio": "panorama general del mercado — watchlist (MERVAL, riesgo país, "
                   "dólares MEP/CCL/oficial, canje, futuros globales y DLR), el briefing "
                   "del día y el trazo grueso de las curvas de bonos",
        "fetch": _fetch_home,
        "extras": _extras_home,
        "jerga_permitida": {"canje", "tna", "mep", "ccl", "carry", "bps", "brecha",
                            "riesgo", "caucion", "valor"},
        "chips": [
            {"label": "¿Cómo viene el mercado?",
             "pregunta": "El pulso de hoy POR SEGMENTO: renta fija (qué curva y qué "
                         "tramo se mueve), acciones, dólares y tasas, commodities e "
                         "índices. Cortito por segmento, sin mezclarlos, y cerrá con "
                         "qué mirar en la rueda."},
            {"label": "Dólares y brecha",
             "pregunta": "¿Cómo están MEP, CCL y oficial hoy y en el mes? ¿El canje "
                         "se abre o se cierra? Una lectura corta del panorama "
                         "cambiario, sin recomendación."},
        ],
        "columnas": [
            ("symbol", "instrumento"), ("grupo", "grupo"),
            ("last", "valor"), ("unit", "unidad"),
            ("pct_day", "var_dia%"), ("ret_7d", "ret_7d%"),
            ("ret_mtd", "ret_mes%"), ("ret_ytd", "ret_año%"),
        ],
        "reglas": _REGLAS_HOME,
    },
    "renta_variable": {
        "titulo": "Renta Variable",
        "modulo": "renta-variable",
        "dominio": "acciones, CEDEARs y ADRs — papeles de equity, retornos, "
                   "sectores, pivots, fundamentals",
        "chips": _CHIPS_RENTA_VARIABLE,
        "fetch": _fetch_cedears,
        "extras": _extras_renta_variable,
        "enriquecer": _enriquecer_cedears,
        # (campo interno, header que ve el modelo) — headers claros y bien
        # distintos entre sí: el modelo confundía mtd/ytd y firmaba mal signos
        "columnas": [
            ("ticker_corto", "ticker"), ("nombre", "nombre"),
            ("underlying", "subyacente"), ("ratio_cedear", "ratio"),
            ("sector", "sector"), ("rubro", "rubro"), ("pais", "pais"),
            ("es_ia", "ia"),
            ("last", "precio_ars"), ("intraday_pct", "var_apertura%"),
            ("vs_1d_pct", "var_dia%"), ("vs_1d_usd_pct", "var_dia_usd%"),
            ("bid", "compra"), ("offer", "venta"), ("spread_pct", "spread%"),
            ("vwap", "vwap"), ("volume", "nominales"), ("total_money", "monto_ars"),
            ("adr_last", "precio_usd_ny"), ("adr_intraday", "ny_hoy%"),
            ("adr_vs_1d_pct", "ny_dia%"),
            ("adr_ret_wtd_pct", "ret_semana%"), ("adr_ret_7d_pct", "ret_7d%"),
            ("adr_ret_15r_pct", "ret_15ruedas%"),
            ("ret_30r", "ret_30ruedas%"), ("ret_45r", "ret_45ruedas%"),
            ("adr_ret_mtd_pct", "ret_mes%"), ("adr_ret_ytd_pct", "ret_año%"),
            ("adr_dollar_vol", "monto_usd_ny"),
            ("piv_anual", "zona_piv_año"), ("piv_mensual", "zona_piv_mes"),
            ("max_serie", "max_hist_usd"), ("min_serie", "min_hist_usd"),
            ("dist_max", "dist_al_max%"),
        ],
        "reglas": _REGLAS_RENTA_VARIABLE,
    },
    "renta_fija": {
        "titulo": "Renta Fija",
        "modulo": "renta-fija",
        "dominio": "bonos y letras (soberanos, lecaps, CER, dollar linked) — "
                   "curvas, TEA, rendimientos, breakevens, carry, canje, fair value",
        "fetch": _fetch_renta_fija,
        "extras": _extras_renta_fija,
        # acá TEA/bps/duration/breakeven SON el idioma — no son jerga
        "jerga_permitida": {"tea", "tem", "paridad", "duration", "dur", "residuo",
                            "residuo_bps", "tc_breakeven", "tea_fit", "nominales_dia",
                            "meses", "vence", "curva", "fit", "bps", "precio"},
        "chips": [
            {"label": "Movimientos del día",
             "pregunta": "¿Cómo se movieron las curvas hoy contra el último cierre? "
                         "Qué comprimió, qué descomprimió, y si hay una historia detrás."},
            {"label": "Baratos vs curva",
             "pregunta": "¿Qué está barato y qué caro contra su curva hoy? Top 3 por "
                         "lado en tabla chica y una lectura que me diga el porqué — sin "
                         "repetir la tabla."},
            {"label": "Forwards desarbitrados",
             "pregunta": "¿Hay forwards lejos de su historia hoy? Contame si huele a "
                         "arbitraje o a cambio de régimen."},
            {"label": "Panorama de pesos",
             "pregunta": "Dame el panorama completo de pesos en 5 líneas: curvas, "
                         "carry, canje y qué mirar en la próxima rueda."},
            {"label": "¿Tasa fija o CER?",
             "pregunta": "Con los breakevens, plazos y rendimientos de hoy: ¿el mercado "
                         "está pagando ir a tasa fija o a CER? ¿Y conviene más el tramo "
                         "corto o el largo? Dame el cuadro con los supuestos de cada "
                         "camino, sin recomendación directa."},
        ],
        "columnas": [
            ("ticker_corto", "ticker"), ("curva_label", "curva"),
            ("fecha_vencimiento", "vence"), ("meses_al_vto", "meses"),
            ("ultimo_precio", "precio"), ("tea", "tea%"), ("tem", "tem%"),
            ("paridad", "paridad%"), ("duration", "dur"),
            ("tc_breakeven", "tc_breakeven"),
            ("tea_teorica", "tea_fit%"), ("residuo_bps", "residuo_bps"),
            ("total_nominals_dia", "nominales_dia"),
        ],
        "reglas": _REGLAS_RENTA_FIJA,
    },
    "trading": {
        "titulo": "Trading",
        "modulo": "trading",
        "dominio": "el monitor intradía del que está operando — sus tarjetas, "
                   "pivots en vivo, libro, tape, movers, y el quote US en vivo + "
                   "los fundamentals Reuters de sus papeles",
        "permitir_pivots": True,  # acá la nomenclatura PP/R1/S3 ES el idioma
        "fetch": _fetch_trading,
        "extras": _extras_trading,
        "chips": [
            {"label": "Mis tarjetas",
             "pregunta": "Estado de mis tarjetas: en qué zona está cada una y cuál "
                         "está peleando un nivel ahora. Tabla y una línea."},
            {"label": "Lectura del libro",
             "pregunta": "Leeme el libro y el tape del activo enfocado: ¿quién tiene "
                         "la presión y contra qué nivel está?"},
            {"label": "Movers en juego",
             "pregunta": "¿Qué se está moviendo fuerte hoy y cuáles de mis tarjetas "
                         "están en juego?"},
        ],
        "columnas": [
            ("ticker", "ticker"), ("foco", "foco"),
            ("last", "last"), ("vwap", "vwap"),
            ("dia_pct", "dia%"), ("rubro", "rubro"),
            ("high", "base_max"), ("low", "base_min"), ("close", "base_cierre"),
            ("pp", "PP"), ("r1", "R1"), ("r2", "R2"), ("r3", "R3"),
            ("s1", "S1"), ("s2", "S2"), ("s3", "S3"),
            ("zona", "zona_actual"), ("nivel_cercano", "nivel_cercano"),
        ],
        "reglas": _REGLAS_TRADING,
    },
    "reuters": {
        "titulo": "Reuters",
        # vive como tab dentro del módulo trading — mismo gate RBAC
        "modulo": "trading",
        "dominio": "precios en vivo en USD de los subyacentes US de los CEDEARs "
                   "suscriptos (fuente Reuters) — bid/ask de NY, pre y after market, "
                   "y retornos por período de 5 días a 5 años",
        "fetch": _fetch_reuters,
        # el idioma del tablero — bid/ask/pre/after acá son vocabulario nativo
        "jerga_permitida": {"bid", "ask", "spread", "pre", "after", "market",
                            "ratio", "ccl", "last", "gap"},
        "chips": [
            {"label": "Panorama del tablero",
             "pregunta": "¿Cómo vienen hoy los papeles del tablero? Qué sube, qué "
                         "baja, y si algo se está moviendo fuerte en el pre o el "
                         "after. Cortito."},
            {"label": "Mejores y peores retornos",
             "pregunta": "Rankeame los papeles por retornos: el mes en curso, el año "
                         "y 1 año móvil. ¿Quién viene ganando y quién quedó atrás? "
                         "Tabla chica y una lectura."},
            {"label": "Fuera de rueda",
             "pregunta": "¿Qué está pasando fuera de rueda? Pre market y after hours "
                         "de cada papel con su variación — y si alguno trae un gap "
                         "que la rueda local todavía no vio."},
        ],
        "columnas": [
            ("ticker", "ticker"), ("ric", "codigo_reuters"),
            ("last", "ultimo_usd"), ("bid", "compra"), ("ask", "venta"),
            ("high", "max_dia"), ("low", "min_dia"),
            ("prev_close", "cierre_ant"), ("volumen", "volumen"),
            ("var_pct", "var_dia%"), ("var_neta", "var_neta_usd"),
            ("pre_last", "pre_market"), ("pre_var_pct", "pre_market%"),
            ("ah_last", "after_hours"), ("ah_var_pct", "after_hours%"),
            ("ret_5d", "ret_5dias%"), ("ret_wtd", "ret_semana%"),
            ("ret_mtd", "ret_mes%"), ("ret_qtd", "ret_trimestre%"),
            ("ret_ytd", "ret_año%"),
            ("ret_1m", "ret_1mes_movil%"), ("ret_3m", "ret_3meses%"),
            ("ret_1y", "ret_1año_movil%"), ("ret_5y", "ret_5años%"),
            ("ratio", "ratio_cedear"), ("ccl", "ccl_implicito"),
            ("hora_dato", "hora_dato"),
        ],
        "reglas": _REGLAS_REUTERS,
    },
    "agro": {
        "titulo": "Agro",
        "modulo": "agro",
        "dominio": "granos (trigo, maíz, soja) — pizarra vs futuros Matba Rofex, "
                   "pase agro, pase con cobertura ON/Pagaré, precios de Cámara",
        "fetch": _fetch_agro,
        "extras": _extras_agro,
        # el idioma del productor/la mesa agro — no es jerga interna
        "jerga_permitida": {"pase", "tnav", "pizarra", "dispo", "bna", "matba",
                            "tna", "caucion", "commodity", "posicion", "vencimiento",
                            "tipo", "ars", "dias", "cobertura", "pagare"},
        "chips": [
            {"label": "Panorama agro",
             "pregunta": "¿Cómo están hoy trigo, maíz y soja? Pizarra contra futuros "
                         "y qué pase se destaca en cada uno. Cortito, por commodity."},
            {"label": "¿ON o Pagaré?",
             "pregunta": "Con las cards del pase con cobertura de hoy: ¿dónde da más "
                         "la vuelta, ON o Pagaré, y en qué posición? Números por "
                         "tonelada y el supuesto de cada camino, sin ordenarme uno."},
            {"label": "Datos de referencia",
             "pregunta": "¿Con qué dólares y tasas está calculado todo hoy? BNA, "
                         "Matba, BNA T-1, tasas ON/Pagaré/caución y el costo pase."},
        ],
        "columnas": [
            ("commodity", "commodity"), ("tipo", "tipo"), ("posicion", "posicion"),
            ("vencimiento", "vence"), ("dias", "dias"),
            ("us", "precio_usd_tn"), ("ars", "precio_ars_tn"),
            ("pase", "pase_usd_tn"), ("tnav", "tnav%"),
        ],
        "reglas": _REGLAS_AGRO,
    },
    "derivados": {
        "titulo": "Opciones",
        "modulo": "derivados",
        "dominio": "opciones financieras (cadena sobre GGAL) — calls y puts, strikes, "
                   "primas, volatilidad implícita, griegas",
        "fetch": _fetch_opciones,
        "extras": _extras_opciones,
        # vocabulario nativo de opciones — no es jerga interna. "adr" acá es
        # legítimo: la vol realizada de referencia viene local Y del ADR
        # (batería 2026-07-14: la autocorrección lo borraba al citarla).
        "jerga_permitida": {"strike", "call", "put", "prima", "spot", "atm", "vega",
                            "delta", "gamma", "theta", "vencimiento", "contrato",
                            "vence", "vol", "subyacente", "adr"},
        "chips": [
            {"label": "Panorama de la chain",
             "pregunta": "¿Cómo está la cadena hoy? Dónde está el spot, qué "
                         "vencimientos concentran el volumen y en qué strikes está "
                         "la actividad. Cortito."},
            {"label": "¿La vol está cara?",
             "pregunta": "¿Qué volatilidad implícita paga cada vencimiento cerca del "
                         "spot y cómo queda contra la vol realizada de referencia? "
                         "¿El seguro está caro o barato hoy?"},
            {"label": "Calls vs puts",
             "pregunta": "¿Dónde está la actividad hoy, en calls o en puts, y en qué "
                         "strikes? Leelo por volumen efectivo, no por cantidad de "
                         "contratos listados."},
        ],
        "columnas": [
            ("instrumento", "contrato"), ("tipo", "tipo"), ("strike", "strike"),
            ("vence", "vence"), ("bid", "compra"), ("offer", "venta"),
            ("last", "ultima_prima"), ("closing_price", "cierre_ant"),
            ("ev", "vol_efectivo"), ("iv", "iv%"),
            ("delta", "delta"), ("gamma", "gamma"), ("theta", "theta"),
            ("vega", "vega"), ("spot", "spot_subyacente"),
        ],
        "reglas": _REGLAS_OPCIONES,
    },
    "ons": {
        "titulo": "ONs",
        # la página /ons vive bajo el módulo renta-fija en la nav — mismo gate
        "modulo": "renta-fija",
        "dominio": "obligaciones negociables — deuda corporativa por sector "
                   "(energía/finanzas/otros), TEA, vencimientos, cupones y "
                   "amortizaciones que vienen",
        "fetch": _fetch_ons,
        "extras": _extras_ons,
        # mismo idioma que renta fija
        "jerga_permitida": {"tea", "paridad", "duration", "dur", "bps", "curva",
                            "cupon", "emisor", "moneda", "meses", "precio", "vence",
                            "vn", "nominales"},
        "chips": [
            {"label": "Panorama de ONs",
             "pregunta": "¿Cómo está la curva de ONs hoy? TEA por sector y moneda, "
                         "y qué papeles operaron de verdad. Cortito."},
            {"label": "Mejores TEA en USD",
             "pregunta": "¿Qué ONs en dólares rinden más hoy entre las que tienen "
                         "volumen real? Tabla chica: ticker | emisor | TEA | vence — "
                         "y marcá dónde la liquidez obliga a tomar el dato con pinzas."},
            {"label": "Pagos próximos",
             "pregunta": "¿Qué cupones y amortizaciones de ONs vienen en los próximos "
                         "90 días? Ordenado por fecha, con emisor y monto por 100 VN."},
        ],
        "columnas": [
            ("ticker_corto", "ticker"), ("emisor", "emisor"),
            ("sector_label", "sector"), ("moneda", "moneda"),
            ("fecha_vencimiento", "vence"), ("meses_al_vto", "meses"),
            ("ultimo_precio", "precio"), ("tea", "tea%"), ("duration", "dur"),
            ("paridad", "paridad%"), ("total_nominals_dia", "nominales_dia"),
        ],
        "reglas": _REGLAS_ONS,
    },
}
