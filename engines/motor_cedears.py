"""motor_cedears.py — feed live de CEDEARs vía pyRofex WS.

Espejo conceptual de motor_rofex pero reducido al mínimo:
  - Universo: tickers ARS 24hs de Trading.Cedears (activo=True).
  - Escribe a Trading.CedearsSnapshot (misma DB que el master y el resto
    del market data — sin crear DBs nuevas).
  - Shape estricto: {ticker, ticker_corto, open, high, low, close, last,
    updated_at}. SIN bid/offer/ev/book — si después se necesita una métrica
    derivada (spread puntas, vwap, vol), se agrega cuando se pida, no
    spec-ahead.

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
from pymongo import UpdateOne

from core.mongo import get_mongo_client
from core.rofex_session import inicializar_sesion
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

    # Entries que pedimos a pyRofex — solo lo que persiste el snapshot
    # (sin BIDS/OFFERS/EV/NV porque no escribimos esos campos hoy).
    _ENTRIES: ClassVar[list] = [
        pyRofex.MarketDataEntry.LAST,
        pyRofex.MarketDataEntry.OPENING_PRICE,
        pyRofex.MarketDataEntry.HIGH_PRICE,
        pyRofex.MarketDataEntry.LOW_PRICE,
        pyRofex.MarketDataEntry.CLOSING_PRICE,
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
            } for t in self.tickers
        }

        self.client = get_mongo_client()
        # Snapshot vive en la misma DB que el master (Trading.Cedears) y
        # el resto del market data (Trading.MarketSnapshot, TimeSales, etc).
        # Sin DB nueva — coherente con el "no migrar nada" del scope.
        self.col_snapshot = self.client["Trading"]["CedearsSnapshot"]

        self._arranque_en_frio()
        threading.Thread(target=self._snapshot_loop, daemon=True).start()

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

    # ──────────────────────────────────────────────────────────────
    # Snapshot loop — 1s, bulk_write a Cedears.Snapshot
    # ──────────────────────────────────────────────────────────────

    def _snapshot_loop(self):
        """Cada 1s reescribe TODOS los docs en Cedears.Snapshot con el
        estado actual de market_state. SIN dirty-check: todos los tickers
        siempre, incluso los que no recibieron tick (preserva updated_at
        fresco, evita el bug de opciones donde ilíquidos quedaban con
        timestamp viejo).
        """
        while True:
            time.sleep(1)
            try:
                ts = datetime.now(UTC)
                ops = []
                for ticker in self.tickers:
                    st = self.market_state[ticker]
                    ops.append(UpdateOne(
                        {"ticker": ticker},
                        {"$set": {
                            "ticker":       ticker,
                            "ticker_corto": self._ticker_corto_map[ticker],
                            "open":         st["open"],
                            "high":         st["high"],
                            "low":          st["low"],
                            "close":        st["close"],
                            "last":         st["last"],
                            "updated_at":   ts,
                        }},
                        upsert=True,
                    ))
                if ops:
                    self.col_snapshot.bulk_write(ops, ordered=False)
            except Exception as e:
                logger.error(f"Error en _snapshot_loop: {e}")


# ──────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────

def _cargar_cedears_master() -> list[dict]:
    """Lee Trading.Cedears con activo=True. Devuelve solo los campos
    necesarios para el motor (ticker, ticker_corto). El resto de la
    metadata se consume del lado del scanner."""
    client = get_mongo_client()
    return list(client["Trading"]["Cedears"].find(
        {"activo": True},
        {"_id": 0, "ticker": 1, "ticker_corto": 1},
    ))


def run():
    if not inicializar_sesion():
        logger.error("No se pudo iniciar sesión Rofex. Abortando.")
        return

    cedears = _cargar_cedears_master()
    logger.info(f"CEDEARs cargados desde Trading.Cedears: {len(cedears)}")
    if not cedears:
        logger.error("Sin CEDEARs activos en Trading.Cedears. Correr scripts/seed_cedears.py primero.")
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
