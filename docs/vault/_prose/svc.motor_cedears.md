Servicio systemd del motor de CEDEARs — corre `engines.motor_cedears`, el feed live de precios de CEDEARs vía pyRofex WS. Alimenta el Scanner de Renta Variable. Vive solo en rueda (cron restart 13:00 UTC / stop 20:05 UTC, L-V).

Conecta con: ejecuta `engines/motor_cedears.py`; escribe los precios live de CEDEARs a Mongo; el Scanner (`api/services/scanner.py`) y el job `adr_live` (precio USD del subyacente) lo complementan. Controlado por cron.
