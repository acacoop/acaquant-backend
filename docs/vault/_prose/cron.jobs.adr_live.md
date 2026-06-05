Cron cada 15 minutos en horario US (13:30-20:00 UTC, L-V) que corre `jobs.adr_live`: trae el precio USD live del subyacente de cada CEDEAR vía Finnhub /quote, para comparar contra el CEDEAR local.

Conecta con: ejecuta `jobs/adr_live.py`; usa `core/finnhub.py`; persiste el precio del ADR/subyacente a Mongo, consumido por el Scanner CEDEARs junto a motor_cedears. Timeout 5m.
