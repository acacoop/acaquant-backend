Script one-shot que agrega/actualiza el campo `segmento` en las contrapartes según reglas simples sobre denominación/contraparte: "FCI" → Fondos, "ALYC" → ALYC, "BANCO" → Bancos. Las que no matchean quedan para asignación manual interactiva por consola (input()).

Conecta con: lee y escribe `CashFlow.Contrapartes` (campo `segmento`). Es utilitario de mantenimiento manual, no un cron (usa input interactivo).
