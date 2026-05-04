import time  # <-- AGREGADO PARA LA MICROPAUSA
from typing import ClassVar

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
        except Exception:
            # En producción podrías usar un logger aquí
            pass

    _ENTRIES: ClassVar[list] = [
        pyRofex.MarketDataEntry.BIDS,
        pyRofex.MarketDataEntry.OFFERS,
        pyRofex.MarketDataEntry.LAST,
        pyRofex.MarketDataEntry.OPENING_PRICE,
        pyRofex.MarketDataEntry.HIGH_PRICE,
        pyRofex.MarketDataEntry.LOW_PRICE,
        pyRofex.MarketDataEntry.CLOSING_PRICE,
        pyRofex.MarketDataEntry.TRADE_EFFECTIVE_VOLUME,
        pyRofex.MarketDataEntry.NOMINAL_VOLUME,
    ]

    def agregar_suscripciones(self, lista_tickers, depth=1, entries=None):
        """Suscribe tickers adicionales sin reabrir la conexión WS.

        Útil para rotación dinámica (ej. nuevos strikes de opciones que
        aparecen durante la rueda). pyRofex.market_data_subscription es
        aditivo — se puede llamar varias veces sobre el mismo socket.

        `entries` opcional permite suscribir un subconjunto distinto al
        default (ej. el motor de order book L2 solo pide BIDS y OFFERS,
        no LA/NV/OHLC). Si no se pasa, usa _ENTRIES default.
        """
        if not lista_tickers:
            return
        ents = entries if entries is not None else self._ENTRIES
        chunk_size = 50
        for i in range(0, len(lista_tickers), chunk_size):
            chunk = lista_tickers[i:i + chunk_size]
            pyRofex.market_data_subscription(
                tickers=chunk,
                entries=ents,
                depth=depth,
            )
            time.sleep(0.01)

    def iniciar_ws(self, lista_tickers, depth=1, entries=None):
        """Configura la suscripción e inicia la conexión viva.

        `entries` opcional: subset de pyRofex.MarketDataEntry. Default = _ENTRIES.
        """
        try:
            pyRofex.add_websocket_market_data_handler(self._handler_mercado)
            self.agregar_suscripciones(lista_tickers, depth=depth, entries=entries)
            pyRofex.init_websocket_connection()
            print(
                f"📡 WebSocket conectado y suscripto a {len(lista_tickers)} activos "
                f"(Lotes: 50, Profundidad: {depth})."
            )
            return True
        except Exception as e:
            print(f"❌ Error al iniciar WebSocket: {e}")
            return False

    def cerrar_ws(self):
        """Cierre seguro de la conexión"""
        pyRofex.close_websocket_connection()
        print("🛑 WebSocket desconectado.")