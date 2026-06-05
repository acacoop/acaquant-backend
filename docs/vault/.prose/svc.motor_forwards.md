Servicio systemd del motor de forwards — corre `engines.forwards`, que calcula la matriz de tasas forward implícitas entre instrumentos en tiempo real. Vive solo en rueda (cron restart 13:00 UTC / stop 20:05 UTC, L-V).

Conecta con: ejecuta `engines/forwards.py`; lee la última TEA por ticker desde `Trading.MarketSnapshot.metrics.TEA` (escrita por motor_curvas) y publica la matriz forward live. Controlado por cron.
