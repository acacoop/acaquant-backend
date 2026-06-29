"""motor_cedears.py — feed live de CEDEARs vía pyRofex WS.

Espejo conceptual de motor_rofex pero reducido al mínimo:
  - Universo: tickers ARS 24hs de Trading.Cedears (activo=True).
  - Escribe a Trading.CedearsSnapshot (misma DB que el master y el resto
    del market data — sin crear DBs nuevas).
  - Shape: {ticker, ticker_corto, open, high, low, close, last, bid, offer,
    spread, vwap, volume, total_money, updated_at}. bid/offer/vol/vwap se
    agregaron para el scanner de trading (spread de puntas + VOL + VWAP),
    mismo patrón que engines/valores.py.

Patrón:
  1. _arranque_en_frio(): REST get_market_data por ticker → seed inicial
     en memoria (idéntico a motor_rofex). Sin esto, tickers ilíquidos
     arrancan en cero y el frontend muestra '0' hasta el primer tick.
  2. WS handler (update_price): actualiza market_state en cada tick.
  3. _snapshot_loop(): cada 1s hace bulk_write a Cedears.Snapshot. SIN
     dirty-check (no repetimos el bug de opciones — todos los docs se
     reescriben siempre).

Cron L-V 13-20 UTC (BYMA horario). systemd unit en deploy/systemd/.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import traceback
from datetime import UTC, datetime
from typing import ClassVar

import pyRofex

from core.mongo import get_mongo_client
from core.rofex_session import inicializar_sesion
from core.threads import lanzar_hilo_vital
from core.websocket import WebSocketManager

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


def _to_float(v) -> float:
    """Normaliza valores que pueden venir como número, dict {price,...} o None."""
    if v is None:
        return 0.0
    if isinstance(v, dict):
        v = v.get("price", 0)
    if isinstance(v, list) and v:
        first = v[0]
        if isinstance(first, dict):
            v = first.get("price", 0)
        else:
            v = first
    try:
        return float(v) if v else 0.0
    except (TypeError, ValueError):
        return 0.0


class CedearsEngine:
    """Mantiene estado live de CEDEARs ARS 24hs y persiste a Cedears.Snapshot.

    El método `update_price(ticker, data)` lo llama el WS handler de
    core.websocket.WebSocketManager con la data cruda de pyRofex.
    """

    # Entries que pedimos a pyRofex. BIDS/OFFERS → spread de puntas;
    # NOMINAL_VOLUME (NV) → VOL; TRADE_EFFECTIVE_VOLUME (EV) + NV → VWAP.
    # Mismo set que engines/valores.py (motor renta fija).
    _ENTRIES: ClassVar[list] = [
        pyRofex.MarketDataEntry.LAST,
        pyRofex.MarketDataEntry.OPENING_PRICE,
        pyRofex.MarketDataEntry.HIGH_PRICE,
        pyRofex.MarketDataEntry.LOW_PRICE,
        pyRofex.MarketDataEntry.CLOSING_PRICE,
        pyRofex.MarketDataEntry.BIDS,
        pyRofex.MarketDataEntry.OFFERS,
        pyRofex.MarketDataEntry.NOMINAL_VOLUME,
        pyRofex.MarketDataEntry.TRADE_EFFECTIVE_VOLUME,
    ]

    def __init__(self, cedears_master: list[dict]):
        """
        Args:
            cedears_master: lista de docs de Trading.Cedears (con ticker,
                ticker_corto). Define el universo a trackear.
        """
        self.cedears_master = cedears_master
        self.tickers: list[str] = [c["ticker"] for c in cedears_master]
        # Map ticker completo → ticker_corto para escribir en el doc.
        self._ticker_corto_map: dict[str, str] = {
            c["ticker"]: c["ticker_corto"] for c in cedears_master
        }

        # Estado in-memory por ticker.
        self.market_state: dict[str, dict] = {
            t: {
                "last":  0.0,
                "open":  0.0,
                "high":  0.0,
                "low":   0.0,
                "close": 0.0,  # closing_price = cierre día anterior
                "bid":   0.0,  # mejor punta compradora
                "offer": 0.0,  # mejor punta vendedora
                "nv":    0.0,  # NOMINAL_VOLUME acumulado del día (VOL)
                "ev":    0.0,  # TRADE_EFFECTIVE_VOLUME acumulado (cash) → VWAP
                "last_nv":  0.0,  # NV del tick anterior → inferir size del trade
                "prev_px":  0.0,  # precio anterior → inferir side por dirección
            } for t in self.tickers
        }

        self.client = get_mongo_client()
        # CedearsSnapshot migrada a SQL (mercado.cedears_snapshot) — cutover 2026-06-24.
        # CedearsTimeSales migrada a SQL (mercado.cedears_time_sales) — decomiso 2026-06-28:
        # el _flush_loop escribe SQL directo (append_native). self.client sigue para el
        # master Trading.Cedears (no migrado todavía). El índice (ticker_corto, ts) lo
        # crea schema.sql. La tabla es intradía: se vacía al cierre (cleanup_cedears_timesales).
        self.trade_buffer: list[dict] = []
        self._buffer_lock = threading.Lock()

        self._arranque_en_frio()
        lanzar_hilo_vital(self._snapshot_loop, "snapshot_loop")
        lanzar_hilo_vital(self._flush_loop, "flush_loop")

    # ──────────────────────────────────────────────────────────────
    # Arranque en frío — REST seed
    # ──────────────────────────────────────────────────────────────

    def _arranque_en_frio(self):
        """REST call por ticker al startup. Carga OP/HI/LO/CL/LA en memoria
        antes de abrir el WS. Patrón idéntico a engines/valores.py.

        Sin esto, ilíquidos del día arrancarían en 0 y permanecerían así
        hasta el primer tick — frontend mostraría 0 incluso teniendo el
        cierre de ayer disponible vía REST.
        """
        logger.info(f"Arranque en frío: REST get_market_data para {len(self.tickers)} tickers")
        for ticker in self.tickers:
            try:
                md = pyRofex.get_market_data(ticker, entries=self._ENTRIES)
                if not md or md.get("status") != "OK":
                    logger.warning(f"  · {ticker}: REST status={md.get('status') if md else 'None'}")
                    continue
                data = md.get("marketData", {})
                st = self.market_state[ticker]
                st["open"]  = _to_float(data.get("OP"))
                st["high"]  = _to_float(data.get("HI"))
                st["low"]   = _to_float(data.get("LO"))
                st["close"] = _to_float(data.get("CL"))
                st["last"]  = _to_float(data.get("LA"))
                st["bid"]   = _to_float(data.get("BI"))   # _to_float toma price del 1er nivel
                st["offer"] = _to_float(data.get("OF"))
                st["nv"]    = _to_float(data.get("NV"))
                st["ev"]    = _to_float(data.get("EV"))
                # Seed para la inferencia de trades: arrancar con el NV/precio
                # del REST evita emitir un trade falso gigante en el 1er tick WS.
                st["last_nv"] = st["nv"]
                st["prev_px"] = st["last"]
            except Exception as e:
                logger.warning(f"  · {ticker}: REST exception {type(e).__name__}: {e}")
        logger.info("Arranque en frío completado")

    # ──────────────────────────────────────────────────────────────
    # WS handler — invocado desde core.websocket.WebSocketManager
    # ──────────────────────────────────────────────────────────────

    def update_price(self, ticker: str, data: dict):
        """Actualiza market_state con la data cruda del WS de pyRofex.

        Tolera campos missing: cada tick solo trae los entries que cambiaron.
        Lo que no viene, no se pisa (no perdemos el OP recibido en arranque
        en frío si un tick solo trae LA).
        """
        if ticker not in self.market_state:
            return
        st = self.market_state[ticker]
        if data.get("OP") is not None:
            st["open"]  = _to_float(data["OP"])
        if data.get("HI") is not None:
            st["high"]  = _to_float(data["HI"])
        if data.get("LO") is not None:
            st["low"]   = _to_float(data["LO"])
        if data.get("CL") is not None:
            st["close"] = _to_float(data["CL"])
        if data.get("LA") is not None:
            st["last"]  = _to_float(data["LA"])
        # Puntas + volúmenes. _to_float tolera None / [] / lista de dicts → no
        # crashea con book vacío (lección incidente FCI book None, motor_rofex).
        if data.get("BI") is not None:
            st["bid"]   = _to_float(data["BI"])
        if data.get("OF") is not None:
            st["offer"] = _to_float(data["OF"])
        if data.get("NV") is not None:
            st["nv"]    = _to_float(data["NV"])
        if data.get("EV") is not None:
            st["ev"]    = _to_float(data["EV"])

        # ── Inferencia de Time & Sales: cada salto de NV = trade(s) agregados
        # desde el último tick. Side por puntas (cruza el offer→BUY, el bid→SELL)
        # y, en el medio, por dirección del precio. Igual que valores.py pero sin
        # VPIN. cash = px·sz (CEDEAR cotiza por acción, NO se divide por 100).
        la = data.get("LA")
        nv_raw = data.get("NV")
        if la is not None and nv_raw is not None:
            px = _to_float(la)
            nv = _to_float(nv_raw)
            if px > 0 and st["last_nv"] > 0 and nv > st["last_nv"]:
                sz = nv - st["last_nv"]
                st["last_nv"] = nv
                b1, o1 = st["bid"], st["offer"]
                if o1 > 0 and px >= o1:
                    side = "BUY"
                elif b1 > 0 and px <= b1:
                    side = "SELL"
                elif px > st["prev_px"]:
                    side = "BUY"
                elif px < st["prev_px"]:
                    side = "SELL"
                else:
                    side = "MID"
                st["prev_px"] = px
                ts_ms = la.get("date") if isinstance(la, dict) else None
                try:
                    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC) if ts_ms else datetime.now(UTC)
                except (TypeError, ValueError, OSError):
                    dt = datetime.now(UTC)
                with self._buffer_lock:
                    self.trade_buffer.append({
                        "ticker":       ticker,
                        "ticker_corto": self._ticker_corto_map[ticker],
                        "timestamp":    dt,
                        "price":        px,
                        "size":         sz,
                        "side":         side,
                        "money":        px * sz,
                    })
            elif px > 0 and st["last_nv"] <= 0:
                # Primer NV visto por WS sin seed (no debería pasar tras el REST):
                # registrar el baseline sin emitir trade.
                st["last_nv"], st["prev_px"] = nv, px

    # ──────────────────────────────────────────────────────────────
    # Snapshot loop — 1s, write_native a mercado.cedears_snapshot (SQL-native)
    # ──────────────────────────────────────────────────────────────

    def _snapshot_loop(self):
        """Cada 1s reescribe TODOS los docs en mercado.cedears_snapshot (SQL-native)
        con el estado actual de market_state. SIN dirty-check: todos los tickers
        siempre, incluso los que no recibieron tick (preserva updated_at fresco).

        Cutover 2026-06-24: antes hacía bulk_write a Trading.CedearsSnapshot (Mongo)
        + espejo SQL bajo SNAPSHOT_SQL. Ahora escribe SOLO SQL (write_native).
        """
        from core import pg_mirror
        while True:
            time.sleep(1)
            try:
                ts = datetime.now(UTC)
                rows = []
                for ticker in self.tickers:
                    st = self.market_state[ticker]
                    bid, offer, nv, ev = st["bid"], st["offer"], st["nv"], st["ev"]
                    spread = round(offer - bid, 4) if (bid > 0 and offer > 0) else 0.0
                    # VWAP = cash efectivo / nominales. CEDEAR cotiza por acción →
                    # NO se multiplica por 100 (eso es convención de bonos).
                    vwap = round(ev / nv, 4) if nv > 0 else 0.0
                    doc = {
                        "ticker":       ticker,
                        "ticker_corto": self._ticker_corto_map[ticker],
                        "open":         st["open"],
                        "high":         st["high"],
                        "low":          st["low"],
                        "close":        st["close"],
                        "last":         st["last"],
                        "bid":          bid,
                        "offer":        offer,
                        "spread":       spread,
                        "volume":       nv,
                        "total_money":  ev,
                        "vwap":         vwap,
                        "updated_at":   ts,
                    }
                    rows.append({
                        "ticker":     ticker,
                        "data":       pg_mirror.doc_iso(doc),
                        "updated_at": ts,
                    })
                if rows:
                    pg_mirror.write_native("mercado.cedears_snapshot", ["ticker"], rows)
            except Exception as e:
                logger.error(f"Error en _snapshot_loop: {e}")

    def _flush_loop(self):
        """Cada 1s vuelca el buffer de trades inferidos a mercado.cedears_time_sales
        (SQL-native, append). Thread aparte para no bloquear el WS handler. La tabla es
        intradía (se vacía al cierre vía cron jobs.cleanup_cedears_timesales)."""
        from core.pg_mirror import append_native
        while True:
            time.sleep(1.0)
            if not self.trade_buffer:
                continue
            with self._buffer_lock:
                batch = self.trade_buffer[:]
                self.trade_buffer = []
            try:
                # Mongo "timestamp" → columna SQL "ts".
                rows = [{
                    "ticker":       t["ticker"],
                    "ticker_corto": t["ticker_corto"],
                    "ts":           t["timestamp"],
                    "price":        t["price"],
                    "size":         t["size"],
                    "side":         t["side"],
                    "money":        t["money"],
                } for t in batch]
                append_native("cedears_time_sales", rows)
            except Exception as e:
                logger.error(f"Error flush trades CEDEARs: {e}")


# ──────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────

def _cargar_cedears_master() -> list[dict]:
    """Universo del motor: SQL `mercado.cedears` con activo=true (ticker, ticker_corto).

    SQL-native (decomiso 2026-06-29): MISMA fuente que el editor (Manager → Renta
    Variable) y el scanner → cero drift. Antes leía Mongo `Trading.Cedears`, que
    driftaba con el master SQL (el motor quedaba con un universo distinto al panel)."""
    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker, ticker_corto FROM mercado.cedears WHERE activo IS TRUE")
        return [{"ticker": t, "ticker_corto": tc} for t, tc in cur.fetchall()]


def run():
    if not inicializar_sesion():
        logger.error("No se pudo iniciar sesión Rofex. Abortando.")
        return

    cedears = _cargar_cedears_master()
    logger.info(f"CEDEARs cargados desde mercado.cedears (SQL): {len(cedears)}")
    if not cedears:
        logger.error("Sin CEDEARs activos en mercado.cedears. Correr scripts/add_cedears_bulk primero.")
        return

    engine = CedearsEngine(cedears)
    ws_manager = WebSocketManager(engine)

    try:
        if ws_manager.iniciar_ws(engine.tickers, depth=1, entries=CedearsEngine._ENTRIES):
            logger.info(f"Motor CEDEARs corriendo. Suscripto a {len(engine.tickers)} activos.")
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Apagado manual detectado.")
    except Exception:
        logger.error("Motor CEDEARs crasheó:")
        traceback.print_exc()
    finally:
        try:
            pyRofex.close_websocket_connection()
        except Exception:
            pass
        os._exit(0)


if __name__ == "__main__":
    run()
