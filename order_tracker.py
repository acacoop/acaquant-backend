import os
import time
import logging
import collections
import pyRofex
from datetime import datetime
from dotenv import load_dotenv

# --- RICH Y TEXTUAL (LA SOLUCIÓN AL PARPADEO) ---
from rich.table import Table
from rich.panel import Panel
from rich.console import Group
from rich import box
from textual.app import App, ComposeResult
from textual.widgets import Static
from textual.containers import ScrollableContainer

load_dotenv()

logging.basicConfig(level=logging.INFO, filename='order_tracker.log',
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("OrderTracker")


class OrderTracker:
    def __init__(self):
        self.account = os.getenv("ROFEX_ACCOUNT")
        if not self.account:
            raise ValueError("ROFEX_ACCOUNT missing")

        self.ordenes_activas = {}
        self.ultimos_eventos = collections.deque(maxlen=8)

    def registrar_evento(self, mensaje, tipo="info"):
        hora = datetime.now().strftime('%H:%M:%S')
        if tipo == "error":
            linea = f"[{hora}] ❌ [red]{mensaje}[/red]"
            logger.error(mensaje)
        elif tipo == "ejecucion":
            linea = f"[{hora}] 💸 [bold green]{mensaje}[/bold green]"
            logger.warning(mensaje)
        elif tipo == "cancelacion":
            linea = f"[{hora}] 🗑️ [yellow]{mensaje}[/yellow]"
            logger.info(mensaje)
        else:
            linea = f"[{hora}] 📩 [cyan]{mensaje}[/cyan]"
            logger.info(mensaje)

        self.ultimos_eventos.appendleft(linea)

    def cargar_estado_inicial(self):
        self.registrar_evento("Sacando foto inicial de las órdenes activas (REST)...")
        try:
            res = pyRofex.get_all_orders_status(account=self.account)
            if not res or res.get("status") != "OK":
                self.registrar_evento(f"Error al consultar historial: {res}", "error")
                return

            ordenes = res.get("orders", [])
            estados_vivos = ["NEW", "PARTIALLY_FILLED", "ACCEPTED_FOR_BIDDING", "PENDING_NEW", "REPLACED"]

            for ord_data in ordenes:
                estado = ord_data.get("status")
                if estado in estados_vivos:
                    clOrdId = ord_data.get("clOrdId")
                    self.ordenes_activas[clOrdId] = ord_data

            self.registrar_evento(f"Se encontraron {len(self.ordenes_activas)} órdenes activas en el mercado.")

        except Exception as e:
            self.registrar_evento(f"Error cargando estado inicial: {e}", "error")

    def procesar_reporte_ws(self, message):
        try:
            order_report = message.get("orderReport", {})
            clOrdId = order_report.get("clOrdId")
            estado = order_report.get("status")

            if not clOrdId: return

            estados_finales = ["FILLED", "CANCELLED", "REJECTED", "EXPIRED"]

            if estado in estados_finales:
                if clOrdId in self.ordenes_activas:
                    del self.ordenes_activas[clOrdId]

                if estado == "FILLED":
                    precio = order_report.get("lastPx", 0)
                    size = order_report.get("lastQty", 0)
                    self.registrar_evento(f"¡EJECUCIÓN! Orden {clOrdId[-6:]} operó {size} nom. a ${precio}", "ejecucion")
                else:
                    self.registrar_evento(f"Orden {clOrdId[-8:]} eliminada. Motivo: {estado}", "cancelacion")

            else:
                self.ordenes_activas[clOrdId] = order_report
                self.registrar_evento(f"Orden {clOrdId[-8:]} viva. Estado: {estado}")

        except Exception as e:
            self.registrar_evento(f"Error procesando WS: {e}", "error")

    def generar_dashboard(self):
        table = Table(title="📡 ÓRDENES VIVAS EN EL MERCADO (OMS)", box=box.ROUNDED, expand=True)
        table.add_column("ID ORDEN", style="cyan", width=25)
        table.add_column("TICKER", style="white")
        table.add_column("SIDE", style="bold")
        table.add_column("SIZE", justify="right", style="cyan")
        table.add_column("PRECIO", justify="right", style="green")
        table.add_column("ESTADO", justify="right", style="yellow")

        if not self.ordenes_activas:
            table.add_row("---", "Mercado limpio", "---", "0", "$0.00", "---")
        else:
            for clOrdId, data in self.ordenes_activas.items():
                ticker = data.get("instrumentId", {}).get("symbol", "N/A")
                side = data.get("side", "N/A")
                size = data.get("leavesQty", data.get("orderQty", 0))
                price = data.get("price", 0)
                estado = data.get("status", "N/A")

                str_side = f"[green]COMPRA[/green]" if side == "BUY" else f"[red]VENTA[/red]"
                table.add_row(clOrdId[:25], ticker, str_side, str(size), f"${price:,.2f}", estado)

        texto_eventos = "\n".join(self.ultimos_eventos) if self.ultimos_eventos else "Esperando eventos..."
        event_panel = Panel(texto_eventos, title="⏱️ ÚLTIMOS EVENTOS (Tiempo Real)", border_style="blue", expand=True)

        return Group(table, event_panel)


# ==========================================
# APP ESTÁTICA TEXTUAL (CERO PARPADEO)
# ==========================================
class OMSApp(App):
    BINDINGS = [("q", "quit", "Cerrar OMS")]

    def __init__(self, tracker):
        super().__init__()
        self.tracker = tracker

    def compose(self) -> ComposeResult:
        with ScrollableContainer():
            yield Static(id="dashboard_panel")

    def on_mount(self) -> None:
        # Se actualiza cada 0.5 segundos internamente sin limpiar la pantalla
        self.set_interval(0.5, self.actualizar_pantalla)

    def actualizar_pantalla(self) -> None:
        # Toma tu grupo de tablas de Rich y lo inyecta en el widget estático
        self.query_one("#dashboard_panel", Static).update(self.tracker.generar_dashboard())


# ==========================================
# INICIO DEL PROGRAMA
# ==========================================
if __name__ == "__main__":
    from session_manager import inicializar_sesion
    import sys
    import time

    def ws_error_handler(message):
        logger.error(f"Error WS: {message}")

    def ws_exception_handler(e):
        logger.critical(f"Excepción WS: {e}")

    os.system('cls' if os.name == 'nt' else 'clear')
    print("🚀 Levantando Order Management System (OMS)...")

    if inicializar_sesion():
        tracker = OrderTracker()
        tracker.cargar_estado_inicial()

        pyRofex.init_websocket_connection(
            order_report_handler=tracker.procesar_reporte_ws,
            error_handler=ws_error_handler,
            exception_handler=ws_exception_handler
        )
        time.sleep(1)
        pyRofex.order_report_subscription(account=tracker.account)
        tracker.registrar_evento("Suscripción al broker WS establecida.", "info")

        # Levantamos la App que bloquea la terminal en modo estático
        try:
            app = OMSApp(tracker)
            app.run()
        except KeyboardInterrupt:
            pass
        finally:
            pyRofex.close_websocket_connection()
            os.system('cls' if os.name == 'nt' else 'clear')
            print("🛑 OMS Detenido correctamente.")
            sys.exit(0)