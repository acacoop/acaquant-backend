# Estado de los datos — Fase 1 (2026-06)

Diagnóstico del cluster Atlas M10 hecho con inventario real (no con el CLAUDE.md):
`scripts/diag_atlas_inventario --idx` (definiciones de índices) + `scripts/diag_atlas_indices`
(Performance Advisor de Atlas). **Todo lo de acá está verificado contra el cluster**,
salvo lo marcado explícitamente como *hipótesis*.

> Permisos: ni el user de lectura (`MONGO_URI_READ`) ni el rw (`MONGO_URI`) tienen
> el privilegio `indexStats` → **no tenemos el USO real de cada índice**. El
> Performance Advisor de Atlas tampoco sugiere dropear nada (sus 3 listas vienen
> vacías). Por eso la limpieza de índices de este documento se basa en
> **redundancia estructural** (determinista) y no en uso medido. Para medir uso
> hace falta un usuario con rol `clusterMonitor` (ver §4).

---

## 0. Foto del cluster

- **Total: 774.6 MB de datos · 324.8 MB de índices · 16 bases de datos.**
- **El 82% del peso de índices vive en 3 colecciones:**

| Colección | Datos | Índices | # idx | índices/datos |
|---|---|---|---|---|
| `CashFlow.Operaciones` | 223.8 MB | **157.8 MB** | 9 | 70% |
| `CashFlow.NegocioMovimientos` | 135.5 MB | **64.8 MB** | 7 | 48% |
| `Valuaciones.AuM` | 71.3 MB | **44.2 MB** | 7 | 62% |

Índices que pesan 50-70% de los datos es la señal de over-indexing, y es lo que
satura el working set en RAM del M10 (la causa de los CPU 100%).

---

## 1. El veredicto sobre las sugerencias de Atlas: NO crearlas (todavía)

El Performance Advisor sugiere crear **~9 índices nuevos**, casi todos en las dos
colecciones que YA dominan el footprint:

**`CashFlow.Operaciones`** (ya tiene 9 idx / 157.8 MB):
- `{moneda, es_cierre, etapa}`
- `{moneda, es_cierre, arancel, etapa}`
- `{moneda, es_cierre, concertacion, etapa}`
- `{denominacion, moneda, es_cierre, bruto, concertacion, etapa}`
- `{es_cierre, concertacion, boleto, cuenta, arancel, etapa}`
- `{concertacion, boleto}`

**`CashFlow.NegocioMovimientos`** (ya tiene 7 idx / 64.8 MB):
- `{ingestado_en}`
- `{id_cuenta, fecha, comprobante, categoria, unidad}`
- `{id_cuenta, fecha, comprobante, categoria, ticker}`

### Por qué NO obedecer

1. **El Advisor optimiza latencia por query, no el footprint total ni la RAM.** Le
   pedís "andá rápido" y te tira 9 índices sin importarle que infles las dos
   colecciones que ya te tiraron el CPU. Crear esos 9 sumaría un estimado de
   **+50-100 MB de índices** justo donde NO sobra RAM.

2. **Hipótesis (sin medir):** esas sugerencias son reacción al **tráfico de esta
   sesión** — los endpoints de Aranceles / Informe Comercial / PnL son nuevos
   (se construyeron estos días) y se golpearon muchas veces mientras se armaban.
   El Advisor mira actividad reciente, no steady-state.
   - *Cómo confirmar:* volver a correr `diag_atlas_indices` dentro de ~3-5 días
     con solo tráfico de producción. Si las sugerencias persisten, son reales.

3. **Esas queries están DISEÑADAS para pegarle a los rollups, no a las
   colecciones base.** Las series de volumen/arancel leen `CashFlow.OpsSerieDiaria`
   (62k docs pre-agregados) y solo el día de HOY cae a `Operaciones` (1 día →
   índice `concertacion`, barato). El informe comercial lee `Clientes.ComercialCache`.
   El PnL lee `Valuaciones.PnLTotalesCache`. Si el rollup ya absorbe la lectura
   pesada, indexar la colección base es bloat puro.

**Conclusión:** no se crea ninguno de los 9 ahora. Si tras la re-medición alguno
persiste, se diseña un set MÍNIMO (varios comparten el prefijo `{moneda, es_cierre}`,
que ya está cubierto por el índice existente `moneda_escierre_concertacion` —
regla ESR: Equality → Sort → Range).

---

## 2. Acciones seguras inmediatas (redundancia estructural)

Verificadas por definición de índice, no por uso. Bajo riesgo, ganancia chica
pero gratis. Van en un script idempotente, fuera de rueda (REGLA #4).

| Colección | Índice a dropear | Por qué | Riesgo |
|---|---|---|---|
| `Market.EconomicCalendar` | `time_1` `{time:1}` | Prefijo de `time_1_country_1_event_1` (UNIQUE) → toda query por `time` usa el compuesto | Nulo |
| `Valuaciones.AuM` | `id_cuenta_1_fecha_snapshot_-1` | Prefijo de `id_cuenta_1_fecha_snapshot_1_valuacion_1`; el sort `-1` lo sirve el compuesto escaneando al revés | Bajo — **verificar antes** que ninguna query haga `hint()` explícito a este índice |

> El segundo necesita un `explain()` sobre las queries de portfolio que ordenan
> `fecha_snapshot:-1` por `id_cuenta` antes de dropear. Es de los pocos drops que
> tocan una de las 3 colecciones pesadas → vale la verificación.

### Colecciones vacías con índices (limpieza)

| Colección | Estado | Acción propuesta |
|---|---|---|
| `Manager.Grupos` | 0 docs, 1 idx | feature de grupos sin usar → confirmar y dropear colección |
| `Operaciones.BracketsLive` | 0 docs, 3 idx | brackets de órdenes nunca usados → confirmar y dropear |
| `Valuaciones.AuMResumen` | 0 docs, 4 idx | resumen AuM nunca poblado → dropear |
| `Valuaciones.AumBackfillRuns` | 0 docs, **1.9 MB de índice** | índice de 1.9 MB sobre 0 docs (anómalo) → dropear colección |

---

## 3. Lo que NO se puede tocar a ciegas (la limpieza grande)

Las dos fuentes reales del bloat son:

- **`Operaciones`**: 5 índices `concertacion_{mercado,operacion,segmento,nivel3}` +
  `cuenta_concertacion`. Se construyeron para las agregaciones por-request que el
  rollup `OpsSerieDiaria` vino a REEMPLAZAR. **Hipótesis (sin medir):** varios
  quedaron sin uso al migrar al rollup. Pero la vista MOVIMIENTOS y los facets de
  cross-filter (por_cuenta / por_instrumento) todavía pegan a `Operaciones` en
  vivo, así que ALGUNOS siguen vivos. **No se dropea ninguno sin medir uso.**
- **`NegocioMovimientos`**: 7 índices, ninguno redundante por prefijo (sirven
  queries distintas: `fecha_{categoria,cuenta,ticker}`, `idcuenta_*`). El peso es
  "legítimo" por estructura (339k docs, campos de alta cardinalidad).

**Para decidir sobre estos hace falta el USO real de cada índice (`$indexStats`),
que hoy no tenemos.** Dropear a ciegas un índice que sí sirve a la vista
MOVIMIENTOS sería volver a romper algo en prod.

---

## 4. El único trabajo de infra que destraba todo: usuario de monitoreo

Crear en Atlas un **Custom Role** con la acción `indexStats` (+ `collStats`,
`find`) sobre todas las DBs, y un database user que lo use, con su connection
string en una env var nueva (ej. `MONGO_URI_MONITOR`). Es trabajo de **consola
Atlas** (no Droplet, no copy-paste de Mongo).

Con eso, `diag_atlas_inventario --idx --uri-env MONGO_URI_MONITOR` trae los
accesos reales por índice → recién ahí se decide, con evidencia, qué dropear de
`Operaciones` y `NegocioMovimientos` (potencial: decenas de MB de RAM liberada).

**Secuencia correcta:** crear user de monitoreo → medir ~1 semana → dropear los
índices con 0 accesos → recién entonces evaluar crear los que Atlas sugiera y
sigan apareciendo.

---

## 5. Topología de datos — el rediseño (Fase 2/3)

Esto es estructural, no urgente, pero responde tu "las colecciones están muy
desorganizadas". Verificado por inventario.

### 5.1 El dólar fragmentado en 4 colecciones / 2 DBs
- `Trading.DOLAR` (830) — BCRA A3500 fixing
- `Valuaciones.Dolar` (1.569) — serie histórica
- `Valuaciones.DolarOficialLive` (1) — feed MAE live
- `Valuaciones.DolarSnapshot` (1)

Cuatro fuentes para "el dólar". **Fase 2: una sola colección fuente-de-verdad**
con un campo `tipo` (oficial/mayorista/mep/a3500) y `origen`. Migración con
backfill idempotente + adaptador en los services para no romper consumidores.

### 5.2 Las copias `*API` — 5 bases de datos de puro espejo
`PortfolioAPI.AumAPI` (53.1 MB, copia 1:1 de `Valuaciones.AuM`),
`OperacionesAPI.{FlujosAPI, MesaAPI}`, `TitulosAPI.{AssetsAPI, ValuacionesAPI}`,
`CuentasAPI.{AccionistasAPI, ContrapartesAPI}` — todas derivadas, mantenidas por
`jobs/sync_api_copies.py`. ~57 MB de datos + ~13 MB de índices duplicados.

> **A verificar antes de proponer colapso:** quién consume cada `*API` hoy
> (`api/db.py`, partner_api, routers). `ACAPortfolio.Cartera` NO es espejo — es la
> DB propia de `partner_api`. La pregunta de arquitectura: ¿se puede leer de la
> fuente con proyección/permiso en vez de mantener 5 DBs espejo? (no asumir que
> están muertas).

### 5.3 `Trading` = cajón de sastre (39 colecciones)
Market data + macro + dólar + cedears + agro + opciones-ish + snapshots, todo
junto. Es el principal sospechoso del desorden. **Fase 3** (no antes de
estabilizar): reordenar por dominio. El reorden de DBs es la migración más cara y
la última — primero datos confiables, después prolijidad.

---

## 6. Roadmap de datos

| # | Acción | Riesgo | Bloquea a | Estado |
|---|---|---|---|---|
| 1 | Drops seguros §2 (redundantes + colecciones vacías) vía script idempotente | Bajo | — | propuesto |
| 2 | Crear user de monitoreo Atlas (`clusterMonitor`/custom) §4 | Nulo (solo lee) | #3 | propuesto |
| 3 | Medir uso de índices 1 semana → dropear los muertos de `Operaciones`/`NegocioMovimientos` | Medio (RED) | — | espera #2 |
| 4 | Re-medir Performance Advisor sin tráfico de dev → set mínimo de índices nuevos si persisten | Medio (RED) | — | espera #1 |
| 5 | Fuente-única del dólar §5.1 | Medio | — | Fase 2 |
| 6 | Evaluar colapso de copias `*API` §5.2 | Alto | — | Fase 2 |
| 7 | Reorden de `Trading` por dominio §5.3 | Alto | — | Fase 3 |
