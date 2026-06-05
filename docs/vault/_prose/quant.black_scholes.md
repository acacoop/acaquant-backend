Modelo Black-Scholes para opciones: precio teórico (`bs_price`), las griegas (delta, gamma, vega, theta) y la volatilidad implícita por Newton-Raphson (`find_iv`). Suma helpers de valor intrínseco/extrínseco y un cálculo de volatilidad histórica a 40 ruedas anualizada (√260) que lee retornos logarítmicos directo de Mongo.

Conecta con: `calcular_hv_40_ruedas` lee `Opciones.VR-GGal` en Mongo. Lo importa `engines/options.py` (motor de opciones GGAL) para valuar la chain y publicar griegas/IV. El resto de funciones son puras (numpy/scipy), sin dependencia de DB.
