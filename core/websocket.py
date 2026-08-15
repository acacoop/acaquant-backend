"""WebSocketManager — conexión WS a pyRofex para los motores de mercado.

Compartido por TODOS los motores (engines/*) vía `iniciar_ws`. Por eso los
cambios acá son sensibles: un bug rompe todos los motores a la vez.

Resiliencia (2026-05-23): antes, si el broker cortaba el WS, el motor seguía
"vivo" sin recibir nada y servía precios viejos en silencio (`except: pass`).
Ahora:
  - el handler de mercado loguea sus errores (no los traga).
  - los handlers de error/excepción LOGUEAN y disparan reconexión con backoff.

Política de alertas (2026-05-25): los cortes transitorios y las reconexiones
van SOLO al log; el agotamiento de reconexión (motor sin datos) se loguea
fuerte como error.

Agotamiento = muerte del proceso: al quemar los 6 intentos, el motor loguea y
se mata (os._exit) para que systemd lo reinicie. Antes se rendía y seguía vivo
("active" para systemd) pero sin datos hasta el restart del cron del día
siguiente: un corte del broker de >4 min a las 14:00 dejaba a la mesa sin
precios el resto de la rueda. Misma filosofía que core/threads.py — morir
ruidosamente + renacer limpio > sobrevivir zombie.

⚠️ La reconexión NO se pudo testear contra el broker en dev — validar en el
Droplet con el primer corte real (confirmar que reconecta y los datos vuelven
a fluir; el diagnóstico de motores lo muestra).
"""
import logging
import os
import re
import time
from typing import ClassVar

import pyRofex

from core.threads import lanzar_hilo_vital

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
        # Filtro de cuarentena: símbolos que ROFEX rechazó hace poco no se
        # vuelven a pedir (pasada la ventana de reintento entran de nuevo solos).
        # Best-effort: sin Postgres, set vacío → se suscribe todo como antes.
        from core.simbolos_cuarentena import excluidos
        q = excluidos()
        if q:
            antes = len(lista_tickers)
            lista_tickers = [t for t in lista_tickers if t not in q]
            if antes != len(lista_tickers):
                logger.info(
                    "WS %s: %d símbolo(s) en cuarentena excluidos de la suscripción",
                    self._nombre, antes - len(lista_tickers))
            if not lista_tickers:
                return
        # VALIDACIÓN contra el catálogo real de Primary. Va acá y no en cada
        # motor a propósito: es el único punto por el que pasan TODAS las
        # suscripciones, así que un motor nuevo la hereda sin escribir nada y no
        # se puede saltear por olvido. Sin criterio confiable (Postgres caído,
        # catálogo vacío) NO filtra — ver core/instrumentos_validos.
        from core.instrumentos_validos import filtrar, validos
        lista_tickers, invalidos = filtrar(lista_tickers, validos())
        if invalidos:
            logger.warning(
                "WS %s: %d símbolo(s) NO existen en Primary — no se suscriben: %s",
                self._nombre, len(invalidos), ", ".join(sorted(invalidos)[:10]))
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
        """Log fuerte para eventos accionables (agotamiento de reconexión).
        Los cortes transitorios NO pasan por acá — van al log normal."""
        logger.error("WS %s: %s", self._nombre, texto)

    # ROFEX, ante un símbolo inválido en la suscripción, devuelve un msg de
    # error con `description: "Product <symbol>:<market> don't exist"`. Capturamos
    # el símbolo para purgarlo (no es un corte de conexión).
    _BAD_PRODUCT_RE: ClassVar = re.compile(r"Product\s+(.+?):[A-Za-z]+\s+don'?t exist", re.I)

    def _simbolos_inexistentes(self, message) -> list[str]:
        """Símbolos que ROFEX reporta como inexistentes en un mensaje de error.

        Sólo miramos `description` (nombra EL símbolo malo); el campo `message`
        ecoa toda la request (todos los símbolos) y no sirve para discriminar."""
        if isinstance(message, dict):
            desc = str(message.get("description") or "")
        else:
            desc = str(message)
        return [m.strip() for m in self._BAD_PRODUCT_RE.findall(desc)]

    def _purgar_simbolos(self, bad: list[str]) -> None:
        """Saca símbolos inválidos de la suscripción guardada (`self._sub`) y del
        engine, para que las reconexiones NO los vuelvan a mandar."""
        if not bad or self._sub is None:
            return
        bad_set = set(bad)
        tickers, depth, entries = self._sub
        self._sub = ([t for t in tickers if t not in bad_set], depth, entries)
        # Best-effort: sacarlo también del set/list de tickers del engine.
        try:
            tk = getattr(self.mm, "tickers", None)
            if isinstance(tk, set):
                tk.difference_update(bad_set)
            elif isinstance(tk, list):
                self.mm.tickers = [t for t in tk if t not in bad_set]
        except Exception:
            logger.debug("WS %s: no pude purgar símbolos del engine", self._nombre)

    def _on_error(self, message):
        # Caso especial: ROFEX rechazó la suscripción por un símbolo INEXISTENTE
        # (delisted/typo). NO es un corte de conexión: reconectar re-manda el
        # símbolo malo → loop infinito que impide que fluya CUALQUIER dato
        # (incidente order book vacío 2026-06-05, ej. "MERV - XMEV - PESOS - 1D").
        # Lo purgamos de la suscripción y reconectamos con la lista limpia → corta
        # el loop y el resto de los tickers vuelve a recibir datos.
        bad = self._simbolos_inexistentes(message)
        if bad:
            logger.error(
                "WS %s: símbolo(s) inexistente(s) en ROFEX, purgo y resuscribo sin ellos: %s",
                self._nombre, bad,
            )
            self._purgar_simbolos(bad)
            # Playbook determinista: persistir la lección para que el próximo
            # arranque NO vuelva a pedir el símbolo muerto (la purga en memoria
            # se perdía en cada reinicio y el error se repetía todos los días).
            # Best-effort + guardrail de masa adentro (core/simbolos_cuarentena).
            from core.simbolos_cuarentena import agregar as _cuarentenar
            _cuarentenar(bad, motivo=f"ROFEX: Product don't exist (WS {self._nombre})")
            self._reconectar()
            return
        # Resto de errores: transitorio → solo log. El loop de reconexión
        # loguea fuerte únicamente si se agota.
        logger.error("WS %s: error de WS: %s", self._nombre, message)
        self._reconectar()

    def _on_exception(self, e):
        logger.error("WS %s: excepción de WS: %s", self._nombre, e, exc_info=True)
        self._reconectar()

    def _reconectar(self) -> None:
        """Dispara UNA reconexión en background (guard anti-reentrada)."""
        if self._reconectando or self._sub is None:
            return
        self._reconectando = True
        lanzar_hilo_vital(self._loop_reconexion, "ws_reconexion")

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
                    # Recuperación transitoria → solo log (se auto-sanó).
                    logger.info("WS %s: reconectado OK (intento %d)", self._nombre, intento)
                    return
                except Exception as e:
                    logger.error("WS %s: reconexión intento %d falló: %s", self._nombre, intento, e)
                    delay = min(delay * 2, 60)
            self._alertar(
                "❌ reconexión AGOTADA tras 6 intentos — motor sin datos, "
                "me mato para que systemd reinicie y reconecte en frío"
            )
            # os._exit (no sys.exit): estamos en un thread no-main y queremos
            # terminar el proceso YA, sin depender de que el main loop coopere.
            os._exit(1)
        finally:
            self._reconectando = False

    def cerrar_ws(self):
        """Cierre seguro de la conexión."""
        try:
            pyRofex.close_websocket_connection()
        except Exception as e:
            logger.warning("WS %s: error al cerrar: %s", self._nombre, e)
        print("🛑 WebSocket desconectado.")
