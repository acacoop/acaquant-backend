Servicio systemd del motor de curvas — corre `engines.curvas`, que enriquece en tiempo real cada trade de renta fija con TEA/TNA/Duration. Vive solo en rueda (cron restart 13:00 UTC / stop 20:05 UTC, L-V).

Conecta con: ejecuta `engines/curvas.py`; lee `Trading.Curvas` (shape de flujos) + market data live, escribe métricas a `Trading.MarketSnapshot.metrics` (TEA leída luego por forwards/breakevens/services). Controlado por cron.
