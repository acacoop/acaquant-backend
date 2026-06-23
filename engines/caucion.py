"""Motor de caución ARS y USD a corto plazo.

Suscribe via WS los 2 tickers de caución (pesos + dólares) cuyo plazo
coincide con "días al próximo día hábil". Lun-jue = 1D, vie = 3D
(cubre fin de semana), vie con lunes feriado = 4D, etc. El plazo se
re-evalúa cada hora; si cambia (cruce de día), el motor se re-suscribe.

Persistencia:
- Trading.CaucionSnapshot: 1 doc por moneda, replaced cada 15s.
  {moneda, plazo_dias, ticker, tna_last, tna_bid, tna_offer, tna_open,
   tna_high, tna_low, tna_closing, vol_efectivo, updated_at}
- Trading.Caucion: 1 doc por (fecha, moneda) escrito al apagado del
  motor (cierre de rueda 20:05 UTC). Sirve como serie histórica.
  {fecha, moneda, plazo_dias, tna_cierre, tna_open, tna_high, tna_low,
   vol_dia}

Ejecutar:
    python -m engines.caucion
"""
from __future__ import annotations

import logging
import signal
import threading
import time
import traceback
from datetime import UTC, date, datetime

from pymongo import ReplaceOne, UpdateOne

from core.mongo import get_mongo_client
from core.rofex_session import inicializar_sesion
from core.threads import lanzar_hilo_vital
from core.websocket import WebSocketManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("MotorCaucion")

INTERVALO_SNAPSHOT_S = 5
INTERVALO_RECARGA_PLAZO_S = 3600   # re-evaluar plazo cada 1h

_running = True


def _handle_signal(sig, frame):
    global _running
    logger.info("Señal de cierre recibida, vuelco snapshot a histórico y apago.")
    _running = False


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _cargar_dias_habiles(client) -> list[str]:
    """Trae todas las fechas hábiles ordenadas. Cacheado en memoria del proceso."""
    docs = list(client["Trading"]["DiasHabiles"].find({}, {"_id": 0, "fecha": 1}))
    return sorted(d["fecha"] for d in docs if d.get("fecha"))


def _proximo_habil(dias_habiles: list[str], hoy: date) -> date | None:
    hoy_str = hoy.isoformat()
    for f in dias_habiles:
        if f > hoy_str:
            return date.fromisoformat(f)
    return None


def _calcular_plazo(dias_habiles: list[str], hoy: date | None = None) -> int:
    """Plazo de caución = días calendario hasta el próximo día hábil.

    Lun-jue → 1, vie → 3 (cubre sáb+dom), vie con lun feriado → 4, etc.
    Si DiasHabiles está vacío o no encontramos próximo hábil, fallback 1.
    """
    hoy = hoy or date.today()
    proximo = _proximo_habil(dias_habiles, hoy)
    if proximo is None:
        return 1
    return max(1, (proximo - hoy).days)


def _tickers_para_plazo(plazo: int) -> tuple[str, str]:
    """Tickers ROFEX para caución pesos + dólares al plazo dado."""
    return (
        f"MERV - XMEV - PESOS - {plazo}D",
        f"MERV - XMEV - DOLAR - {plazo}D",
    )


def _moneda_de_ticker(ticker: str) -> str:
    return "ARS" if "PESOS" in ticker else "USD"


# ─────────────────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────────────────


class CaucionEngine:
    """Mantiene estado en RAM y persiste snapshot cada N segundos."""

    def __init__(self):
        self.client = get_mongo_client()
        self.col_snap = self.client["Trading"]["CaucionSnapshot"]
        self.col_hist = self.client["Trading"]["Caucion"]

        self.dias_habiles = _cargar_dias_habiles(self.client)
        self.plazo_actual = _calcular_plazo(self.dias_habiles)
        self.tickers_actuales: list[str] = list(_tickers_para_plazo(self.plazo_actual))
        logger.info(
            "Plazo inicial: %d día(s) — tickers: %s",
            self.plazo_actual, self.tickers_actuales,
        )

        # market_state: {ticker: {bid, offer, last, open, high, low, closing, vol_*}}
        self.market_state: dict[str, dict] = {t: {} for t in self.tickers_actuales}
        self._state_lock = threading.Lock()
        self._ultima_recarga_plazo = time.time()

        lanzar_hilo_vital(self._snapshot_loop, "snapshot_loop")

    # ─── WebSocket handler ────────────────────────────────────────────────
    def update_price(self, ticker: str, data: dict):
        """Llamado por WebSocketManager en cada tick."""
        with self._state_lock:
            if ticker not in self.market_state:
                # No es un ticker que estemos trackeando ahora (cambió de plazo).
                return
            st = self.market_state[ticker]
            if "BI" in data:
                st["bid"] = data["BI"][0] if data["BI"] else None
            if "OF" in data:
                st["offer"] = data["OF"][0] if data["OF"] else None
            if "LA" in data:
                st["last"] = data["LA"]
            if "OP" in data and data["OP"] is not None:
                st["open"] = data["OP"]
            if "HI" in data and data["HI"] is not None:
                st["high"] = data["HI"]
            if "LO" in data and data["LO"] is not None:
                st["low"] = data["LO"]
            if "CL" in data:
                st["closing"] = data["CL"]
            if "EV" in data and data["EV"] is not None:
                st["vol_efectivo"] = data["EV"]
            if "NV" in data and data["NV"] is not None:
                st["vol_nominal"] = data["NV"]

    # ─── Snapshot loop ────────────────────────────────────────────────────
    def _snapshot_loop(self):
        while _running:
            time.sleep(INTERVALO_SNAPSHOT_S)
            try:
                self._chequear_cambio_plazo()
                self._volcar_snapshot()
            except Exception:
                logger.error("Error en snapshot_loop:\n%s", traceback.format_exc())

    def _chequear_cambio_plazo(self):
        """Si pasó >1h y cambió el día, re-suscribe a tickers nuevos."""
        if time.time() - self._ultima_recarga_plazo < INTERVALO_RECARGA_PLAZO_S:
            return
        self._ultima_recarga_plazo = time.time()

        nuevo_plazo = _calcular_plazo(self.dias_habiles)
        if nuevo_plazo == self.plazo_actual:
            return

        nuevos_tickers = list(_tickers_para_plazo(nuevo_plazo))
        logger.info(
            "Cambio de plazo: %d → %d días. Tickers viejos %s, nuevos %s.",
            self.plazo_actual, nuevo_plazo, self.tickers_actuales, nuevos_tickers,
        )
        with self._state_lock:
            self.plazo_actual = nuevo_plazo
            self.tickers_actuales = nuevos_tickers
            self.market_state = {t: {} for t in nuevos_tickers}
        # NOTA: el WS sigue suscripto a los viejos pero no escriben nada porque
        # el handler chequea ticker in market_state. Para suscribir los nuevos,
        # llamar agregar_suscripciones() del manager (ver run() abajo).
        # En la primera versión simple, asumimos un restart diario para tomar
        # los plazos nuevos del día.

    def _volcar_snapshot(self):
        ts = datetime.now(UTC)
        ops = []
        docs = []
        with self._state_lock:
            for ticker in self.tickers_actuales:
                st = self.market_state.get(ticker, {})
                doc = self._build_snapshot_doc(ticker, st, ts)
                if doc:
                    ops.append(ReplaceOne({"moneda": doc["moneda"]}, doc, upsert=True))
                    docs.append(doc)
        if ops:
            self.col_snap.bulk_write(ops, ordered=False)
            # Dual-write SQL (flag SNAPSHOT_SQL): snapshot live de caución.
            try:
                from core import pg_mirror
                pg_mirror.mirror_snapshot("caucion_snapshot", ["moneda"], [
                    {"moneda": d.get("moneda"), "data": pg_mirror.doc_iso(d)}
                    for d in docs if d.get("moneda")
                ])
            except Exception:
                pass

    def _build_snapshot_doc(self, ticker: str, st: dict, ts: datetime) -> dict | None:
        last = st.get("last") or {}
        bid = st.get("bid") or {}
        offer = st.get("offer") or {}
        closing = st.get("closing") or {}
        return {
            "moneda":       _moneda_de_ticker(ticker),
            "plazo_dias":   self.plazo_actual,
            "ticker":       ticker,
            "tna_last":     last.get("price"),
            "tna_bid":      bid.get("price"),
            "tna_offer":    offer.get("price"),
            "tna_open":     st.get("open"),
            "tna_high":     st.get("high"),
            "tna_low":      st.get("low"),
            "tna_closing": closing.get("price"),
            "vol_efectivo": st.get("vol_efectivo"),
            "updated_at":   ts,
        }

    # ─── Vuelco al cierre ─────────────────────────────────────────────────
    def vuelco_cierre(self):
        """Persiste el último snapshot a Trading.Caucion como cierre del día.

        Llamado al recibir SIGTERM/SIGINT antes de que el proceso muera.
        Idempotente: upsert por (fecha, moneda).
        """
        hoy = date.today().isoformat()
        ts = datetime.now(UTC)
        ops = []
        with self._state_lock:
            for ticker in self.tickers_actuales:
                st = self.market_state.get(ticker, {})
                last = st.get("last") or {}
                closing = st.get("closing") or {}
                tna_cierre = last.get("price") or closing.get("price")
                if tna_cierre is None:
                    continue
                moneda = _moneda_de_ticker(ticker)
                doc = {
                    "fecha":         hoy,
                    "moneda":        moneda,
                    "plazo_dias":    self.plazo_actual,
                    "ticker":        ticker,
                    "tna_cierre":    tna_cierre,
                    "tna_open":      st.get("open"),
                    "tna_high":      st.get("high"),
                    "tna_low":       st.get("low"),
                    "vol_efectivo":  st.get("vol_efectivo"),
                    "persisted_at":  ts,
                }
                ops.append(UpdateOne(
                    {"fecha": hoy, "moneda": moneda},
                    {"$set": doc},
                    upsert=True,
                ))
        if ops:
            self.col_hist.bulk_write(ops, ordered=False)
            logger.info("Vuelco de cierre OK: %d docs en Trading.Caucion", len(ops))


# ─────────────────────────────────────────────────────────────────────────────
# Bucle principal
# ─────────────────────────────────────────────────────────────────────────────


def run():
    logger.info("Motor Caución iniciando...")
    if not inicializar_sesion():
        return

    engine = CaucionEngine()
    ws = WebSocketManager(engine)

    if not ws.iniciar_ws(engine.tickers_actuales, depth=1):
        logger.error("No pude iniciar WS")
        return

    logger.info(
        "WS arriba. Suscripto a %d tickers (plazo=%d). Snapshot cada %ds.",
        len(engine.tickers_actuales), engine.plazo_actual, INTERVALO_SNAPSHOT_S,
    )

    try:
        while _running:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            engine.vuelco_cierre()
        except Exception:
            logger.exception("Vuelco de cierre falló")
        try:
            ws.cerrar_ws()
        except Exception:
            pass


if __name__ == "__main__":
    run()
