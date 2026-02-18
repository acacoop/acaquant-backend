import time
import config
from logger_config import setup_logger
from session_manager import inicializar_sesion
from market_manager import MarketManager
from websocket_manager import WebSocketManager
from Excel.excel_manager import ExcelManager

# Configuramos el logger global
logger = setup_logger()


def main():
    while True:  # Loop externo para reconexión total si todo falla
        try:
            if not inicializar_sesion():
                logger.error("Fallo inicializacion Rofex. Reintentando en 5s...")
                time.sleep(5)
                continue

            market = MarketManager()
            ws = WebSocketManager(market)
            excel = ExcelManager()

            # Suscribimos a la lista fija del config
            if ws.iniciar_ws(config.TICKERS_LIST):
                logger.info("Sistema Operativo - Monitoreando lista estatica")

                while True:
                    # 1. Chequeo de salud AGRESIVO
                    if not market.check_health():
                        # Usamos logger.warning para que lo veas rápido en la consola
                        logger.warning("¡ALERTA! Conexión caída o congelada. Reiniciando sistema YA...")

                        # Intentamos cerrar la conexión vieja por las dudas antes de salir
                        try:
                            import pyRofex
                            pyRofex.close_websocket_connection()
                        except:
                            pass

                        break  # Esto rompe el loop y vuelve al "inicializar_sesion"

                    # 2. Tu proceso normal de 2 segundos
                    matriz = market.get_data_for_excel()
                    if excel.escribir_data(matriz):
                        # print resumido para no llenar de logs pero saber que está vivo
                        print(f"[{time.strftime('%H:%M:%S')}] OK - Sync {len(matriz) - 1} filas", end='\r')

                    time.sleep(2)

        except Exception as e:
            logger.critical(f"Error catastrofico en loop: {e}. Reiniciando en 8s...")
            time.sleep(8)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n")
        logger.info("Detencion manual detectada. Cerrando procesos...")
        try:
            import pyRofex

            pyRofex.close_websocket_connection()
            logger.info("WebSocket desconectado. Bot fuera de linea.")
        except:
            pass