import os
import gspread
import config
import logging
from oauth2client.service_account import ServiceAccountCredentials

logger = logging.getLogger("TradingBot")


class GoogleSheetsManager:
    def __init__(self):
        # 1. Ruta absoluta a prueba de balas para el JSON
        base_path = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(base_path, "ons-fx.json")

        self.creds = ServiceAccountCredentials.from_json_keyfile_name(json_path, config.GS_SCOPE)
        self.client = None
        self.spreadsheet = None
        self.conectar()

    def conectar(self):
        try:
            self.client = gspread.authorize(self.creds)
            self.spreadsheet = self.client.open(config.SPREADSHEET_NAME)
            return True
        except Exception as e:
            logger.error(f"Error de conexión a Google Sheets: {e}")
            return False

    # ---------------------------------------------------------
    # MÉTODO 1: PARA TU MAIN (Real-time Market Data)
    # ---------------------------------------------------------
    def escribir_mercado(self, matriz):
        """Sobreescribe la celda A1 de la pestaña MARKET con la matriz rápida"""
        intentos = 3
        for i in range(intentos):
            try:
                sheet_market = self.spreadsheet.worksheet(config.SHEET_MARKET)
                sheet_market.update(range_name='A1', values=matriz)
                return True
            except Exception as e:
                logger.warning(f"Falla API Google Mercado (Intento {i + 1}/{intentos}): {e}")
                import time;
                time.sleep((i + 1) * 2)
        return False

    # ---------------------------------------------------------
    # MÉTODO 2: PARA TUS CARTERAS (Aunesa)
    # ---------------------------------------------------------
    def escribir_carteras(self, df):
        """Limpia y pega el DataFrame de Aunesa en la pestaña CARTERAS"""
        if df is None or df.empty: return False

        try:
            df = df.fillna("")
            encabezados = df.columns.tolist()
            valores = df.values.tolist()
            matriz = [encabezados] + valores

            sheet_carteras = self.spreadsheet.worksheet("CARTERAS")

            num_columnas = len(encabezados)
            letra_columna = chr(64 + num_columnas)

            # Limpiamos solo el rango que vamos a usar
            sheet_carteras.batch_clear([f"A1:{letra_columna}"])

            # Escribimos
            sheet_carteras.update(range_name=f"A1:{letra_columna}{len(matriz)}", values=matriz)
            return True
        except Exception as e:
            logger.error(f"Error escribiendo en CARTERAS: {e}")
            return False