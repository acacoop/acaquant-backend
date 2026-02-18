import pymongo
import logging
from datetime import datetime

logger = logging.getLogger("TradingBot")

class MongoManager:
    def __init__(self, db_name="Opciones", collection_name="Data"):
        try:
            self.client = pymongo.MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=5000)
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
                # --- AGREGADO: Guardamos el timestamp del ultimo trade ---
                "last_timestamp": data.get('last_timestamp')
            }

            if griegas and isinstance(griegas, dict):
                registro.update(griegas)

            self.collection.insert_one(registro)
        except Exception as e:
            logger.error(f"Error al insertar trade en Mongo: {e}")