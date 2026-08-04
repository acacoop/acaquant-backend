"""engines/_motor_base.py — esqueleto común de los motores snapshot WS → SQL.

Los 5 motores chicos (caucion, dolares, futuros_dlr, motor_agro,
motor_agro_opciones) repetían VERBATIM: el signal handling, el parser de ticks
de `update_price`, el snapshot-loop con try/except, el `run()` completo
(sesión → engine → WS → espera → cierre) y los helpers de discovery ROFEX.
Cada fix al harness había que replicarlo a mano en 5 archivos — y ya habían
divergido (motores que trackean distintos entries, rediscovery que solo
logueaba). Acá vive UNA sola vez.

Contrato de cada motor:
  - subclasear `SnapshotEngine`, llamar `super().__init__(tickers)` al final
    del propio __init__ (arranca el snapshot-loop en un hilo vital).
  - definir `_volcar_snapshot()` (persistencia) y opcionalmente
    `_pre_snapshot()` (hook por iteración: rediscovery, cambio de plazo) y
    `vuelco_cierre()` (histórico al apagado — correr_motor lo llama solo).
  - `ENTRIES` acota qué entries del tick se trackean (preserva el
    comportamiento exacto de cada motor: opciones no trackea OP/HI/LO/NV,
    dólares no trackea EV/NV).
  - para rediscovery con re-suscripción EN CALIENTE: setear
    `INTERVALO_REDISCOVERY_S` y definir `_descubrir()` / `_ticker_item()` /
    `_aplicar_universo()`; llamar `self._maybe_rediscover()` desde
    `_pre_snapshot()`. Antes el rediscovery bajaba el padrón completo de
    ROFEX cada 30' SOLO para loguear "restart del motor para tomar cambios";
    ahora el contrato nuevo se suscribe al toque (agregar_suscripciones es
    aditivo, no reabre el WS).

`run()` de cada motor queda en una línea: `correr_motor("Nombre", Engine)`.
"""
from __future__ import annotations

import logging
import signal
import threading
import time
import traceback
import unicodedata
from datetime import date

from core.rofex_session import inicializar_sesion
from core.threads import lanzar_hilo_vital
from core.websocket import WebSocketManager

_running = True


def sigue() -> bool:
    """Flag global de apagado — los loops de los motores lo consultan."""
    return _running


def _handle_signal(sig, frame):
    global _running
    logging.getLogger("MotorBase").info(
        "Señal de cierre recibida — vuelco cierre (si aplica) y apago.")
    _running = False


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de discovery ROFEX compartidos (antes copiados en 3-4 motores)
# ─────────────────────────────────────────────────────────────────────────────


def ticker_de(inst: dict) -> str:
    """ROFEX a veces deja symbol=None y pone el ticker en instrumentId.symbol."""
    sym = inst.get("symbol")
    if isinstance(sym, str) and sym:
        return sym
    iid = inst.get("instrumentId") or {}
    return iid.get("symbol") if isinstance(iid.get("symbol"), str) else ""


def maturity(inst: dict) -> str:
    return inst.get("maturityDate") or inst.get("maturity_date") or ""


def dias_a_vto(mat_str: str) -> int:
    """Días calendario entre hoy y maturity. Mínimo 1 para evitar div/0."""
    try:
        vto = date(int(mat_str[:4]), int(mat_str[4:6]), int(mat_str[6:8]))
        return max(1, (vto - date.today()).days)
    except Exception:
        return 1


def norm_texto(s: str) -> str:
    """lower + strip de tildes para matching robusto."""
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


# Matcher por keywords normalizadas (lower + sin tildes). Cubre 'Trigo
# Rosario', 'TRIGO ROSARIO', 'Maíz Rosario', 'Maiz Rosario', etc.
# Solo Rosario, NO Chicago. Compartido por motor_agro y motor_agro_opciones.
COMMODITY_MATCHERS: dict[str, tuple[str, ...]] = {
    "TRIGO": ("trigo", "rosario"),
    "MAIZ":  ("maiz",  "rosario"),
    "SOJA":  ("soja",  "rosario"),
}


def classify_commodity(underlying: str) -> str | None:
    """Devuelve TRIGO/MAIZ/SOJA si el underlying matchea alguno; None si no."""
    n = norm_texto(underlying)
    if not n:
        return None
    for commodity, kws in COMMODITY_MATCHERS.items():
        if all(kw in n for kw in kws):
            return commodity
    return None


def borrar_stale_sql(table: str, tickers_actuales: list[str],
                     logger: logging.Logger | None = None) -> None:
    """Borra del espejo SQL los tickers que ya no están en el universo vigente.

    Sin esto, un vencimiento cumplido / variante retirada quedaría zombie en la
    tabla y el GET lo renderearía. Best-effort — un fallo de PG al arranque deja
    zombies hasta el próximo restart, no tumba el motor."""
    log = logger or logging.getLogger("MotorBase")
    if not tickers_actuales:
        return
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(f"DELETE FROM {table} WHERE ticker <> ALL(%s)", (tickers_actuales,))
            if cur.rowcount:
                log.info("Limpieza stale SQL: %d snapshots viejos borrados", cur.rowcount)
    except Exception as e:
        log.error("Limpieza stale SQL %s: %s", table, str(e).splitlines()[0][:200])


# ─────────────────────────────────────────────────────────────────────────────
# Engine base
# ─────────────────────────────────────────────────────────────────────────────


class SnapshotEngine:
    """Estado de mercado en RAM (alimentado por WS) + snapshot-loop periódico."""

    INTERVALO_SNAPSHOT_S: int = 5
    INTERVALO_REDISCOVERY_S: int | None = None   # None = sin rediscovery
    # Entries del tick que este motor trackea (subset del universo pyRofex).
    ENTRIES: tuple[str, ...] = ("BI", "OF", "LA", "OP", "HI", "LO", "CL", "EV", "NV")

    def __init__(self, tickers: list[str]):
        self.logger = logging.getLogger(type(self).__name__)
        # market_state: {ticker: {bid, offer, last, open, high, low, closing, vol_*}}
        self.market_state: dict[str, dict] = {t: {} for t in tickers}
        self._state_lock = threading.Lock()
        self._universo_tickers: set[str] = set(tickers)
        self._ultimo_discovery = time.time()
        self._ws: WebSocketManager | None = None   # lo setea correr_motor
        lanzar_hilo_vital(self._snapshot_loop, "snapshot_loop")

    def tickers_ws(self) -> list[str]:
        """Tickers a suscribir al arrancar el WS (orden de inserción)."""
        return list(self.market_state)

    # ─── WS handler ───────────────────────────────────────────────────────
    def update_price(self, ticker: str, data: dict):
        """Llamado por WebSocketManager en cada tick."""
        e = self.ENTRIES
        with self._state_lock:
            if ticker not in self.market_state:
                # No es un ticker que estemos trackeando ahora (cambió el universo).
                return
            st = self.market_state[ticker]
            if "BI" in e and "BI" in data:
                st["bid"] = data["BI"][0] if data["BI"] else None
            if "OF" in e and "OF" in data:
                st["offer"] = data["OF"][0] if data["OF"] else None
            if "LA" in e and "LA" in data:
                st["last"] = data["LA"]
            if "OP" in e and "OP" in data and data["OP"] is not None:
                st["open"] = data["OP"]
            if "HI" in e and "HI" in data and data["HI"] is not None:
                st["high"] = data["HI"]
            if "LO" in e and "LO" in data and data["LO"] is not None:
                st["low"] = data["LO"]
            if "CL" in e and "CL" in data:
                st["closing"] = data["CL"]
            if "EV" in e and "EV" in data and data["EV"] is not None:
                st["vol_efectivo"] = data["EV"]
            if "NV" in e and "NV" in data and data["NV"] is not None:
                st["vol_nominal"] = data["NV"]

    # ─── Snapshot loop ────────────────────────────────────────────────────
    def _snapshot_loop(self):
        while _running:
            time.sleep(self.INTERVALO_SNAPSHOT_S)
            try:
                self._pre_snapshot()
                self._volcar_snapshot()
            except Exception:
                self.logger.error("Error en snapshot_loop:\n%s", traceback.format_exc())

    def _pre_snapshot(self) -> None:
        """Hook por iteración (rediscovery, cambio de plazo). Default: nada."""

    def _volcar_snapshot(self) -> None:
        raise NotImplementedError

    # ─── Rediscovery con re-suscripción en caliente ───────────────────────
    def _descubrir(self) -> list:
        """Universo vigente (lista de items del motor). Solo si hay rediscovery."""
        raise NotImplementedError

    def _ticker_item(self, item) -> str:
        """Ticker de un item del universo. Solo si hay rediscovery."""
        raise NotImplementedError

    def _aplicar_universo(self, items: list) -> None:
        """Asigna el universo nuevo a la estructura propia del motor (se llama
        bajo _state_lock). Solo si hay rediscovery."""
        raise NotImplementedError

    def _maybe_rediscover(self):
        """Re-discovery periódico: contratos nuevos se agregan al estado y se
        suscriben EN CALIENTE (agregar_suscripciones, aditivo). Los que salen
        del universo se dejan de volcar (la fila SQL stale la limpia
        borrar_stale_sql al próximo boot)."""
        if self.INTERVALO_REDISCOVERY_S is None:
            return
        if time.time() - self._ultimo_discovery < self.INTERVALO_REDISCOVERY_S:
            return
        self._ultimo_discovery = time.time()
        items = self._descubrir()
        if not items:
            return   # discovery falló — no tocar el universo vigente
        nuevos_set = {self._ticker_item(u) for u in items}
        if nuevos_set == self._universo_tickers:
            return
        agregados = sorted(nuevos_set - self._universo_tickers)
        sacados = sorted(self._universo_tickers - nuevos_set)
        with self._state_lock:
            self._aplicar_universo(items)
            self._universo_tickers = nuevos_set
            for t in agregados:
                self.market_state.setdefault(t, {})
        if agregados and self._ws is not None:
            self._ws.agregar_suscripciones(agregados, depth=1)
        self.logger.info(
            "Discovery cambió: +%s -%s (%d suscriptos en caliente)",
            agregados, sacados, len(agregados) if self._ws is not None else 0,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Harness del proceso
# ─────────────────────────────────────────────────────────────────────────────


def correr_motor(nombre: str, engine_factory, *, depth: int = 1,
                 log_arranque: str = "") -> None:
    """Esqueleto completo del proceso: señales → sesión pyRofex → engine → WS →
    espera → vuelco de cierre (si el engine define `vuelco_cierre`) → cierre WS."""
    logger = logging.getLogger(nombre)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    logger.info("%s iniciando...", nombre)
    if not inicializar_sesion():
        return

    try:
        engine = engine_factory()
    except RuntimeError as e:
        logger.error(str(e))
        return

    ws = WebSocketManager(engine)
    engine._ws = ws
    tickers = engine.tickers_ws()

    if not ws.iniciar_ws(tickers, depth=depth):
        logger.error("No pude iniciar WS")
        return

    logger.info(
        "WS arriba. Suscripto a %d tickers. Snapshot cada %ds.%s",
        len(tickers), engine.INTERVALO_SNAPSHOT_S,
        f" {log_arranque}" if log_arranque else "",
    )

    try:
        while _running:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        vuelco = getattr(engine, "vuelco_cierre", None)
        if callable(vuelco):
            try:
                vuelco()
            except Exception:
                logger.exception("Vuelco de cierre falló")
        try:
            ws.cerrar_ws()
        except Exception:
            pass
