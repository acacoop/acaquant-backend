Cron L-V a 22:30 UTC (19:30 ART) que corre `jobs.actividad_mensual`: arma el snapshot mensual de cuentas activas (cuántas operaron en el mes) para el seguimiento comercial.

Conecta con: ejecuta `jobs/actividad_mensual.py`; lee actividad de `CashFlow.NegocioMovimientos` y persiste el agregado mensual a Mongo. Timeout 15m.
