Tab BOLETOS dentro de MANAGER → AUNESA, con sub-tabs FALTANTES (lista boletos sin arancel asignado, con resumen por categoría/op y detalle) y BACKFILL (dispara el matching de aranceles contra Aunesa). Filtra por rango de fechas e `id_cuenta`; excluye futuros DLR en backend.

Conecta con: pollea `GET /api/manager/aunesa/boletos/faltantes`; los datos salen de `CashFlow.NegocioMovimientos`. Solo manager.
