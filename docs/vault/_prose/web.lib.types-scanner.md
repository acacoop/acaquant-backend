Tipos TypeScript del módulo Scanner (Renta Variable). Define los contratos de las respuestas del backend: `CclLive` (KPI live de CCL para el shell), `PivotData`/`PivotFrame`/`PivotLevels` (pivot points en 4 timeframes sobre el subyacente USD) y los docs del scanner de CEDEARs (join de master categórico + snapshot live).

Conecta con: refleja lo que sirven `GET /api/scanner/*` (`api.routers.scanner` + `api.services.scanner`), que cruzan `Trading.Cedears`/`CedearsSnapshot` (vía `engines.motor_cedears`) y los pivots de `quant.pivot_points`; lo importan las vistas del Scanner en el frontend.
