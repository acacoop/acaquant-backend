import pyRofex
import time  # <-- AGREGADO PARA LA MICROPAUSA


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

    # --- CAMBIO: Agregamos depth=1 como parámetro por defecto ---
    def iniciar_ws(self, lista_tickers, depth=1):
        """Configura la suscripción e inicia la conexión viva"""
        try:
            # 1. Definimos qué función va a recibir los datos
            pyRofex.add_websocket_market_data_handler(self._handler_mercado)

            # 2. Nos suscribimos a los instrumentos filtrados (AHORA POR LOTES)
            chunk_size = 50
            for i in range(0, len(lista_tickers), chunk_size):
                chunk = lista_tickers[i:i + chunk_size]

                # --- ACÁ ESTABA EL CUELLO DE BOTELLA ---
                # Ahora sí le exigimos al broker que nos mande la data completa
                pyRofex.market_data_subscription(
                    tickers=chunk,
                    entries=[
                        pyRofex.MarketDataEntry.BIDS,
                        pyRofex.MarketDataEntry.OFFERS,
                        pyRofex.MarketDataEntry.LAST,
                        pyRofex.MarketDataEntry.OPENING_PRICE,
                        pyRofex.MarketDataEntry.HIGH_PRICE,
                        pyRofex.MarketDataEntry.LOW_PRICE,
                        pyRofex.MarketDataEntry.CLOSING_PRICE,
                        pyRofex.MarketDataEntry.TRADE_EFFECTIVE_VOLUME,
                        pyRofex.MarketDataEntry.NOMINAL_VOLUME
                    ],
                    depth=depth
                )
                time.sleep(0.01)  # <-- AGREGADO: Micropausa para que el broker asimile el lote

            # 3. Abrimos la conexión (esto corre en un hilo separado de fondo)
            pyRofex.init_websocket_connection()
            print(
                f"📡 WebSocket conectado y suscripto a {len(lista_tickers)} activos (Lotes: {chunk_size}, Profundidad: {depth}).")
            return True
        except Exception as e:
            print(f"❌ Error al iniciar WebSocket: {e}")
            return False

    def cerrar_ws(self):
        """Cierre seguro de la conexión"""
        pyRofex.close_websocket_connection()
        print("🛑 WebSocket desconectado.")