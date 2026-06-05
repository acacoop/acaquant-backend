Normaliza e ingiere operaciones a `CashFlow.Operaciones`, la fuente de verdad de operaciones (boletos de la API "informes" de Aunesa, que no incluye movimientos administrativos ni FCI bilateral). Mapea headers heterogéneos (API e histórico en Excel) a 10 campos canónicos y persiste idempotente: un boleto = un documento, con índice único sobre `boleto`. Service puro, sin FastAPI.

Conecta con: escribe en `CashFlow.Operaciones`; usa `api.services._mep` para pesificar; lo invoca `api/routers/manager/operaciones.py` (backfill por CSV desde la UI) y a futuro el job de ingesta diaria.
