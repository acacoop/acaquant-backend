Conexión Mongo del servicio: singleton thread-safe a la base `ACAPortfolio`. Usa `PARTNER_MONGO_URI`, que apunta a un usuario Mongo con permiso de SOLO lectura scopeado únicamente a esa base — así, aunque el proceso se comprometa entero, no puede leer otras bases ni escribir. Pool chico (maxPoolSize=5) con compresión.

Conecta con: lee `PARTNER_MONGO_URI` y `DB_NAME` de `partner_api.settings`; expone `get_db()` que usan `partner_api.auth` (colección `ApiUsers`) y `partner_api.routes` (colección `Cartera`).
