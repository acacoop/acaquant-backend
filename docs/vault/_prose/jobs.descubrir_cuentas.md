Descubre qué cuentas autoriza el broker para el user master: barre un rango de IDs (default 1-12000) llamando a pyRofex `get_account_report` por cada una y guarda las autorizadas con su snapshot de saldos (ARS/USD disponible, n° posiciones, flag activa). Las no autorizadas se ignoran en silencio.

Pensado como backfill (1ra corrida ~40 min) y luego 1×/día para detectar cuentas nuevas. 100% read-only contra el broker; idempotente (upsert por account_id); con timeout de socket y cota de tiempo total para no colgarse.

Conecta con: pyRofex (broker, sesión LIVE), escribe `Operaciones.AccountsDescubiertas`. Creds desde `.env` (ROFEX_USER/PASSWORD/ACCOUNT).
