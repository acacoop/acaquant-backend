# 🗺️ Mapa de DECOMMISSION de Mongo (gobierno del apagado total)

> **REGLA #1 (19/6):** Mongo desaparece del stack. Lo funcional → SQL nativo (sin
> arrastrar lógica/campos de Mongo). Lo fuera de uso → se BORRA. El bar NO es
> "SQL == Mongo"; es "SQL sirve el dato correcto y Mongo se apaga".

Este doc es el **tablero de gobierno** del decommission. Se construye cruzando 3 fuentes
(todas refrescables, ninguna asume):

| Fuente | Qué aporta | Cómo refrescar |
|---|---|---|
| `scripts/uso_mongo_codigo.py` | quién LEE/ESCRIBE cada colección en el código + capa | `python -m scripts.uso_mongo_codigo` (local, escanea fuente) |
| `scripts/diag_inventario_mongo_sql.py` | conteos reales prod + qué tiene espejo SQL | correr en Droplet (read-only) |
| `scripts/estado_sql.py` | qué dominio LEE SQL vs Mongo (flags `.env`) | correr en Droplet |

> Conteos prod de este doc = última medición conocida (roadmap 2026-06-12). **Refrescar
> con `diag_inventario_mongo_sql` antes de dropear nada** (REGLA #4).

---

## 🚨 HALLAZGO CRÍTICO — código Mongo MUERTO leyendo colecciones ya DROPEADAS

Verificado con prod (`estado_sql` + `diag_inventario_mongo_sql`, 2026-06-22). Flags
`OPERACIONES_SQL / COMERCIAL_SQL / PORTFOLIO_SQL / VALUACIONES_SQL / PNL_SQL` todos 🟢 **ON**.
Inventario confirma DROPEADAS: `Valuaciones.AuM`, `Valuaciones.Assets`, `CashFlow.Operaciones`,
`CashFlow.NegocioMovimientos`, `CashFlow.Contrapartes`, `OpsSerieDiaria` (no aparecen). Los
paths Mongo que las leen son **código muerto confirmado** (flag ON + colección inexistente):

| Código (path Mongo muerto) | Colección dropeada que lee | Estado |
|---|---|---|
| ~~`api/routers/cuentas.py` (contrapartes)~~ | `CashFlow.Contrapartes` | ✅ **YA SQL** (clientes.contrapartes) |
| ~~`api/services/risk.py` (`_nombres_por_id_cuenta`)~~ | `CashFlow.Contrapartes` | ✅ **FIXEADO 22/6** (→ SQL) |
| ~~`api/services/operaciones_view.py`~~ | `CashFlow.Operaciones`/`OpsSerieDiaria`/`Comitentes` | ✅ **YA PURGADO** (solo helpers) |
| `api/services/comercial.py` (path Mongo) | `Comitentes`, `NegocioMovimientos`, `ComercialCache` | ⬜ purgar readers (flag `COMERCIAL_SQL` ON) |
| `api/services/pnl.py` (fallback) | `NegocioMovimientos`, `Valuaciones.AuM` | ⬜ purgar readers (flag `PNL_SQL` ON) |
| `api/services/portfolio.py` (Mongo) | `Valuaciones.AuM` (~9 reads) | ⬜ purgar readers (flag `PORTFOLIO_SQL` ON) |
| `api/services/valuaciones.py` (Mongo) | `Valuaciones.AuM` (~12 reads), `NegocioMovimientos` | ⬜ purgar readers (flag `VALUACIONES_SQL` ON) |

**OJO (no es "borrar el archivo"):** los servicios SQL importan **helpers PUROS** de estos
mismos archivos (`comercial_sql`←`comercial`, `pnl_sql`←`pnl`, `valuaciones_sql`←`valuaciones._es_cash`,
`comercial`←`portfolio._fci_assets_map`) + jobs/tests. El purgado es **quirúrgico**: sacar las
FUNCIONES que leen Mongo, CONSERVAR los helpers. Hacerlo de a un archivo, validando (import+ruff+test).

---

## ✅ BUCKET 1 — YA en SQL (source of truth). Falta: borrar Mongo + código muerto

Cutover hecho, SQL es la fuente. Mongo dropeada o a punto.

| Dominio | Colecciones Mongo | Flag lectura | Pendiente |
|---|---|---|---|
| Operaciones | `CashFlow.Operaciones`, `OpsSerieDiaria` | `OPERACIONES_SQL` 🟢 | ✅ **CERRADO** — `/ops/*` SQL-only, path Mongo borrado (19/6) |
| Negocio | `CashFlow.NegocioMovimientos` | (negocio) 🟢 | borrar refs muertas |
| Comercial | `Clientes.ComercialCache` | `COMERCIAL_SQL` 🟢 | borrar path Mongo (`comercial.py`) |
| Comitentes | `Clientes.Comitentes` | (auth/comercial) 🟢 | borrar refs muertas |
| Contrapartes | `CashFlow.Contrapartes` | `CONTRAPARTES_SQL` ⚪ **OFF** | **flag a SQL + borrar `cuentas.py` Mongo** |
| AuM / Assets | `Valuaciones.AuM/Assets` | `PORTFOLIO_SQL` 🟢 | borrar fallback (`pnl.py`) |

> Todas estas colecciones figuran **DROPEADAS de Mongo** ya. La deuda es **de código**, no de datos.

---

## 🔌 BUCKET 2 — Código SQL listo, FLAG apagado en prod (solo prender + dropear)

Espejo escrito + servicio SQL + validado, pero `estado_sql` los muestra ⚪ MONGO. Es
**cutover de un flag**, no código nuevo:

| Dominio | Flag | Colecciones Mongo a dropear después |
|---|---|---|
| Macro (7 series) | `MACRO_SQL` 🟢 **ON 19/6** (gate 29/29) | CER, DOLAR, BADLAR, TAMAR, RiesgoPais, InflacionMensual, InflacionInteranual |
| REM | `REM_SQL` 🟢 **ON 19/6** (gate 13/13) | REM |
| Históricos mercado | `MERCADO_HIST_SQL` 🟢 **ON 19/6** (gate 10/10 historia) | BreakevensHistorico, ForwardsHistorico, FuturosDLR, Caucion, FitParams, FairValueResiduos |

> **Lectura CUTOVER (19/6)** ✅. **DROP BLOQUEADO** (verificado 19/6): los MOTORES
> (curvas, forwards, futuros_dlr, caucion, breakevens) y jobs (argentina_datos, fair_value,
> backfills, forwards_zscore) **todavía leen/escriben estas colecciones en Mongo** para
> calcular. Para dropear hay que **migrar esos motores/jobs a SQL** (el laburo pesado,
> de a uno, Rojo). Hasta entonces Mongo sigue vivo de respaldo (dual-write). Pendiente menor:
> `SNAPSHOT_SQL=1` post-cierre (fila de hoy de breakevens/forwards live).

---

## 🔨 BUCKET 3 — MIGRAR (lectura VIVA en la app, todavía sin SQL)

Lo que el scanner marca `[MIGRAR] lectura viva`. Orden sugerido menor→mayor riesgo.

### 3a. Renta fija / mercado LIVE (4° corte — CABLEADO, falta cutover)
- `MarketSnapshot` (read) → **`renta_fija_sql.py` COMPLETO + selector cableado** (flag
  `RENTA_FIJA_SQL`, `?_engine` override) en `cotizaciones.py` (renta-fija, snapshot-live,
  historico/curva) y `analitica.py` (listar-curva). Gate: `scripts/compare_renta_fija_sql_vs_mongo.py`.
  `DiasHabiles` ya migró a SQL. **Cutover**: correr el gate → si OK, `RENTA_FIJA_SQL=1` + restart.
  Híbrido que queda: MEP live (`get_ultimo_mep` → `DolarSnapshot`, feed WS, sin espejo SQL aún).
- Snapshots LIVE (migran con su vista, dual-write motor): `ForwardsLive`, `BreakevensLive`,
  `ForwardsZscore`, `CaucionSnapshot`, `FuturosDLRSnapshot`, `DolarSnapshot`, `SnapshotsSinteticos`.

### 3b. Renta variable / scanner
- `Cedears`, `CedearsSnapshot`, `CedearsTimeSales`, `PreciosAcciones`, `AdrSnapshot`, `DayTradingStats`.

### 3c. Opciones
- `Opciones.{Data, DataHistorica, Metadata, OptionsSnapshot, VR-GGal}`. (`Data` ~1M docs = piedra grande.)

### 3d. Agro / Derivados (carga manual mesa)
- `AgroSnapshot`, `AgroOpcionesSnapshot`, `Derivados.{AgroPizarra, CamaraCereales}` (+ `*Audit`).

### 3e. CashFlow restante
- `Movimientos`, `Acreencias`, `Productores`, `Accionistas`, `VolumenMercadoAgro`, `TiposOperacion`.

### 3f. Caches / back-office
- `Valuaciones.{PnLTotalesCache, ConsolidadoCuentas, TenenciaHD}`, `PortfolioSnapshot`, `UVA`.

### 3g. Manager infra + MCP + AUTH writes
- `Manager.{JobRuns, HealthReports, WatchdogAlertas, RoleAudit, Grupos, Users, RoleMatrix, ActividadMensual, PyRofex*}`.
- `MCP.{OAuthClients, OAuthCodes, OAuthTokens}`.
- (lecturas de roles/grupos ya en SQL vía `AUTH_SQL` 🟢; falta su EDICIÓN/writes.)

### 3h. 🪨 Las 3 piedras grandes (cada una = mini-proyecto con su gate)
1. ~~`Trading.TimeSales` (~5.6M docs)~~ ✅ **DROPEADA 2026-06-22.** SQL-only (`mercado.timesales`),
   valores.py escribe SQL (append_native) + read de arranque SQL, prune 7d. Tape = solo HOY
   (front filtra). Lectores históricos (liquidez/serie-ticker/snapshot-fallback) eran MCP-only
   → borrados. `ts` naive ART (no timestamptz, sino el tape corría 3hs). Forwards/curvas leen
   MarketSnapshot (nunca TimeSales); breakevens.obtener_precios → MarketSnapshot.last_price.
2. `Opciones.Data` (~1M docs).
3. **Motor de órdenes** `Operaciones.{OrdenesLive, OrdenesAudit, TriggersMep, BracketsLive, OperativasMep}` — real-time, crítico.

---

## 🗑️ BUCKET 4 — BORRAR (no se migra)

- **Código Mongo muerto** del HALLAZGO CRÍTICO (paths de fallback de dominios ya cutover).
- **Campos muertos**: `total_money` en market_snapshot (motor lo dejó de escribir) — ya quitado de `renta_fija_sql`.
- **Colecciones sin lector vivo**: confirmar con `diag_inventario` (conteo) + scanner (0 capas api). Candidatas a medир: `Trading.OrderBookL2` (stream, sin consumidor SQL), `*Audit` viejos.
- **`OnsIgnoradas`**: revisar si sigue en uso (write en service).

> Antes de cada DROP: refrescar conteos (`diag_inventario`) + confirmar 0 lectores vivos (scanner). Reversible hasta el drop.

---

## 📌 Apps separadas
- `partner_api` usa base `ACAPortfolio` (`Cartera`, `ApiUsers`) — app independiente.
  **Migrada a SQL (dual-run) el 2026-06-23** → schema `partner` (`partner.cartera`,
  `partner.api_users`), flags `PARTNER_SQL` (lectura) / `PARTNER_SQL_WRITE` (escritura).
  Ver `docs/PARTNER_API.md`. Con `PARTNER_SQL=1` ya no depende de Mongo → su base
  `ACAPortfolio` puede dropearse tras el cutover.

---

## 🏃 RUNBOOK — cutover Bucket 2 (Macro / REM / Históricos)

Domínios con código + gate LISTOS y dual-write ya poblando SQL (`MERCADO_SQL_WRITE` ON).
Cutover de LECTURA = prender flags. Cada paso corre en el Droplet (`cd /root/TradingAV`).

**Paso 0 — Medir (REGLA #2/#4):**
```
python -m scripts.diag_inventario_mongo_sql
```
Confirmar que `macro.series_macro`, `macro.rem`, `mercado.mercado_hist` tienen filas
(≈ a Mongo). Pasarme la salida si hay dudas.

**Paso 1 — Gates de paridad (read-only):**
```
python -m scripts.compare_macro_sql_vs_mongo
python -m scripts.compare_rem_sql_vs_mongo
python -m scripts.compare_mercado_hist_sql_vs_mongo
```
Exigir paridad. Único drift tolerado: la **fila de HOY** de breakevens/forwards
(intradía-mutable, esperado). Si una serie histórica diffea fuerte → NO prender;
correr `python -m scripts.sync_postgres --full` (fuera de rueda) y re-validar.

**Paso 2 — Prender flags (cutover):** agregar al `.env` del Droplet y reiniciar:
```
MACRO_SQL=1
REM_SQL=1
MERCADO_HIST_SQL=1
```
`systemctl restart api.service`

**Paso 3 — Verificar:**
```
python -m scripts.estado_sql        # los 3 deben quedar 🟢 SQL
```
+ ojo a las vistas en la app (cotizaciones macro, REM, históricos de mercado).

**Rollback:** sacar los 3 flags del `.env` + restart → vuelve a Mongo al instante
(el dual-write sigue escribiendo Mongo). Reversible hasta el Paso 4.

**Paso 4 — DECOMMISSION (apagar Mongo de estos dominios) — requiere código previo:**
Hoy los jobs **dual-escriben** (Mongo+SQL). Para poder DROPEAR las colecciones, antes
hay que pasar esos writes a **SQL-only** (lo hago yo en código, post-cutover de lectura):
`jobs.bcra`/`jobs.argentina_datos` (series_macro+REM) y los motores de históricos.
Recién ahí: `python -m scripts.drop_coleccion <Coll> --dry-run` → `--apply`.

> Orden no negociable: **lectura SQL (1-3) → write SQL-native (4a) → drop (4b)**. Dropear
> con el job todavía escribiendo Mongo lo recrea/rompe.

---

## ▶️ Próximos pasos propuestos
1. **Refrescar el mapa con prod**: correr en Droplet `diag_inventario_mongo_sql` + `estado_sql` → pegar conteos reales acá (confirma muertas).
2. **Quick win seguro**: borrar el código Mongo muerto del Bucket 1 + fix `cuentas.py`/Contrapartes (está roto). Cero migración, solo limpieza — alinea con REGLA #1.
3. **Cutover de flags Bucket 2** (Macro/REM/Históricos): prender + dropear esas colecciones.
4. Seguir Bucket 3 por orden (renta fija en curso).
