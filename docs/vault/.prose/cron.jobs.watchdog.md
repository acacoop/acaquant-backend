Cron cada 5 minutos (todos los días) que corre `jobs.watchdog`: escanea `ps` buscando jobs python que lleven más tiempo que su presupuesto y avisa por Telegram. Es la visibilidad que faltó en el incidente de CPU del 2026-06-03; solo alerta (matar lo hace el timeout de run_job.sh).

Conecta con: ejecuta `jobs/watchdog.py`; lee la tabla de procesos del Droplet y notifica vía `core/notify.py` (Telegram). Timeout propio 2m.
