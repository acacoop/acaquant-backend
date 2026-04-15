import hashlib
import json
import logging
import threading
import time
from datetime import datetime

from pymongo import UpdateOne

from core.mongo import get_mongo_client

logger = logging.getLogger("SnapshotWriter")


class SnapshotWriter:
    """
    Background writer genérico para cualquier motor de trading.

    - Llama a data_fn() cada `interval` segundos.
    - Hashea el resultado con MD5: si el estado no cambió, NO escribe.
    - Cuando cambia, hace un bulk_write con UpdateOne(upsert=True) por fila:
      un solo round-trip a MongoDB sin importar cuántos activos haya.
    - Reconexión automática si falla el driver.

    Uso:
        writer = SnapshotWriter(
            db_name="Trading",
            collection_name="ArbitrageSnapshot",
            data_fn=engine.get_snapshot_data,
            key_field="asset"
        ).start()
    """

    def __init__(self, db_name, collection_name, data_fn, key_field, interval=0.5):
        self.db_name = db_name
        self.collection_name = collection_name
        self.data_fn = data_fn
        self.key_field = key_field
        self.interval = interval
        self._last_hash = None
        self._collection = None
        self._running = False

    def _get_collection(self):
        if self._collection is None:
            client = get_mongo_client()
            self._collection = client[self.db_name][self.collection_name]
        return self._collection

    def _write_loop(self):
        while self._running:
            try:
                data = self.data_fn()
                if data:
                    serialized = json.dumps(data, default=str, sort_keys=True)
                    current_hash = hashlib.md5(serialized.encode()).hexdigest()

                    if current_hash != self._last_hash:
                        ts = datetime.now()
                        ops = [
                            UpdateOne(
                                {self.key_field: row[self.key_field]},
                                {"$set": {**row, "updated_at": ts}},
                                upsert=True
                            )
                            for row in data if self.key_field in row
                        ]
                        if ops:
                            self._get_collection().bulk_write(ops, ordered=False)
                            self._last_hash = current_hash

            except Exception as e:
                logger.error(f"SnapshotWriter [{self.collection_name}] error: {e}")
                self._collection = None  # fuerza reconexión en la próxima iteración

            time.sleep(self.interval)

    def start(self):
        self._running = True
        t = threading.Thread(
            target=self._write_loop,
            daemon=True,
            name=f"SW-{self.collection_name}"
        )
        t.start()
        return self

    def stop(self):
        self._running = False
