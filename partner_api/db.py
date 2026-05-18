"""Conexión Mongo del partner_api — singleton read-only a la base `Partner`.

Usa `PARTNER_MONGO_URI`, que DEBE apuntar a un usuario Mongo con permiso
`read` SOLO sobre la base `Partner`. Así, aunque este proceso se
comprometa por completo, no hay forma de leer otras bases ni de escribir.
"""
from __future__ import annotations

import threading

import pymongo

from partner_api.settings import DB_NAME, PARTNER_MONGO_URI

_client: pymongo.MongoClient | None = None
_lock = threading.Lock()


def get_db():
    """Devuelve el handle a la base `Partner` (singleton thread-safe)."""
    global _client
    if not PARTNER_MONGO_URI:
        raise RuntimeError("Falta PARTNER_MONGO_URI en el entorno")
    if _client is None:
        with _lock:
            if _client is None:
                _client = pymongo.MongoClient(
                    PARTNER_MONGO_URI,
                    serverSelectionTimeoutMS=30000,
                    maxPoolSize=5,
                    compressors="zstd,snappy,zlib",
                )
    return _client[DB_NAME]
