import logging
import os
import threading
from datetime import datetime

import pymongo
from dotenv import load_dotenv
from pymongo import ReadPreference

from core import mongo_monitor

mongo_monitor.register()

# Cargamos las variables del archivo .env en la raíz del proyecto
# (este módulo vive en core/, por eso subimos un nivel)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_PROJECT_ROOT, '.env'))

logger = logging.getLogger("TradingBot")

# ==========================================
# 🔑 LA LLAVE MAESTRA (OCULTA EN EL .ENV)
# ==========================================
MONGO_URI      = os.getenv("MONGO_URI")
MONGO_URI_READ = os.getenv("MONGO_URI_READ")

# Singleton thread-safe: un solo MongoClient compartido por todo el proceso.
# MongoClient maneja internamente el connection pool y es thread-safe.
_client:      pymongo.MongoClient | None = None
_client_read: pymongo.MongoClient | None = None
_client_lock:      threading.Lock = threading.Lock()
_client_read_lock: threading.Lock = threading.Lock()


def get_mongo_client() -> pymongo.MongoClient:
    """Devuelve el cliente singleton a MongoDB Atlas.

    Crea la conexión la primera vez; las llamadas siguientes reutilizan el
    mismo pool. pymongo se encarga de detectar desconexiones y reconectar
    automáticamente, así que no hace falta un health-check en cada llamada
    (el ping costaba ~180ms por invocación y explotaba la latencia de la API).
    """
    global _client
    if not MONGO_URI:
        logger.error("CRÍTICO: No se encontró MONGO_URI en el archivo .env")
        raise ValueError("Falta MONGO_URI en el entorno")

    if _client is None:
        with _client_lock:
            if _client is None:  # double-checked locking
                _client = pymongo.MongoClient(
                    MONGO_URI,
                    serverSelectionTimeoutMS=30000,
                    maxPoolSize=20,
                    compressors="zstd,snappy,zlib",
                )
    return _client


def get_mongo_client_read() -> pymongo.MongoClient:
    """Devuelve el cliente singleton read-only a MongoDB Atlas.

    Usa MONGO_URI_READ si está definido; si no, cae a MONGO_URI. Igual que
    el cliente RW, delega la detección de desconexiones al driver y evita
    el ping por llamada.
    """
    global _client_read
    uri = MONGO_URI_READ or MONGO_URI
    if not uri:
        raise ValueError("Falta MONGO_URI en el entorno")

    if _client_read is None:
        with _client_read_lock:
            if _client_read is None:
                # secondaryPreferred: si no hay primary (elecciones, upgrades)
                # la API sigue leyendo de un secundario. En M10 el lag es
                # típicamente <1s → aceptable para la mayoría de endpoints.
                _client_read = pymongo.MongoClient(
                    uri,
                    serverSelectionTimeoutMS=30000,
                    maxPoolSize=20,
                    compressors="zstd,snappy,zlib",
                    read_preference=ReadPreference.SECONDARY_PREFERRED,
                )
    return _client_read


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

    def guardar_snapshot_opciones(self, symbol, data, griegas=None):
        """
        Upsert del estado más reciente de cada opción.
        Se llama cada ~2 segundos con el estado completo de RAM.
        Permite que Streamlit vea bid/offer/high/low/ev en tiempo real.
        """
        try:
            doc = {
                "updated_at": datetime.now(),
                "symbol": symbol,
                "bid":    data.get('bid', 0),
                "offer":  data.get('offer', 0),
                "last":   data.get('last', 0),
                "open":   data.get('open', 0),
                "high":   data.get('high', 0),
                "low":    data.get('low', 0),
                "ev":     data.get('ev', 0),
                "strike": data.get('strike'),
                "tipo":   data.get('tipo'),
                "spot":   data.get('spot', 0),
            }
            if griegas and isinstance(griegas, dict):
                doc.update(griegas)
            self.db["OptionsSnapshot"].update_one(
                {"symbol": symbol},
                {"$set": doc},
                upsert=True
            )
        except Exception as e:
            logger.error(f"Error al guardar snapshot: {e}")