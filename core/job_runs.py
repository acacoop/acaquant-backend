"""Context manager para registrar runs de jobs automáticos en Manager.JobRuns.

Cada cron job puede envolverse en `with JobRunLogger("tipo") as run:` para
capturar stdout, stats estructurados, duración, errores y persistir un doc
al salir — sin perder los logs de archivo que ya existen.

Esquema del doc en Manager.JobRuns:
    {
        tipo:         str,                # "carteras", "aum", etc.
        started_at:   datetime (UTC),
        finished_at:  datetime (UTC),
        elapsed_s:    float,
        status:       "ok" | "partial" | "error",
        stats:        dict,               # contadores estructurados, libre
        errors:       list[str],          # mensajes non-fatal acumulados
        log:          list[str],          # últimas ~200 líneas
    }

El índice TTL en Manager.JobRuns se crea en scripts/crear_indices.py.
"""
from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import Any

_MAX_LOG_LINES = 200


class JobRunLogger:
    def __init__(self, tipo: str):
        self.tipo = tipo
        self.stats: dict[str, Any] = {}
        self.errors: list[str] = []
        self._log: list[str] = []
        self._started: datetime | None = None
        self._start_perf: float = 0.0

    def __enter__(self) -> JobRunLogger:
        self._started = datetime.now(UTC)
        self._start_perf = time.perf_counter()
        return self

    def log(self, msg: str) -> None:
        """Imprime a stdout (para que siga en el log de archivo) y acumula."""
        print(msg, flush=True)
        ts = datetime.now(UTC).strftime("%H:%M:%S")
        self._log.append(f"{ts} {msg}")

    def error(self, msg: str) -> None:
        self.errors.append(msg)
        self.log(f"⚠️  {msg}")

    def set_stat(self, key: str, value: Any) -> None:
        self.stats[key] = value

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        finished = datetime.now(UTC)
        elapsed = time.perf_counter() - self._start_perf

        if exc_type is not None:
            status = "error"
            self.errors.append(f"{exc_type.__name__}: {exc_val}")
        elif self.errors:
            status = "partial"
        else:
            status = "ok"

        doc = {
            "tipo":        self.tipo,
            "started_at":  self._started,
            "finished_at": finished,
            "elapsed_s":   round(elapsed, 2),
            "status":      status,
            "stats":       self.stats,
            "errors":      self.errors,
            "log":         self._log[-_MAX_LOG_LINES:],
        }

        run_id = None
        try:
            from core.mongo import get_mongo_client
            res = get_mongo_client()["Manager"]["JobRuns"].insert_one(doc)
            run_id = str(res.inserted_id)
        except Exception as e:
            # No queremos que un fallo al registrar tire abajo el job.
            print(f"⚠️  JobRunLogger: no se pudo persistir run: {e}", flush=True)

        # Dual-write best-effort a SQL (manager.job_runs), gateado por MANAGER_SQL_WRITE.
        # Mongo es la fuente de verdad; si SQL falla NO debe tumbar el job (try/except).
        if run_id and os.getenv("MANAGER_SQL_WRITE") == "1":
            try:
                from core.pg_mirror import doc_iso, write_native
                # doc.pop("_id") no hace falta: doc_iso ignora el ObjectId (cae a str
                # dentro del jsonb vía json.dumps(default=str)). started_at/finished_at
                # son AWARE UTC → la columna timestamptz no corre la hora.
                write_native("manager.job_runs", ["run_id"], [{
                    "run_id":      run_id,
                    "tipo":        self.tipo,
                    "started_at":  self._started,
                    "finished_at": finished,
                    "status":      status,
                    "data":        doc_iso({k: v for k, v in doc.items() if k != "_id"}),
                }])
            except Exception as e:
                print(f"⚠️  JobRunLogger: dual-write SQL falló: {e}", flush=True)

        # Alerta operativa si el job no terminó OK. Solo metadata (el detalle
        # ya quedó en Manager.JobRuns). No-op si Telegram no está configurado;
        # nunca tira excepción (no debe tumbar el job).
        if status in ("error", "partial"):
            try:
                from core.notify import notify_job_failure
                notify_job_failure(
                    self.tipo,
                    status,
                    elapsed_s=round(elapsed, 2),
                    n_errors=len(self.errors),
                    last_error=self.errors[-1] if self.errors else None,
                )
            except Exception as e:
                print(f"⚠️  JobRunLogger: no se pudo alertar: {e}", flush=True)

        return False  # re-raise si hubo excepción
