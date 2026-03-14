import os
import time
import threading
import collections
import pyRofex
from datetime import datetime
from dotenv import load_dotenv

# --- RICH Y TEXTUAL ---
from rich.table import Table
from rich.panel import Panel
from rich.console import Group
from rich.layout import Layout
from rich import box
from textual.app import App, ComposeResult
from textual.widgets import Static, Header
from textual.containers import ScrollableContainer

# --- TUS MANAGERS ---
from session_manager import inicializar_sesion
from websocket_manager import WebSocketManager

load_dotenv()


# ==========================================
# 1. EL CEREBRO: OMSEngine
# ==========================================
class OMSEngine:
    def __init__(self):
        self.account = os.getenv("ROFEX_ACCOUNT")
        if not self.account:
            raise ValueError("ROFEX_ACCOUNT missing en el .env")

        self.ordenes_activas = {}
        self.ultimos_eventos = collections.deque(maxlen=10)
        self.saldos = {}
        self.tenencia = {}
        self._lock = threading.Lock()

    def registrar_evento(self, mensaje, tipo="info"):
        hora = datetime.now().strftime('%H:%M:%S')
        colores = {"error": "red", "ejecucion": "bold green", "cancelacion": "yellow", "info": "cyan"}
        icono = {"error": "❌", "ejecucion": "💸", "cancelacion": "🗑️", "info": "📩"}

        c = colores.get(tipo, "white")
        i = icono.get(tipo, "➤")
        self.ultimos_eventos.appendleft(f"[{hora}] {i} [{c}]{mensaje}[/{c}]")

    # --- CONSULTAS REST (FOTOS) ---
    def actualizar_foto_cuenta(self):
        """Consulta Saldos y Tenencias vía REST"""
        try:
            # Saldos
            res_saldos = pyRofex.get_account_report(account=self.account)
            if res_saldos and res_saldos.get("status") == "OK":
                reports = res_saldos.get("accountData", {}).get("detailedAccountReports", {})
                saldos_temp = {}
                for plazo in ["0", "2"]:
                    saldos_temp[plazo] = {"ARS": 0.0, "MEP": 0.0}
                    if plazo in reports:
                        bals = reports[plazo].get("currencyBalance", {}).get("detailedCurrencyBalance", {})
                        if "ARS" in bals: saldos_temp[plazo]["ARS"] = float(bals["ARS"].get("available", 0.0))
                        if "USD D" in bals: saldos_temp[plazo]["MEP"] = float(bals["USD D"].get("available", 0.0))
                self.saldos = saldos_temp

            # Tenencias
            res_ten = pyRofex.get_account_position(account=self.account)
            if res_ten and res_ten.get("status") == "OK":
                pos_temp = {}
                for pos in res_ten.get("positions", []):
                    sym = pos.get("instrument", {}).get("symbolReference", "DESC")
                    neta = float(pos.get("buySize", 0.0)) - float(pos.get("sellSize", 0.0))
                    if neta != 0: pos_temp[sym] = pos_temp.get(sym, 0.0) + neta
                self.tenencia = pos_temp

        except Exception as e:
            self.registrar_evento(f"Error actualizando saldos: {e}", "error")

    def cargar_ordenes_activas_rest(self):
        """Busca las órdenes que ya estaban vivas antes de encender el bot"""
        self.registrar_evento("Sincronizando estado inicial con el mercado...")
        try:
            res = pyRofex.get_all_orders_status(account=self.account)
            if not res or res.get("status") != "OK": return

            vivas = ["NEW", "PARTIALLY_FILLED", "ACCEPTED_FOR_BIDDING", "PENDING_NEW", "REPLACED"]
            for o in res.get("orders", []):
                if o.get("status") in vivas and o.get("clOrdId"):
                    self.ordenes_activas[o.get("clOrdId")] = o
            self.registrar_evento(f"Sincronización completa: {len(self.ordenes_activas)} órdenes activas previas.")
        except Exception as e:
            self.registrar_evento(f"Error en sincronización: {e}", "error")

    # --- INYECCIÓN DEL WEBSOCKET ---
    def update_order_report(self, message):
        """El WebSocketManager llama acá cada vez que pasa algo con una orden"""
        try:
            report = message.get("orderReport", {})
            clOrdId = report.get("clOrdId")
            estado = report.get("status")
            if not clOrdId: return

            with self._lock:
                finales = ["FILLED", "CANCELLED", "REJECTED", "EXPIRED"]

                if estado in finales:
                    if clOrdId in self.ordenes_activas:
                        del self.ordenes_activas[clOrdId]

                    if estado == "FILLED":
                        px = report.get("lastPx", 0)
                        sz = report.get("lastQty", 0)
                        tkr = report.get("instrumentId", {}).get("symbol", "")
                        self.registrar_evento(f"EJECUCIÓN: {sz} nom. de {tkr} a ${px}", "ejecucion")
                        # Disparamos actualización REST de saldos en background porque la cuenta cambió
                        threading.Thread(target=self.actualizar_foto_cuenta, daemon=True).start()
                    else:
                        self.registrar_evento(f"Orden eliminada ({estado}): {clOrdId[-6:]}", "cancelacion")
                else:
                    self.ordenes_activas[clOrdId] = report
                    self.registrar_evento(f"Orden actualizada ({estado}): {clOrdId[-6:]}")

        except Exception as e:
            self.registrar_evento(f"Error procesando evento WS: {e}", "error")


# ==========================================
# 2. LA INTERFAZ: OMSApp
# ==========================================
class OMSApp(App):
    BINDINGS = [("q", "quit", "Cerrar OMS")]

    def __init__(self, engine):
        super().__init__()
        self.engine = engine

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with ScrollableContainer():
            yield Static(id="dashboard")

    def on_mount(self) -> None:
        self.set_interval(0.5, self.refresh_ui)
        # Actualizamos saldos cada 1 minuto por si hubo algún movimiento externo (ej: cobro de cupones)
        self.set_interval(60.0, self.engine.actualizar_foto_cuenta)

    def refresh_ui(self) -> None:
        # 1. Panel de Cuenta (Saldos y Tenencia)
        t_cuenta = Table(title="🏦 ESTADO DE CUENTA", box=box.SIMPLE_HEAVY, expand=True)
        t_cuenta.add_column("PLAZO", justify="center", style="bold")
        t_cuenta.add_column("DISPONIBLE ARS", justify="right", style="green")
        t_cuenta.add_column("DISPONIBLE MEP", justify="right", style="green")

        s = self.engine.saldos
        t_cuenta.add_row("Contado Inmediato", f"${s.get('0', {}).get('ARS', 0):,.2f}",
                         f"U$S {s.get('0', {}).get('MEP', 0):,.2f}")
        t_cuenta.add_row("Plazo 48hs", f"${s.get('2', {}).get('ARS', 0):,.2f}",
                         f"U$S {s.get('2', {}).get('MEP', 0):,.2f}")

        ten_str = " | ".join([f"{k}: [bold]{v:,.0f}[/]" for k, v in self.engine.tenencia.items()])
        if not ten_str: ten_str = "Sin posiciones abiertas."
        p_cuenta = Group(t_cuenta, Panel(f"📦 [cyan]CARTERA NETA:[/cyan] {ten_str}", border_style="cyan"))

        # 2. Panel de Órdenes Vivas
        t_ord = Table(title="📡 ÓRDENES VIVAS EN EL MERCADO", box=box.ROUNDED, expand=True)
        for col in ["ID", "TICKER", "SIDE", "SIZE", "PRECIO", "ESTADO"]: t_ord.add_column(col,
                                                                                          justify="center" if col == "SIDE" else "left")

        if not self.engine.ordenes_activas:
            t_ord.add_row("---", "Mercado limpio", "---", "0", "$0.00", "---")
        else:
            for k, v in list(self.engine.ordenes_activas.items()):
                side = v.get("side", "")
                c_side = "[green]COMPRA[/]" if side == "BUY" else "[red]VENTA[/]"
                t_ord.add_row(
                    k[:20] + "...", v.get("instrumentId", {}).get("symbol", ""), c_side,
                    str(v.get("leavesQty", v.get("orderQty", 0))),
                    f"${v.get('price', 0):,.2f}", v.get("status", "")
                )

        # 3. Log de Eventos
        log_str = "\n".join(self.engine.ultimos_eventos) if self.engine.ultimos_eventos else "Esperando eventos..."
        p_log = Panel(log_str, title="⏱️ TAPE DE EJECUCIONES", border_style="yellow")

        # Layout final
        self.query_one("#dashboard", Static).update(Group(Panel(p_cuenta, border_style="blue"), t_ord, p_log))


# ==========================================
# 3. ORQUESTADOR
# ==========================================
def run():
    os.system('cls' if os.name == 'nt' else 'clear')
    if not inicializar_sesion(): return

    engine = OMSEngine()

    # 1. Foto inicial (REST)
    engine.actualizar_foto_cuenta()
    engine.cargar_ordenes_activas_rest()

    # 2. Inyectamos motor al WS y prendemos la escucha de Órdenes
    ws = WebSocketManager(engine)
    if ws.iniciar_ws(activar_oms=True, account=engine.account):
        # 3. Lanzamos Pantalla
        OMSApp(engine).run()


if __name__ == "__main__":
    run()