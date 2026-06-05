Backfill multi-mes (one-shot) que rehace los snapshots de AuM de 2025 (jul..dic) que habían quedado mal por el T+2 del cron. Por cada cierre de mes y en orden: DELETE del snapshot viejo → BACKFILL pidiendo a Aunesa → CORRECCIÓN de precios; recién ahí pasa al siguiente. Las cuentas que timeotean quedan en `docs/cuentas_con_error.json`.
Se corre con `python -m scripts.backfill_2025` (orquesta vía subprocess `aum_backfill` y `fix_precios_aum`).
Conecta con: escribe `Valuaciones.AuM`; encadena scripts `aum_backfill` y `fix_precios_aum`; fuente de precios en `docs/precios_*.json`.
