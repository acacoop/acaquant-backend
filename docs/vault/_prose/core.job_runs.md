Context manager `JobRunLogger("tipo")` para instrumentar los cron jobs: captura stdout, contadores estructurados (`set_stat`), errores non-fatal, duración y estado (ok/partial/error), y al salir persiste un doc resumen en `Manager.JobRuns` sin perder los logs de archivo. Guarda las últimas ~200 líneas de log.

Conecta con: escribe `Manager.JobRuns` (TTL creado en `scripts/crear_indices.py`); lo envuelven los jobs batch (aum, carteras, negocio_movimientos, etc.); lo lee el panel de `/manager` (status e historial de jobs).
