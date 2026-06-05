Servicio systemd del motor de captura ROFEX/BYMA — corre `engines.valores` (main_valores), el feed crudo de market data vía pyRofex WebSocket. Es la fuente base de precios que alimenta a todos los demás motores. Vive solo en rueda: el cron lo reinicia a 13:00 UTC y lo frena a 20:05 UTC (L-V).

Conecta con: ejecuta `engines/valores.py`; suscribe tickers vía pyRofex WS y escribe a `Trading.MarketSnapshot` / `TimeSales`; aguas abajo lo consumen motor_curvas, motor_forwards, etc. Controlado por cron (no autostart).
