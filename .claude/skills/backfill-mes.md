---
name: backfill-mes
description: Rehacer el snapshot de AuM de un mes (cierre EOM) — delete + backfill + fix de precios. Usar cuando hay que recalcular Valuaciones.AuM de un mes histórico, corregir precios de un cierre, o reintentar cuentas que quedaron afuera de un backfill anterior.
---

# Backfill de un mes de AuM

Playbook para rehacer el snapshot mensual de `Valuaciones.AuM`. Mismo flujo
que usan `scripts/backfill_2025.py` y `backfill_2026.py`.

## Cuándo aplica
- Recalcular el cierre EOM de un mes (datos viejos, precios mal, descalce T+2).
- Corregir los precios de un cierre ya backfilleado.
- Reintentar cuentas que un backfill anterior dejó afuera (timeout / error).

## Flujo por mes — EN ORDEN, una cosa por vez

1. **DELETE** del snapshot:
   `python -m scripts.delete_snapshot_aum --snapshot <YYYY-MM-DD> --apply`
2. **BACKFILL**:
   `python -m jobs.aum_backfill <YYYY-MM-DD> --solo-aum`
   - `aum_backfill` pide a Aunesa `desde = fecha + 1 día` (Aunesa con `desde=X`
     devuelve la posición al cierre del día PREVIO a X).
   - Las cuentas que timeotean quedan en `docs/cuentas_con_error.json`.
3. **FIX DE PRECIOS** (solo si el usuario pasó el Excel del mes):
   `python -m scripts.fix_precios_aum --precios docs/precios_<MMDD>.json --snapshot <YYYY-MM-DD> --apply`
   - El JSON `{unidad: precio}` se arma a mano desde el Excel (`docs/<MMDD>.xlsx`).
     Los `.xlsx` NO se versionan; los `precios_<MMDD>.json` SÍ.

## Cuentas puntuales (reintento)
- Backfill de cuentas específicas: sumar `--cuenta <ids> --workers 1 --timeout 360 --retries 5`.
- Fix de precios de esas cuentas: sumar `--cuenta <ids>`.
- Para descubrir qué cuentas faltan en un snapshot:
  `python -m scripts.diag_cuentas_faltantes`
  (compara membresía contra el snapshot anterior ∩ posterior).

## Reglas
- **REGLA #0**: todo se entrega como código en `scripts/` y lo corre el
  usuario en el Droplet — nada de comandos sueltos para copiar/pegar. Para
  varios meses encadenados, escribir un `scripts/backfill_<x>.py` o
  `scripts/retry_<x>.py` (ver `backfill_2026.py` / `retry_cuentas_2026.py`
  de patrón: por mes, DELETE → BACKFILL → FIX, con RESUMEN al final).
- Después de tocar AuM, la vista TOTALES lee `Valuaciones.PnLTotalesCache`
  y POR CUENTA lee `Valuaciones.ConsolidadoCuentas` — correr los jobs
  `jobs.pnl_totales_precompute` / `jobs.consolidado_cuentas` para refrescar.
- Cerrar verificando con `scripts.diag_cuentas_faltantes` que no queden huecos.
