# estrategias_opciones.py

ESTRATEGIAS = [
    # --- 1. SPREADS DIRECCIONALES CLÁSICOS (BULL / BEAR) ---
    {'nombre': 'Bull Call 61/63', 'tipo': 'bull', 'patas': [
        {'symbol': 'MERV - XMEV - GFGC61262A - 24hs', 'lado': 'compra', 'ratio': 1},
        {'symbol': 'MERV - XMEV - GFGC63747A - 24hs', 'lado': 'venta', 'ratio': 1}
    ]},
    {'nombre': 'Bull Call 63/67', 'tipo': 'bull', 'patas': [
        {'symbol': 'MERV - XMEV - GFGC63747A - 24hs', 'lado': 'compra', 'ratio': 1},
        {'symbol': 'MERV - XMEV - GFGC67747A - 24hs', 'lado': 'venta', 'ratio': 1}
    ]},
    {'nombre': 'Bull Call 67/69', 'tipo': 'bull', 'patas': [
        {'symbol': 'MERV - XMEV - GFGC67747A - 24hs', 'lado': 'compra', 'ratio': 1},
        {'symbol': 'MERV - XMEV - GFGC69029A - 24hs', 'lado': 'venta', 'ratio': 1}
    ]},
    {'nombre': 'Bull Call 69/71', 'tipo': 'bull', 'patas': [
        {'symbol': 'MERV - XMEV - GFGC69029A - 24hs', 'lado': 'compra', 'ratio': 1},
        {'symbol': 'MERV - XMEV - GFGC71747A - 24hs', 'lado': 'venta', 'ratio': 1}
    ]},
     {'nombre': 'Bull Call 69/75', 'tipo': 'bull', 'patas': [
        {'symbol': 'MERV - XMEV - GFGC69029A - 24hs', 'lado': 'compra', 'ratio': 1},
        {'symbol': 'MERV - XMEV - GFGC75029A - 24hs', 'lado': 'venta', 'ratio': 1}
    ]},
    {'nombre': 'Bull Call 71/73', 'tipo': 'bull', 'patas': [
        {'symbol': 'MERV - XMEV - GFGC71747A - 24hs', 'lado': 'compra', 'ratio': 1},
        {'symbol': 'MERV - XMEV - GFGC73262A - 24hs', 'lado': 'venta', 'ratio': 1}
    ]},
    {'nombre': 'Bull Call 73/75', 'tipo': 'bull', 'patas': [
        {'symbol': 'MERV - XMEV - GFGC73262A - 24hs', 'lado': 'compra', 'ratio': 1},
        {'symbol': 'MERV - XMEV - GFGC75029A - 24hs', 'lado': 'venta', 'ratio': 1}
    ]},
    {'nombre': 'Bull Call 75/77', 'tipo': 'bull', 'patas': [
        {'symbol': 'MERV - XMEV - GFGC75029A - 24hs', 'lado': 'compra', 'ratio': 1},
        {'symbol': 'MERV - XMEV - GFGC77262A - 24hs', 'lado': 'venta', 'ratio': 1}
    ]},
    {'nombre': 'Bull Call 77/79', 'tipo': 'bull', 'patas': [
        {'symbol': 'MERV - XMEV - GFGC75029A - 24hs', 'lado': 'compra', 'ratio': 1},
        {'symbol': 'MERV - XMEV - GFGC79262A - 24hs', 'lado': 'venta', 'ratio': 1}
    ]},
    {'nombre': 'Bear Put  63/59', 'tipo': 'bear', 'patas': [
        {'symbol': 'MERV - XMEV - GFGV63747A - 24hs', 'lado': 'compra', 'ratio': 1},
        {'symbol': 'MERV - XMEV - GFGV59501A - 24hs', 'lado': 'venta', 'ratio': 1}
    ]},
    {'nombre': 'Bear Put  65/61', 'tipo': 'bear', 'patas': [
        {'symbol': 'MERV - XMEV - GFGV65747A - 24hs', 'lado': 'compra', 'ratio': 1},
        {'symbol': 'MERV - XMEV - GFGV61262A - 24hs', 'lado': 'venta', 'ratio': 1}
    ]},

    {'nombre': 'Bear Put ITM  71/69', 'tipo': 'bear', 'patas': [
        {'symbol': 'MERV - XMEV - GFGV71747A - 24hs', 'lado': 'compra', 'ratio': 1},
        {'symbol': 'MERV - XMEV - GFGV69029A - 24hs', 'lado': 'venta', 'ratio': 1}
    ]},

    {'nombre': 'Put Backspread 1x2 (V67/C61)', 'tipo': 'ratio_comprado', 'patas': [
        {'symbol': 'MERV - XMEV - GFGV67747A - 24hs', 'lado': 'venta', 'ratio': 1},
        {'symbol': 'MERV - XMEV - GFGV61262A - 24hs', 'lado': 'compra', 'ratio': 2}
    ]},

    # 4. EL "COBRADOR DE TASAS BAJISTA" (Put Front Ratio 1x2)
    # Comprás 1 Put 6574 (Pagás 405) y Vendés 2 Puts 5950 (Cobrás 142 x 2 = 284).
    # Costo neto ridículo ($120 pesos). Ganás el máximo si GGAL frena exacto en 5950.
    # Ideal si creés que GGAL baja, pero estás 100% seguro de que de 5900 no pasa.
    {'nombre': 'Put Front Ratio 1x2 (C65/V59)', 'tipo': 'ratio_vendido', 'patas': [
        {'symbol': 'MERV - XMEV - GFGV65747A - 24hs', 'lado': 'compra', 'ratio': 1},
        {'symbol': 'MERV - XMEV - GFGV59501A - 24hs', 'lado': 'venta', 'ratio': 2}
    ]}

]
