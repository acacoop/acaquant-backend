import os
import queue
import logging
import pyRofex
import threading
import time
import traceback
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from collections import deque
from pymongo import ReplaceOne

# --- TUS MANAGERS DE INFRAESTRUCTURA ---
from session_manager import inicializar_sesion
from websocket_manager import WebSocketManager
from mongo_manager import get_mongo_client
from tickers import MERV_TICKERS as TICKERS

logger = logging.getLogger("MotorValores")

ART = ZoneInfo("America/Argentina/Buenos_Aires")

VOLUME_BUCKET_SIZES = {
    "MERV - XMEV - TZXM6 - 24hs": 1056003093,
    "MERV - XMEV - S17A6 - 24hs": 1269958754, "MERV - XMEV - S30A6 - 24hs": 205979378,
    "MERV - XMEV - S29Y6 - 24hs": 384357785, "MERV - XMEV - T30J6 - 24hs": 291213720,
    "MERV - XMEV - S31L6 - 24hs": 93799093, "MERV - XMEV - S31G6 - 24hs": 39515420,
    "MERV - XMEV - S30O6 - 24hs": 30698089, "MERV - XMEV - S30N6 - 24hs": 25196577,
    "MERV - XMEV - T15E7 - 24hs": 157290278, "MERV - XMEV - T30A7 - 24hs": 438883807,
    "MERV - XMEV - T31Y7 - 24hs": 98359279, "MERV - XMEV - T30J7 - 24hs": 21615689,
    "MERV - XMEV - TY30P - 24hs": 26615939, "MERV - XMEV - X15Y6 - 24hs": 113153036,
    "MERV - XMEV - X29Y6 - 24hs": 426532800, "MERV - XMEV - TZX26 - 24hs": 363769490,
    "MERV - XMEV - X31L6 - 24hs": 67044105, "MERV - XMEV - TX26 - 24hs": 139822955,
    "MERV - XMEV - TZXO6 - 24hs": 208287938, "MERV - XMEV - X30N6 - 24hs": 80774915,
    "MERV - XMEV - TZXD6 - 24hs": 262315858, "MERV - XMEV - TZXM7 - 24hs": 176877365,
    "MERV - XMEV - TZXY7 - 24hs": 133934, "MERV - XMEV - TZX27 - 24hs": 9495066,
    "MERV - XMEV - TX28 - 24hs": 23072063, "MERV - XMEV - TZXD7 - 24hs": 156324283,
    "MERV - XMEV - TZX28 - 24hs": 172279837, "MERV - XMEV - DICP - 24hs": 27262545,
    "MERV - XMEV - PARP - 24hs": 4436050
}

# ==========================================
# 1. EL CEREBRO: MicrostructureEngine (INTACTO)
# ==========================================
class MicrostructureEngine:
    def __init__(self, tickers):
        self.tickers = tickers
        self.tick_queue = queue.Queue()
        self.trade_buffer = []
        self._buffer_lock = threading.Lock()
        self.market_state = {
            t: {
                "book": {"bids": [], "offers": []},
                "trades": deque(maxlen=100),
                "last_nv": 0.0,
                "last_price":    0.0,
                "open_price":    0.0,
                "high_price":    0.0,
                "low_price":     0.0,
                "closing_price": 0.0,
                "closed_vpins": deque(maxlen=50),
                "vpin_stats": {"current_buy_vol": 0, "current_sell_vol": 0, "last_vpin": 0.0},
                "daily_financials": {"total_money": 0.0, "buy_money": 0.0, "sell_money": 0.0, "total_nominals": 0.0},
                "top_trades": [],
                "hourly_stats": {h: {"buy": 0.0, "sell": 0.0, "total": 0.0} for h in range(10, 18)}
            } for t in self.tickers
        }
        try:
            self.mongo_client = get_mongo_client()
            self.db = self.mongo_client["Trading"]
            self.col_trades = self.db["TimeSales"]
            self.col_snapshot = self.db["MarketSnapshot"]
        except Exception as e:
            print(f"Error conectando a Mongo en main_ts: {e}")
            self.col_trades = None
            self.col_snapshot = None

        self._arranque_en_frio()
        threading.Thread(target=self._worker_loop, daemon=True).start()
        threading.Thread(target=self._flush_loop, daemon=True).start()
        threading.Thread(target=self._snapshot_loop, daemon=True).start()

    def _arranque_en_frio(self):
        # 1) Recuperar OPEN/HIGH/LOW reales desde el REST API de Rofex
        for ticker in self.tickers:
            try:
                md = pyRofex.get_market_data(
                    ticker,
                    entries=[
                        pyRofex.MarketDataEntry.OPENING_PRICE,
                        pyRofex.MarketDataEntry.HIGH_PRICE,
                        pyRofex.MarketDataEntry.LOW_PRICE,
                        pyRofex.MarketDataEntry.CLOSING_PRICE,
                        pyRofex.MarketDataEntry.LAST,
                    ]
                )
                data = md.get("marketData", {})
                st = self.market_state[ticker]
                if data.get("OP"):
                    st["open_price"] = float(data["OP"])
                if data.get("HI"):
                    st["high_price"] = float(data["HI"])
                if data.get("LO"):
                    st["low_price"] = float(data["LO"])
                if data.get("CL"):
                    st["closing_price"] = float(data["CL"])
                la = data.get("LA")
                if la and la.get("price"):
                    st["last_price"] = float(la["price"])
            except Exception as e:
                print(f"⚠️ No se pudo obtener market data REST para {ticker}: {e}")

        # 2) Reconstruir financials del día desde trades en MongoDB
        if self.col_trades is None: return
        _art_now = datetime.utcnow() - timedelta(hours=3)
        inicio = _art_now.replace(hour=0, minute=0, second=0, microsecond=0)
        for ticker in self.tickers:
            st = self.market_state[ticker]
            for doc in self.col_trades.find({"ticker": ticker, "timestamp": {"$gte": inicio}}):
                px, sz, sd = doc.get("price", 0), doc.get("size", 0), doc.get("side", "MID")
                cash = (px / 100.0) * sz
                st["daily_financials"]["total_nominals"] += sz
                st["daily_financials"]["total_money"] += cash
                if sd == "BUY":
                    st["daily_financials"]["buy_money"] += cash
                elif sd == "SELL":
                    st["daily_financials"]["sell_money"] += cash
                h = doc["timestamp"].hour
                if 10 <= h <= 17:
                    st["hourly_stats"][h]["total"] += cash
                    if sd == "BUY":
                        st["hourly_stats"][h]["buy"] += cash
                    elif sd == "SELL":
                        st["hourly_stats"][h]["sell"] += cash
                st["top_trades"].append(
                    {"timestamp": doc["timestamp"], "price": px, "size": sz, "side": sd, "money": cash})

            st["top_trades"] = sorted(st["top_trades"], key=lambda x: x["money"], reverse=True)[:15]

    def update_price(self, ticker, data):
        self.tick_queue.put((ticker, data))

    def _worker_loop(self):
        while True:
            try:
                ticker, data = self.tick_queue.get(timeout=0.5)
                try:
                    self._procesar_tick_logica(ticker, data)
                except Exception:
                    logger.error(f"Error procesando tick {ticker}:\n{traceback.format_exc()}")
            except queue.Empty:
                pass

    def _flush_loop(self):
        """Thread dedicado: persiste trades en MongoDB sin bloquear el worker."""
        while True:
            time.sleep(1.0)
            if not self.trade_buffer or self.col_trades is None:
                continue
            with self._buffer_lock:
                batch = self.trade_buffer[:]
                self.trade_buffer = []
            try:
                self.col_trades.insert_many(batch)
            except Exception as e:
                print(f"Error flush trades: {e}")

    def _procesar_tick_logica(self, ticker, data):
        st = self.market_state[ticker]
        if "BI" in data: st["book"]["bids"] = data["BI"][:5]
        if "OF" in data: st["book"]["offers"] = data["OF"][:5]
        if "EV" in data and data["EV"] is not None: st["daily_financials"]["total_money"] = float(data["EV"])
        if "NV" in data and data["NV"] is not None: st["daily_financials"]["total_nominals"] = float(data["NV"])
        def _to_float(v):
            if isinstance(v, dict): return float(v.get("price", 0) or 0)
            return float(v) if v else 0.0
        if "OP" in data and data["OP"]: st["open_price"]    = _to_float(data["OP"])
        if "HI" in data and data["HI"]: st["high_price"]    = _to_float(data["HI"])
        if "LO" in data and data["LO"]: st["low_price"]     = _to_float(data["LO"])
        if "CL" in data and data["CL"]: st["closing_price"] = _to_float(data["CL"])

        last, nv = data.get("LA"), data.get("NV")
        if last and nv is not None:
            px, ts_ms = float(last.get("price", 0)), last.get("date", 0)
            if px <= 0: return
            if st["last_nv"] == 0:
                st["last_nv"], st["last_price"] = nv, px
                return

            if nv > st["last_nv"]:
                sz = nv - st["last_nv"]
                st["last_nv"] = nv

                b1 = st["book"]["bids"][0]["price"] if st["book"]["bids"] else 0
                o1 = st["book"]["offers"][0]["price"] if st["book"]["offers"] else 0
                side = "MID"
                if px >= o1 and o1 > 0:
                    side = "BUY"
                elif px <= b1 and b1 > 0:
                    side = "SELL"
                elif px > st["last_price"]:
                    side = "BUY"
                elif px < st["last_price"]:
                    side = "SELL"
                st["last_price"] = px

                cash = (px / 100.0) * sz

                bucket_limit = VOLUME_BUCKET_SIZES.get(ticker, 1000000)
                rem_size = sz
                while rem_size > 0:
                    fill = st["vpin_stats"]["current_buy_vol"] + st["vpin_stats"]["current_sell_vol"]
                    chunk = min(rem_size, bucket_limit - fill)
                    if side == "BUY":
                        st["vpin_stats"]["current_buy_vol"] += chunk
                    elif side == "SELL":
                        st["vpin_stats"]["current_sell_vol"] += chunk
                    rem_size -= chunk
                    if (st["vpin_stats"]["current_buy_vol"] + st["vpin_stats"]["current_sell_vol"]) >= bucket_limit:
                        v_diff = abs(st["vpin_stats"]["current_buy_vol"] - st["vpin_stats"]["current_sell_vol"])
                        vpin_val = v_diff / bucket_limit
                        st["closed_vpins"].append(vpin_val)
                        st["vpin_stats"]["last_vpin"] = sum(st["closed_vpins"]) / len(st["closed_vpins"])
                        st["vpin_stats"]["current_buy_vol"], st["vpin_stats"]["current_sell_vol"] = 0, 0

                st["daily_financials"]["total_nominals"] += sz
                if side == "BUY":
                    st["daily_financials"]["buy_money"] += cash
                elif side == "SELL":
                    st["daily_financials"]["sell_money"] += cash

                dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).astimezone(ART).replace(tzinfo=None)
                h = dt.hour
                if 10 <= h <= 17:
                    st["hourly_stats"][h]["total"] += cash
                    if side == "BUY":
                        st["hourly_stats"][h]["buy"] += cash
                    elif side == "SELL":
                        st["hourly_stats"][h]["sell"] += cash

                trade = {"timestamp": dt, "price": px, "size": sz, "side": side, "money": cash}
                st["top_trades"].append(trade)
                st["top_trades"] = sorted(st["top_trades"], key=lambda x: x["size"], reverse=True)[:15]
                st["trades"].appendleft(trade)
                with self._buffer_lock:
                    self.trade_buffer.append({"ticker": ticker, **trade})

    def _calcular_metricas(self, ticker):
        """Calcula métricas de microestructura para el snapshot."""
        st = self.market_state[ticker]
        b = st["book"]["bids"]
        o = st["book"]["offers"]
        fs = st["daily_financials"]
        vs = st["vpin_stats"]

        m_px, sp, imb = 0.0, 0.0, 0.0
        if b and o:
            b_px, b_sz = b[0]['price'], b[0]['size']
            o_px, o_sz = o[0]['price'], o[0]['size']
            m_px = (b_px * o_sz + o_px * b_sz) / (b_sz + o_sz) if (b_sz + o_sz) > 0 else 0
            sp = o_px - b_px
            tot_b = sum(x['size'] for x in b)
            tot_o = sum(x['size'] for x in o)
            imb = (tot_b - tot_o) / (tot_b + tot_o) if (tot_b + tot_o) > 0 else 0

        bucket_limit = VOLUME_BUCKET_SIZES.get(ticker, 1000000)
        tot_bucket = vs["current_buy_vol"] + vs["current_sell_vol"]
        progreso = tot_bucket / bucket_limit if bucket_limit > 0 else 0
        v_vivo = abs(vs["current_buy_vol"] - vs["current_sell_vol"]) / tot_bucket if tot_bucket > 0 else 0

        return {
            "micro_price": m_px,
            "spread": sp,
            "imbalance": imb,
            "total_nominals": fs["total_nominals"],
            "total_money": fs["total_money"],
            "buy_money": fs["buy_money"],
            "sell_money": fs["sell_money"],
            "vwap": (fs["total_money"] / fs["total_nominals"] * 100) if fs["total_nominals"] > 0 else 0,
            "vpin_prom": vs["last_vpin"],
            "vpin_vivo": v_vivo,
            "progreso": progreso,
            "buy_b": vs["current_buy_vol"],
            "sell_b": vs["current_sell_vol"],
            "last_price":    st["last_price"],
            "open_price":    st["open_price"],
            "high_price":    st["high_price"],
            "low_price":     st["low_price"],
            "closing_price": st["closing_price"],
        }

    def _snapshot_loop(self):
        """
        Escribe el estado completo de todos los tickers a MarketSnapshot cada 1s.
        Un único bulk_write reemplaza N round-trips individuales a Atlas.
        """
        while True:
            time.sleep(1)
            if self.col_snapshot is None:
                continue
            try:
                ts = datetime.now()
                ops = []
                for ticker in self.tickers:
                    st = self.market_state[ticker]
                    metricas = self._calcular_metricas(ticker)
                    doc = {
                        "ticker": ticker,
                        "updated_at": ts,
                        "book": {
                            "bids":   list(st["book"]["bids"]),
                            "offers": list(st["book"]["offers"]),
                        },
                        "metrics": metricas,
                        "hourly_stats": {str(k): v for k, v in st["hourly_stats"].items()},
                        "top_trades":   list(st["top_trades"]),
                        "recent_trades": list(st["trades"])[:30],
                    }
                    ops.append(ReplaceOne({"ticker": ticker}, doc, upsert=True))

                if ops:
                    self.col_snapshot.bulk_write(ops, ordered=False)

            except Exception as e:
                logger.error(f"Error escribiendo snapshots: {e}")


# ==========================================
# 2. EL BUCLE PRINCIPAL (MODO MOTOR CIEGO)
# ==========================================
def run():
    print("🚀 Iniciando Motor de Escritura (Modo Headless / Sin Interfaz)...")
    if not inicializar_sesion(): return

    engine = MicrostructureEngine(TICKERS)
    ws_manager = WebSocketManager(engine)

    try:
        # Iniciamos el WebSocket
        if ws_manager.iniciar_ws(TICKERS, depth=5):
            print("✅ Conectado a Rofex. Escuchando y guardando datos...")
            # Como borramos la app visual, necesitamos este bucle infinito
            # para que el script no se cierre y el thread de Rofex siga vivo.
            while True:
                time.sleep(1)

    except KeyboardInterrupt:
        print("\n🛑 Apagado manual detectado.")
    except Exception:
        print("\n❌ EL PROGRAMA CRASHEÓ:")
        traceback.print_exc()

    finally:
        print("\n🛑 Cerrando WebSocket y limpiando hilos...")
        try:
            pyRofex.close_websocket_connection()
        except:
            pass
        os._exit(0)

if __name__ == "__main__":
    run()