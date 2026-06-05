Servicio systemd del motor de breakevens — corre `engines.breakevens`, que calcula la inflación implícita CER/Lecap en tiempo real (método Buscar Objetivo, match mismo vto Lecap↔CER). Vive solo en rueda (cron restart 13:00 UTC / stop 20:05 UTC, L-V).

Conecta con: ejecuta `engines/breakevens.py`; lee TEA/precios de `Trading.MarketSnapshot` + `Trading.Curvas` (cer_emision, flujos); persiste breakevens live consumidos por la API. Controlado por cron.
