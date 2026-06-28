# Decomiso de MongoDB — TradingAV

> Plan ejecutivo para apagar Mongo Atlas. Generado sobre el mapa por base/colección + reporte de completitud.
> **Lenguaje PM**: "dropear" = borrar la colección de Mongo. "SQL-native" = el código escribe directo a Postgres y ya no toca Mongo. "Dual-write" = escribe en los dos (Mongo sigue siendo la red de seguridad).

---

## PROGRESO DEL DECOMISO (bitácora)

- **2026-06-28** — Grupo CALENDARIO + MACRO liquidado (lectura+escritura SQL-only,
  sin fallback Mongo):
  - `Trading.DiasHabiles` → `mercado.dias_habiles`. Helper único `core/calendario.py`.
    **DROPEADA** (243 docs).
  - `Trading.{CER,DOLAR,BADLAR,TAMAR,RiesgoPais,InflacionMensual,InflacionInteranual}`
    → `macro.series_macro`. Helper `core/series_macro.py`. Writers `bcra.py` +
    `argentina_datos.py` SQL-native. **DROPEADAS** (~13.000 docs).
  - `Trading.REM` → `macro.rem`. `argentina_datos` SQL-native (incl. dedup).
    Lista para DROP (verificar tras deploy).
  - `sync_postgres`: retirados `sync_series_macro` y `sync_rem` (eran puentes Mongo→SQL).
  - Pendiente de limpieza: twins Mongo gateados (`macro.py`, `renta_fija.py`) se
    borran cuando se elimine el selector dual-run.
- **Próximo:** `Trading.MarketSnapshot` (lo leen breakevens/forwards/curvas) →
  `mercado.market_snapshot` (SNAPSHOT_SQL on). Después renta fija derivada.

---

## 0. ESTADO REAL VERIFICADO (flags de prod, 2026-06-28) — LEER PRIMERO

> Esta sección corrige al mapa por colección de abajo, que se generó leyendo
> CÓDIGO sin ver los flags del `.env` → sobreestimó lo que falta. Fuente: salida
> real de `python -m scripts.estado_sql` en el Droplet.

**Las LECTURAS ya están casi todas migradas a SQL.** El sistema HOY: **lee
Postgres, escribe Mongo** (y espeja a SQL por dual-write). Por eso Mongo sigue
prendido: lo que falta NO es migrar lecturas (hecho), es volver las
**ESCRITURAS** SQL-native y dropear.

| Read-side | Estado |
|---|---|
| OPERACIONES, VOLUMEN_AGRO, COMERCIAL, PORTFOLIO, VALUACIONES, CONTRAPARTES, PNL, PNL_TOTALES, NEWS, MARKET, REM, MACRO, MERCADO_HIST, RENTA_FIJA, AGRO, OPCIONES, SCANNER, AUTH, MANAGER | 🟢 **lee SQL** |
| **ORDENES** (read-side) | ⚪ todavía Mongo |

| Write-side (dual-write a SQL) | Flag | Estado |
|---|---|---|
| Motores → market_snapshot | `SNAPSHOT_SQL` | 🟢 on |
| Jobs mercado → series_macro / rem / cierres | `MERCADO_SQL_WRITE` | 🟢 on |
| Manager infra → job_runs / role_audit | `MANAGER_SQL_WRITE` | ⚪ off |
| Motor órdenes → ordenes_live/audit/… | `ORDENES_SQL_WRITE` | ⚪ off |

**Trabajo restante real (no "meses desde cero"):**
1. Prender los 2 dual-write que faltan (`MANAGER_SQL_WRITE`, `ORDENES_SQL_WRITE`) + verificar paridad.
2. Flipear la última lectura Mongo (`ORDENES_SQL`) tras paridad.
3. **Cutover de ESCRITURAS** dominio por dominio: que cada motor/job deje de escribir Mongo (escriba SQL only). Red de seguridad: las lecturas ya son SQL.
4. Matar `sync_postgres` (puente Mongo→SQL) cuando ningún writer dependa de él.
5. Dropear colecciones + apagar Atlas.

> El detalle de abajo (mapa por colección + roadmap de fases) sigue siendo útil
> para el ORDEN y los bloqueantes, pero leélo con esta corrección: el read-side
> ya está, y varios "blockers" del audit (ej. `bcra.py sin dual-write`) son
> FALSOS — verificá contra código antes de actuar (REGLA #2).

---

## 1. RESUMEN

**84 colecciones mapeadas** en 11 bases (Trading, Valuaciones, CashFlow, Clientes, Manager, Opciones, Derivados, Operaciones, Market, News, ACAPortfolio).

| Estado | Cantidad | % | Significado |
|---|---|---|---|
| **Muertas** (`dead`) | 12 | 14% | Sin lectores ni escritores reales. Droppables ya (o casi). |
| **Cutover hecho** (`cutover_done`) | 8 | 10% | Ya NO se escribe en Mongo. Quedan lectores sueltos por migrar antes del drop. |
| **Lee SQL / escribe Mongo** (`read_sql_write_mongo`) | 32 | 38% | Lo más avanzado: ya hay dual-write y casi todos leen SQL. Falta volver el writer SQL-native y matar lectores rezagados. |
| **100% Mongo** (`fully_mongo`) | 32 | 38% | El núcleo pesado: motores live y batch que escriben Mongo como fuente primaria. Varias **sin tabla SQL todavía**. |

**Migrado de hecho (puede salir de Mongo): 20 colecciones ≈ 24%.**
**Todavía atadas a Mongo: 64 colecciones ≈ 76%.**

Además, el reporte de completitud detectó **4 colecciones sin plan de migración** (no estaban en el mapa): `Trading.PortfolioSnapshot`, `Trading.AdhocSubscriptions`, `Manager.PortfolioSnapshotLog`, `Manager.AranceelesJobRuns`. Ninguna tiene tabla SQL — son agujeros del plan.

### Veredicto honesto: **NO se puede apagar Mongo hoy. Ni cerca.**

Razones duras (verificadas en el mapa):

1. **Todos los motores live escriben Mongo como primario.** `motor_ordenes`, `options`, `motor_cedears`, `caucion`, `futuros_dlr`, `breakevens`, `forwards`, `dolar_mep`, `dolares`, `motor_agro*`, `portfolio_snapshot`. El dual-write SQL existe en varios, pero el origen sigue siendo Mongo.
2. **Hay colecciones críticas sin tabla SQL siquiera creada:** `CedearsTimeSales`, `SnapshotsSinteticos`, `DolarSnapshot`, `DolarOficialLive`, `UVA`, `Flujo`, `HealthReports`, `WatchdogAlertas`, `PyRofexDiscovery`, `PyRofexInstruments`, más las 4 no cubiertas (`PortfolioSnapshot`, `AdhocSubscriptions`, etc.).
3. **`CashFlow.Operaciones` (vista MOVIMIENTOS/comercial) está 100% Mongo, sin tabla SQL.** Es corazón del negocio comercial.
4. **Órdenes en vivo (`Operaciones.*`) tienen lectores acoplados a Mongo** (`operativa_mep.py`, `_idempotencia.py`, `risk.py`) sin path SQL. Tocar esto mal = la mesa no operás o se duplican órdenes. Es lo más riesgoso de todo.
5. **`DolarOficialLive` lo escribe una PC externa** (script `mae_forex.py` por IP whitelisteada). Migrarlo exige cambiar software fuera de este repo.

Es un trabajo de **semanas/meses por fases**, no un switch. La buena noticia: el 38% `read_sql_write_mongo` ya tiene la infraestructura dual hecha y es mayormente "encender flags + matar lectores viejos".

---

## 2. TABLA POR COLECCIÓN

Leyenda clasificación: **dead** = sin uso real · **cutover** = ya no escribe Mongo · **rd-SQL/wr-Mongo** = dual, lee SQL · **Mongo** = 100% Mongo.

### Trading (33)
| Colección | Clase | Estado SQL | Escritor(es) | Qué falta para dropear |
|---|---|---|---|---|
| Curvas | rd-SQL/wr-Mongo | dual_write | `ons.py`, `bonos_admin.py` | `cleanup_curvas.py` leer SQL; `ons.py` dual-write SQL |
| BondsMaster | dead | dead | — | Nada. Consolidada en `mercado.curvas`. **DROP YA** |
| MarketSnapshot | Mongo | dual_write | `valores.py` (SNAPSHOT_SQL) | Readers (`forwards`, `breakevens`, `snapshot_cierre`) a SQL; flag ON |
| SnapshotsCierre | dead | dead | — | Eliminada 26-06-24. **DROP YA** |
| CanjeCierre | dead | dead | — | Eliminada 26-06-24. **DROP YA** |
| TimeSales | cutover | sql_native | — (SQL-only) | Readers en `jobs/backfill_*.py` a SQL → luego DROP |
| DOLAR | Mongo | dual_write | `bcra.py` | `bcra.py` dual-write `macro.series_macro`; readers a `macro_sql` |
| CER | Mongo | dual_write | `bcra.py` | `bcra.py` dual-write; `curvas`/`breakevens` a SQL |
| BADLAR | rd-SQL/wr-Mongo | dual_write | `bcra.py` | `bcra.py` dual-write; matar reader legacy `macro.py` |
| TAMAR | rd-SQL/wr-Mongo | dual_write | `bcra.py` | idem BADLAR |
| RiesgoPais | rd-SQL/wr-Mongo | dual_write | `argentina_datos.py` | `argentina_datos.py` dual-write |
| InflacionMensual | Mongo | dual_write | `argentina_datos.py` | dual-write + `breakevens.py` a SQL |
| InflacionInteranual | Mongo | dual_write | `argentina_datos.py` | dual-write; verificar 0 readers |
| **UVA** | Mongo | **none** | (carga manual) | **Crear tabla SQL**; migrar lectura `macro.py` |
| BreakevensLive | Mongo | dual_write | `breakevens.py` | Engine dual-write `mercado_hist`; `derivados.py` a SQL |
| BreakevensHistorico | rd-SQL/wr-Mongo | dual_write | `breakevens.py` (SNAPSHOT_SQL) | Flag ON; validar mirror |
| ForwardsLive | Mongo | dual_write | `forwards.py` | Engine dual-write; `derivados.py` a SQL |
| ForwardsHistorico | rd-SQL/wr-Mongo | dual_write | `forwards.py` | Flag ON; `forwards_zscore.py` a SQL |
| ForwardsZscore | rd-SQL/wr-Mongo | dual_write | `forwards_zscore.py` | Dual-write `mercado.forwards_zscore` |
| FitParams | Mongo | dual_write | `fair_value.py` | dual-write; `fair_value.py` reader a SQL |
| FairValueResiduos | Mongo | dual_write | `fair_value.py` | dual-write; verificar 0 readers |
| DiasHabiles | Mongo | dual_write | `dias_habiles.py` | dual-write; varios engines a SQL |
| REM | Mongo | dual_write | `argentina_datos.py` | dual-write; `rem.py` a SQL |
| FuturosDLR | Mongo | dual_write | `futuros_dlr.py` | dual-write hist; `derivados.py` a SQL |
| FuturosDLRSnapshot | Mongo | dual_write | `futuros_dlr.py` (SNAPSHOT_SQL) | Flag ON; `cleanup_futuros_dlr.py` a SQL |
| Caucion | Mongo | dual_write | `caucion.py` | dual-write; `repo.py` a SQL |
| CaucionSnapshot | Mongo | dual_write | `caucion.py` (SNAPSHOT_SQL) | Flag ON |
| Cedears | Mongo | dual_write | `manager/renta_variable.py` | dual-write; `motor_cedears`/`scanner` a SQL |
| **CedearsTimeSales** | Mongo | **none** | `motor_cedears.py` (bulk 1s) | **Crear tabla SQL**; dual-write motor; 3 readers |
| AgroSnapshot | Mongo | dual_write | `motor_agro.py` (SNAPSHOT_SQL) | Flag ON; `derivados_agro.py` a SQL |
| AgroOpcionesSnapshot | Mongo | dual_write | `motor_agro_opciones.py` | Flag ON; reader a SQL |
| **SnapshotsSinteticos** | Mongo | **none** | `snapshot_sinteticos.py` | **Crear tabla SQL**; dual-write |
| ONSnapshot | dead | dead | — | Legacy. **DROP YA** |
| *PortfolioSnapshot* (no mapeada) | Mongo | **none** | `portfolio_snapshot.py` (1s) | **Sin plan**: crear tabla SQL + dual-write motor |
| *AdhocSubscriptions* (no mapeada) | Mongo | **none** | `adhoc_subscriptions.py` | **Sin plan**: TTL + CRUD, crear SQL |

### Valuaciones (8)
| Colección | Clase | Estado SQL | Escritor(es) | Qué falta para dropear |
|---|---|---|---|---|
| ConsolidadoCuentas | cutover | dual_write | — (SQL-native) | `valuaciones.py` lee Mongo (dual-run flag); flip → DROP |
| PnLTotalesCache | cutover | dual_write | — (SQL-native) | `pnl.py` lee Mongo; flip → DROP |
| **Dolar** | Mongo | table_exists_empty | `dolar_mep.py` | Tabla vacía; **agregar dual-write best-effort**; muchos readers |
| **DolarSnapshot** | Mongo | **none** | `dolares.py` (~5s) | **Crear tabla** `dolar_snapshot`; dual-write motor |
| **DolarOficialLive** | Mongo | **none** | `core/dolar_oficial.py` vía `/ingest` (**PC externa**) | **Cambiar el script externo** o el endpoint a SQL |
| TenenciaHD | dead | read_migrated | — | Obsoleta → `portafolio.tenencia`. **DROP YA** |
| Assets | dead | read_migrated | — | Deprecada 15-06. **DROP** (ver conflicto §5) |
| AuM | dead | read_migrated | — | Eliminada 15-06. **DROP** (ver conflicto §5) |

### CashFlow (9)
| Colección | Clase | Estado SQL | Escritor(es) | Qué falta para dropear |
|---|---|---|---|---|
| Accionistas | rd-SQL/wr-Mongo | table_exists_empty | — | Tabla vacía; poblar SQL; cambiar 3 readers; sacar de `sync_postgres` |
| TiposOperacion | rd-SQL/wr-Mongo | table_exists_empty | `fci_bilateral.py` | Writer y reader a SQL |
| VolumenMercadoAgro | rd-SQL/wr-Mongo | table_exists_empty | (carga manual) | Definir destino carga; reader a SQL |
| Acreencias | cutover | sql_native | — | Nada. **DROP** tras verificación |
| Movimientos | cutover | sql_native | — | Nada. **DROP** tras verificación |
| **Operaciones** | Mongo | **none** | `operaciones_informes.py` | **Crear tabla `operaciones.operaciones`**, writer SQL, migrar `comercial.py`, **backfill histórico** |
| NegocioMovimientos | rd-SQL/wr-Mongo | dual_write | `negocio_movimientos.py` | `comercial.py`/`pnl.py`/`_universo_portfolio.py` a SQL; writer SQL-native |
| **Flujo** | Mongo | **none** | `flujo_contrapartes.py` | **Sin readers**: investigar si es viva → si no, DROP |
| Contrapartes | cutover | sql_native | — | Nada. **DROP** |

### Clientes (3)
| Colección | Clase | Estado SQL | Escritor(es) | Qué falta para dropear |
|---|---|---|---|---|
| Comitentes | cutover | sql_native | — (SQL-native) | `comercial.py` lee Mongo en 10+ lugares → migrar → DROP |
| ActividadMensual | rd-SQL/wr-Mongo | dual_write | `actividad_mensual.py` | Writer a SQL-native (hoy escribe Mongo y luego sincroniza) |
| ComercialCache | dead | none/zombi | — | Colección zombi, nunca tuvo writer. Limpiar `comercial.py` → **DROP** |

### Manager (9+)
| Colección | Clase | Estado SQL | Escritor(es) | Qué falta para dropear |
|---|---|---|---|---|
| Users | rd-SQL/wr-Mongo | dual_write | `core/roles.py` | `AUTH_SQL=1` + `MANAGER_SQL_WRITE=1` → SQL-native |
| RoleMatrix | rd-SQL/wr-Mongo | dual_write | `core/roles.py` | idem (flags) |
| RoleAudit | rd-SQL/wr-Mongo | dual_write | `core/roles.py` | idem |
| Grupos | rd-SQL/wr-Mongo | dual_write | `core/grupos.py` | idem |
| JobRuns | rd-SQL/wr-Mongo | dual_write | `core/job_runs.py` | idem; varios readers a SQL |
| **HealthReports** | Mongo | **none** | `informe_salud.py` | **Crear tabla** + sync; bajo ROI |
| **WatchdogAlertas** | Mongo | **none** | `watchdog.py` | **Crear tabla**; bajo impacto |
| **PyRofexDiscovery** | Mongo | **none** | — (**sin writer**) | Writer desaparecido; investigar; crear SQL |
| **PyRofexInstruments** | Mongo | **none** | — (**sin writer**) | **CRÍTICA** (valida operables); writer perdido; crear SQL |
| *AranceelesJobRuns* (no mapeada) | Mongo | none | `manager/aunesa.py` | Sin plan: tracking de backfill |
| *PortfolioSnapshotLog* (no mapeada) | Mongo | none | `portfolio_snapshot.py` | Sin plan: audit |

### Opciones (5)
| Colección | Clase | Estado SQL | Escritor(es) | Qué falta para dropear |
|---|---|---|---|---|
| Data | rd-SQL/wr-Mongo | dual_write | `options.py` (tick) | Motor SQL-native (hoy `insert_one` + mirror) |
| OptionsSnapshot | rd-SQL/wr-Mongo | dual_write | `options.py` (~1s) | Motor SQL-native (cortar `bulk_write` Mongo) |
| Metadata | rd-SQL/wr-Mongo | dual_write | `options.py`, `manager/options.py`, `volatilidad_ggal.py` | Múltiples writers a SQL |
| DataHistorica | dead | sql_native | — | DROPEADA 24-06. Nada |
| VR-GGal | rd-SQL/wr-Mongo | dual_write | `volatilidad_ggal.py` | Job a SQL-native (hoy solo Mongo + baseline) |

### Derivados (4)
| Colección | Clase | Estado SQL | Escritor(es) | Qué falta para dropear |
|---|---|---|---|---|
| AgroPizarra | rd-SQL/wr-Mongo | dual_write | `derivados_agro.py` | `AGRO_SQL=1` default; sacar write Mongo; 30d SQL → DROP |
| CamaraCereales | rd-SQL/wr-Mongo | dual_write | `camara_cereales.py` | `AGRO_SQL=1`; 3 read paths a SQL; sacar write Mongo |
| AgroPizarraAudit | dead | none | `derivados_agro.py` (audit) | TTL 90d; **DROP con el padre** |
| CamaraCerealesAudit | dead | none | `camara_cereales.py` (audit) | TTL 90d; **DROP con el padre** |

### Operaciones (8) — la mesa en vivo
| Colección | Clase | Estado SQL | Escritor(es) | Qué falta para dropear |
|---|---|---|---|---|
| OrdenesLive | rd-SQL/wr-Mongo | dual_write | `ordenes.py`, `motor_ordenes.py` | `ORDENES_SQL=1` prod; `operativa_mep.py` lee Mongo directo |
| OrdenesAudit | rd-SQL/wr-Mongo | dual_write | `ordenes.py`, `motor_ordenes.py`, `operativa_mep.py` | Igualar retención/TTL en SQL |
| BracketsLive | rd-SQL/wr-Mongo | dual_write | `core/brackets.py` | `motor_ordenes.py` lee Mongo directo; sin dual-run lectura |
| **OperativasMep** | rd-SQL/wr-Mongo | dual_write | `operativa_mep.py` | **No existe `operativa_mep_sql.py`**; lecturas 100% Mongo |
| TriggersMep | dead | table_exists_empty | — | Scanner inactivo, vacía. **DROP YA** |
| MotorOrdenesHeartbeat | rd-SQL/wr-Mongo | dual_write | `motor_ordenes.py` (30s) | Reader monitoring a SQL (trivial) |
| **OrdenesIdempotency** | rd-SQL/wr-Mongo | dual_write | `_idempotencia.py` | **Lectura SIEMPRE Mongo** (anti-doble-click); TTL en SQL; si Mongo cae, dedup cae |
| AccountsDescubiertas | rd-SQL/wr-Mongo | dual_write | `descubrir_cuentas.py` | `risk.py` lee Mongo directo; agregar dual-run |

### Market (2)
| Colección | Clase | Estado SQL | Escritor(es) | Qué falta para dropear |
|---|---|---|---|---|
| Quotes | Mongo | dual_write | `market_quotes.py`, `market_anchors.py` | `MARKET_SQL=1`; cambiar default API a SQL; verificar |
| EconomicCalendar | Mongo | dual_write | `economic_calendar.py` | `MARKET_SQL=1`; verificar migración schema v1→v2 |

### News (1)
| Colección | Clase | Estado SQL | Escritor(es) | Qué falta para dropear |
|---|---|---|---|---|
| Headlines | cutover | sql_native | — (SQL-only) | `manager/status.py` lee Mongo sin flag → migrar → `NEWS_SQL=1` → DROP |

### ACAPortfolio (2) — Partner API
| Colección | Clase | Estado SQL | Escritor(es) | Qué falta para dropear |
|---|---|---|---|---|
| Cartera | rd-SQL/wr-Mongo | dual_write | `partner_export.py` | `PARTNER_SQL_WRITE=1` + `PARTNER_SQL=1`; sacar write Mongo |
| ApiUsers | rd-SQL/wr-Mongo | dual_write | `partner_user.py` | **Fuente de auth**: testear a fondo antes de sacar Mongo |

---

## 3. ROADMAP POR FASES

Ordenado por **dependencia y riesgo**: primero lo muerto y lo read-only batch (bajo riesgo), el corazón (motores live) al final. **Cada fase respeta REGLA #4: medir antes, batchear, fuera de rueda, idempotente.**

### Fase 0 — Quick wins: dropear lo muerto (días, riesgo casi nulo)
Dropear las 12 `dead` + cerrar los `cutover_done` ya terminados. Ver §5. No requiere tocar código de escritura; sí limpiar lectores zombi (`comercial.py::_por_cuenta_cache`, etc.). **Gana: menos colecciones, menos ruido, baseline limpio.**

### Fase 1 — Cerrar cutovers a medio camino (1-2 semanas, riesgo bajo)
Ya NO se escribe Mongo; solo quedan lectores rezagados:
- **News.Headlines**: migrar `manager/status.py` a SQL → `NEWS_SQL=1` → DROP.
- **Clientes.Comitentes**: migrar los 10+ reads de `comercial.py` a `clientes.comitentes` SQL → DROP.
- **Valuaciones.ConsolidadoCuentas / PnLTotalesCache**: flip de lectura (flag `VALUACIONES_SQL`) en `valuaciones.py`/`pnl.py` → DROP.
- **Trading.TimeSales**: migrar `backfill_breakevens/forwards` a `mercado.timesales` → DROP.
**Riesgo:** bajo. Son lecturas con SQL ya poblado. Comparar conteos antes del flip.

### Fase 2 — Macro y series batch (2-3 semanas, riesgo bajo)
Jobs diarios fuera de rueda, idempotentes, datos chicos. **Acá vive el verdadero gate de muchos engines.**
- `bcra.py` → dual-write `macro.series_macro` (DOLAR, CER, BADLAR, TAMAR).
- `argentina_datos.py` → dual-write (RiesgoPais, Inflación×2, REM).
- `dias_habiles.py` → dual-write.
- **Crear tabla SQL para UVA** (no existe) e integrar el load manual.
- Migrar readers: `macro.py` legacy fuera, `rem.py`, `breakevens.py`, `curvas.py`, `futuros_dlr.py` → `macro_sql`.
**Riesgo:** bajo (batch, off-market). **Cuidado REGLA #4:** el backfill histórico de series va batcheado + sleep, fuera de 13-20 UTC.

### Fase 3 — Renta fija derivada: snapshots e históricos (2-4 semanas, riesgo medio)
Motores live de cálculo que ya tienen dual-write/mirror; falta **encender `SNAPSHOT_SQL`** y migrar readers de `derivados.py`/`repo.py`/`fair_value.py`:
- `breakevens`, `forwards`, `caucion`, `futuros_dlr` (Live + Historico + Snapshots).
- `forwards_zscore`, `fair_value` (FitParams, Residuos).
**Riesgo:** medio — son motores en rueda. Validar mirror con comparador SQL↔Mongo antes de flip de lectura.

### Fase 4 — Manager / Auth (1-2 semanas, riesgo medio)
Datos chicos, infra dual ya hecha:
- Encender `AUTH_SQL=1` + `MANAGER_SQL_WRITE=1` → Users/RoleMatrix/RoleAudit/Grupos/JobRuns SQL-native.
- **Crear tablas** para HealthReports, WatchdogAlertas (bajo ROI, pero bloquean el apagado).
- **Investigar PyRofexDiscovery/PyRofexInstruments**: el writer desapareció del repo. `PyRofexInstruments` es CRÍTICA (valida órdenes operables). Recuperar/recrear writer + tabla SQL **antes** de tocar nada.
**Riesgo:** medio (auth en vivo). El fallback Mongo da red.

### Fase 5 — Agro + Opciones + Market (2-3 semanas, riesgo medio)
- **Agro**: `AGRO_SQL=1` default, sacar write Mongo de pizarra/cámara, esperar 30d, DROP (+ audits).
- **Opciones**: motor `options.py` SQL-native (cortar `bulk_write`/`insert_one` Mongo); `volatilidad_ggal.py` SQL-native.
- **Market**: `MARKET_SQL=1`, cambiar default API a SQL (Quotes, EconomicCalendar), verificar schema v1→v2.
- **Crear tablas** para `SnapshotsSinteticos`, `CedearsTimeSales` (stream append-only de alto volumen — dimensionar bien).
**Riesgo:** medio. `CedearsTimeSales` es bulk cada 1s → el insert SQL tiene que aguantar el throughput.

### Fase 6 — Comercial / Operaciones histórico (3-5 semanas, riesgo medio-alto)
- **Crear tabla `operaciones.operaciones`** (hoy `none`), writer SQL en `operaciones_informes.py`, **backfill histórico completo desde Mongo** (REGLA #4: batcheado, fuera de rueda, idempotente).
- `NegocioMovimientos`: writer SQL-native; migrar `comercial.py`/`pnl.py`/`_universo_portfolio.py`.
- `ActividadMensual`: writer SQL-native.
- CashFlow chicos: `Accionistas`, `TiposOperacion`, `VolumenMercadoAgro` (poblar tablas vacías + readers).
- **`Flujo`**: confirmar si tiene uso; si no, DROP directo.
**Riesgo:** medio-alto. Backfill grande + lógica comercial sensible. **Gate obligatorio:** comparador SQL↔Mongo con paridad total antes de cutover.

### Fase 7 — Órdenes en vivo (semanas, RIESGO ALTO — la mesa)
El núcleo transaccional. Acá un error = no operás o duplicás órdenes.
- **Crear `operativa_mep_sql.py`** (no existe); migrar `listar_operativas_dia`/`obtener_detalle_operativa`.
- **OrdenesIdempotency**: lectura siempre Mongo hoy; necesita TTL en SQL idéntico + dual-run de lectura. Anti-doble-click no puede fallar.
- `ORDENES_SQL=1` prod; `risk.py` y `motor_ordenes.py` (brackets) con dual-run de lectura.
- `MotorOrdenesHeartbeat`, `AccountsDescubiertas` (más simples).
**Riesgo:** ALTO. Migrar de a una colección, con rollback por flag, fuera de horario de mercado, monitoreo intensivo.

### Fase 8 — Dólar live + Partner + snapshots de portfolio (semanas, riesgo alto)
Los feeds más acoplados y el writer externo:
- `dolar_mep.py` (Valuaciones.Dolar, tabla vacía): dual-write best-effort.
- `dolares.py` (DolarSnapshot): **crear tabla** `dolar_snapshot` + dual-write.
- **DolarOficialLive**: requiere **cambiar el script externo `mae_forex.py`** (PC oficina) para que escriba SQL, o que el endpoint `/ingest` haga dual-write. Coordinación fuera del repo.
- **Partner API** (`Cartera`, `ApiUsers`): `PARTNER_SQL_WRITE=1` + `PARTNER_SQL=1`; auth crítica, testear a fondo.
- **`PortfolioSnapshot`** (motor 1s, sin plan): crear tabla + dual-write.
**Riesgo:** alto (muchos lectores del dólar; auth externa).

### Fase 9 — Apagado y decom (días, una vez todo en verde)
- Migrar/retirar infra Mongo: `core/mongo.py` singletons, `core/mongo_monitor.py`, `AdhocSubscriptions`, logs de audit (`PortfolioSnapshotLog`, `AranceelesJobRuns`).
- Quitar `sync_postgres.py` (ya no hay origen Mongo).
- Apagar Atlas (`atlas_cluster.sh`), borrar `MONGO_URI` de `.env` y unit files.
**Solo cuando ninguna ruta lea/escriba Mongo** (verificable con `scripts.estado_sql`).

---

## 4. BLOQUEANTES DUROS — deben volverse SQL-native sí o sí

Nada de esto se apaga mientras estos procesos escriban Mongo como primario:

**Motores live (always-on / en rueda):**
| Motor | Colecciones | Estado del dual-write |
|---|---|---|
| `engines/motor_ordenes.py` | OrdenesLive, OrdenesAudit, Heartbeat, Brackets | dual ✓ pero lectores en Mongo |
| `engines/options.py` | Data, OptionsSnapshot, Metadata | dual ✓, primario sigue Mongo |
| `engines/motor_cedears.py` | Cedears, **CedearsTimeSales** | CedearsTimeSales **sin tabla SQL** |
| `engines/caucion.py` | Caucion, CaucionSnapshot | dual ✓ (flag) |
| `engines/futuros_dlr.py` | FuturosDLR, FuturosDLRSnapshot | dual ✓ (flag) |
| `engines/breakevens.py` | BreakevensLive/Historico | dual ✓ (flag) |
| `engines/forwards.py` | ForwardsLive/Historico | dual ✓ (flag) |
| `engines/dolar_mep.py` | Valuaciones.Dolar | **sin dual-write** (tabla vacía) |
| `engines/dolares.py` | DolarSnapshot | **sin tabla SQL** |
| `engines/motor_agro*.py` | Agro(Opciones)Snapshot | dual ✓ (flag) |
| `engines/portfolio_snapshot.py` | PortfolioSnapshot | **sin tabla, sin plan** |
| `engines/valores.py` | MarketSnapshot | dual ✓, readers en Mongo |

**Jobs/servicios batch que aún escriben Mongo:**
- `jobs/bcra.py` (DOLAR/CER/BADLAR/TAMAR — **sin dual-write**)
- `jobs/argentina_datos.py` (RiesgoPais/Inflación/REM — **sin dual-write**)
- `jobs/dias_habiles.py`, `jobs/forwards_zscore.py`, `jobs/fair_value.py`
- `jobs/snapshot_sinteticos.py` (**sin tabla SQL**)
- `jobs/volatilidad_ggal.py` (VR-GGal — solo Mongo)
- `api/services/operaciones_informes.py` (**CashFlow.Operaciones — sin tabla SQL**)
- `jobs/negocio_movimientos.py`, `jobs/actividad_mensual.py`
- `jobs/market_quotes.py`, `jobs/economic_calendar.py` (dual ✓, falta flip default)
- `jobs/partner_export.py`, `scripts/partner_user.py`
- `core/roles.py`, `core/grupos.py`, `core/job_runs.py` (dual ✓, falta flags)
- `jobs/watchdog.py`, `jobs/informe_salud.py` (**sin tabla SQL**)

**Caso especial fuera del repo:** `DolarOficialLive` lo escribe la **PC de oficina** vía `/ingest`. Migrarlo requiere cambiar ese software o el endpoint — coordinación externa.

**Writers perdidos a investigar:** `PyRofexInstruments`/`PyRofexDiscovery` no tienen writer en el repo (¿`scripts/discovery_pyrofex.py` borrado?). `PyRofexInstruments` valida órdenes operables → si queda congelada y se dropea sin reemplazo SQL, **se rompe el envío de órdenes**.

---

## 5. QUICK WINS — dropeables YA (o casi)

**Drop inmediato (muertas, 0 lectores/escritores reales):**
- `Trading.BondsMaster` — consolidada en `mercado.curvas`.
- `Trading.SnapshotsCierre`, `Trading.CanjeCierre` — eliminadas 24-06, SQL-native.
- `Trading.ONSnapshot` — legacy, ONs salen de `Trading.Curvas`.
- `Operaciones.TriggersMep` — scanner inactivo, colección vacía.
- `Opciones.DataHistorica` — ya dropeada 24-06.
- `Valuaciones.TenenciaHD` — obsoleta → `portafolio.tenencia`.

**Drop con limpieza mínima de código primero:**
- `Clientes.ComercialCache` — zombi, nunca tuvo writer. Limpiar `comercial.py::_por_cuenta_cache` (ya tiene fallback a live) → DROP.
- `Derivados.AgroPizarraAudit`, `CamaraCerealesAudit` — audit-only, TTL 90d, sin readers. DROP junto con sus padres.
- `CashFlow.Acreencias`, `Movimientos`, `Contrapartes` — cutover hecho, verificar y DROP.

**⚠️ Conflicto a verificar antes de dropear `Valuaciones.AuM` / `Assets`:**
El mapa las marca `dead` (eliminadas 15-06), **pero el reporte de completitud muestra que `api/services/import_tenencia.py` aún hace insert/upsert manual a `Valuaciones.AuM`** (import masivo, `origen=import_manual`). **Hipótesis (sin verificar): o `import_tenencia.py` es código muerto, o `AuM` no está del todo eliminada.** No dropear `AuM` hasta confirmar que ese path está retirado o reapuntado a `portafolio.tenencia`. Vale un diag read-only antes de tocar.

---

### Cierre ejecutivo

📋 **Qué soluciona:** te da el mapa real de qué falta para dejar de pagar Mongo, sin humo: qué se puede borrar hoy, qué es trabajo de flags, y qué es cirugía mayor en los motores.

📋 **Qué genera:** un plan en 9 fases de menor a mayor riesgo. Lo barato (24% ya migrado + quick wins) sale en semanas. El corazón (motores live, órdenes, operaciones comercial, dólar, snapshots 1s) es de meses y exige crear ~8 tablas SQL que **todavía no existen**, recuperar writers perdidos, coordinar un cambio en la PC externa, y backfills grandes batcheados. **Mongo no se apaga hasta cerrar la Fase 8.** El gate de cada cutover es el comparador SQL↔Mongo con paridad total (REGLA #4: medir, batchear, fuera de rueda, idempotente).

---

## Apéndice — Crítico de completitud (re-grep exhaustivo, raw)

Generado por un agente independiente que re-grepeó TODO el repo (`get_mongo_client`, `pymongo`, `MONGO_URI`, accesos a colecciones, config/systemd/crontab) buscando lo que el mapa por base no cubrió. **Esto es lo que históricamente se escapaba.**

### Archivos de producción que tocan Mongo y NO estaban en el mapa por colección

| Archivo | Uso Mongo |
|---|---|
| `engines/portfolio_snapshot.py` | Trading.PortfolioSnapshot (bulk_write UpdateOne cada 1s, upsert), Manager.PortfolioSnapshotLog (insert_one audit) |
| `core/adhoc_subscriptions.py` | Trading.AdhocSubscriptions (insert_one, update_one, delete_one, find, count_documents, TTL index management) |
| `core/mongo_monitor.py` | Monitoring listener de pymongo para capturar queries con duración (CommandListener - no acceso directo a colecciones pero infraestructura crítica) |
| `api/routers/manager/aunesa.py` | Manager.AranceelesJobRuns (insert_one, update_one, find_one, find - backfill aranceles job tracking) |
| `api/routers/manager/operaciones.py` | CashFlow (lectura de maps de enriquecimiento para backfill de operaciones desde CSV) |
| `api/services/acreencias.py` | Trading (via get_db_trading) - find, aggregate |
| `api/services/analitica.py` | Trading (via get_db_trading) |
| `api/services/bonos_admin.py` | Trading (via get_db_trading) |
| `api/services/comparar_inversion.py` | Trading (via get_db_trading) |
| `api/services/day_trading.py` | Trading (via get_db_trading) |
| `api/services/debug_curva.py` | Trading, Valuaciones (via get_mongo_client_read read-only para debugging) |
| `api/services/descomposicion_retorno.py` | Trading (via get_db_trading) - find, aggregate |
| `api/services/import_tenencia.py` | Valuaciones.AuM (insert/upsert manual masivo, origen=import_manual, auditoria) |
| `api/services/operaciones_sql.py` | Trading, CashFlow (via get_db_trading) - lecturas para validación/enriquecimiento |
| `api/services/order_book.py` | Trading.MarketSnapshot (via get_db_trading) - find para order book |
| `api/services/pnl_sql.py` | Valuaciones, Cashflow, Trading (via get_db_*) - precomputes PnL |
| `api/services/renta_fija.py` | Trading, Valuaciones (via get_db_trading/valuaciones) - find, aggregate |
| `api/services/rv_motor.py` | Trading (via get_db_trading) |
| `api/services/sensibilidad.py` | Trading (via get_db_trading) |
| `api/services/sinteticos.py` | Trading (via get_db_trading) |
| `api/services/titulos_flujos.py` | Trading (via get_db_trading) |

### Referencias de infraestructura / deploy a Mongo

- core/mongo.py - Singleton MongoClient (RW: get_mongo_client con maxPoolSize=20, READ: get_mongo_client_read con maxPoolSize=50 + SECONDARY_PREFERRED), carga MONGO_URI y MONGO_URI_READ desde .env (linea 23-24)
- core/db.py - api/db.py helpers de acceso: get_db_opciones, get_db_trading, get_db_valuaciones, get_db_cashflow, get_db_clientes, get_db_manager
- deploy/systemd/motor_portfolio_snapshot.service - Nuevo motor que escribe Trading.PortfolioSnapshot (último precio de tickers en tenencia)
- deploy/crontab.txt - Múltiples referencias a jobs Mongo: descubrir_cuentas (línea 65), volatilidad_ggal (68), economic_calendar (200), sync_postgres (217-218), archive_options_data (161), partner_export (139-140), pnl_totales_precompute (144), consolidado_cuentas (128), etc. + pause/resume Atlas (líneas 188-189)
- deploy/atlas_cluster.sh - Script shell para pausa/resume del cluster MongoDB Atlas (diariamente 03:30/11:30 UTC para ahorro)
- .env - Contiene MONGO_URI (connection string escribible, RW) y MONGO_URI_READ (read-only a secundario si está definido, fallback a MONGO_URI)

### Base no mapeada

- **MCP** (`OAuthClients/OAuthCodes/OAuthTokens`): no se completó el mapeo estructurado, pero es OAuth del MCP server con **TTL automático** (codes 10min, tokens 1h). No tiene espejo SQL ni lo necesita; es efímera y de bajo volumen. Decisión: dejar en Mongo o mover a Postgres con TTL — irrelevante para el costo. Evaluar al final.
