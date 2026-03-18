import os
import pymongo
import logging
from datetime import datetime
from dotenv import load_dotenv

# Cargamos las variables del archivo .env (path absoluto para que funcione desde cron)
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

logger = logging.getLogger("TradingBot")

# ==========================================
# 🔑 LA LLAVE MAESTRA (OCULTA EN EL .ENV)
# ==========================================
MONGO_URI = os.getenv("MONGO_URI")


def get_mongo_client():
    """
    Devuelve la conexión universal a Atlas.
    Cualquier otro archivo del bot llama a esta función.
    """
    if not MONGO_URI:
        logger.error("CRÍTICO: No se encontró MONGO_URI en el archivo .env")
        raise ValueError("Falta MONGO_URI en el entorno")

    return pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)


# ==========================================

class MongoManager:
    def __init__(self, db_name="Opciones", collection_name="Data"):
        try:
            # Ahora usa la función segura
            self.client = get_mongo_client()
            self.db = self.client[db_name]
            self.collection = self.db[collection_name]
            self.client.server_info()
            logger.info(f"MongoDB Conectado -> DB: {db_name} | Coll: {collection_name}")
        except Exception as e:
            logger.error(f"Error conexion MongoDB: {e}")

    def guardar_operacion_unica(self, symbol, data, server_time=None, griegas=None):
        """
        Guarda una operacion individual.
        Ahora es dinamico: si viene last_timestamp en data, lo guarda.
        """
        try:
            registro = {
                "timestamp": server_time if server_time else datetime.now(),
                "symbol": symbol,
                "bid": data.get('bid', 0),
                "bid_size": data.get('bid_size', 0),
                "offer": data.get('offer', 0),
                "offer_size": data.get('offer_size', 0),
                "last": data.get('last', 0),
                "last_size": data.get('last_size', 0),
                "last_timestamp": data.get('last_timestamp'),
                "strike": data.get('strike'),
                "tipo": data.get('tipo'),
                "spot": data.get('spot'),
                "open": data.get('open', 0),
                "high": data.get('high', 0),
                "low": data.get('low', 0),
                "ev": data.get('ev', 0),
            }

            if griegas and isinstance(griegas, dict):
                registro.update(griegas)

            self.collection.insert_one(registro)

        except Exception as e:
            logger.error(f"Error al guardar operacion: {e}")