import config
import logging
import time

logger = logging.getLogger("TradingBot")


class MarketManager:
    def __init__(self):
        self.last_update_time = time.time()
        # Diccionario inicializado con ceros para evitar KeyErrors
        self.precios_vivos = {
            t: {
                'bid': 0.0, 'bid_size': 0,
                'offer': 0.0, 'offer_size': 0,
                'last': 0.0, 'last_size': 0
            }
            for t in config.TICKERS_LIST
        }
        logger.info(f"MarketManager iniciado con {len(config.TICKERS_LIST)} activos.")

    def update_price(self, ticker, data):
        """
        Actualiza los precios con validación de existencia total.
        Si un ticker no viene en la data, esta función simplemente no hace nada
        y mantiene los valores previos (o ceros).
        """
        # 1. Validación de seguridad: ¿El ticker es de los que nos interesa?
        if not ticker or ticker not in self.precios_vivos:
            return

        try:
            self.last_update_time = time.time()
            p = self.precios_vivos[ticker]

            # 2. Procesamiento SEGURO de Bids
            # Chequeamos que sea lista, tenga elementos y que el elemento tenga la llave 'price'
            bi = data.get('BI', [])
            if isinstance(bi, list) and len(bi) > 0:
                first_bi = bi[0]
                if isinstance(first_bi, dict):
                    p['bid'] = float(first_bi.get('price', p['bid']))
                    p['bid_size'] = int(first_bi.get('size', p['bid_size']))

            # 3. Procesamiento SEGURO de Offers
            of = data.get('OF', [])
            if isinstance(of, list) and len(of) > 0:
                first_of = of[0]
                if isinstance(first_of, dict):
                    p['offer'] = float(first_of.get('price', p['offer']))
                    p['offer_size'] = int(first_of.get('size', p['offer_size']))

            # 4. Procesamiento SEGURO de Last (Trade)
            la = data.get('LA')
            if isinstance(la, dict):
                p['last'] = float(la.get('price', p['last']))
                p['last_size'] = int(la.get('size', p['last_size']))

        except Exception as e:
            # Captura cualquier error de conversión (float/int) para que el bot no muera
            logger.error(f"⚠️ Error procesando ticker {ticker}: {e}")

    def check_health(self):
        """Detección de inactividad (30 segundos)."""
        return (time.time() - self.last_update_time) <= 30

    def get_data_for_excel(self):
        """
        Genera la matriz para Excel respetando el orden de config.py.
        Si un ticker nunca se encontró, enviará los 0 iniciales.
        """
        headers = ["Ticker", "Bid", "Bid Size", "Offer", "Offer Size", "Last", "Last Size"]
        matriz = [headers]

        for full_name in config.TICKERS_LIST:
            # .get() con fallback a ceros por si config.TICKERS_LIST cambió en caliente
            p = self.precios_vivos.get(full_name, {
                'bid': 0.0, 'bid_size': 0, 'offer': 0.0, 'offer_size': 0, 'last': 0.0, 'last_size': 0
            })

            matriz.append([
                full_name,
                p['bid'],
                p['bid_size'],
                p['offer'],
                p['offer_size'],
                p['last'],
                p['last_size']
            ])

        return matriz