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

Verificado leyendo el código (no asumido). Estos dominios ya están **cutover a SQL +
Mongo DROPEADA** (docs/SQL.md, 2026-06-15/16), pero **quedó el path Mongo de fallback
en el código**, leyendo colecciones que **ya no existen**:

| Código (path Mongo muerto) | Colección dropeada que lee | Estado | Acción |
|---|---|---|---|
| `api/routers/cuentas.py` (contrapartes) | `CashFlow.Contrapartes` | ⚠️ **ROTO EN VIVO** (sin flag, colección no existe) | **fix/borrar ya** |
| `api/services/comercial.py` (path Mongo) | `Comitentes`, `NegocioMovimientos`, `Operaciones`, `ComercialCache` | muerto (flag `COMERCIAL_SQL` ON) | borrar path Mongo |
| `api/services/operaciones_view.py` | `CashFlow.Operaciones` | muerto (flag `OPERACIONES_SQL` ON) | borrar |
| `api/services/pnl.py` (fallback) | `NegocioMovimientos`, `Valuaciones.AuM` | muerto (flag `PNL_SQL` ON) | borrar |

**Por qué importa:** (1) `cuentas.py` está sirviendo vacío/error hoy. (2) El resto es una
mina: si alguien apaga un flag `*_SQL`, la vista intenta leer una colección inexistente y
se cae. Borrar estos paths es parte del decommission (REGLA #1: lo muerto se borra) y
además **simplifica el cutover** (sin fallback que mantener).

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
| Macro (7 series) | `MACRO_SQL` ⚪ | CER, DOLAR, BADLAR, TAMAR, RiesgoPais, InflacionMensual, InflacionInteranual |
| REM | `REM_SQL` ⚪ | REM |
| Históricos mercado | `MERCADO_HIST_SQL` ⚪ | BreakevensHistorico, ForwardsHistorico, FuturosDLR, Caucion, FitParams, FairValueResiduos |

---

## 🔨 BUCKET 3 — MIGRAR (lectura VIVA en la app, todavía sin SQL)

Lo que el scanner marca `[MIGRAR] lectura viva`. Orden sugerido menor→mayor riesgo.

### 3a. Renta fija / mercado LIVE (EN CURSO — 4° corte)
- `MarketSnapshot` (read) → **renta_fija_sql.py en curso**. Dep: `DiasHabiles` (tabla SQL nueva) + MEP live (`DolarSnapshot`).
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
1. `Trading.TimeSales` (~5.1M docs) — TTL 15d + write SQL-native (ver bitácora 19/6).
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

## 📌 Fuera de alcance (apps separadas)
- `partner_api` usa base `ACAPortfolio` (`Cartera`, `ApiUsers`) — app independiente, NO entra en este decommission.

---

## ▶️ Próximos pasos propuestos
1. **Refrescar el mapa con prod**: correr en Droplet `diag_inventario_mongo_sql` + `estado_sql` → pegar conteos reales acá (confirma muertas).
2. **Quick win seguro**: borrar el código Mongo muerto del Bucket 1 + fix `cuentas.py`/Contrapartes (está roto). Cero migración, solo limpieza — alinea con REGLA #1.
3. **Cutover de flags Bucket 2** (Macro/REM/Históricos): prender + dropear esas colecciones.
4. Seguir Bucket 3 por orden (renta fija en curso).
