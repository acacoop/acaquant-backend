Router de la vista Operaciones (back-office de la mesa). Sirve el flujo de contrapartes (MesaAPI), los movimientos (FlujosAPI) y la "vista de negocio del día" sobre NegocioMovimientos, con filtros por contraparte/moneda/segmento y por tipo de cuenta. Aplica scope de grupos para que cada usuario vea solo sus cuentas y excluye futuros del agregado de negocio.

Conecta con: lee `CashFlow.NegocioMovimientos` + colecciones `*API` (Mesa/Flujos); usa `_cuentas_filter`, `_grupos_scope` y `_negocio_futuros`; gate RBAC módulo `operaciones`; lo consume la vista /operaciones del frontend.
