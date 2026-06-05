Servicio systemd del motor de futuros agro — corre `engines.motor_agro`, el feed live de futuros de Trigo / Maíz / Soja en Rosario (contratos FXXXSX). Vive solo en rueda (cron restart 13:00 UTC / stop 20:05 UTC, L-V).

Conecta con: ejecuta `engines/motor_agro.py`; suscribe los futuros agro vía pyRofex WS y publica precios live consumidos por el módulo `/api/derivados/agro` (Pase Agro, estrategias). Controlado por cron.
