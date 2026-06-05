Servicio systemd del motor de opciones agro — corre `engines.motor_agro_opciones`, el feed live de opciones sobre futuros de Trigo / Maíz / Soja en Rosario (calls/puts OCAFXS/OPAFXS). Vive solo en rueda (cron restart 13:00 UTC / stop 20:05 UTC, L-V).

Conecta con: ejecuta `engines/motor_agro_opciones.py`; suscribe las opciones agro vía pyRofex WS y publica precios/griegas live consumidos por `/api/derivados/agro`. Controlado por cron.
