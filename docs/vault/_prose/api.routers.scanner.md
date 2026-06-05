Router de la vista Scanner del módulo Renta Variable. `GET /api/scanner/cedears` devuelve la lista de CEDEARs activos cruzando el master con el snapshot live (underlying, ratio, sector, last/open/high/low, intraday% y vs-1D en ARS/USD); `/ccl` da el CCL live + variación 1D para el KPI del shell.

Conecta con: delega en `api.services.scanner` y `api.services.rv_motor` (leen `Trading.CedearsSnapshot` + `Trading.PreciosAcciones`, alimentadas por `engines.motor_cedears`); gate RBAC módulo `renta-variable`; lo consume /renta-variable del frontend.
