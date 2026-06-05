Grafica la curva completa de futuros DLR (Dólar A3500) — precio vs días al vencimiento — como scatter + línea. Resaltea el contrato seleccionado en el watchlist; cualquier ticker "DLR/*" o "FUTUROS ROFEX" muestra la curva entera. Pollea cada 5s.

Conecta con: hace fetch a `/api/futuros-dlr` (datos de `Trading.FuturosDLRSnapshot` que alimenta `motor_futuros_dlr`). Lo usa `home-view` como chart alternativo a TradingView cuando el ticker es un DLR.
