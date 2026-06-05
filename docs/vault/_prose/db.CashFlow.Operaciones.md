Operaciones normalizadas e ingestadas desde los informes de Aunesa, en la base `CashFlow`. Es el registro canónico de operaciones para análisis y backfill (carga por CSV o por job).

Conecta con: la escriben `jobs/operaciones_informes.py` y `api/services/operaciones_informes.py` (normalización + ingesta idempotente); backfill manual por CSV vía `api/routers/manager/operaciones.py`. La leen routers de operaciones/operativa y el scoping de grupos (`_grupos_scope.py`).
