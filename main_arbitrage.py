import os
import time
import logging
import threading
import pyRofex
from datetime import datetime
from pymongo import MongoClient

# --- RICH Y TEXTUAL ---
from rich.table import Table
from rich import box
from textual.app import App, ComposeResult
from textual.widgets import Static
from textual.containers import ScrollableContainer

# --- TUS MANAGERS ---
from session_manager import inicializar_sesion
from websocket_manager import WebSocketManager
from mongo_manager import MongoManager
from arbitraje_fx.buscador_caucion import obtener_caucion_mas_corta

# Configuración de logs para auditoría
logging.basicConfig(level=logging.INFO, filename='arbitraje.log')
logger = logging.getLogger("MonitorArbitraje")

FEE = 0.000847  # Derechos de mercado 0.07% + IVA


# ==========================================
# 1. EL CEREBRO: ArbitrageEngine
# ==========================================
class ArbitrageEngine:
    """
    Motor Cuantitativo para Arbitraje de Plazos.
    Se inyecta en el WebSocketManager.
    """

    def __init__(self):
        self.precios = {}
        self.caucion_ticker = None
        self.caucion_dias = 1
        self.tna_caucion_offer = 0.0
        self.catalogo = []
        self.last_update = time.time()

        # --- NUEVO: Conexión a Mongo y Filtro Anti-Spam ---
        self.mongo = MongoManager(db_name="Trading", collection_name="CI24")
        self.last_saved_signature = None

    def setup_inicial(self):
        """Prepara el entorno ANTES de conectar el WebSocket"""
        print("🔍 Buscando Caución más corta...")
        self.caucion_ticker, self.caucion_dias = obtener_caucion_mas_corta()
        if not self.caucion_ticker:
            print("❌ No se encontró caución activa.")
            return False

        self.caucion_dias = max(self.caucion_dias, 1)

        print("📚 Validando catálogo de pares en Mongo...")
        self.catalogo = self._cargar_y_validar_catalogo()
        if not self.catalogo:
            return False

        return True

    def _cargar_y_validar_catalogo(self):
        """Consulta MongoDB y cruza con el padrón de Rofex del día"""
        try:
            client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=2000)
            db = client["Trading"]
            coleccion = db["TasasAssets"]
            pares_mongo = list(coleccion.find({"activo": True}))
            client.close()

            if not pares_mongo:
                print("⚠️ No hay documentos activos en 'TasasAssets'.")
                return []

            # Handshake con Rofex
            resp = pyRofex.get_all_instruments()
            if not resp or resp.get('status') != 'OK':
                print("❌ Error al obtener instrumentos de Rofex.")
                return []

            oficiales = {inst['instrumentId']['symbol'] for inst in resp['instruments']}
            validados = []

            for p in pares_mongo:
                if p['patas'].get('ci') in oficiales and p['patas'].get('24hs') in oficiales:
                    validados.append(p)
                else:
                    logger.warning(f"Descartado {p.get('asset')}: Faltan patas en Rofex hoy.")

            print(f"✅ Catálogo listo: {len(validados)} pares validados.")
            return validados
        except Exception as e:
            print(f"❌ Error en carga de catálogo: {e}")
            return []

    def get_tickers_suscripcion(self):
        """Genera la lista única de tickers para que el WebSocketManager se suscriba"""
        tickers_sub = [self.caucion_ticker]
        for p in self.catalogo:
            tickers_sub.extend([p['patas']['ci'], p['patas']['24hs']])
        return list(set(tickers_sub))

    def update_price(self, ticker, data):
        """
        ACTUALIZACIÓN CRÍTICA: Si no hay puntas, seteamos 0.0 para no operar con fantasmas.
        """
        of = data.get('OF', [])
        bi = data.get('BI', [])

        # Si 'of' es una lista vacía, el offer es 0.0 (Liquidez agotada)
        self.precios[ticker] = {
            'offer': of[0]['price'] if of else 0.0,
            'offer_size': of[0]['size'] if of else 0,
            'bid': bi[0]['price'] if bi else 0.0,
            'bid_size': bi[0]['size'] if bi else 0
        }

        if ticker == self.caucion_ticker:
            # Para la caución, si no hay offer, mantenemos la última para no romper el cálculo,
            # pero para activos de trading, el 0.0 es obligatorio.
            if of: self.tna_caucion_offer = of[0]['price']

        self.last_update = time.time()

    def check_health(self):
        """Vigilante de conexión"""
        return (time.time() - self.last_update) < 30

    def _guardar_trades_background(self, resultados):
        """Guarda en Mongo de forma asíncrona solo si hay ganancia y es un dato nuevo"""
        # 1. Filtramos los positivos
        positivos = [r for r in resultados if r['pnl'] > 0]
        if not positivos:
            return

        # 2. Firma anti-spam (compara el activo y su PNL redondeado)
        firma_actual = str([(r['asset'], round(r['pnl'], 2)) for r in positivos])
        if firma_actual == self.last_saved_signature:
            return

        self.last_saved_signature = firma_actual

        # 3. Preparamos y guardamos
        timestamp_actual = datetime.now()
        docs_a_guardar = []
        for p in positivos:
            doc = p.copy()
            doc['timestamp'] = timestamp_actual
            docs_a_guardar.append(doc)

        try:
            self.mongo.collection.insert_many(docs_a_guardar)
        except Exception as e:
            logger.error(f"Falla al insertar arbitrajes en CI24: {e}")

    def calcular_resultados(self):
        """Motor Matemático: Divide por 100 y cruza Puntas Reales"""
        resultados = []
        for doc in self.catalogo:
            # BYMA: Bonos dividen por 100, Acciones/Cedears por 1.
            # Usamos el lote de la DB, pero si es 0 o None, defaulteamos a 100 por seguridad en bonos.
            lote = doc.get('lote', 100)

            p_ci = self.precios.get(doc['patas']['ci'], {'offer': 0.0, 'offer_size': 0})
            p_24 = self.precios.get(doc['patas']['24hs'], {'bid': 0.0, 'bid_size': 0})

            # CRUCIAL: Solo calculamos si AMBAS puntas existen en este milisegundo
            if p_ci['offer'] > 0 and p_24['bid'] > 0 and self.tna_caucion_offer > 0:
                size_maximo = min(p_ci['offer_size'], p_24['bid_size'])

                # Capitalizado: (Size * Precio / Lote)
                monto_ci = (size_maximo * p_ci['offer'] / lote) * (1 + FEE)
                monto_24 = (size_maximo * p_24['bid'] / lote) * (1 - FEE)

                rend_directo = (monto_24 / monto_ci) - 1
                costo_fondeo = monto_ci * (self.tna_caucion_offer / 100) * (self.caucion_dias / 365)
                pnl_neto = (monto_24 - monto_ci) - costo_fondeo

                if pnl_neto > -500:  # Filtro de ruido para no llenar la tabla de basura
                    resultados.append({
                        'asset': doc['asset'],
                        'offer_ci': p_ci['offer'],
                        'bid_24': p_24['bid'],
                        'size': size_maximo,
                        'rend_directo': rend_directo * 100,
                        'pnl': pnl_neto
                    })

        resultados.sort(key=lambda x: x['pnl'], reverse=True)
        threading.Thread(target=self._guardar_trades_background, args=(resultados,), daemon=True).start()
        return resultados


# ==========================================
# 2. LA INTERFAZ: ArbitrageApp
# ==========================================
class ArbitrageApp(App):
    """
    Vista pura. Solo le pide datos al Engine y los dibuja.
    """
    BINDINGS = [("q", "quit", "Salir")]

    def __init__(self, engine):
        super().__init__()
        self.engine = engine

    def compose(self) -> ComposeResult:
        with ScrollableContainer():
            yield Static(id="tabla_dinamica")

    def on_mount(self) -> None:
        self.set_interval(1.0, self.refresh_ui)

    def refresh_ui(self) -> None:
        if not self.engine.check_health():
            pass

        resultados = self.engine.calcular_resultados()

        table = Table(
            title=f"⚖️ [bold yellow]MONITOR ARBITRAJE DE PLAZOS[/bold yellow] | [cyan]FONDEO: {self.engine.tna_caucion_offer:.2f}% ({self.engine.caucion_dias}D)[/cyan]",
            box=box.ROUNDED, header_style="bold magenta", expand=True
        )

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
                f"{self.engine.tna_caucion_offer:.2f}%",
                f"{r['size']:,}",
                f"{r['rend_directo']:.4f}%",
                f"{color_pnl}${r['pnl']:,.2f}"
            )

        if not resultados:
            table.add_row("Aguardando liquidez en ambas puntas...", "", "", "", "", "", "")

        self.query_one("#tabla_dinamica", Static).update(table)


# ==========================================
# 3. EL ORQUESTADOR
# ==========================================
def run():
    os.system('cls' if os.name == 'nt' else 'clear')

    if not inicializar_sesion(): return

    engine = ArbitrageEngine()
    if not engine.setup_inicial():
        return

    ws_manager = WebSocketManager(engine)

    if ws_manager.iniciar_ws(engine.get_tickers_suscripcion()):
        ArbitrageApp(engine).run()


if __name__ == "__main__":
    run()