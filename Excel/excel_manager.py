import gspread
import time
import config
import logging
from oauth2client.service_account import ServiceAccountCredentials

logger = logging.getLogger("TradingBot")


class ExcelManager:
    def __init__(self):
        self.creds = ServiceAccountCredentials.from_json_keyfile_name(config.GS_CREDS_FILE, config.GS_SCOPE)
        self.client = None
        self.sheet = None
        self.conectar()

    def conectar(self):
        """Establece o refresca la conexion con la hoja de calculo"""
        try:
            self.client = gspread.authorize(self.creds)
            # Abrimos el archivo y la hoja especifica del config
            self.sheet = self.client.open(config.SPREADSHEET_NAME).worksheet(config.SHEET_MARKET)
            return True
        except Exception as e:
            logger.error(f"Error de conexion a Google Sheets: {e}")
            return False

    def escribir_data(self, matriz):
        """
        Escribe la matriz en el Excel con logica de reintento.
        Como la matriz es fija, siempre sobreescribe el mismo rango.
        """
        intentos = 3
        for i in range(intentos):
            try:
                # Tu código original intocable
                self.sheet.update(range_name='A1', values=matriz)
                return True
            except Exception as e:
                # ACA ESTA EL CAMBIO: Imprimimos la 'e' para ver el error real
                espera = (i + 1) * 3
                logger.warning(f"Falla API Google (Intento {i + 1}/{intentos}) - ERROR REAL: {str(e)}")
                time.sleep(espera)
                self.conectar()

        logger.error("No se pudo actualizar el Excel tras varios intentos.")
        return False