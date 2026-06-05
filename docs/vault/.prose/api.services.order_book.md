Devuelve el libro de órdenes (LOB) live de un ticker, con profundidad 5 (bids/offers) más precios open/high/low/last/cierre. Sin histórico: solo el último estado vivo. Acepta ticker completo o corto+plazo y cubre todos los tickers que el motor suscribe, no solo los de curva. Latencia ~10-50ms.

Conecta con: lee `Trading.MarketSnapshot` (lo popula `engines/valores.py` cada 1s desde pyRofex WS) con proyección acotada al libro; cachea la lista de tickers por curva desde `Trading.Curvas`. Lo invoca el router de cotizaciones / order book.
