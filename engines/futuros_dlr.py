"""Motor de futuros DLR (Dólar A3500) — outrights single-leg.

Discovery dinámico: cada N minutos consulta pyRofex.get_detailed_instruments()
y filtra los outrights vigentes del underlying 'Dólar USA A3500':
  - cficode == 'FXXXSX' (outrights, no calendar spreads que son FXXXXX)
  - maturityDate > hoy
  - ticker tiene exactamente 1 '/' (DLR/MMMYY) — no DLR/MMMYY/MMMYY (spreads)
  - ticker NO termina en 'M' (variantes paralelas, no las queremos)

Persistencia:
- Trading.FuturosDLRSnapshot: 1 doc por ticker, replaced cada 15s.
  {ticker, vencimiento, dias_a_vto, bid, offer, last, open, high, low,
   closing, vol_efectivo, tasa_implicita_tna, updated_at}
- Trading.FuturosDLR: 1 doc por (fecha, ticker) escrito al apagado del
  motor (cierre 20:05 UTC). Sirve como serie histórica.
  {fecha, ticker, vencimiento, dias_a_vto, precio_cierre, vol_dia,
   tasa_implicita_tna_cierre}

Tasa implícita: TNA LINEAL = (precio_dlr/spot - 1) × (365 / dias_a_vto).
Antes era TEA compuesta ((precio/spot)^(365/dias) - 1) — la mesa pidió
TNA lineal porque es lo que muestra el terminal Rofex y los traders
comparan tasas en esa convención. Para vencimientos largos (>180 días)
la TNA lineal es 2-4 puntos más baja que la TEA compuesta.

Se persisten 3 tasas separadas — sobre bid, sobre last y sobre offer.
La principal (`tasa_implicita_tna`) es la del last; las otras dos
quedan en `_bid` y `_offer` para mostrar dispersión en la watchlist.

Spot de referencia — feed MAE mayorista UST$T plazo 000:
    1. Valuaciones.DolarOficialLive (script local PC oficina) vía
       core.dolar_oficial.mid_oficial_live. Es el spot que liquida los
       DLR (mayorista A3500 contado).
    2. Trading.DOLAR (BCRA A3500 fixing diario) — fallback si MAE está
       caído (PC apagada).
    3. Valuaciones.Dolar.mep — último fallback para que nunca quede None.

Ejecutar:
    python -m engines.futuros_dlr
"""
from __future__ import annotations

import logging
import signal
import threading
import time
import traceback
from datetime import UTC, date, datetime

import pyRofex
from pymongo import ReplaceOne, UpdateOne

from core.mongo import get_mongo_client
from core.rofex_session import inicializar_sesion
from core.threads import lanzar_hilo_vital
from core.websocket import WebSocketManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("MotorFuturosDLR")

INTERVALO_SNAPSHOT_S = 5
INTERVALO_REDISCOVERY_S = 300   # cada 5 min re-evalúa universo de tickers

UNDERLYING_DLR = "Dólar USA A3500"
CFICODE_OUTRIGHT = "FXXXSX"

_running = True


def _handle_signal(sig, frame):
    global _running
    logger.info("Señal de cierre recibida — vuelco snapshot a histórico y apago.")
    _running = False


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _ticker_de(inst: dict) -> str:
    """ROFEX a veces deja symbol=None y pone el ticker en instrumentId.symbol."""
    sym = inst.get("symbol")
    if isinstance(sym, str) and sym:
        return sym
    iid = inst.get("instrumentId") or {}
    return iid.get("symbol") if isinstance(iid.get("symbol"), str) else ""


def _maturity(inst: dict) -> str:
    return inst.get("maturityDate") or inst.get("maturity_date") or ""


def descubrir_outrights_dlr() -> list[tuple[str, str]]:
    """Devuelve [(ticker, maturity_yyyymmdd)] de outrights DLR vigentes.

    Filtros aplicados (en orden, por seguridad):
    1. underlying == 'Dólar USA A3500'
    2. cficode == 'FXXXSX'
    3. maturityDate > hoy
    4. ticker tiene exactamente 1 '/' (descarta calendar spreads como
       'DLR/MAY26/JUN26')
    5. ticker NO termina en 'M' (descarta variantes paralelas como
       'DLR/JUN26M')
    """
    res = pyRofex.get_detailed_instruments()
    if not res or res.get("status") != "OK":
        logger.error("get_detailed_instruments() falló: %s", res)
        return []

    hoy_str = datetime.now().strftime("%Y%m%d")
    out: list[tuple[str, str]] = []
    for inst in res.get("instruments") or []:
        if inst.get("underlying") != UNDERLYING_DLR:
            continue
        if inst.get("cficode") != CFICODE_OUTRIGHT:
            continue
        mat = _maturity(inst)
        if mat <= hoy_str:
            continue
        ticker = _ticker_de(inst)
        if not ticker:
            continue
        if ticker.count("/") != 1:
            continue
        if ticker.endswith("M"):
            continue
        out.append((ticker, mat))
    out.sort(key=lambda x: x[1])  # por vencimiento ascendente
    return out


def _dias_a_vto(mat_str: str) -> int:
    """Días calendario entre hoy y maturity. Mínimo 1 para evitar div/0."""
    try:
        vto = date(int(mat_str[:4]), int(mat_str[4:6]), int(mat_str[6:8]))
        return max(1, (vto - date.today()).days)
    except Exception:
        return 1


def _spot_referencia(client) -> tuple[float | None, str]:
    """Spot de referencia para calcular tasa implícita. Devuelve (valor, fuente).

    Prefiere precioUltimo del oficial mayorista (UST$T MAE) vía
    core.dolar_oficial — fuente única compartida con el watchlist ARGY.
    Cae a A3500 BCRA fixing si el feed MAE está caído, y finalmente a MEP.
    """
    # 1) Spot mayorista MAE — fuente única
    from core.dolar_oficial import mid_oficial_live
    live = mid_oficial_live("oficial")
    if live.get("value"):
        return float(live["value"]), live.get("source") or "oficial_mae"
    # Si MAE está offline (PC apagada, etc.), value=None y caemos al fallback.

    # 2) A3500 BCRA fixing diario (SQL-only: macro.series_macro)
    from core.series_macro import ultimo_valor
    v = ultimo_valor("DOLAR", positivo=True)
    if v is not None:
        return v, "a3500_bcra"

    # 3) Último fallback: MEP (incorrecto conceptualmente pero mejor que None)
    from core import dolar_sql
    doc = dolar_sql.ultimo("mep")
    if doc and doc.get("mep"):
        return float(doc["mep"]), "mep_fallback"

    return None, "none"


def _tasa_implicita_tna(precio_dlr: float | None, spot: float | None, dias: int) -> float | None:
    """TNA lineal: (dlr/spot - 1) × (365/dias), en porcentaje.

    Convención del terminal Rofex y de la mesa local. NO es TEA compuesta
    (que sería ((dlr/spot)^(365/dias) - 1)). Para vencimientos cortos
    convergen, para largos divergen 2-4 puntos.

    None si falta data.
    """
    if not precio_dlr or not spot or precio_dlr <= 0 or spot <= 0 or dias <= 0:
        return None
    try:
        return round((precio_dlr / spot - 1) * (365 / dias) * 100, 4)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────────────────


class FuturosDLREngine:
    def __init__(self):
        self.client = get_mongo_client()
        self.col_snap = self.client["Trading"]["FuturosDLRSnapshot"]
        self.col_hist = self.client["Trading"]["FuturosDLR"]

        self.tickers_actuales: list[tuple[str, str]] = descubrir_outrights_dlr()
        if not self.tickers_actuales:
            raise RuntimeError("No hay outrights DLR vigentes — abortando.")
        logger.info(
            "Outrights DLR descubiertos: %d (de %s a %s)",
            len(self.tickers_actuales),
            self.tickers_actuales[0][0],
            self.tickers_actuales[-1][0],
        )

        # market_state: {ticker: {bid, offer, last, open, high, low, closing, vol_*}}
        self.market_state: dict[str, dict] = {t: {} for t, _ in self.tickers_actuales}
        self._state_lock = threading.Lock()
        self._ultimo_discovery = time.time()

        lanzar_hilo_vital(self._snapshot_loop, "snapshot_loop")

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
        """Re-discovery cada 5 min para captar vencimientos nuevos / vencidos.
        En esta versión simple solo loggea diferencias — re-suscripción WS
        requiere extender WebSocketManager."""
        if time.time() - self._ultimo_discovery < INTERVALO_REDISCOVERY_S:
            return
        self._ultimo_discovery = time.time()
        nuevos = descubrir_outrights_dlr()
        viejos_set = {t for t, _ in self.tickers_actuales}
        nuevos_set = {t for t, _ in nuevos}
        if nuevos_set != viejos_set:
            agregados = nuevos_set - viejos_set
            sacados = viejos_set - nuevos_set
            logger.info(
                "Discovery cambió: +%s -%s (restart del motor para tomar cambios)",
                sorted(agregados), sorted(sacados),
            )

    def _volcar_snapshot(self):
        ts = datetime.now(UTC)
        spot, fuente_spot = _spot_referencia(self.client)
        ops = []
        docs = []
        with self._state_lock:
            for ticker, mat in self.tickers_actuales:
                st = self.market_state.get(ticker, {})
                doc = self._build_snapshot_doc(ticker, mat, st, spot, fuente_spot, ts)
                if doc:
                    ops.append(ReplaceOne({"ticker": ticker}, doc, upsert=True))
                    docs.append(doc)
        if ops:
            self.col_snap.bulk_write(ops, ordered=False)
            # Dual-write SQL (flag SNAPSHOT_SQL): snapshot live de futuros DLR.
            try:
                from core import pg_mirror
                pg_mirror.mirror_snapshot("futuros_dlr_snapshot", ["ticker"], [
                    {"ticker": d.get("ticker"), "vencimiento": d.get("vencimiento"),
                     "data": pg_mirror.doc_iso(d)}
                    for d in docs if d.get("ticker")
                ])
            except Exception:
                pass

    def _build_snapshot_doc(
        self, ticker: str, mat: str, st: dict, spot: float | None,
        fuente_spot: str, ts: datetime,
    ) -> dict | None:
        last = st.get("last") or {}
        bid = st.get("bid") or {}
        offer = st.get("offer") or {}
        closing = st.get("closing") or {}
        precio_last = last.get("price")
        precio_bid = bid.get("price")
        precio_offer = offer.get("price")
        dias = _dias_a_vto(mat)
        return {
            "ticker":                    ticker,
            "vencimiento":               mat,
            "dias_a_vto":                dias,
            "bid_price":                 precio_bid,
            "bid_size":                  bid.get("size"),
            "offer_price":               precio_offer,
            "offer_size":                offer.get("size"),
            "last_price":                precio_last,
            "last_size":                 last.get("size"),
            "open":                      st.get("open"),
            "high":                      st.get("high"),
            "low":                       st.get("low"),
            "closing":                   closing.get("price"),
            "vol_efectivo":              st.get("vol_efectivo"),
            # 3 TNAs separadas — la principal es sobre last, las otras dos
            # se persisten para mostrar la dispersión en la watchlist.
            "tasa_implicita_tna":        _tasa_implicita_tna(precio_last, spot, dias),
            "tasa_implicita_tna_bid":    _tasa_implicita_tna(precio_bid, spot, dias),
            "tasa_implicita_tna_offer":  _tasa_implicita_tna(precio_offer, spot, dias),
            "spot_referencia":           spot,
            "fuente_spot":               fuente_spot,
            "updated_at":                ts,
        }

    # ─── Vuelco al cierre ─────────────────────────────────────────────────
    def vuelco_cierre(self):
        hoy = date.today().isoformat()
        ts = datetime.now(UTC)
        spot, fuente_spot = _spot_referencia(self.client)
        ops = []
        with self._state_lock:
            for ticker, mat in self.tickers_actuales:
                st = self.market_state.get(ticker, {})
                last = st.get("last") or {}
                closing = st.get("closing") or {}
                precio_cierre = last.get("price") or closing.get("price")
                if precio_cierre is None:
                    continue
                dias = _dias_a_vto(mat)
                doc = {
                    "fecha":                       hoy,
                    "ticker":                      ticker,
                    "vencimiento":                 mat,
                    "dias_a_vto":                  dias,
                    "precio_cierre":               precio_cierre,
                    "open":                        st.get("open"),
                    "high":                        st.get("high"),
                    "low":                         st.get("low"),
                    "vol_efectivo":                st.get("vol_efectivo"),
                    "tasa_implicita_tna_cierre":   _tasa_implicita_tna(precio_cierre, spot, dias),
                    "spot_referencia":             spot,
                    "fuente_spot":                 fuente_spot,
                    "persisted_at":                ts,
                }
                ops.append(UpdateOne(
                    {"fecha": hoy, "ticker": ticker},
                    {"$set": doc},
                    upsert=True,
                ))
        if ops:
            self.col_hist.bulk_write(ops, ordered=False)
            logger.info("Vuelco de cierre OK: %d docs en Trading.FuturosDLR", len(ops))


# ─────────────────────────────────────────────────────────────────────────────
# Bucle principal
# ─────────────────────────────────────────────────────────────────────────────


def run():
    logger.info("Motor Futuros DLR iniciando...")
    if not inicializar_sesion():
        return

    try:
        engine = FuturosDLREngine()
    except RuntimeError as e:
        logger.error(str(e))
        return

    ws = WebSocketManager(engine)
    tickers = [t for t, _ in engine.tickers_actuales]

    if not ws.iniciar_ws(tickers, depth=1):
        logger.error("No pude iniciar WS")
        return

    logger.info(
        "WS arriba. Suscripto a %d outrights DLR. Snapshot cada %ds. "
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
            engine.vuelco_cierre()
        except Exception:
            logger.exception("Vuelco de cierre falló")
        try:
            ws.cerrar_ws()
        except Exception:
            pass


if __name__ == "__main__":
    run()
