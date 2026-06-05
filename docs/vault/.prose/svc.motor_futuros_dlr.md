Servicio systemd del motor de futuros DLR — corre `engines.futuros_dlr`, que arma la curva de futuros de Dólar A3500 (outrights single-leg) con su tasa implícita. Vive solo en rueda (cron restart 13:00 UTC / stop 20:05 UTC, L-V).

Conecta con: ejecuta `engines/futuros_dlr.py`; suscribe los contratos DLR vía pyRofex WS y escribe a `Trading.FuturosDLRSnapshot`; el job `cleanup_futuros_dlr` purga contratos vencidos. Controlado por cron.
