Valida el motor de pricing de opciones Black-Scholes: precio (put-call parity ATM, intrínseco en vencimiento, deep ITM), los Greeks (delta ITM/OTM/ATM, gamma y vega siempre positivos, theta del call negativo) y la volatilidad implícita por round-trip (precio sintético con σ conocida → `find_iv` debe recuperarla). También cubre intrínseco/extrínseco y bordes (precio cercano al intrínseco → IV=0).

Conecta con: blinda `quant/black_scholes.py`, el cálculo puro que alimenta el módulo de opciones (`api/services/opciones.py`) y el motor de opciones.
