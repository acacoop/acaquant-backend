import os
import time
import pyRofex
import threading
from datetime import datetime
from dotenv import load_dotenv
from session_manager import inicializar_sesion

load_dotenv()


class BackendOMS:
    def __init__(self):
        self.account = os.getenv("ROFEX_ACCOUNT")
        if not self.account:
            raise ValueError("ROFEX_ACCOUNT missing en el .env")

        self.ordenes_activas = {}
        self._lock = threading.Lock()

    def log(self, mensaje):
        """Imprime por consola con formato de timestamp, sin borrar nada (cero parpadeos)"""
        hora = datetime.now().strftime('%H:%M:%S.%f')[:-3]  # Con milisegundos
        print(f"[{hora}] {mensaje}")

    # ==========================================
    # 1. ACCOUNT MANAGER (TU CÓDIGO ORIGINAL)
    # ==========================================
    def obtener_saldos(self):
        try:
            res = pyRofex.get_account_report(account=self.account)
            if not res or res.get("status") != "OK": return {}
            reports = res.get("accountData", {}).get("detailedAccountReports", {})
            saldos = {}
            for plazo in ["0", "2"]:
                saldos[plazo] = {"ARS": {"available": 0.0, "consumed": 0.0}, "MEP": {"available": 0.0, "consumed": 0.0}}
                if plazo in reports:
                    bals = reports[plazo].get("currencyBalance", {}).get("detailedCurrencyBalance", {})
                    if "ARS" in bals:
                        saldos[plazo]["ARS"]["available"] = float(bals["ARS"].get("available", 0.0))
                        saldos[plazo]["ARS"]["consumed"] = float(bals["ARS"].get("consumed", 0.0))
                    if "USD D" in bals:
                        saldos[plazo]["MEP"]["available"] = float(bals["USD D"].get("available", 0.0))
                        saldos[plazo]["MEP"]["consumed"] = float(bals["USD D"].get("consumed", 0.0))
            return saldos
        except Exception as e:
            self.log(f"Error parseando saldos: {e}")
            return {}

    def obtener_tenencias(self):
        try:
            res = pyRofex.get_account_position(account=self.account)
            if not res or res.get("status") != "OK": return {}
            posiciones = res.get("positions", [])
            cartera = {}
            for pos in posiciones:
                sym_ref = pos.get("instrument", {}).get("symbolReference", "DESCONOCIDO")
                b_size = float(pos.get("buySize", 0.0))
                s_size = float(pos.get("sellSize", 0.0))
                neta = b_size - s_size
                if neta != 0:
                    cartera[sym_ref] = cartera.get(sym_ref, 0.0) + neta
            return cartera
        except Exception as e:
            self.log(f"Error parseando tenencia: {e}")
            return {}

    def imprimir_foto_cuenta(self, motivo_trigger):
        """Busca y muestra los saldos (Se dispara solo por eventos)"""
        saldos = self.obtener_saldos()
        tenencia = self.obtener_tenencias()

        print("\n" + "=" * 60)
        self.log(f"🔄 ACTUALIZACIÓN REST DISPARADA POR: {motivo_trigger}")

        print("  🏦 SALDOS DISPONIBLES:")
        for plazo, datas in saldos.items():
            nombre_plazo = "CI" if plazo == "0" else "48hs"
            print(
                f"     [{nombre_plazo}] ARS: ${datas['ARS']['available']:,.2f}  |  MEP: U$S {datas['MEP']['available']:,.2f}")

        print("  📦 TENENCIA NETA:")
        if not tenencia:
            print("     (Sin posiciones abiertas)")
        else:
            for activo, cantidad in tenencia.items():
                print(f"     ➤ {activo}: {cantidad:,.0f} nom.")
        print("=" * 60 + "\n")

    # ==========================================
    # 2. ORDER TRACKER (REAL TIME WS)
    # ==========================================
    def cargar_ordenes_rest(self):
        self.log("Buscando órdenes activas por REST (Foto Inicial)...")
        try:
            res = pyRofex.get_all_orders_status(account=self.account)
            if not res or res.get("status") != "OK": return

            ordenes = res.get("orders", [])
            estados_vivos = ["NEW", "PARTIALLY_FILLED", "ACCEPTED_FOR_BIDDING", "PENDING_NEW", "REPLACED"]
            for ord_data in ordenes:
                if ord_data.get("status") in estados_vivos:
                    clOrdId = ord_data.get("clOrdId")
                    if clOrdId: self.ordenes_activas[clOrdId] = ord_data

            self.log(f"Se cargaron {len(self.ordenes_activas)} órdenes activas previas.")
        except Exception as e:
            self.log(f"Error cargando estado inicial: {e}")

    def procesar_reporte_ws(self, message):
        """LÓGICA EXACTA DE EVENTOS: Solo reacciona cuando algo pasa"""
        try:
            order_report = message.get("orderReport", {})
            clOrdId = order_report.get("clOrdId")
            estado = order_report.get("status")
            if not clOrdId: return

            with self._lock:
                estados_finales = ["FILLED", "CANCELLED", "REJECTED", "EXPIRED"]
                evento_str = ""

                if estado in estados_finales:
                    if clOrdId in self.ordenes_activas:
                        del self.ordenes_activas[clOrdId]

                    if estado == "FILLED":
                        precio = order_report.get("lastPx", 0)
                        size = order_report.get("lastQty", 0)
                        ticker = order_report.get("instrumentId", {}).get("symbol", "")
                        evento_str = f"💸 ¡EJECUCIÓN! {size} nom. de {ticker} a ${precio}"
                    else:
                        evento_str = f"🗑️ ORDEN ELIMINADA: {estado} (ID: {clOrdId[-6:]})"
                else:
                    self.ordenes_activas[clOrdId] = order_report
                    evento_str = f"📩 ORDEN VIVA: {estado} (ID: {clOrdId[-6:]})"

            self.log(evento_str)

            # --- MAGIA DEL TRIGGER ---
            # Como hubo un cambio, lanzamos el chequeo de saldos REST en un hilo fantasma
            # para que el WebSocket no se congele ni se retrase.
            threading.Thread(target=self.imprimir_foto_cuenta, args=(evento_str,), daemon=True).start()

        except Exception as e:
            self.log(f"Error procesando reporte WS: {e}")


# ==========================================
# 3. LOOP PRINCIPAL
# ==========================================
if __name__ == "__main__":
    import sys

    os.system('cls' if os.name == 'nt' else 'clear')

    oms = BackendOMS()
    oms.log("🚀 Iniciando OMS Backend Orientado a Eventos...")


    def ws_error_handler(msg):
        oms.log(f"Error WS: {msg}")


    def ws_exception_handler(e):
        oms.log(f"Excepción WS: {e}")


    while True:
        try:
            if inicializar_sesion():
                # 1. Cargamos el estado inicial REST (una vez)
                oms.cargar_ordenes_rest()
                oms.imprimir_foto_cuenta("INICIO DEL SISTEMA")

                # 2. Conectamos al WS para escuchar novedades
                pyRofex.init_websocket_connection(
                    order_report_handler=oms.procesar_reporte_ws,
                    error_handler=ws_error_handler,
                    exception_handler=ws_exception_handler
                )
                time.sleep(1)
                pyRofex.order_report_subscription(account=oms.account)
                oms.log("📡 Suscripción WS establecida. Escuchando al mercado...")

                # 3. Se queda vivo para siempre escuchando
                while True:
                    time.sleep(1)

            else:
                oms.log("Reintentando sesión en 10s...")
                time.sleep(10)
        except KeyboardInterrupt:
            pyRofex.close_websocket_connection()
            print("\n🛑 OMS Apagado manualmente.")
            sys.exit(0)
        except Exception as e:
            oms.log(f"Fallo del proceso principal: {e}")
            time.sleep(5)