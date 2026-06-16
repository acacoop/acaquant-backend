# jobs/ — contexto del subdirectorio

Batch / cron. El `CLAUDE.md` raíz tiene lo project-wide; `deploy/crontab.txt`
es la fuente de verdad de los horarios. `jobs/` usa `core/` + `quant/`, no
importa de `api/` (excepción puntual: jobs de precompute que reusan un
service — ver `pnl_totales_precompute` / `consolidado_cuentas`).

## Filtros de exclusión del AuM

`jobs/_aum_filters.py` define qué se excluye al persistir el snapshot diario:

1. `unidad == "USDL"` (cash USD link).
2. `cuenta` o `unidad` con `OTC` o `CDC` (case-insensitive substring).
3. `id_cuenta` ∈ `CuentasAPI.ContrapartesAPI.id_cuenta` (FCI / sociedades
   gerentes — match por id, no por nombre, los formatos difieren).
4. `cuenta` contiene como palabra completa un nombre de
   `CashFlow.Contrapartes.contraparte` (ADCAP, LOMBARD, etc. — `\bNOMBRE\b`
   case-insensitive). Cubre cuentas que se escapan de la regla 3.
5. `unidad == "ARS"` para `[100]` y `[101]` (cash de cuentas propias).

Aplica al cron diario (`jobs/aum.py`) y al cleanup retroactivo
(`scripts/cleanup_aum_excluidos.py`). Las reglas 3 y 4 viven en BD — el
equipo edita Contrapartes y se respeta solo en el próximo run.

**Cuenta 255** además se excluye SOLO de la **vista** AuM (no de la
persistencia) — `_EXCLUDED_FROM_AUM_VIEW = {"255"}` en
`api/services/portfolio.py`. Sigue capturándose para verla individualmente.

## Reglas

- `python -m jobs.<job>` desde la raíz siempre.
- Nunca `client.close()` sobre los Mongo singletons — mata el pool.
- Job nuevo: patrón `JobRunLogger`, entrada en `crontab.txt`, índices.
  Ver skill `add-job`.
