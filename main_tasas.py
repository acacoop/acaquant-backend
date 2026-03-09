import os
import time
import logging
import pyRofex
from pymongo import MongoClient
from rich.table import Table
from rich import box
from textual.app import App, ComposeResult
from textual.widgets import Static
from textual.containers import ScrollableContainer

from session_manager import inicializar_sesion
from arbitraje_fx.buscador_caucion import obtener_caucion_mas_corta

# Configuración de logs para auditoría silenciosa
logging.basicConfig(level=logging.INFO, filename='arbitraje.log')
logger = logging.getLogger("MonitorArbitraje")

# Comisión total (Derechos de mercado 0.07% + IVA = 0.0847%)
FEE = 0.000847


# ==========================================
# 1. CARGA Y VALIDACIÓN DE MONGO
# ==========================================
def cargar_y_validar_catalogo():
    try:
        client = MongoClient("mongodb://localhost:27017/")
        db = client["Trading"]
        coleccion = db["TasasAssets"]

        pares_mongo = list(coleccion.find({"activo": True}))
        client.close()

        if not pares_mongo:
            print("⚠️ No se encontraron documentos activos en 'TasasAssets'.")
            return []

        # Handshake con Rofex para filtrar lo que no cotiza hoy
        resp = pyRofex.get_all_instruments()
        if not resp or resp.get('status') != 'OK':
            print("❌ Error al obtener instrumentos oficiales de Rofex.")
            return []

        oficiales = {inst['instrumentId']['symbol'] for inst in resp['instruments']}

        validados = []
        for p in pares_mongo:
            t_ci = p['patas'].get('ci')
            t_24 = p['patas'].get('24hs')

            if t_ci in oficiales and t_24 in oficiales:
                validados.append(p)
            else:
                logger.warning(f"Descartado {p.get('asset')}: Una de las patas no existe hoy.")

        print(f"✅ Catálogo validado: {len(validados)} pares listos para monitorear.")
        return validados
    except Exception as e:
        print(f"❌ Error crítico en carga de catálogo: {e}")
        return []


# ==========================================
# 2. GESTOR DE CÁLCULOS (ArbitrageManager)
# ==========================================
class ArbitrageManager:
    def __init__(self, caucion_ticker, caucion_dias, catalogo_validado):
        self.precios = {}
        self.caucion_ticker = caucion_ticker
        self.caucion_dias = max(caucion_dias, 1)
        self.tna_caucion_offer = 0.0
        self.catalogo = catalogo_validado
        self.last_update = time.time()

    def update_price(self, ticker, data):
        of = data.get('OF', [])
        bi = data.get('BI', [])
        self.precios[ticker] = {
            'offer': of[0]['price'] if of else 0.0,
            'offer_size': of[0]['size'] if of else 0,
            'bid': bi[0]['price'] if bi else 0.0,
            'bid_size': bi[0]['size'] if bi else 0
        }
        if ticker == self.caucion_ticker:
            self.tna_caucion_offer = self.precios[ticker]['offer']
        self.last_update = time.time()

    def check_health(self):
        # Si no recibimos data por 30 segundos, algo se desconectó
        return (time.time() - self.last_update) < 30

    def calcular_resultados(self):
        resultados = []
        for doc in self.catalogo:
            lote = doc.get('lote', 1)  # Divisor para bonos (100) o acciones (1)
            p_ci = self.precios.get(doc['patas']['ci'], {'offer': 0, 'offer_size': 0})
            p_24 = self.precios.get(doc['patas']['24hs'], {'bid': 0, 'bid_size': 0})

            if p_ci['offer'] > 0 and p_24['bid'] > 0 and self.tna_caucion_offer > 0:
                # 1. Calculamos el SIZE MÁXIMO del cruce (lo que hay en ambas puntas)
                size_maximo = min(p_ci['offer_size'], p_24['bid_size'])

                # 2. Convertimos a Montos Reales ($) usando el LOTE de la DB
                # Monto = (Cantidad * Precio) / Lote
                monto_ci = (size_maximo * p_ci['offer'] / lote) * (1 + FEE)
                monto_24 = (size_maximo * p_24['bid'] / lote) * (1 - FEE)

                # 3. Matemática del Arbitraje
                # Rendimiento Directo (lo que le ganás al capital en este plazo)
                rend_directo = (monto_24 / monto_ci) - 1

                # Costo de financiar la compra en CI con Caución
                costo_fondeo = monto_ci * (self.tna_caucion_offer / 100) * (self.caucion_dias / 365)

                # PNL Neto Final en Pesos
                pnl_neto = (monto_24 - monto_ci) - costo_fondeo

                resultados.append({
                    'asset': doc['asset'],
                    'offer_ci': p_ci['offer'],
                    'bid_24': p_24['bid'],
                    'size': size_maximo,
                    'rend_directo': rend_directo * 100,
                    'pnl': pnl_neto
                })

        # Ordenamos por PNL Neto (donde está la plata)
        resultados.sort(key=lambda x: x['pnl'], reverse=True)
        return resultados


# ==========================================
# 3. INTERFAZ DE TERMINAL (Textual App)
# ==========================================
class ArbitrageApp(App):
    BINDINGS = [("q", "quit", "Salir")]

    def __init__(self, manager):
        super().__init__()
        self.manager = manager

    def compose(self) -> ComposeResult:
        with ScrollableContainer():
            yield Static(id="tabla_dinamica")

    def on_mount(self) -> None:
        self.set_interval(1.0, self.refresh_ui)

    def refresh_ui(self) -> None:
        if not self.manager.check_health():
            return

        resultados = self.manager.calcular_resultados()

        table = Table(
            title=f"⚖️ [bold yellow]MONITOR ARBITRAJE DE PLAZOS[/bold yellow] | [cyan]FONDEO: {self.manager.tna_caucion_offer:.2f}% ({self.manager.caucion_dias}D)[/cyan]",
            box=box.ROUNDED, header_style="bold magenta", width=160
        )

        # Columnas según tu requerimiento exacto
        table.add_column("ASSET", justify="left", style="white")
        table.add_column("OFFER CI", justify="right", style="bold red")
        table.add_column("BID 24HS", justify="right", style="bold green")
        table.add_column("OFFER CAU", justify="center", style="red")
        table.add_column("SIZE CRUCE", justify="right", style="cyan")
        table.add_column("REND. DIR.", justify="right", style="bold yellow")
        table.add_column("PNL NETO ($)", justify="right", style="bold green")

        for r in resultados:
            color_pnl = "[bold green]" if r['pnl'] > 0 else "[white]"
            table.add_row(
                r['asset'],
                f"${r['offer_ci']:,.2f}",
                f"${r['bid_24']:,.2f}",
                f"{self.manager.tna_caucion_offer:.2f}%",
                f"{r['size']:,}",
                f"{r['rend_directo']:.4f}%",
                f"{color_pnl}${r['pnl']:,.2f}"
            )

        if not resultados:
            table.add_row("Aguardando liquidez en ambas puntas...", "", "", "", "", "", "")

        self.query_one("#tabla_dinamica", Static).update(table)


# ==========================================
# 4. LOOP PRINCIPAL
# ==========================================
def main():
    os.system('cls' if os.name == 'nt' else 'clear')
    if not inicializar_sesion(): return

    catalogo = cargar_y_validar_catalogo()
    if not catalogo: return

    ticker_cau, dias_cau = obtener_caucion_mas_corta()
    if not ticker_cau: return

    manager = ArbitrageManager(ticker_cau, dias_cau, catalogo)

    def handler(m):
        if m.get("type") == "Md":
            manager.update_price(m['instrumentId']['symbol'], m['marketData'])

    pyRofex.init_websocket_connection(market_data_handler=handler)

    # Preparamos la suscripción de todas las patas
    tickers_sub = [ticker_cau]
    for p in catalogo:
        tickers_sub.extend([p['patas']['ci'], p['patas']['24hs']])

    # Suscribimos por lotes para evitar errores de buffer en la API
    for i in range(0, len(tickers_sub), 50):
        pyRofex.market_data_subscription(
            tickers=tickers_sub[i:i + 50],
            entries=[pyRofex.MarketDataEntry.BIDS, pyRofex.MarketDataEntry.OFFERS]
        )
        time.sleep(0.2)

    # Lanzamos la App visual
    app = ArbitrageApp(manager)
    app.run()


if __name__ == "__main__":
    main()