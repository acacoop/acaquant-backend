import gspread
import config
import logging
from oauth2client.service_account import ServiceAccountCredentials

logger = logging.getLogger("TradingBot")


class ExcelCarterasManager:
    def __init__(self):
        self.creds = ServiceAccountCredentials.from_json_keyfile_name(config.GS_CREDS_FILE, config.GS_SCOPE)
        self.client = None
        self.sheet = None
        self.conectar()

    def conectar(self):
        try:
            self.client = gspread.authorize(self.creds)
            # Apuntamos a la nueva hoja
            self.sheet = self.client.open(config.SPREADSHEET_NAME).worksheet("CARTERAS")
            return True
        except Exception as e:
            logger.error(f"Error de conexion Excel (Carteras): {e}")
            return False

    def escribir_data(self, df):
        if df is None or df.empty:
            logger.warning("No hay datos para escribir en CARTERAS.")
            return False

        try:
            # 1. Reemplazamos NaN o nulos por strings vacíos para que Google Sheets no se queje
            df = df.fillna("")

            # 2. Preparamos la matriz (Encabezados + Datos)
            encabezados = df.columns.tolist()
            valores = df.values.tolist()
            matriz = [encabezados] + valores

            # 3. Calculamos dimensiones para limpieza quirúrgica
            num_filas = len(matriz)
            num_columnas = len(encabezados)

            # Convierte índice de columna a letra (ej: 7 -> 'G')
            letra_columna = chr(64 + num_columnas)
            rango_total = f"A1:{letra_columna}"  # Ej: A1:G

            # Limpiamos SOLAMENTE de la A a la G en toda su longitud.
            # Las columnas H en adelante no se tocan.
            self.sheet.batch_clear([rango_total])

            # 4. Escribimos la matriz nueva
            rango_escritura = f"A1:{letra_columna}{num_filas}"
            self.sheet.update(range_name=rango_escritura, values=matriz)

            logger.info(f"✅ Excel CARTERAS actualizado: {num_filas - 1} registros.")
            return True

        except Exception as e:
            logger.error(f"Error escribiendo en CARTERAS: {e}")
            self.conectar()
            return False