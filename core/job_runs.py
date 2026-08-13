"""Context manager para registrar runs de jobs automáticos en manager.job_runs (SQL).

Cada cron job puede envolverse en `with JobRunLogger("tipo") as run:` para
capturar stdout, stats estructurados, duración, errores y persistir una fila
al salir — sin perder los logs de archivo que ya existen.

Escribe `manager.job_runs` (Postgres). Es el único writer del historial.

Esquema de la fila en manager.job_runs (columnas materializadas + `data` jsonb):
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

La retención de manager.job_runs la aplica el cleanup de Postgres (no TTL nativo).
"""
from __future__ import annotations

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

        # SQL-ONLY (decomiso Mongo 2026-06-28): manager.job_runs es la fuente de verdad.
        # PK run_id = uuid SQL-native (antes era str(ObjectId) del insert Mongo). Best-effort:
        # lo usan TODOS los jobs/motores → un fallo al registrar NUNCA puede tumbar el job.
        try:
            from uuid import uuid4

            from core.pg_mirror import doc_iso, write_native
            # started_at/finished_at son AWARE UTC → la columna timestamptz no corre la hora.
            write_native("manager.job_runs", ["run_id"], [{
                "run_id":      str(uuid4()),
                "tipo":        self.tipo,
                "started_at":  self._started,
                "finished_at": finished,
                "status":      status,
                "data":        doc_iso(doc),
            }])
        except Exception as e:
            print(f"⚠️  JobRunLogger: no se pudo persistir run en SQL: {e}", flush=True)

        return False  # re-raise si hubo excepción
