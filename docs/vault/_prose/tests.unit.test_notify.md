Valida el notificador de alertas Telegram (`core/notify.py`): que sea no-op sin config (sin token/chat), que nunca propague un fallo de red al caller (devuelve False, no lanza) y que la alerta de fallo de job lleve metadata segura (nombre, status, referencia a Manager.JobRuns) con el último error truncado a 180 chars.

Conecta con: importa `core.notify`; red de seguridad de las notificaciones operativas que disparan los crons vía `core.job_runs`.
