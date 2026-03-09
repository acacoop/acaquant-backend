# Constante de costos (Derecho de mercado 0.07% + IVA 21%)
FEE_TOTAL = 0.000847

def calcular_compra_usd(offer_ars, size_offer_ars, bid_usd, size_bid_usd, lote):
    """
    Simula: Entregar Pesos -> Recibir Dólares.
    FILTRO: Si falta una sola punta o el size es nulo, el TC NO EXISTE.
    """
    if offer_ars <= 0 or bid_usd <= 0 or size_offer_ars <= 0 or size_bid_usd <= 0:
        return 0, 0, 0

    # APLICACIÓN DE COSTOS
    precio_pagado_ars = offer_ars * (1 + FEE_TOTAL)
    precio_cobrado_usd = bid_usd * (1 - FEE_TOTAL)

    # 1. Tipo de Cambio NETO
    tc_neto = precio_pagado_ars / precio_cobrado_usd

    # 2. Size Ejecutable (Cuello de botella en nominales de pantalla)
    max_nominales = min(size_offer_ars, size_bid_usd)

    # 3. Volumen Real en USD que entran a la cuenta
    plata_usd_neta = (max_nominales / lote) * precio_cobrado_usd

    return tc_neto, max_nominales, plata_usd_neta


def calcular_venta_usd(bid_ars, size_bid_ars, offer_usd, size_offer_usd, lote):
    """
    Simula: Entregar Dólares -> Recibir Pesos.
    FILTRO: Si falta una sola punta o el size es nulo, el TC NO EXISTE.
    """
    if bid_ars <= 0 or offer_usd <= 0 or size_bid_ars <= 0 or size_offer_usd <= 0:
        return 0, 0, 0

    # APLICACIÓN DE COSTOS
    precio_cobrado_ars = bid_ars * (1 - FEE_TOTAL)
    precio_pagado_usd = offer_usd * (1 + FEE_TOTAL)

    # 1. Tipo de Cambio NETO
    tc_neto = precio_cobrado_ars / precio_pagado_usd

    # 2. Size Ejecutable (Cuello de botella en nominales)
    max_nominales = min(size_bid_ars, size_offer_usd)

    # 3. Volumen Real en USD que tenés que entregar (Brutos)
    plata_usd_pagada = (max_nominales / lote) * precio_pagado_usd

    return tc_neto, max_nominales, plata_usd_pagada