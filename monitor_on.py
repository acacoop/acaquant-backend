import os
import time
import logging
import pyRofex
from pymongo import MongoClient
from session_manager import inicializar_sesion

# ==========================================
# IMPORTAMOS RICH Y TEXTUAL
# ==========================================
from rich.table import Table
from rich import box
from textual.app import App, ComposeResult
from textual.widgets import Static
from textual.containers import ScrollableContainer

# ==========================================
# IMPORTAMOS TUS MOTORES DE BONOS Y TIR
# ==========================================
from live_pricing_bonds.db_bonds import cargar_catalogo_bonos
from live_pricing_bonds.yield_engine import calcular_tir_live

# Logger al archivo
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename='monitor.log',
    filemode='a'
)
logger = logging.getLogger("MonitorTerminal")

# ==========================================
# 0. VARIABLES DE ENTORNO
# ==========================================
DOLAR_A3500 = 1420
TICKERS_MEP = ["MERV - XMEV - AL30 - 24hs", "MERV - XMEV - AL30D - 24hs"]


# ==========================================
# 1. MARKET MANAGER
# ==========================================
class MarketManager:
    def __init__(self, catalogo_valido):
        self.last_update_time = time.time()
        self.catalogo = catalogo_valido
        self.lista_tickers = list(self.catalogo.keys())

        self.precios_vivos = {
            t: {'bid': 0.0, 'bid_size': 0, 'offer': 0.0, 'offer_size': 0, 'last': 0.0, 'last_size': 0}
            for t in self.lista_tickers
        }

    def update_price(self, ticker, data):
        if not ticker or ticker not in self.precios_vivos:
            return
        try:
            self.last_update_time = time.time()
            p = self.precios_vivos[ticker]
            bi = data.get('BI', [])
            if isinstance(bi, list) and len(bi) > 0:
                p['bid'] = float(bi[0].get('price', p['bid']))
                p['bid_size'] = int(bi[0].get('size', p['bid_size']))
            of = data.get('OF', [])
            if isinstance(of, list) and len(of) > 0:
                p['offer'] = float(of[0].get('price', p['offer']))
                p['offer_size'] = int(of[0].get('size', p['offer_size']))
        except Exception:
            pass

    def get_mep_dinamico(self):
        try:
            offer_ars = self.precios_vivos.get("MERV - XMEV - AL30 - 24hs", {}).get('offer', 0.0)
            bid_usd = self.precios_vivos.get("MERV - XMEV - AL30D - 24hs", {}).get('bid', 0.0)
            if offer_ars > 0 and bid_usd > 0:
                return offer_ars / bid_usd
        except Exception:
            pass
        return 0.0

    def check_health(self):
        return (time.time() - self.last_update_time) <= 30

    def get_data_for_terminal(self):
        matriz = []
        mep_vivo = self.get_mep_dinamico()

        for ticker in self.lista_tickers:
            if ticker in TICKERS_MEP:
                continue
            p = self.precios_vivos.get(ticker)
            info_bono = self.catalogo.get(ticker)
            if p['bid'] == 0 and p['offer'] == 0:
                continue

            tir_bid, tir_offer = None, None
            emisor, vencimiento_str, moneda_flujo = "S/D", "---", "---"

            if info_bono and info_bono.get('asset') != 'MEP_DUMMY':
                moneda_cotiz = info_bono.get('moneda_cotizacion', 'ARS')
                emisor = info_bono.get('emisor', 'S/D')
                moneda_flujo = info_bono.get('moneda_flujo', 'USD')  # Extraemos Moneda Flujo

                venc = info_bono.get('vencimiento', '')
                if hasattr(venc, 'strftime'):
                    vencimiento_str = venc.strftime('%m/%Y')
                elif isinstance(venc, str) and len(venc) >= 7:
                    vencimiento_str = f"{venc[5:7]}/{venc[:4]}"

                if p['bid'] > 0:
                    try:
                        tir_bid = calcular_tir_live(p['bid'], moneda_cotiz, info_bono, DOLAR_A3500, mep_vivo)
                    except:
                        pass
                if p['offer'] > 0:
                    try:
                        tir_offer = calcular_tir_live(p['offer'], moneda_cotiz, info_bono, DOLAR_A3500, mep_vivo)
                    except:
                        pass

            vol_bid_plata = (p['bid_size'] / 100) * p['bid']
            vol_offer_plata = (p['offer_size'] / 100) * p['offer']

            matriz.append([
                ticker, emisor, vencimiento_str, moneda_flujo,
                vol_bid_plata, p['bid'], tir_bid, tir_offer, p['offer'], vol_offer_plata
            ])

        matriz.sort(key=lambda x: x[7] if x[7] is not None else -999, reverse=True)
        return matriz


# ==========================================
# 2. WEBSOCKET MANAGER
# ==========================================
class WebSocketManager:
    def __init__(self, market_manager):
        self.mm = market_manager

    def _handler_mercado(self, message):
        try:
            self.mm.update_price(message['instrumentId']['symbol'], message['marketData'])
        except Exception:
            pass

    def iniciar_ws(self, lista_tickers):
        try:
            pyRofex.add_websocket_market_data_handler(self._handler_mercado)
            for i in range(0, len(lista_tickers), 50):
                pyRofex.market_data_subscription(
                    tickers=lista_tickers[i:i + 50],
                    entries=[pyRofex.MarketDataEntry.BIDS, pyRofex.MarketDataEntry.OFFERS]
                )
                time.sleep(0.5)
            pyRofex.init_websocket_connection()
            return True
        except Exception:
            return False

    def cerrar_ws(self):
        pyRofex.close_websocket_connection()


# ==========================================
# 3. TABLA RICH
# ==========================================
def generar_tabla_rich(matriz_datos, mep_vivo):
    str_mep = f"${mep_vivo:,.2f}" if mep_vivo > 0 else "Calculando..."
    table = Table(
        title=f"📊 [bold cyan]YIELD SCREENER QUANT[/bold cyan] | 🔴 MEP EN VIVO: [bold red]{str_mep}[/bold red] | TC OFICIAL: [bold green]${DOLAR_A3500:,.2f}[/bold green]",
        box=box.ROUNDED,
        header_style="bold white",
        title_justify="center",
        width=165
    )

    table.add_column("TICKER", justify="left", style="white", no_wrap=True)
    table.add_column("EMISOR", justify="left", style="dim")
    table.add_column("VENCE", justify="center")
    table.add_column("MON", justify="center", style="cyan")  # Nueva columna Moneda Flujo
    table.add_column("VOL BID ($)", justify="right", style="green")
    table.add_column("BID PX", justify="right", style="bold green")
    table.add_column("TIR BID", justify="right", style="bold yellow")
    table.add_column("TIR OFF", justify="right", style="bold yellow")
    table.add_column("OFF PX", justify="right", style="bold red")
    table.add_column("VOL OFF ($)", justify="right", style="red")

    for fila in matriz_datos:
        str_vol_bid = f"${fila[4]:,.0f}" if fila[4] > 0 else "---"
        str_bid_px = f"${fila[5]:,.2f}" if fila[5] > 0 else "S/D"
        str_tir_bid = f"{fila[6] * 100:.2f}%" if fila[6] is not None else "---"
        str_tir_off = f"{fila[7] * 100:.2f}%" if fila[7] is not None else "---"
        str_off_px = f"${fila[8]:,.2f}" if fila[8] > 0 else "S/D"
        str_vol_off = f"${fila[9]:,.0f}" if fila[9] > 0 else "---"

        table.add_row(fila[0][:30], str(fila[1])[:13], str(fila[2]), str(fila[3]), str_vol_bid, str_bid_px, str_tir_bid,
                      str_tir_off, str_off_px, str_vol_off)

    if not matriz_datos:
        table.add_row("Aguardando...", "", "", "", "", "", "", "", "", "")

    table.caption = f"Última actualización: {time.strftime('%H:%M:%S')} | [bold]Q[/bold] para salir"
    return table


# ==========================================
# 4. APLICACIÓN TEXTUAL
# ==========================================
class QuantTerminal(App):
    BINDINGS = [("q", "quit", "Cerrar Bot")]

    def __init__(self, market_manager):
        super().__init__()
        self.market = market_manager

    def compose(self) -> ComposeResult:
        with ScrollableContainer():
            yield Static(id="panel_tabla")

    def on_mount(self) -> None:
        self.set_interval(1.0, self.actualizar_pantalla)  # Bajado a 1 seg para más fluidez

    def actualizar_pantalla(self) -> None:
        if not self.market.check_health():
            self.exit()
            return
        matriz = self.market.get_data_for_terminal()
        mep = self.market.get_mep_dinamico()
        self.query_one("#panel_tabla", Static).update(generar_tabla_rich(matriz, mep))


# ==========================================
# 5. FILTRO Y MAIN
# ==========================================
def extraer_catalogo_validado():
    try:
        catalogo_completo = cargar_catalogo_bonos()
        if not catalogo_completo: return {}
        for t in TICKERS_MEP:
            if t not in catalogo_completo: catalogo_completo[t] = {'asset': 'MEP_DUMMY'}
        respuesta_rofex = pyRofex.get_all_instruments()
        if not respuesta_rofex or respuesta_rofex.get('status') != 'OK': return {}
        activos_vivos = [inst['instrumentId']['symbol'] for inst in respuesta_rofex['instruments']]
        return {k: v for k, v in catalogo_completo.items() if k in activos_vivos}
    except Exception:
        return {}


def main():
    os.system('cls' if os.name == 'nt' else 'clear')
    while True:
        try:
            if not inicializar_sesion():
                time.sleep(5);
                continue
            catalogo_dinamico = extraer_catalogo_validado()
            if not catalogo_dinamico: break
            market = MarketManager(catalogo_dinamico)
            ws = WebSocketManager(market)
            if ws.iniciar_ws(list(catalogo_dinamico.keys())):
                QuantTerminal(market).run()
                ws.cerrar_ws();
                break
        except Exception as e:
            logger.critical(f"Error: {e}");
            time.sleep(8)


if __name__ == "__main__":
    main()