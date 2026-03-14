import time
from datetime import datetime
from session_manager import inicializar_sesion
from websocket_manager import WebSocketManager

TICKER = "MERV - XMEV - T30A7 - 24hs"

# ==========================================
# 1. EL CEREBRO DE PRUEBA (TestEngine)
# ==========================================
class TestEngine:
    """Motor minimalista solo para auditar la cinta (Tape)"""
    def get_tickers_suscripcion(self):
        return [TICKER]

    def update_price(self, ticker, data):
        # El WebSocketManager nos inyecta toda la data, filtramos solo LA
        last = data.get("LA")
        if last and last.get("price") is not None:
            price = last["price"]
            size = last["size"]
            timestamp_ms = last["date"]

            ts = datetime.fromtimestamp(timestamp_ms / 1000.0)
            hora_str = ts.strftime("%H:%M:%S.%f")[:-3]

            print(f"[{hora_str}] TRADE -> Px: {price} | Sz: {size:,}")

# ==========================================
# 2. EL ORQUESTADOR DE PRUEBA
# ==========================================
def run_auditor():
    print("Iniciando sesión en pyRofex...")
    if not inicializar_sesion():
        print("Fallo en la sesión.")
        return

    # Instanciamos el motor de prueba y se lo inyectamos al manager central
    engine = TestEngine()
    ws_manager = WebSocketManager(engine)

    print(f"Suscribiendo a {TICKER} a través del Manager Central...")
    if ws_manager.iniciar_ws(engine.get_tickers_suscripcion()):
        print("Escuchando el mercado... (Presioná Ctrl+C para salir)\n" + "-" * 50)

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nCerrando auditor...")
            ws_manager.cerrar_ws()

if __name__ == "__main__":
    run_auditor()