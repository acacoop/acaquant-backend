Configuración del servicio: lee su propio set de env vars desde el `.env` del Droplet, a propósito sin importar el `config.py` de la mesa (proceso aparte, mínimo). Define `PARTNER_MONGO_URI` (usuario Mongo read-only sobre `ACAPortfolio`), `PARTNER_JWT_SECRET` (firma de los JWT; sin esto el servicio no arranca) y `PARTNER_TOKEN_TTL_MIN` (vida del token, default 60). Fija `DB_NAME = "ACAPortfolio"`.

Conecta con: lo importan `partner_api.db` (URI + nombre de base), `partner_api.security` (secret JWT + TTL) y `partner_api.main` (chequeo de env vars al arrancar).
