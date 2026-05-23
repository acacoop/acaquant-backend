"""WebSocketManager — conexión WS a pyRofex para los motores de mercado.

Compartido por TODOS los motores (engines/*) vía `iniciar_ws`. Por eso los
cambios acá son sensibles: un bug rompe todos los motores a la vez.

Resiliencia (2026-05-23): antes, si el broker cortaba el WS, el motor seguía
"vivo" sin recibir nada y servía precios viejos en silencio (`except: pass`).
Ahora:
  - el handler de mercado loguea sus errores (no los traga).
  - hay handlers de error/excepción que avisan (log + Telegram) y disparan
    una reconexión con backoff.

⚠️ La reconexión NO se pudo testear contra el broker en dev — validar en el
Droplet con el primer corte real (confirmar que reconecta y los datos vuelven
a fluir; el diagnóstico de motores lo muestra).
"""
import logging
import threading
import time
from typing import ClassVar

import pyRofex

logger = logging.getLogger("core.websocket")


class WebSocketManager:
    def __init__(self, market_manager):
        """market_manager: instancia del cerebro que guarda los precios."""
        self.mm = market_manager
        self._nombre = getattr(market_manager, "nombre", type(market_manager).__name__)
        self._sub: tuple | None = None          # (tickers, depth, entries) para reconectar
        self._handler_registrado = False
        self._reconectando = False

    def _handler_mercado(self, message):
        """Traduce el mensaje de Rofex para el MarketManager.

        Loguea (no traga) los errores: un mensaje malformado se ignora pero
        queda registrado, en vez de desaparecer en silencio."""
        try:
            ticker = message["instrumentId"]["symbol"]
            data = message["marketData"]
            self.mm.update_price(ticker, data)
        except Exception:
            logger.exception("WS %s: mensaje de mercado ignorado por error", self._nombre)

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

        Aditivo — se puede llamar varias veces sobre el mismo socket.
        `entries` opcional: subset distinto al default (ej. order book L2 solo
        pide BIDS/OFFERS)."""
        if not lista_tickers:
            return
        ents = entries if entries is not None else self._ENTRIES
        chunk_size = 50
        for i in range(0, len(lista_tickers), chunk_size):
            chunk = lista_tickers[i:i + chunk_size]
            pyRofex.market_data_subscription(tickers=chunk, entries=ents, depth=depth)
            time.sleep(0.01)

    def iniciar_ws(self, lista_tickers, depth=1, entries=None):
        """Configura la suscripción e inicia la conexión viva.

        `entries` opcional: subset de pyRofex.MarketDataEntry. Default = _ENTRIES.
        """
        self._sub = (list(lista_tickers), depth, entries)
        try:
            # El handler de mercado se registra una sola vez (pyRofex lo
            # conserva entre re-inits); error/exception van en cada init.
            if not self._handler_registrado:
                pyRofex.add_websocket_market_data_handler(self._handler_mercado)
                self._handler_registrado = True
            pyRofex.init_websocket_connection(
                error_handler=self._on_error,
                exception_handler=self._on_exception,
            )
            self.agregar_suscripciones(lista_tickers, depth=depth, entries=entries)
            print(
                f"📡 WebSocket conectado y suscripto a {len(lista_tickers)} activos "
                f"(Lotes: 50, Profundidad: {depth})."
            )
            return True
        except Exception as e:
            logger.error("WS %s: error al iniciar: %s", self._nombre, e)
            print(f"❌ Error al iniciar WebSocket: {e}")
            return False

    # ── Resiliencia ──────────────────────────────────────────────────────────

    def _alertar(self, texto: str) -> None:
        """Log fuerte + alerta Telegram (no-op si no está configurada)."""
        logger.error("WS %s: %s", self._nombre, texto)
        try:
            from core.notify import send_telegram
            send_telegram(f"🔌 WS motor {self._nombre}: {texto[:150]}")
        except Exception as e:
            logger.warning("WS %s: no pude alertar: %s", self._nombre, e)

    def _on_error(self, message):
        self._alertar(f"error de WS: {message}")
        self._reconectar()

    def _on_exception(self, e):
        logger.error("WS %s: excepción: %s", self._nombre, e, exc_info=True)
        self._alertar(f"excepción de WS: {e}")
        self._reconectar()

    def _reconectar(self) -> None:
        """Dispara UNA reconexión en background (guard anti-reentrada)."""
        if self._reconectando or self._sub is None:
            return
        self._reconectando = True
        threading.Thread(target=self._loop_reconexion, daemon=True).start()

    def _loop_reconexion(self) -> None:
        tickers, depth, entries = self._sub
        delay = 5
        try:
            for intento in range(1, 7):
                time.sleep(delay)
                logger.warning("WS %s: reconectando (intento %d)…", self._nombre, intento)
                try:
                    try:
                        pyRofex.close_websocket_connection()
                    except Exception:
                        pass
                    pyRofex.init_websocket_connection(
                        error_handler=self._on_error,
                        exception_handler=self._on_exception,
                    )
                    self.agregar_suscripciones(tickers, depth=depth, entries=entries)
                    logger.info("WS %s: reconectado OK (intento %d)", self._nombre, intento)
                    self._alertar("✅ reconectado")
                    return
                except Exception as e:
                    logger.error("WS %s: reconexión intento %d falló: %s", self._nombre, intento, e)
                    delay = min(delay * 2, 60)
            self._alertar("❌ reconexión AGOTADA tras 6 intentos — motor sin datos")
        finally:
            self._reconectando = False

    def cerrar_ws(self):
        """Cierre seguro de la conexión."""
        try:
            pyRofex.close_websocket_connection()
        except Exception as e:
            logger.warning("WS %s: error al cerrar: %s", self._nombre, e)
        print("🛑 WebSocket desconectado.")
