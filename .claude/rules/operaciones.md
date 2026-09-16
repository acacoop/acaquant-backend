---
paths:
  - "api/services/operaciones_sql.py"
  - "api/services/comercial_sql.py"
  - "api/services/comercial.py"
 a nivel   - "api/services/control_comercial_sql.py"
  - "api/services/produccion.py"
  - "api/services/mesa_dinero.py"
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

## PRODUCCIÓN del operador — el arancel NO es lo único que genera (2026-09-16)

`api/services/produccion.py` es el **catálogo declarado** de por dónde un comercial genera plata. Nació porque la vista OPERADORES medía a la gente con una sola fuente (el `arancel` de boletos) mientras MESA DE DINERO generaba plata que moría en su propia tab: medido con `scripts/diag_produccion_operador`, eran **ARS 45,4M sobre 208,6M (+21,8%)** y **daba vuelta el ranking en dos posiciones** (un comercial tenía el 81,7% de su producción sin contar).

- **No es todo «arancel».** El arancel lo paga el CLIENTE por operar; lo de Mesa es **resultado de intermediación** y NO baja a nivel cliente. Por eso la respuesta trae los **componentes por separado + el total**, y `comisiones` pasó a significar PRODUCCIÓN TOTAL. Sumarlos dentro de un campo llamado `arancel` daría un número que nadie puede explicar.
- **El 50%, no el 100%.** Se toma la mitad del operador de la REGLA 50/50 que la tab RESULTADOS ya aplica — el mismo divisor, para que las dos pantallas no digan cosas distintas del mismo hecho.
- **Se atribuye por EMAIL** (`mesa_dinero.observacion_email`), nunca por el nombre de `observacion`: `operadores.nombre` no tiene UNIQUE y es editable (REGLA #9, declarado en `core/duplicados.py`). El selector sale de `clientes.operadores`, **no** de la allowlist de Manager → MESA: medido, hay un operador con plata cargada que no está en esa allowlist.
- **Un filtro por CUENTA apaga Mesa entera** (`_intermediacion` / `_mesa_aplica`): esa plata no cuelga de un comitente, así que no se puede recortar por nivel/segmento/referido. Se excluye y se declara — a medias y en silencio, nunca. Filtrar por OPERADOR sí la deja entrar (la acota).
- **Dónde se ve** (vista OPERADORES):
  - **INFORME** → `comercial_sql.informe_comercial`: entra a **ARANC. TOTAL** y **ARANC. MES** del ranking por comercial, con las MISMAS ventanas TOTAL/MES que `_rollup_por_cuenta` (si se separaran, la fila mediría otro período que el resto de la tabla).
  - **ARANCELES POR SEGMENTO** (`informe_aranceles_segmento`, el drill-down de la fila) → fila propia **`INTERMEDIACIÓN (MESA)`**. Va como fila y no dentro de un segmento porque la tabla agrupa por `nivel_1` de la CUENTA y esta plata no tiene cuenta. Sin `vol_total` ni ticket: no hay boletos detrás.
  - **CONTROL COMERCIAL** → `control_comercial_sql`: Tablas 2 y 3 (`comisiones` = producción total; los objetivos se miden contra ella).
- **`cobertura()` distingue «sin datos» de «cero»**: Mesa arranca 2026-07-01; pedir antes no es una caída de producción.
- ⚠️ **La Tabla 1 de CONTROL COMERCIAL (totales ALyC) NO incluye intermediación** y por eso **no da la suma de la Tabla 2**. Es deliberado: un total de la casa tendría que llevar el 100% del resultado, no el 50% atribuido. Está declarado en el docstring de `control_comercial_sql.py`.
- **FASE 2 — FCI**: se agrega como tercera `Fuente`. El cálculo ya existe casi entero en `comercial.referido_fci` (saldo promedio diario × `assets.fee_admin` × días/365, por sociedad gerente); falta generalizarlo de *referido* a *operador*.


