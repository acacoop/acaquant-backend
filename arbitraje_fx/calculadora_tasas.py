# Constante de costos (Derecho de mercado 0.07% + IVA 21%)
FEE_TOTAL = 0.000847


def calcular_trade_a(offer_ci, size_offer_ci, bid_24hs, size_bid_24hs, tna_caucion_tomadora, dias_plazo, lote):
    """
    TRADE A (Sintético Tomador):
    Comprar en CI (pagás Offer hoy) -> Vender en 24hs (cobrás Bid mañana).
    Se financia tomando pesos prestados en Caución.

    Retorna: (tna_implicita_neta, spread_tna_neto, max_nominales, capital_requerido_ars)
    """
    # Filtro de seguridad: si no hay liquidez o la caución no existe, no calculamos nada
    if offer_ci <= 0 or bid_24hs <= 0 or size_offer_ci <= 0 or size_bid_24hs <= 0 or tna_caucion_tomadora <= 0:
        return 0, 0, 0, 0

    # 1. APLICACIÓN DE COSTOS (Comprar te sale más caro, vender te da menos plata)
    precio_compra_neto = offer_ci * (1 + FEE_TOTAL)
    precio_venta_neto = bid_24hs * (1 - FEE_TOTAL)

    # Si aún sin contar el costo del dinero, ya perdés plata por precio, abortamos
    if precio_compra_neto >= precio_venta_neto:
        return 0, 0, 0, 0

    # 2. CALCULO DE TASA IMPLÍCITA NETA
    retorno_directo = (precio_venta_neto / precio_compra_neto) - 1

    # Lo pasamos a TNA (Tasa Nominal Anual) multiplicando por 365 y dividiendo por los días del pase
    tna_implicita_neta = retorno_directo * (365 / dias_plazo) * 100

    # 3. SPREAD REAL (El alfa del trade)
    # Cuántos puntos de tasa le ganás limpiamente al costo de pedir la plata prestada
    spread_tna_neto = tna_implicita_neta - tna_caucion_tomadora

    # 4. VOLUMEN Y CAPITAL (Cuello de botella)
    max_nominales = min(size_offer_ci, size_bid_24hs)
    capital_requerido_ars = (max_nominales / lote) * precio_compra_neto

    return tna_implicita_neta, spread_tna_neto, max_nominales, capital_requerido_ars