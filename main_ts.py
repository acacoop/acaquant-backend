import os
import queue
import pyRofex
import threading
import time
import traceback
from datetime import datetime, timezone
from collections import deque
from rich.table import Table
from rich.panel import Panel
from rich import box
from textual.app import App, ComposeResult
from textual.widgets import Static, Select
from textual.containers import Horizontal, Vertical
from pymongo import MongoClient
import psutil
import os

# --- TUS MANAGERS DE INFRAESTRUCTURA ---
from session_manager import inicializar_sesion
from websocket_manager import WebSocketManager
from mongo_manager import get_mongo_client

# --- CONFIGURACIÓN MULTIACTIVO ---
TICKERS = [
    "MERV - XMEV - TZXM6 - 24hs", "MERV - XMEV - S17A6 - 24hs",
    "MERV - XMEV - S30A6 - 24hs", "MERV - XMEV - S29Y6 - 24hs", "MERV - XMEV - T30J6 - 24hs",
    "MERV - XMEV - S31L6 - 24hs", "MERV - XMEV - S31G6 - 24hs", "MERV - XMEV - S30O6 - 24hs",
    "MERV - XMEV - S30N6 - 24hs", "MERV - XMEV - T15E7 - 24hs", "MERV - XMEV - T30A7 - 24hs",
    "MERV - XMEV - T31Y7 - 24hs", "MERV - XMEV - T30J7 - 24hs", "MERV - XMEV - TY30P - 24hs",
    "MERV - XMEV - X15Y6 - 24hs", "MERV - XMEV - X29Y6 - 24hs", "MERV - XMEV - TZX26 - 24hs",
    "MERV - XMEV - X31L6 - 24hs", "MERV - XMEV - TX26 - 24hs", "MERV - XMEV - TZXO6 - 24hs",
    "MERV - XMEV - X30N6 - 24hs", "MERV - XMEV - TZXD6 - 24hs", "MERV - XMEV - TZXM7 - 24hs",
    "MERV - XMEV - TZXY7 - 24hs", "MERV - XMEV - TZX27 - 24hs", "MERV - XMEV - TX28 - 24hs",
    "MERV - XMEV - TZXD7 - 24hs", "MERV - XMEV - TZX28 - 24hs", "MERV - XMEV - DICP - 24hs",
    "MERV - XMEV - PARP - 24hs"
]

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
# 1. EL CEREBRO: MicrostructureEngine
# ==========================================
class MicrostructureEngine:
    def __init__(self, tickers):
        self.tickers = tickers
        self.tick_queue = queue.Queue()
        self.trade_buffer = []
        self.market_state = {
            t: {
                "book": {"bids": [], "offers": []},
                "trades": deque(maxlen=100),
                "last_nv": 0.0,
                "last_price": 0.0,
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
        except Exception as e:
            print(f"Error conectando a Mongo en main_ts: {e}")
            self.col_trades = None

        self.col_snapshots = self.db["MarketSnapshot"] if self.mongo_client else None

        self._arranque_en_frio()
        threading.Thread(target=self._worker_loop, daemon=True).start()
        threading.Thread(target=self._snapshot_writer_loop, daemon=True).start()

    def _arranque_en_frio(self):
        if self.col_trades is None: return
        inicio = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
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
                # Agregamos money para que el sort inicial sea correcto
                st["top_trades"].append(
                    {"timestamp": doc["timestamp"], "price": px, "size": sz, "side": sd, "money": cash})

            # Whales: Ordenadas por plata ($)
            st["top_trades"] = sorted(st["top_trades"], key=lambda x: x["money"], reverse=True)[:15]

    def update_price(self, ticker, data):
        self.tick_queue.put((ticker, data))

    def _worker_loop(self):
        last_flush = time.time()
        while True:
            try:
                ticker, data = self.tick_queue.get(timeout=0.5)
                try:
                    self._procesar_tick_logica(ticker, data)
                except Exception:
                    with open("errores_bot.txt", "a") as f:
                        f.write(traceback.format_exc())
            except queue.Empty:
                pass
            if len(self.trade_buffer) >= 50 or (len(self.trade_buffer) > 0 and time.time() - last_flush > 1.0):
                if self.col_trades is not None: self.col_trades.insert_many(self.trade_buffer)
                self.trade_buffer, last_flush = [], time.time()

    def _procesar_tick_logica(self, ticker, data):
        st = self.market_state[ticker]
        if "BI" in data: st["book"]["bids"] = data["BI"][:5]
        if "OF" in data: st["book"]["offers"] = data["OF"][:5]
        if "EV" in data and data["EV"] is not None: st["daily_financials"]["total_money"] = float(data["EV"])
        # AGREGADO: Sincronizamos nominales totales con el dato oficial de Rofex
        if "NV" in data and data["NV"] is not None: st["daily_financials"]["total_nominals"] = float(data["NV"])

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

                # --- LÓGICA VPIN ---
                # 1.000.000 es el valor por defecto si el ticker no está en tu lista
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

                dt = datetime.fromtimestamp(ts_ms / 1000.0)
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
                self.trade_buffer.append({"ticker": ticker, **trade})

    def _snapshot_writer_loop(self):
        while True:
            try:
                if self.col_snapshots is not None:
                    for ticker in self.tickers:
                        view = self.get_market_view(ticker)
                        doc = {
                            "ticker": ticker,
                            "updated_at": datetime.now(),
                            "book": view["book"],
                            "metrics": view["metrics"],
                            "hourly_stats": {str(k): v for k, v in view["hourly_stats"].items()},
                            "recent_trades": list(view["trades"])[:30],
                            "top_trades": view["top_trades"],
                        }
                        self.col_snapshots.update_one(
                            {"ticker": ticker},
                            {"$set": doc},
                            upsert=True
                        )
            except Exception:
                with open("errores_bot.txt", "a") as f:
                    f.write(traceback.format_exc())
            time.sleep(1)

    def get_market_view(self, ticker):
        st = self.market_state[ticker]
        b, o, fs, vs = st["book"]["bids"], st["book"]["offers"], st["daily_financials"], st["vpin_stats"]

        m_px, sp, imb = 0.0, 0.0, 0.0
        if b and o:
            b_px, b_sz, o_px, o_sz = b[0]['price'], b[0]['size'], o[0]['price'], o[0]['size']
            m_px = (b_px * o_sz + o_px * b_sz) / (b_sz + o_sz) if (b_sz + o_sz) > 0 else 0
            sp = o_px - b_px
            tot_b, tot_o = sum(x['size'] for x in b), sum(x['size'] for x in o)
            imb = (tot_b - tot_o) / (tot_b + tot_o) if (tot_b + tot_o) > 0 else 0

        # --- NUEVOS CÁLCULOS PARA VISIBILIDAD DE BALDE ---
        bucket_limit = VOLUME_BUCKET_SIZES.get(ticker, 1000000)
        tot_bucket = vs["current_buy_vol"] + vs["current_sell_vol"]

        # Progreso: porcentaje de llenado del balde actual
        progreso = tot_bucket / bucket_limit if bucket_limit > 0 else 0

        # VPIN Vivo: el desbalance del balde que se está llenando AHORA
        v_vivo = abs(vs["current_buy_vol"] - vs["current_sell_vol"]) / tot_bucket if tot_bucket > 0 else 0

        return {
            "book": st["book"],
            "trades": list(st["trades"]),
            "top_trades": st["top_trades"],
            "hourly_stats": st["hourly_stats"],
            "metrics": {
                "micro_price": m_px, "spread": sp, "imbalance": imb,
                "total_nominals": fs["total_nominals"], "total_money": fs["total_money"],
                "buy_money": fs["buy_money"], "sell_money": fs["sell_money"],
                "vwap": (fs["total_money"] / fs["total_nominals"] * 100) if fs["total_nominals"] > 0 else 0,
                "vpin_prom": vs["last_vpin"],
                "vpin_vivo": v_vivo,
                "progreso": progreso,  # <--- PARA LA BARRA DE CARGA
                "buy_b": vs["current_buy_vol"],  # <--- COMPRA EN EL BALDE
                "sell_b": vs["current_sell_vol"]  # <--- VENTA EN EL BALDE
            }
        }


class MicroApp(App):
    # --- CSS CORREGIDO: ACÁ DEFINIMOS ANCHOS Y ALTURAS ---
    CSS = """
    #main_layout { height: 100%; }
    #left_col { width: 33%; height: 100%; }
    #right_col { width: 67%; height: 100%; }
    #split_row { height: 65%; }
    #time_sales { width: 55%; }
    #top_trades_panel { width: 45%; }
    Static { margin: 0; padding: 0; border: none; }
    """

    def __init__(self, engine):
        super().__init__()
        self.engine, self.active_ticker = engine, TICKERS[0]

    def compose(self) -> ComposeResult:
        yield Select([(t, t) for t in TICKERS], value=self.active_ticker, id="ticker_select")
        with Horizontal(id="main_layout"):
            with Vertical(id="left_col"):
                yield Static(id="order_book")
                yield Static(id="metrics")
                yield Static(id="hourly_volume_panel")
            with Vertical(id="right_col"):
                with Horizontal(id="split_row"):  # Usamos ID en vez de style
                    yield Static(id="time_sales")
                    yield Static(id="top_trades_panel")

    def on_mount(self) -> None:
        self.set_interval(0.2, self.refresh_ui)

    def on_select_changed(self, event: Select.Changed) -> None:
        self.active_ticker = str(event.value)

    def refresh_ui(self) -> None:
        v = self.engine.get_market_view(self.active_ticker)
        b, o, m = v["book"]["bids"], v["book"]["offers"], v["metrics"]

        # 1. DEPTH (Libro)
        t_b = Table(title=f"DEPTH: {self.active_ticker}", box=box.SIMPLE, expand=True)
        t_b.add_column("Bid Q", justify="right", style="cyan"); t_b.add_column("Bid P", justify="right", style="bold green")
        t_b.add_column("Ask P", justify="right", style="bold red"); t_b.add_column("Ask Q", justify="right", style="cyan")
        for i in range(5):
            t_b.add_row(f"{b[i]['size']:,.0f}" if i < len(b) else "-", f"{b[i]['price']:,.2f}" if i < len(b) else "-",
                        f"{o[i]['price']:,.2f}" if i < len(o) else "-", f"{o[i]['size']:,.0f}" if i < len(o) else "-")

            # 2. QUANT ANALYTICS (Actualizado con detalles de balde)
            t_q = Table(title="QUANT ANALYTICS", box=box.SIMPLE, expand=True)
            t_q.add_column("Métrica", style="bold yellow");
            t_q.add_column("Valor", justify="right")
            t_q.add_row("Micro-Price", f"{m['micro_price']:,.4f}")
            t_q.add_row("Spread", f"{m['spread']:,.2f}")
            t_q.add_row("Order Imbalance", f"{m['imbalance']:.2%}")

            t_q.add_section()
            # VPIN Stats
            t_q.add_row("VPIN Promedio", f"[{'bold red' if m['vpin_prom'] > 0.7 else 'white'}]{m['vpin_prom']:.2%}[/]")
            t_q.add_row("VPIN Vivo", f"{m['vpin_vivo']:.2%}")

            # --- NUEVO: PROGRESO Y VOLUMEN DEL BALDE ---
            # Creamos una barrita visual de 10 segmentos
            prog_bar = "█" * int(m['progreso'] * 10) + "░" * (10 - int(m['progreso'] * 10))
            t_q.add_row("Bucket Progress", f"{prog_bar} {m['progreso']:.1%}")
            t_q.add_row("Bucket B / S Vol", f"[green]{m['buy_b']:,.0f}[/] / [red]{m['sell_b']:,.0f}[/]")
            # -------------------------------------------

            t_q.add_section()
            t_q.add_row("Nominales Totales", f"{m['total_nominals']:,.0f}")
            t_q.add_row("VWAP (Daily)", f"${m['vwap']:,.2f}")
            t_q.add_row("Total Money", f"[bold white]${m['tot_money']:,.0f}[/]")
            # Usamos 'money' local para el Buy/Sell para que sea consistente
            t_q.add_row("Buy / Sell ($) Session", f"[green]${m['buy_money']:,.0f}[/] / [red]${m['sell_money']:,.0f}[/]")

        # 3. HOURLY (Perfil Horario)
        t_h = Table(title="HOURLY VOL", box=box.SIMPLE, expand=True)
        for h in range(10, 18):
            d = v["hourly_stats"].get(h, {"buy": 0, "sell": 0, "total": 0})
            bar = f"[green]{'█' * int((d['buy'] / d['total']) * 10)}[/][red]{'█' * int((d['sell'] / d['total']) * 10)}[/]" if d["total"] > 0 else ""
            t_h.add_row(f"{h}hs", f"${d['total']:,.0f}", bar)

        # 4. TAPE (Cinta de tiempo real)
        t_t = Table(title="TAPE", box=box.SIMPLE, expand=True)
        t_t.add_column("Hora", style="dim"); t_t.add_column("Px"); t_t.add_column("Sz"); t_t.add_column("Side")
        for tr in v["trades"][:30]:
            c = "bold green" if tr["side"] == "BUY" else "bold red" if tr["side"] == "SELL" else "white"
            t_t.add_row(tr["timestamp"].strftime("%H:%M:%S"), f"{tr['price']:,.2f}", f"{tr['size']:,.0f}", f"[{c}]{tr['side']}[/]")

        # 5. WHALES (Ballenas por DINERO)
        t_w = Table(title="TOP 15 WHALES (CASH)", box=box.SIMPLE, expand=True)
        t_w.add_column("Px", justify="right"); t_w.add_column("Monto ($)", justify="right", style="bold yellow"); t_w.add_column("Side", justify="center")
        for tr in v["top_trades"]:
            c = "bold green" if tr["side"] == "BUY" else "bold red" if tr["side"] == "SELL" else "white"
            monto = tr.get('money', (tr['price'] / 100) * tr['size']) # Fallback por si falta la key
            t_w.add_row(f"{tr['price']:,.2f}", f"${monto:,.0f}", f"[{c}]{tr['side']}[/]")

        # Actualización final (Aseguramos que todos los widgets se actualicen con variables definidas)
        self.query_one("#order_book", Static).update(Panel(t_b, border_style="blue"))
        self.query_one("#metrics", Static).update(Panel(t_q, border_style="yellow"))
        self.query_one("#hourly_volume_panel", Static).update(Panel(t_h, border_style="cyan"))
        self.query_one("#time_sales", Static).update(Panel(t_t, border_style="white"))
        self.query_one("#top_trades_panel", Static).update(Panel(t_w, border_style="magenta"))


def run():
    os.system('cls' if os.name == 'nt' else 'clear')

    if not inicializar_sesion(): return

    engine = MicrostructureEngine(TICKERS)
    ws_manager = WebSocketManager(engine)

    # --- SEGURO DE VIDA ---
    try:
        # Iniciamos el WebSocket
        if ws_manager.iniciar_ws(TICKERS, depth=5):
            # Corremos la interfaz
            app = MicroApp(engine)
            app.run()

    except Exception:
        # Si algo falla, lo anotamos para saber qué fue
        print("\n❌ EL PROGRAMA CRASHEÓ:")
        traceback.print_exc()

    finally:
        # ESTO ES LO QUE MATA AL ZOMBIE:
        # Pase lo que pase (error o cierre manual), cerramos la conexión a Rofex
        print("\n🛑 Cerrando WebSocket y limpiando hilos...")
        try:
            pyRofex.close_websocket_connection()
        except:
            pass

        # Forzamos al sistema operativo a liquidar este proceso de Python
        # Esto evita que quede el 'fantasma' escribiendo en Mongo
        os._exit(0)


if __name__ == "__main__": run()