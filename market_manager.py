import config
import logging
import time

# Obtenemos el logger configurado en el main
logger = logging.getLogger("TradingBot")


class MarketManager:
    def __init__(self):
        """
        Gestor de precios estático. Mantiene el orden del config.py
        """
        # Marcador para el Watchdog
        self.last_update_time = time.time()

        # Memoria viva de precios inicializada con los tickers del config
        self.precios_vivos = {
            t: {'bid': 0.0, 'bid_size': 0, 'offer': 0.0, 'offer_size': 0}
            for t in config.TICKERS_LIST
        }
        logger.info(f"MarketManager iniciado con {len(config.TICKERS_LIST)} activos (MODO ESTÁTICO).")

    def update_price(self, ticker, data):
        """Actualiza los precios en memoria."""
        if ticker in self.precios_vivos:
            try:
                self.last_update_time = time.time()
                p = self.precios_vivos[ticker]

                if data.get('BI'):
                    p['bid'] = float(data['BI'][0]['price'])
                    p['bid_size'] = int(data['BI'][0]['size'])
                if data.get('OF'):
                    p['offer'] = float(data['OF'][0]['price'])
                    p['offer_size'] = int(data['OF'][0]['size'])
            except Exception as e:
                logger.error(f"Error al parsear data de {ticker}: {e}")

    def check_health(self):
        """
        Detección de desconexión.
        Subimos a 30 segundos para evitar falsos positivos por lentitud.
        """
        tiempo_inactivo = time.time() - self.last_update_time

        if tiempo_inactivo > 30:  # <--- SUBIMOS A 30
            return False
        return True

    def get_data_for_excel(self):
        """
        Genera la matriz respetando el ORDEN EXACTO del config.py.
        SIN FILTROS: Cada ticker tiene su fila fija.
        """
        headers = ["Ticker", "Bid", "Bid Size", "Offer", "Offer Size"]
        matriz = [headers]

        # Iteramos sobre la lista de config para mantener el orden y la posición
        for full_name in config.TICKERS_LIST:
            p = self.precios_vivos.get(full_name)

            # Agregamos la fila SIEMPRE (tenga o no tenga puntas)
            # Si no hay data, mandará los 0.0 iniciales
            matriz.append([
                full_name,
                p['bid'],
                p['bid_size'],
                p['offer'],
                p['offer_size']
            ])

        return matriz