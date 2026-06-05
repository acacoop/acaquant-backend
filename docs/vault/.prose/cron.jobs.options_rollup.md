Cron L-V a 20:15 UTC (17:15 ART) que corre `jobs.options_rollup`: rollup diario de la cadena de opciones, pasando `Opciones.Data` (live intra-día) a `Opciones.DataHistorica`.

Conecta con: ejecuta `jobs/options_rollup.py`; lee `Opciones.Data` (escrita por motor_options) y escribe a `Opciones.DataHistorica`. Timeout 15m.
