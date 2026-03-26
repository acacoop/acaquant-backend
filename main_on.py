import os
import time
import logging
import pyRofex
import threading

# --- RICH Y TEXTUAL ---
from rich.table import Table
from rich.panel import Panel
from rich import box
from textual.app import App, ComposeResult
from textual.widgets import Static, Header
from textual.containers import ScrollableContainer

# --- TUS MANAGERS GLOBALES ---
from session_manager import inicializar_sesion
from websocket_manager import WebSocketManager
from snapshot_writer import SnapshotWriter
from mongo_manager import get_mongo_client

# --- MOTORES DE CÁLCULO ---
from live_pricing_bonds.db_bonds import cargar_catalogo_bonos
from live_pricing_bonds.yield_engine import calcular_tir_live

# Configuración de logs
logging.basicConfig(level=logging.INFO, filename='monitor.log', filemode='a',
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("MonitorONs")

TICKERS_MEP = ["MERV - XMEV - AL30 - 24hs", "MERV - XMEV - AL30D - 24hs"]
TC_OFICIAL_FALLBACK = 1000.0   # Solo si Mongo no tiene el valor configurado
TC_REFRESH_INTERVAL = 30       # Segundos entre lecturas de TC desde Mongo


# ==========================================
# 1. EL CEREBRO: MarketManager (ONs Engine)
# ==========================================
class MarketManager:
    """
    Motor Cuantitativo para Obligaciones Negociables.
    Se inyecta en el WebSocketManager.
    """

    def __init__(self, catalogo_valido):
        self._lock = threading.Lock()
        self.last_update_time = time.time()
        self.catalogo = catalogo_valido
        self.lista_tickers = list(self.catalogo.keys())

        # TC Oficial (leído desde Mongo, actualizado periódicamente)
        self._tc_oficial = TC_OFICIAL_FALLBACK
        self._tc_last_read = 0.0

        # Inicialización de estado en RAM
        self.precios_vivos = {
            t: {'bid': 0.0, 'bid_size': 0, 'offer': 0.0, 'offer_size': 0, 'last': 0.0}
            for t in self.lista_tickers
        }

    def _refrescar_tc_oficial(self):
        """Lee TC oficial desde Valuaciones.Dolar en Atlas. Cachea por TC_REFRESH_INTERVAL segundos."""
        ahora = time.time()
        if ahora - self._tc_last_read < TC_REFRESH_INTERVAL:
            return
        try:
            col = get_mongo_client()["Valuaciones"]["Dolar"]
            doc = col.find_one({"type": "config_on"})
            if doc and doc.get("tc_oficial"):
                self._tc_oficial = float(doc["tc_oficial"])
            self._tc_last_read = ahora
        except Exception as e:
            logger.warning(f"No se pudo leer TC oficial desde Mongo: {e}")

    def get_tickers_suscripcion(self):
        """Retorna la lista de tickers para el WS Manager"""
        return self.lista_tickers

    def update_price(self, ticker, data):
        """
        EL MÉTODO DE INYECCIÓN.
        El WebSocketManager llama a esto automáticamente con cada tick.
        """
        if not ticker or ticker not in self.precios_vivos: return

        with self._lock:
            self.last_update_time = time.time()
            p = self.precios_vivos[ticker]

            bi = data.get('BI', [])
            if bi:
                p['bid'] = float(bi[0].get('price', p['bid']))
                p['bid_size'] = int(bi[0].get('size', p['bid_size']))

            of = data.get('OF', [])
            if of:
                p['offer'] = float(of[0].get('price', p['offer']))
                p['offer_size'] = int(of[0].get('size', p['offer_size']))

    def get_mep_dinamico(self):
        """Calcula el MEP en tiempo real usando AL30/AL30D"""
        with self._lock:
            try:
                offer_ars = self.precios_vivos.get("MERV - XMEV - AL30 - 24hs", {}).get('offer', 0.0)
                bid_usd = self.precios_vivos.get("MERV - XMEV - AL30D - 24hs", {}).get('bid', 0.0)
                if offer_ars > 0 and bid_usd > 0:
                    return offer_ars / bid_usd
            except:
                pass
            return 0.0

    def calcular_pantalla(self):
        """Motor de cálculo de Yields (TIRs)"""
        self._refrescar_tc_oficial()
        matriz = []
        mep_vivo = self.get_mep_dinamico()
        tc_oficial = self._tc_oficial

        with self._lock:
            for ticker in self.lista_tickers:
                if ticker in TICKERS_MEP: continue
                p = self.precios_vivos.get(ticker).copy()
                info = self.catalogo.get(ticker)

                if p['bid'] == 0 and p['offer'] == 0: continue

                tir_b, tir_o = None, None
                if info:
                    mon_cot = info.get('moneda_cotizacion', 'ARS')
                    v = info.get('vencimiento', '')
                    v_str = v.strftime('%m/%Y') if hasattr(v, 'strftime') else "---"

                    # Cálculo Seguro de TIR BID
                    if p['bid'] > 0:
                        try:
                            tir_val = calcular_tir_live(p['bid'], mon_cot, info, tc_oficial, mep_vivo)
                            if tir_val is not None and -0.5 < tir_val < 5.0:
                                tir_b = tir_val
                        except Exception as e:
                            logger.debug(f"TIR BID Error en {ticker}: {e}")

                    # Cálculo Seguro de TIR OFFER
                    if p['offer'] > 0:
                        try:
                            tir_val = calcular_tir_live(p['offer'], mon_cot, info, tc_oficial, mep_vivo)
                            if tir_val is not None and -0.5 < tir_val < 5.0:
                                tir_o = tir_val
                        except Exception as e:
                            logger.debug(f"TIR OFF Error en {ticker}: {e}")

                    vol_bid_moneda = (p['bid_size'] / 100) * p['bid']
                    vol_off_moneda = (p['offer_size'] / 100) * p['offer']

                    matriz.append({
                        'ticker': ticker,
                        'asset':  info.get('asset', ticker),
                        'emisor': info.get('emisor', 'S/D'),
                        'vence': v_str,
                        'moneda': info.get('moneda_flujo', 'USD'),
                        'vol_bid': vol_bid_moneda,
                        'px_bid': p['bid'],
                        'tir_bid': tir_b,
                        'tir_off': tir_o,
                        'px_off': p['offer'],
                        'vol_off': vol_off_moneda
                    })

        # Ordenamos por la TIR del Offer de mayor a menor (las más baratas de comprar primero)
        matriz.sort(key=lambda x: x['tir_off'] if x['tir_off'] is not None else -999, reverse=True)
        return matriz, mep_vivo

    def get_snapshot(self):
        """Entrypoint para el SnapshotWriter. Convierte TIRs a % y añade MEP."""
        matriz, mep_vivo = self.calcular_pantalla()
        for row in matriz:
            row['mep_vivo'] = round(mep_vivo, 2)
            if row['tir_bid'] is not None:
                row['tir_bid'] = round(row['tir_bid'] * 100, 4)
            if row['tir_off'] is not None:
                row['tir_off'] = round(row['tir_off'] * 100, 4)
        return matriz

    def check_health(self):
        return (time.time() - self.last_update_time) <= 60


# ==========================================
# 2. LA INTERFAZ: ONTerminal (Textual)
# ==========================================
class ONTerminal(App):
    BINDINGS = [("q", "quit", "Salir")]

    def __init__(self, market_manager):
        super().__init__()
        self.engine = market_manager

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with ScrollableContainer():
            yield Static(id="panel_tabla")

    def on_mount(self) -> None:
        self.set_interval(1.0, self.actualizar_pantalla)

    def actualizar_pantalla(self) -> None:
        # 1. Chequeo de Salud
        if not self.engine.check_health():
            # Si se desconecta, podríamos cerrar la app para forzar el reinicio en el main
            self.exit()
            return

        # 2. Procesamiento de Datos
        datos, mep_vivo = self.engine.calcular_pantalla()

        # 3. Renderizado Visual
        str_mep = f"${mep_vivo:,.2f}" if mep_vivo > 0 else "Calculando..."
        table = Table(
            title=f"📊 [bold cyan]YIELD SCREENER QUANT (O.N.)[/bold cyan] | MEP REF: [bold red]{str_mep}[/bold red]",
            box=box.ROUNDED, header_style="bold white", expand=True
        )

        table.add_column("TICKER", justify="left", style="white")
        table.add_column("EMISOR", style="dim")
        table.add_column("VENCE", justify="center")
        table.add_column("MON", justify="center", style="cyan")
        table.add_column("VOL BID ($)", justify="right", style="green")
        table.add_column("BID PX", justify="right", style="bold green")
        table.add_column("TIR BID", justify="right", style="bold yellow")
        table.add_column("TIR OFF", justify="right", style="bold yellow")
        table.add_column("OFF PX", justify="right", style="bold red")
        table.add_column("VOL OFF ($)", justify="right", style="red")

        for d in datos:
            table.add_row(
                d['ticker'], d['emisor'], d['vence'], d['moneda'],
                f"${d['vol_bid']:,.0f}",
                f"${d['px_bid']:,.2f}",
                f"{d['tir_bid'] * 100:.2f}%" if d['tir_bid'] is not None else "---",
                f"{d['tir_off'] * 100:.2f}%" if d['tir_off'] is not None else "---",
                f"${d['px_off']:,.2f}",
                f"${d['vol_off']:,.0f}"
            )

        if not datos:
            table.add_row("Aguardando precios de mercado...", "", "", "", "", "", "", "", "", "")

        self.query_one("#panel_tabla", Static).update(Panel(table, border_style="blue"))


# ==========================================
# 3. EL ORQUESTADOR
# ==========================================
def main(headless=False):
    # El Watchdog / Auto-Reconector sigue vivo en el Main
    while True:
        try:
            os.system('cls' if os.name == 'nt' else 'clear')
            print("\n🔄 Iniciando Motor Quant de O.N. ...")

            # 1. Sesión
            if not inicializar_sesion():
                time.sleep(10)
                continue

            # 2. Carga y Validación de BD
            print("📚 Cargando base de bonos...")
            catalogo = cargar_catalogo_bonos()

            resp = pyRofex.get_all_instruments()
            if not resp or resp.get('status') != 'OK':
                print("❌ Falla contra padrón Rofex.")
                time.sleep(5)
                continue

            vivos = [i['instrumentId']['symbol'] for i in resp['instruments']]
            validado = {k: v for k, v in catalogo.items() if k in vivos}

            # Agregamos MEP ficticio si no está en la BD para que lo lea el engine
            for t in TICKERS_MEP:
                if t not in validado: validado[t] = {'asset': 'MEP_DUMMY'}

            if not validado:
                print("❌ Catálogo vacío.")
                time.sleep(5)
                continue

            # 3. Iniciamos Motor (Cerebro)
            engine = MarketManager(validado)

            # 4. Iniciamos Red (Infraestructura) inyectando el motor
            ws_manager = WebSocketManager(engine)
            if ws_manager.iniciar_ws(engine.get_tickers_suscripcion()):
                print("✅ Conexión establecida. Levantando Screener...")

                # 5. Snapshot para Streamlit
                snapshot_writer = SnapshotWriter(
                    db_name="Trading",
                    collection_name="ONSnapshot",
                    data_fn=engine.get_snapshot,
                    key_field="ticker",
                    interval=0.5
                ).start()

                if headless:
                    # Modo servicio: loop silencioso hasta SIGTERM
                    print("▶ Modo headless — escribiendo snapshots a Mongo.", flush=True)
                    while engine.check_health():
                        time.sleep(5)
                    print("⚠ Health check falló, reconectando...", flush=True)
                else:
                    # Modo interactivo: Textual UI
                    ONTerminal(engine).run()

                snapshot_writer.stop()

            # Si salimos es porque falló el check_health o tocamos la Q
            pyRofex.close_websocket_connection()
            time.sleep(2)

        except KeyboardInterrupt:
            print("\n🛑 Apagado manual.")
            try:
                pyRofex.close_websocket_connection()
            except:
                pass
            break

        except Exception as e:
            print(f"🔥 Error crítico en orquestador: {e}")
            try:
                pyRofex.close_websocket_connection()
            except:
                pass
            time.sleep(5)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true", help="Sin UI Textual (para servicio systemd)")
    args = parser.parse_args()
    main(headless=args.headless)