import pyRofex


class WebSocketManager:
    def __init__(self, market_manager):
        """
        market_manager: Instancia del cerebro que guarda los precios.
        """
        self.mm = market_manager

    def _handler_mercado(self, message):
        """Handler interno que traduce el mensaje de Rofex para el MarketManager"""
        try:
            ticker = message['instrumentId']['symbol']
            data = message['marketData']
            # Le pasamos la data cruda al gestor de mercado
            self.mm.update_price(ticker, data)
        except Exception as e:
            # En producción podrías usar un logger aquí
            pass

    def iniciar_ws(self, lista_tickers):
        """Configura la suscripción e inicia la conexión viva"""
        try:
            # 1. Definimos qué función va a recibir los datos
            pyRofex.add_websocket_market_data_handler(self._handler_mercado)

            # 2. Nos suscribimos a los instrumentos filtrados
            # Pedimos Puntas (BI/OF) y Último operado (LA)
            pyRofex.market_data_subscription(
                tickers=lista_tickers,
                entries=[
                    pyRofex.MarketDataEntry.BIDS,
                    pyRofex.MarketDataEntry.OFFERS,
                    pyRofex.MarketDataEntry.LAST
                ]
            )

            # 3. Abrimos la conexión (esto corre en un hilo separado de fondo)
            pyRofex.init_websocket_connection()
            print(f"📡 WebSocket conectado y suscripto a {len(lista_tickers)} activos.")
            return True
        except Exception as e:
            print(f"❌ Error al iniciar WebSocket: {e}")
            return False

    def cerrar_ws(self):
        """Cierre seguro de la conexión"""
        pyRofex.close_websocket_connection()
        print("🛑 WebSocket desconectado.")