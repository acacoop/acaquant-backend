---
paths:
  - "api/services/operaciones_sql.py"
  - "api/services/comercial_sql.py"
  - "api/services/comercial.py"
  - "api/routers/operaciones*.py"
  - "jobs/ops_agregado.py"
  - "jobs/operaciones_informes.py"
  - "jobs/fci_bilateral.py"
  - "jobs/negocio_movimientos.py"
  - "jobs/movimientos_propias.py"
---
# Operaciones y Tablero Comercial — SQL, no inferible

## Tablero Comercial (lente por operador)

El Tablero Comercial se sirve SQL-only desde `api/services/comercial_sql.py` (el router `operaciones.py::_com_motor` siempre devuelve SQL; `comercial.py` quedó como helpers/funciones SQL — ver "Capa SQL"). Cruza todo por `id_cuenta`: QUIÉN (`clientes.comitentes` → operador + `nivel_1`), ACTIVIDAD (`operaciones.negocio_movimientos`/`operaciones.operaciones` → última op), TAMAÑO (`portafolio.tenencia`, `aum='si'`), operador↔usuario (`manager.manager_users`, para cuentas huérfanas). Estado comercial por días desde última op: ACTIVA ≤45 / ENFRIANDOSE 45-90 / DORMIDA / NUEVA. Agrega EN VIVO con índices (sin precompute — no se recrean rollups).

**DÍAS SIN OPERAR es auditable por fila** (2026-08-07, mismo patrón que el modal por celda de Tesorería → BANCOS): click en una fila de la tabla ESTADO COMERCIAL abre `GET /api/operaciones/comercial/analisis/detalle?id_cuenta&fecha` (`comercial_sql.detalle_ultima_op`) y muestra **cuál boleto** fija el número — todos los boletos de ese día, el historial reciente, y los que **NO** cuentan con su motivo (anulados, posteriores al corte en modo foto). El insumo es `operaciones.operaciones`: cuenta CUALQUIER boleto no anulado, sin filtro de tipo/mercado/etapa. Ese predicado vive UNA sola vez (`comercial_sql._ULT_OP_WHERE`) y lo comparten la tabla y el modal — el front no recalcula nada, así el detalle no puede contradecir al número.

## Operaciones — SQL (migrado de Mongo 2026-06-16, CRÍTICO no inferible)

`operaciones.operaciones` (SQL Postgres) es la fuente de la vista MOVIMIENTOS (`/api/operaciones/ops/*`) + Contrapartes (`/operaciones/flujo`). **`CashFlow.Operaciones` (Mongo) y el rollup `CashFlow.OpsSerieDiaria` fueron ELIMINADOS** — ver `docs/SQL.md`. Origen: `jobs.operaciones_informes` (API informes Aunesa) que escribe SQL directo vía `operaciones_informes.ingestar_filas_sql` (normaliza + enriquece inline `moneda`/`mercado`/`operacion`/`nivel_3`/`segmento`/`es_cierre`/`commodity`/`mep`). `jobs.fci_bilateral` escribe el FCI bilateral (campo `etapa`) — upsert por boleto que NO pisa el resto. El catálogo `tipos_operacion` vive en SQL (`operaciones.tipos_operacion`).

**Series: HOT/COLD (decisión 2026-08-04, revierte el "no precomputes").** Los días CERRADOS viven pre-agregados en `operaciones.ops_agregado_diario` (mantenida por `jobs/ops_agregado` cada hora en rueda — recomputa POR DÍA SUCIO vía `ingestado_en`, así los backfills históricos re-agregan su día solo y NO puede driftear como el viejo `ops_rollup`); HOY se agrega EN VIVO. `/ops/serie` sin filtros lee agregado+hoy; con filtros va 100% en vivo (`GROUP BY` + índices, `api/services/operaciones_sql.py`). `/ops/aranceles` sigue 100% en vivo (candidato a adoptar el agregado).

- **El arancel y el bruto NO comparten filtro de cierre**: para volumen `bruto` excluye `es_cierre=true`; para `arancel` se INCLUYEN los cierres (el **arancel de caución vive SOLO en el cierre**). `etapa <> 'solicitud'` siempre (la liquidación CL ya cuenta).
- **`es_cierre`** materializado (bool) separa volumen de arancel sin regex.
- El motor de PnL no usa esta tabla (cost-basis sale de `negocio_movimientos`); acá viven volumen/arancel comercial.
- Opciones mantiene su rollup propio (`jobs/options_rollup.py`) sobre tablas SQL.

