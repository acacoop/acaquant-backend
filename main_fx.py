import os
import time
import sys
import pyRofex
import threading
from datetime import datetime

# --- TEXTUAL Y RICH (CERO PARPADEO) ---
from rich.table import Table
from rich import box
from textual.app import App, ComposeResult
from textual.widgets import Static
from textual.containers import Vertical

# --- TUS MANAGERS DE INFRAESTRUCTURA ---
from session_manager import inicializar_sesion
from websocket_manager import WebSocketManager

# --- TUS MÓDULOS DE ARBITRAJE ---
from arbitraje_fx.calculadora import calcular_compra_usd, calcular_venta_usd
from arbitraje_fx.mongo_assets import AssetManager
from arbitraje_fx.trade_logger import TradeLogger


# ==========================================
# 1. EL CEREBRO: FXArbitrageEngine
# ==========================================
class FXArbitrageEngine:
    def __init__(self):
        self.precios = {}
        self.pares_validados = []
        self.tickers_totales = []
        self.trade_logger = TradeLogger()
        self.last_update = time.time()

    def setup_inicial(self):
        """Carga los pares desde Mongo y los valida contra Rofex"""
        print("🔍 Conectando con MongoDB y validando catálogo de FX...")
        am = AssetManager()
        resp = pyRofex.get_all_instruments()

        if not resp or resp.get('status') != 'OK':
            print("❌ Falla al obtener padrón oficial de Rofex.")
            return False

        padron = {i['instrumentId']['symbol'] for i in resp['instruments']}
        self.pares_validados, self.tickers_totales = am.obtener_pares_activos(padron)

        if not self.pares_validados:
            print("❌ No hay pares válidos para operar hoy.")
            return False

        print(f"✅ Catálogo listo: {len(self.pares_validados)} pares FX validados.")

        # Forzamos un update inicial para que el Watchdog no dispare en el segundo 1
        self.last_update = time.time()
        return True

    def get_tickers_suscripcion(self):
        return self.tickers_totales

    def update_price(self, ticker, data):
        """
        INYECCIÓN DIRECTA: El WebSocketManager llama a este método.
        Es thread-safe por naturaleza ya que solo actualiza un diccionario en RAM.
        """
        of = data.get('OF', [])
        bi = data.get('BI', [])

        self.precios[ticker] = {
            'offer': of[0]['price'] if of else self.precios.get(ticker, {}).get('offer', 0.0),
            'offer_size': of[0]['size'] if of else self.precios.get(ticker, {}).get('offer_size', 0),
            'bid': bi[0]['price'] if bi else self.precios.get(ticker, {}).get('bid', 0.0),
            'bid_size': bi[0]['size'] if bi else self.precios.get(ticker, {}).get('bid_size', 0)
        }
        self.last_update = time.time()

    def _calcular_arbitrajes_cruzados(self, lista_compra, lista_venta):
        """Matching Engine: Cruza dólares baratos con caros consumiendo la liquidez."""
        trades = []
        buys = [dict(b) for b in lista_compra if b['tc'] > 0]
        sells = [dict(v) for v in lista_venta if v['tc'] > 0]

        i_b, i_v = 0, 0

        while i_b < len(buys) and i_v < len(sells):
            b = buys[i_b]
            v = sells[i_v]

            if v["tc"] <= b["tc"]:
                break

            match_usd = min(b["usd"], v["usd"])

            if match_usd > 1.0:
                trades.append({
                    "buy_asset": b["asset"],
                    "buy_tc": b["tc"],
                    "sell_asset": v["asset"],
                    "sell_tc": v["tc"],
                    "volumen_usd": match_usd,
                    "ganancia_ars": match_usd * (v["tc"] - b["tc"]),
                    "spread_pct": (v["tc"] / b["tc"] - 1) * 100
                })

                b["usd"] -= match_usd
                v["usd"] -= match_usd

            if b["usd"] <= 0.01: i_b += 1
            if v["usd"] <= 0.01: i_v += 1

        return trades

    def procesar_mercado(self):
        """Calcula el estado actual del mercado para la interfaz"""
        lista_compra, lista_venta = [], []
        al30_tc_comp, al30_tc_vend = 0.0, 0.0

        for par in self.pares_validados:
            p_ars = self.precios.get(par["ars"], {'offer': 0, 'offer_size': 0, 'bid': 0, 'bid_size': 0})
            p_usd = self.precios.get(par["usd"], {'offer': 0, 'offer_size': 0, 'bid': 0, 'bid_size': 0})

            # Calcula TC de COMPRA de USD
            tc_c, nom_c, usd_c = calcular_compra_usd(
                p_ars['offer'], p_ars['offer_size'], p_usd['bid'], p_usd['bid_size'], par['lote']
            )

            # Calcula TC de VENTA de USD
            tc_v, nom_v, usd_v = calcular_venta_usd(
                p_ars['bid'], p_ars['bid_size'], p_usd['offer'], p_usd['offer_size'], par['lote']
            )

            if par["asset"] == "AL30":
                al30_tc_comp, al30_tc_vend = tc_c, tc_v

            if tc_c > 0: lista_compra.append({"asset": par["asset"], "tc": tc_c, "usd": usd_c})
            if tc_v > 0: lista_venta.append({"asset": par["asset"], "tc": tc_v, "usd": usd_v})

        # Ordenamos: Queremos comprar barato y vender caro
        lista_compra.sort(key=lambda x: x['tc'])
        lista_venta.sort(key=lambda x: x['tc'], reverse=True)

        trades = self._calcular_arbitrajes_cruzados(lista_compra, lista_venta)

        # Logueamos en Mongo si hay operaciones viables
        if trades:
            threading.Thread(target=self.trade_logger.procesar_trades, args=(trades,), daemon=True).start()

        return {
            "compra": lista_compra,
            "venta": lista_venta,
            "trades": trades,
            "ref_compra": al30_tc_comp,
            "ref_venta": al30_tc_vend
        }


# ==========================================
# 2. LA INTERFAZ: FXApp (COMPACTA Y NARANJA)
# ==========================================
class FXApp(App):
    # Agregamos bordes naranjas y limpiamos márgenes
    CSS = """
    #main_container { 
        border: round darkorange;
        height: 100%;
        padding: 0 1;
    }
    #header { margin-bottom: 1; text-align: center; }
    #top_panel { height: 60%; }
    #bottom_panel { height: auto; }
    """

    BINDINGS = [("q", "quit", "Salir")]

    def __init__(self, engine):
        super().__init__()
        self.engine = engine

    def compose(self) -> ComposeResult:
        with Vertical(id="main_container"):
            yield Static(id="header")
            yield Static(id="top_panel")
            yield Static(id="bottom_panel")

    def on_mount(self) -> None:
        self.set_interval(0.5, self.refresh_ui)

    def refresh_ui(self) -> None:
        # --- EL PERRO GUARDIÁN (WATCHDOG) ---
        if (time.time() - self.engine.last_update) > 60:
            self.exit("RECONNECT")
            return

        # 1. Obtener datos procesados del Engine
        data = self.engine.procesar_mercado()
        lista_compra = data["compra"]
        lista_venta = data["venta"]
        trades = data["trades"]

        # 2. Header Naranja
        str_head = f"🚀 [bold dark_orange]QUANT FX ARBITRAGE[/] | REFERENCIA MEP (AL30): COMPRA ${data['ref_compra']:.2f} / VENTA ${data['ref_venta']:.2f} | HORA: {datetime.now().strftime('%H:%M:%S')}"
        self.query_one("#header", Static).update(str_head)

        # 3. Puntas FX (Diseño Simple y Compacto)
        t_puntas = Table(title="📊 PUNTAS SINTÉTICAS (MEP/CCL)", box=box.SIMPLE, expand=True, title_style="bold orange1")
        t_puntas.add_column("COMPRAR DÓLARES (TC Más Barato)", style="cyan", header_style="bold cyan")
        t_puntas.add_column("VENDER DÓLARES (TC Más Caro)", style="magenta", header_style="bold magenta")

        for i in range(12):  # Mostrar top 12 para aprovechar la pantalla
            c_str, v_str = "---", "---"
            if i < len(lista_compra):
                c = lista_compra[i]
                c_str = f"{c['asset']:<8} ${c['tc']:>7.2f}  |  Liq: U$S {c['usd']:>7,.0f}"
            if i < len(lista_venta):
                v = lista_venta[i]
                v_str = f"{v['asset']:<8} ${v['tc']:>7.2f}  |  Liq: U$S {v['usd']:>7,.0f}"
            t_puntas.add_row(c_str, v_str)

        # 4. Matching Engine (Arbitrajes Activos)
        t_trades = Table(title="⚡ MATCHING ENGINE", box=box.SIMPLE, expand=True, title_style="bold orange1")
        t_trades.add_column("EJECUCIÓN", justify="left", style="white")
        t_trades.add_column("VOLUMEN USD", justify="right", style="bold cyan")
        t_trades.add_column("PNL NETO ($)", justify="right", style="bold green")
        t_trades.add_column("SPREAD", justify="right", style="bold yellow")

        for t in trades[:6]:
            t_trades.add_row(
                f"Comprar en {t['buy_asset']} (${t['buy_tc']:.2f}) ➔ Vender en {t['sell_asset']} (${t['sell_tc']:.2f})",
                f"U$S {t['volumen_usd']:,.0f}",
                f"${t['ganancia_ars']:,.0f}",
                f"{t['spread_pct']:.2f}%"
            )

        if not trades:
            t_trades.add_row("Aguardando desfasaje en el mercado...", "-", "-", "-")

        self.query_one("#top_panel", Static).update(t_puntas)
        self.query_one("#bottom_panel", Static).update(t_trades)


# ==========================================
# 3. EL ORQUESTADOR (BLINDADO CON AUTO-RECONEXIÓN)
# ==========================================
def run():
    while True:  # Loop infinito que blinda al bot
        os.system('cls' if os.name == 'nt' else 'clear')

        # 1. Sesión
        if not inicializar_sesion():
            time.sleep(5)
            continue

        # 2. Inicializamos el Motor (Negocio)
        engine = FXArbitrageEngine()
        if not engine.setup_inicial():
            time.sleep(5)
            continue

        # 3. Conexión de Red (Infraestructura)
        ws_manager = WebSocketManager(engine)

        if ws_manager.iniciar_ws(engine.get_tickers_suscripcion()):
            # 4. Lanzamos la UI y esperamos a ver cómo se cierra
            motivo_cierre = FXApp(engine).run()

            if motivo_cierre == "RECONNECT":
                print("\n🔴 CONEXIÓN CONGELADA DETECTADA.")
                print("🔄 Ejecutando reinicio de emergencia en 3 segundos...")
                ws_manager.cerrar_ws()
                time.sleep(3)
                continue  # Vuelve al inicio del while True de forma limpia
            else:
                break  # El usuario apretó 'Q' para salir a propósito
        else:
            print("❌ Error al suscribir WebSocket.")
            time.sleep(5)


if __name__ == "__main__":
    run()