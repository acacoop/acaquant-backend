import time
import config
from Excel.logger_config import setup_logger
from session_manager import inicializar_sesion
from websocket_manager import WebSocketManager
from google_sheets_manager import GoogleSheetsManager  # <- Nuestro nuevo manager único

logger = setup_logger()


class ExcelFeederEngine:
    """Mini-motor dedicado exclusivamente a juntar precios para el Excel"""

    def __init__(self):
        self.precios_vivos = {
            t: {'bid': 0.0, 'bid_size': 0, 'offer': 0.0, 'offer_size': 0, 'last': 0.0, 'last_size': 0}
            for t in config.TICKERS_LIST
        }
        self.last_update_time = time.time()

    def update_price(self, ticker, data):
        """Inyectado por el WebSocketManager"""
        if not ticker or ticker not in self.precios_vivos: return

        self.last_update_time = time.time()
        p = self.precios_vivos[ticker]

        if 'BI' in data and data['BI']:
            p['bid'], p['bid_size'] = data['BI'][0]['price'], data['BI'][0]['size']
        if 'OF' in data and data['OF']:
            p['offer'], p['offer_size'] = data['OF'][0]['price'], data['OF'][0]['size']
        if 'LA' in data and isinstance(data['LA'], dict):
            p['last'], p['last_size'] = data['LA']['price'], data['LA']['size']

    def check_health(self):
        return (time.time() - self.last_update_time) <= 30

    def generar_matriz(self):
        """Arma la lista de listas exactamente como la espera tu Excel"""
        headers = ["Ticker", "Bid", "Bid Size", "Offer", "Offer Size", "Last", "Last Size"]
        matriz = [headers]
        for t in config.TICKERS_LIST:
            p = self.precios_vivos[t]
            matriz.append([t, p['bid'], p['bid_size'], p['offer'], p['offer_size'], p['last'], p['last_size']])
        return matriz


def main():
    while True:
        try:
            if not inicializar_sesion():
                time.sleep(5);
                continue

            engine = ExcelFeederEngine()
            ws = WebSocketManager(engine)
            gs = GoogleSheetsManager()

            if ws.iniciar_ws(config.TICKERS_LIST):
                print("✅ Conectado. Enviando datos a Google Sheets (Presioná Ctrl+C para detener)...")

                while True:
                    if not engine.check_health():
                        print("⚠️ Flujo de datos congelado. Reiniciando...")
                        break

                    # Extraemos la matriz y la mandamos al método específico
                    matriz = engine.generar_matriz()
                    if gs.escribir_mercado(matriz):
                        print(f"[{time.strftime('%H:%M:%S')}] OK - Sincronizado", end='\r')

                    time.sleep(3)  # Pausa para no saturar la API de Google

        except KeyboardInterrupt:
            print("\n🛑 Transmisión a Excel detenida.")
            break
        except Exception as e:
            print(f"🔥 Error: {e}. Reiniciando en 5s...")
            time.sleep(5)


if __name__ == "__main__":
    main()