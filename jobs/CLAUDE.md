# jobs/ — contexto del subdirectorio

Batch / cron. El `CLAUDE.md` raíz tiene lo project-wide; `deploy/crontab.txt`
es la fuente de verdad de los horarios. `jobs/` usa `core/` + `quant/`, no
importa de `api/` (excepción puntual: jobs de precompute que reusan un
service — ver `pnl_totales_precompute` / `consolidado_cuentas`).

## Filtros de exclusión del AuM

`jobs/_aum_filters.py::is_excluded()` define qué NO cuenta como AuM. Lo consumen
**TRES** lugares, y dos NO son jobs: `api/services/import_tenencia_sql.py` y
`api/services/sin_operador.py` además de **`jobs/portafolio_backfill.py`** (el
writer diario SQL, `--diario`,
11:00 UTC L-V): por cada fila setea la columna `aum` ('si'/'no') en
`portafolio.tenencia`. (El viejo cron `jobs/aum.py::run` ya no existe;
`aum.py` sobrevive solo como helpers Aunesa.) Reglas:

1. `unidad == "USDL"` (cash USD link).
2. `cuenta` o `unidad` con `OTC` o `CDC` (case-insensitive substring).
3. `id_cuenta` ∈ **SQL `clientes.contrapartes.id_cuenta`** (FCI / sociedades
   gerentes — match por id, no por nombre, los formatos difieren).
4. `cuenta` contiene como palabra completa un nombre de **SQL
   `clientes.contrapartes.contraparte`** (ADCAP, LOMBARD, etc. — `\bNOMBRE\b`
   case-insensitive). Cubre cuentas que se escapan de la regla 3.
5. `unidad == "ARS"` para `[100]` y `[101]` (cash de cuentas propias).

Las reglas 3 y 4 viven en BD (SQL `contrapartes`) — el equipo edita
Contrapartes y se respeta solo en el próximo run.

**Cuenta 255**: `portfolio_sql.py` la incluye en el AuM. Sigue
capturándose para verla individualmente.

## Reglas

- `python -m jobs.<job>` desde la raíz siempre.
- Conexión SQL: `core.postgres.get_pool()` — nunca cerrarlo (mata el pool).
- Job nuevo: patrón `JobRunLogger`, entrada en `crontab.txt`, índices.
  Ver skill `add-job`.
