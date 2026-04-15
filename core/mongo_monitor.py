"""mongo_monitor.py — listener de pymongo para grabar queries del dashboard.

Usa la API oficial `pymongo.monitoring.CommandListener` para capturar cada
comando (`find`, `aggregate`, `count`, `update`, etc.) con su duración. Los
registros quedan en un ring-buffer en memoria (thread-safe).

API pública:
    register()            — registra el listener global (idempotente)
    start() / stop()      — activa/desactiva la grabación
    is_recording() -> bool
    clear()               — vacía el buffer
    get_records() -> list — copia de todos los registros en memoria

Activación opcional: la grabación arranca apagada. Un toggle en el Manager
la prende cuando se quiere medir.
"""

import threading
import time
from collections import deque

from pymongo import monitoring

_MAX_RECORDS = 2000

_records: deque = deque(maxlen=_MAX_RECORDS)
_pending: dict = {}
_lock = threading.Lock()
_recording = False
_registered = False


def start() -> None:
    global _recording
    _recording = True


def stop() -> None:
    global _recording
    _recording = False


def is_recording() -> bool:
    return _recording


def clear() -> None:
    with _lock:
        _records.clear()
        _pending.clear()


def get_records() -> list[dict]:
    with _lock:
        return list(_records)


def _short(obj, maxlen: int = 140) -> str | None:
    if obj is None:
        return None
    try:
        s = repr(obj)
    except Exception:
        s = "<unrepr>"
    return s[:maxlen] + ("..." if len(s) > maxlen else "")


def _summarize_command(cmd: dict, op: str) -> tuple[str | None, str | None]:
    """Devuelve (collection, filter_repr) a partir del command document."""
    coll = cmd.get(op) if isinstance(cmd.get(op), str) else None
    filt: object | None = None
    if op == "find":
        filt = cmd.get("filter")
    elif op == "aggregate":
        pipe = cmd.get("pipeline") or []
        filt = pipe[:2]
    elif op in ("count", "countDocuments"):
        filt = cmd.get("query") or cmd.get("filter")
    elif op in ("update", "delete"):
        updates = cmd.get("updates") or cmd.get("deletes") or []
        if updates:
            filt = updates[0].get("q")
    elif op == "findAndModify":
        filt = cmd.get("query")
    elif op == "getMore":
        filt = {"batchSize": cmd.get("batchSize")}
    return coll, _short(filt)


class _Listener(monitoring.CommandListener):
    def started(self, event):
        if not _recording:
            return
        coll, filt = _summarize_command(event.command, event.command_name)
        with _lock:
            _pending[event.request_id] = {
                "ts":     time.time(),
                "db":     event.database_name,
                "coll":   coll,
                "op":     event.command_name,
                "filter": filt,
            }

    def _finish(self, event, status: str):
        if not _recording:
            return
        with _lock:
            info = _pending.pop(event.request_id, None)
            if info is None:
                return
            info["ms"]     = event.duration_micros / 1000.0
            info["status"] = status
            _records.append(info)

    def succeeded(self, event):
        self._finish(event, "ok")

    def failed(self, event):
        self._finish(event, "fail")


def register() -> None:
    """Registra el listener globalmente. Debe llamarse antes de crear el MongoClient."""
    global _registered
    if _registered:
        return
    monitoring.register(_Listener())
    _registered = True
