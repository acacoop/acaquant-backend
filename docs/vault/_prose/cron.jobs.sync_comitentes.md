Cron L-V en 3 corridas (14/17/21 UTC) que corre `jobs.sync_comitentes`: sincroniza las cuentas comitentes desde Aunesa hacia el master `Clientes.Comitentes` (altas/bajas/cambios de datos).

Conecta con: ejecuta `jobs/sync_comitentes.py`; usa `core/aunesa.py` y escribe a `Clientes.Comitentes`, base del Tablero Comercial y la segmentación. Timeout 15m.
