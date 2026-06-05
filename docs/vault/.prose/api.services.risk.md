Servicio RISK: datos de cuenta del broker (saldos, posiciones, márgenes) vía los endpoints REST `rest/risk/...` de pyRofex. Funciones puras con cache 3-5s para no machacar al broker bajo polling de la UI. Parsea sub-bloques por rueda (CI/24hs) y distingue los múltiples tipos de USD del broker (expone MEP y ARS).

Conecta con: usa la sesión pyRofex compartida de `core.rofex_orders_session` (misma que `ordenes`); no toca Mongo. Lo invoca el router `/api/risk`, gateado por módulo `operaciones` (admin+trader, no sales).
