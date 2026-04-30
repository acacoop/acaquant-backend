"""Motor de Futuros Agro Rosario — Trigo / Maíz / Soja.

Análogo al motor_futuros_dlr pero para el universo agro Rosario. Suscribe
los outrights single-leg (cficode FXXXSX) cuyos underlyings matchean
'Trigo Rosario', 'Maíz Rosario' y 'Soja Rosario' (matcher tolerante a
tildes y mayúsculas para que sobreviva cambios de spelling de Primary).

Persistencia (decisión consciente de la mesa):
- Trading.AgroSnapshot: 1 doc por ticker, replaced cada 5s.
  {ticker, commodity, underlying, vencimiento, dias_a_vto,
   bid_price, bid_size, offer_price, offer_size,
   last_price, last_size, open, high, low, closing,
   vol_efectivo, updated_at}
- NO se escribe Trading.TimeSales (a diferencia del motor DLR). La vista
  PASE AGRO sólo necesita el último precio para calcular pase y TNAV;
  guardar cada tick agrega presión a Atlas sin beneficio para esta tabla.
- NO se escribe histórico diario al cierre. Si en el futuro se necesita
  serie histórica por commodity, agregar vuelco_cierre() análogo al del
  motor DLR.

Uso:
    python -m engines.motor_agro

Cron: L-V 13-20 UTC (mismo horario que el resto de motores ROFEX).
"""
from __future__ import annotations

import logging
import signal
import threading
import time
import traceback
import unicodedata
from datetime import UTC, date, datetime

import pyRofex
from pymongo import ReplaceOne

from core.mongo import get_mongo_client
from core.rofex_session import inicializar_sesion
from core.websocket import WebSocketManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("MotorAgro")

INTERVALO_SNAPSHOT_S = 5
INTERVALO_REDISCOVERY_S = 300

CFICODE_OUTRIGHT = "FXXXSX"

# Matcher por keywords normalizadas (lower + sin tildes). Cubre 'Trigo
# Rosario', 'TRIGO ROSARIO', 'Maíz Rosario', 'Maiz Rosario', etc.
COMMODITY_MATCHERS: dict[str, tuple[str, ...]] = {
    "TRIGO": ("trigo", "rosario"),
    "MAIZ":  ("maiz",  "rosario"),
    "SOJA":  ("soja",  "rosario"),
}

_running = True


def _handle_signal(sig, frame):
    global _running
    logger.info("Señal de cierre recibida — apago WS.")
    _running = False


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _norm(s: str) -> str:
    """lower + strip de tildes para matching robusto."""
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _classify_commodity(underlying: str) -> str | None:
    """Devuelve TRIGO/MAIZ/SOJA si el underlying matchea alguno; None si no."""
    n = _norm(underlying)
    if not n:
        return None
    for commodity, kws in COMMODITY_MATCHERS.items():
        if all(kw in n for kw in kws):
            return commodity
    return None


def _ticker_de(inst: dict) -> str:
    sym = inst.get("symbol")
    if isinstance(sym, str) and sym:
        return sym
    iid = inst.get("instrumentId") or {}
    return iid.get("symbol") if isinstance(iid.get("symbol"), str) else ""


def _maturity(inst: dict) -> str:
    return inst.get("maturityDate") or inst.get("maturity_date") or ""


def descubrir_outrights_agro() -> list[dict]:
    """Devuelve [{ticker, maturity, commodity, underlying}] de outrights agro vigentes."""
    res = pyRofex.get_detailed_instruments()
    if not res or res.get("status") != "OK":
        logger.error("get_detailed_instruments() falló: %s", res)
        return []

    hoy_str = datetime.now().strftime("%Y%m%d")
    out: list[dict] = []
    for inst in res.get("instruments") or []:
        if inst.get("cficode") != CFICODE_OUTRIGHT:
            continue
        underlying = inst.get("underlying") or ""
        commodity = _classify_commodity(underlying)
        if not commodity:
            continue
        mat = _maturity(inst)
        if mat <= hoy_str:
            continue
        ticker = _ticker_de(inst)
        if not ticker or ticker.count("/") != 1:
            continue
        out.append({
            "ticker":     ticker,
            "maturity":   mat,
            "commodity":  commodity,
            "underlying": underlying,
        })
    out.sort(key=lambda x: (x["commodity"], x["maturity"]))
    return out


def _dias_a_vto(mat_str: str) -> int:
    try:
        vto = date(int(mat_str[:4]), int(mat_str[4:6]), int(mat_str[6:8]))
        return max(1, (vto - date.today()).days)
    except Exception:
        return 1


# ─────────────────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────────────────


class AgroEngine:
    def __init__(self):
        self.client = get_mongo_client()
        self.col_snap = self.client["Trading"]["AgroSnapshot"]

        self.universo: list[dict] = descubrir_outrights_agro()
        if not self.universo:
            raise RuntimeError("No hay outrights agro vigentes — abortando.")

        commodities = sorted({u["commodity"] for u in self.universo})
        logger.info(
            "Outrights agro descubiertos: %d (commodities: %s)",
            len(self.universo), commodities,
        )

        self.market_state: dict[str, dict] = {u["ticker"]: {} for u in self.universo}
        self._state_lock = threading.Lock()
        self._ultimo_discovery = time.time()

        threading.Thread(target=self._snapshot_loop, daemon=True).start()

    # ─── WS handler ───────────────────────────────────────────────────────
    def update_price(self, ticker: str, data: dict):
        with self._state_lock:
            if ticker not in self.market_state:
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
                self._maybe_rediscover()
                self._volcar_snapshot()
            except Exception:
                logger.error("Error en snapshot_loop:\n%s", traceback.format_exc())

    def _maybe_rediscover(self):
        if time.time() - self._ultimo_discovery < INTERVALO_REDISCOVERY_S:
            return
        self._ultimo_discovery = time.time()
        nuevos = descubrir_outrights_agro()
        viejos_set = {u["ticker"] for u in self.universo}
        nuevos_set = {u["ticker"] for u in nuevos}
        if nuevos_set != viejos_set:
            agregados = nuevos_set - viejos_set
            sacados = viejos_set - nuevos_set
            logger.info(
                "Discovery cambió: +%s -%s (restart del motor para tomar cambios)",
                sorted(agregados), sorted(sacados),
            )

    def _volcar_snapshot(self):
        ts = datetime.now(UTC)
        ops = []
        with self._state_lock:
            for u in self.universo:
                st = self.market_state.get(u["ticker"], {})
                doc = self._build_snapshot_doc(u, st, ts)
                if doc:
                    ops.append(ReplaceOne({"ticker": u["ticker"]}, doc, upsert=True))
        if ops:
            self.col_snap.bulk_write(ops, ordered=False)

    def _build_snapshot_doc(self, u: dict, st: dict, ts: datetime) -> dict:
        last = st.get("last") or {}
        bid = st.get("bid") or {}
        offer = st.get("offer") or {}
        closing = st.get("closing") or {}
        return {
            "ticker":        u["ticker"],
            "commodity":     u["commodity"],
            "underlying":    u["underlying"],
            "vencimiento":   u["maturity"],
            "dias_a_vto":    _dias_a_vto(u["maturity"]),
            "bid_price":     bid.get("price"),
            "bid_size":      bid.get("size"),
            "offer_price":   offer.get("price"),
            "offer_size":    offer.get("size"),
            "last_price":    last.get("price"),
            "last_size":     last.get("size"),
            "open":          st.get("open"),
            "high":          st.get("high"),
            "low":           st.get("low"),
            "closing":       closing.get("price"),
            "vol_efectivo":  st.get("vol_efectivo"),
            "updated_at":    ts,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Bucle principal
# ─────────────────────────────────────────────────────────────────────────────


def run():
    logger.info("Motor Agro iniciando...")
    if not inicializar_sesion():
        return

    try:
        engine = AgroEngine()
    except RuntimeError as e:
        logger.error(str(e))
        return

    ws = WebSocketManager(engine)
    tickers = [u["ticker"] for u in engine.universo]

    if not ws.iniciar_ws(tickers, depth=1):
        logger.error("No pude iniciar WS")
        return

    logger.info(
        "WS arriba. Suscripto a %d outrights agro. Snapshot cada %ds. "
        "Re-discovery cada %ds.",
        len(tickers), INTERVALO_SNAPSHOT_S, INTERVALO_REDISCOVERY_S,
    )

    try:
        while _running:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            ws.cerrar_ws()
        except Exception:
            pass


if __name__ == "__main__":
    run()
