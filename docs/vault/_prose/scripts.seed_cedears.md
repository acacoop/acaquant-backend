Seed idempotente que upsertea el master de CEDEARs en Trading.Cedears: metadata categórica (nombre, sector, industria, región, país) por ticker. Es el espejo conceptual de Trading.Curvas pero para acciones vía CEDEAR; no toca market data, que vive aparte en Trading.CedearsSnapshot (escrito por engines/motor_cedears.py cada 1s). Alimenta la categorización del Scanner de Renta Variable. Uso: `python -m scripts.seed_cedears [--dry-run]`.

Conecta con: Trading.Cedears (escribe), engines.motor_cedears + Trading.CedearsSnapshot (market data), api.services.scanner (lo consume).
