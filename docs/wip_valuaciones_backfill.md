# WIP — Valuaciones / Backfill / Precios (2026-05-15/16)

Estado del trabajo de corrección de `Valuaciones.AuM`. Scratch — retomar
desde acá.

## El problema raíz: descalce de fechas (T+2)

El job de AuM (`jobs/aum.py` cron diario y `jobs/aum_backfill.py`) pedía a
Aunesa `posicionValuada` con `desde = T+2 hábil`. **Aunesa con `desde=X`
devuelve la posición al cierre del día PREVIO a X.** El T+2 traía la
posición proyectada a fecha de liquidación, no la del cierre real del
día → distorsión, peor a fin de mes (depósitos de sueldos sin liquidar).

- **FIX aplicado**: `aum_backfill.py` ahora pide `desde = fecha + 1 día`
  (commit `2f751f8`). Para ver el cierre del 30/06 → pide `01/07`.
- **PENDIENTE**: el cron diario `jobs/aum.py` SIGUE usando `fecha_t2()`
  (T+2). NO se cambió. Decidir si también va a `fecha + 1`.

## El problema de precios

Los precios de las posiciones venían mal de Aunesa → valuación mal. El
usuario pasa un Excel de precios correctos por mes (`docs/<MMDD>.xlsx`),
se genera `docs/precios_<MMDD>.json` (`{unidad: precio}`), y
`scripts/fix_precios_aum.py` reemplaza el campo `precio` en
`Valuaciones.AuM` y recalcula `valuacion`.

JSONs generados y pusheados en `docs/` (los `.xlsx` NO se pushean):
`precios_3006` (jun), `3107` (jul), `3108` (ago), `3009` (sep),
`3110` (oct), `3011` (nov), `3112` (dic), `3101` (ene-26), `2802` (feb-26),
`3103` (mar-26 — 761 claves, generado del Excel 2026-05-16, commit `a90c35b`).

- `Max Dinamico II` (`[4318]`, `[4319]`): el precio correcto es el del
  Excel (~1,46 A / ~1,49 B). Hubo idas y vueltas (1400, sacarlo) — quedó
  restaurado al del Excel en `precios_3006.json`.

## Bug LELIQ (resuelto)

`[9327] D16E6`, tipoTitulo `"Letras de Liquidez del Banco Central"`, no
estaba en `TIPOS_DIVISOR_100` → valuación inflada x100. FIX: agregado a
`TIPOS_DIVISOR_100` en `jobs/aum.py` (commit `4d837a1`).
`scripts/fix_valuacion_tipo.py` recalcula los snapshots viejos.
PENDIENTE (decidido NO tocar por ahora): otros tipoTitulo sin /100
sospechosos — `Fideicomisos`, `ECHEQ`, `Pagarés` (12k docs), `Cupones`.

## Scripts (en `scripts/`)

- `fix_precios_aum.py` — corrige precios de un snapshot desde un JSON.
  Flags `--snapshot --precios --cuenta --apply`. Acumula faltantes en
  `docs/precios_faltantes.json`.
- `fix_valuacion_tipo.py` — recalcula valuación de un tipoTitulo (default
  LELIQ).
- `delete_snapshot_aum.py` — borra un snapshot de `Valuaciones.AuM`.
- `backfill_2025.py` — rehace snapshots jul-dic 2025: delete + backfill
  (`--solo-aum`) + fix de precios, en orden.
- `backfill_2026.py` — ene, feb (con fix) + marzo (`2026-03-31`, sin fix
  en su corrida — ya hay Excel; el fix de marzo se corre aparte con
  `fix_precios_aum --precios docs/precios_3103.json --snapshot 2026-03-31`).
- `backfill_meses_cuenta.py` — backfill + fix por cuenta(s) puntual(es).
- `diag_*.py` — `diag_descalce_flujo_aum`, `diag_unidad_aum`,
  `diag_aunesa_posicion_cruda` (pide a Aunesa sin T+2), `diag_retorno_total`,
  `diag_assets_aum_coverage`, `diag_fci_fechas`, `diag_pivot_anual`.

`jobs/aum_backfill.py`: `--solo-aum` (cuentas de `Valuaciones.AuM`
directo), `--cuenta` no pega a `listadoCuentas` (daba 400),
`desde = fecha+1`, registra cuentas fallidas en `docs/cuentas_con_error.json`.

## Estado de los backfills

- **Junio 2025**: backfilleado (3395 registros), 9 cuentas timeout
  (`101,106,108,110,163,170,176,194,255`). FALTA re-correr el fix de
  precios de junio con `precios_3006.json` (ya corregido).
- **backfill_2025** (jul-dic): ✅ verificado OK.
- **backfill_2026** (ene-feb-mar): ✅ verificado OK 2026-05-16
  (`OK: ['2026-01-31','2026-02-28','2026-03-31']`). FALTA correr el fix
  de precios de marzo (`precios_3103.json`).
- **Retry cuentas timeout** (`retry_cuentas_error.py`, jul-oct 2025):
  ✅ corrido 2026-05-17 — los 4 meses con `backfill rc=0, fix rc=0`,
  cuenta 106 entró. `docs/cuentas_con_error.json` quedó vacío → **0
  cuentas con error en todo el rango jul/2025 → mar/2026**.
  Nota: el fix de precios de cuenta 106 (oct) no corrigió nada — sus 10
  posiciones son futuros ROFEX agro (`MAI/SOJ/TRI .ROS`) que no están en
  `precios_3110.json`. Pendiente decidir si los futuros ROFEX van al fix.

## PENDIENTE para retomar

1. ✅ HECHO (2026-05-17) — `backfill_2025` y `backfill_2026` verificados OK.
2. ✅ HECHO (2026-05-17) — cuentas timeout reintentadas con
   `retry_cuentas_error.py`; `docs/cuentas_con_error.json` quedó vacío.
3. Re-correr el fix de precios de junio (`precios_3006.json` corregido).
4. **Marzo / abril / mayo**: revisar el descalce y los precios. Marzo
   tiene snapshots DIARIOS (no mensual puro).
5. Verificar que el descalce de fechas no se siga rompiendo.
6. Revisar "precios de hygirus" (instrumento mencionado por el usuario —
   confirmar cuál es y si su precio está mal).
7. Decidir si el cron diario `jobs/aum.py` también pasa a `desde=fecha+1`.
8. Bug abierto: 500 en Estrategia → Retorno Total — correr
   `scripts.diag_retorno_total` y ver el traceback.
9. El `timestamp` de los docs de AuM es ficticio (derivado de la fecha
   del snapshot, no el real de Aunesa) — el usuario lo marcó como
   problema a revisar.
