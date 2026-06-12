# RENTA VARIABLE — mapa de datos (Mongo + SQL)

> **Qué es este documento.** Mapa verificado **desde el código** de la vista
> **RENTA VARIABLE** (`/renta-variable` en acaquant-web) = **Scanner de CEDEARs +
> CCL + day-trading**. Qué muestra, de qué colección Mongo sale, quién la llena,
> relaciones y qué hay en SQL.
>
> **Método.** Verificado leyendo archivo:línea. Lo no confirmable se marca
> `⚠️ a verificar`. No se asumió nada. Relevamiento: **2026-06-12**.

---

## 1. Resumen ejecutivo

📋 **Qué es la vista:** `/renta-variable` (`RentaVariableShell` → `ScannerView`):
tabla scanner de CEDEARs (live BYMA + ADR), Time & Sales intradía, métricas por
sector ("Pulso"), Pivot Points, Vol/Beta, charts y un módulo de day-trading
(costumbre de vueltas). **Gate:** `require_module("renta-variable")` (admin +
trader + sales). Verificado en `api/routers/scanner.py:16-18`.

📋 **De dónde sale todo:** Mongo. Colecciones de la base `Trading` (6) + el CCL de
`Valuaciones`. **No aparece ninguna base nueva** (a diferencia de Agro/Derivados).

📋 **Estado SQL (lo importante):** **RENTA VARIABLE está 100% afuera de SQL.** Las
6 colecciones dan **0 apariciones** en `sql/schema.sql`, y `scanner.py` /
`day_trading.py` **no leen Postgres** (verificado).

---

## 2. Bloques de la vista y endpoints

> **Router:** `api/routers/scanner.py`, prefijo `/api/scanner`. El front pega vía
> proxy `/api/scanner/*`.

| Bloque | Endpoint backend | Poll | Service |
|---|---|---|---|
| **Tabla CEDEARs** (live BYMA + ADR EOD) | `GET /api/scanner/cedears` | 2s | `scanner.py::get_cedears_scanner` |
| **CCL** (header) | `GET /api/scanner/ccl` | 5s | `scanner.py::get_ccl_live` |
| **Time & Sales intradía** | `GET /api/scanner/cedears/trades?ticker=` | 2s | `scanner.py::get_cedears_trades` |
| **Chart intradía (live)** | `GET /api/scanner/cedears/intraday?ticker=` | 3s | `scanner.py::get_cedears_intraday` |
| **Pivot Points** (USD del subyacente) | `GET /api/scanner/pivot/{ticker}` | 60s | `scanner.py::get_pivot_points` |
| **Vol / Beta** (lazy) | `GET /api/scanner/quant/{ticker}` | on-demand | `scanner.py::get_quant_stats` |
| **Retornos diarios** (histograma, lazy) | `GET /api/scanner/returns/{ticker}` | on-demand | `scanner.py::get_ticker_returns` |
| **Pulso por sector** | — (cálculo client-side sobre la tabla) | — | — |
| **Chart histórico** | TradingView (widget externo) | — | — |
| **Day-trading** (costumbre/vueltas) | `GET /api/scanner/day-trading`, `/companeros/{t}`, `/correlaciones`, `/trade-analysis/{t}`, `/book-analysis` | — | `day_trading.py`, `rv_motor.py` |

---

## 3. Colecciones Mongo — quién las lee y quién las llena (verificado)

| Colección | Base | Qué es | La lee | La llena (verificado) |
|---|---|---|---|---|
| **Cedears** | `Trading` | Maestro categórico (ticker, underlying, sector, ratio) | scanner, day_trading, rv_motor | seed/manual (master) |
| **CedearsSnapshot** | `Trading` | Estado live por CEDEAR (precio/book/vol, ~1s) | scanner, day_trading | **`engines/motor_cedears.py`** (`bulk_write` cada 1s — `motor_cedears.py:122,296`) |
| **CedearsTimeSales** | `Trading` | Trades intradía (tape) — se vacía al cierre | scanner, day_trading | `engines/motor_cedears.py` (`insert_many` — `motor_cedears.py:126,312`) |
| **PreciosAcciones** | `Trading` | Cierres EOD del subyacente US (+ SPY/QQQ) | scanner (returns/quant/pivot), rv_motor | **`jobs/precios_acciones_daily.py`** (post-cierre US, Yahoo) |
| **AdrSnapshot** | `Trading` | ADR live (cada ~15 min en hs US) | scanner (pivot, adr) | `jobs/adr_live.py` (Finnhub) ⚠️ *job a confirmar* |
| **DayTradingStats** | `Trading` | "Costumbre" de vueltas (~20 ruedas) | day_trading | `jobs/day_trading_stats.py` ⚠️ *a confirmar* |
| **DolarSnapshot / Dolar** | `Valuaciones` | CCL live / cierre | scanner (`/ccl`) | motores dólares (`engines/dolares.py` / `dolar_mep`) |

---

## 4. Relaciones / lógica clave (verificado)

1. **CEDEAR (ARS) ↔ subyacente (USD):** `Cedears.underlying` resuelve el ticker
   BYMA corto (ej. `YPFD`→`YPF`) al símbolo US para leer `PreciosAcciones`/
   `AdrSnapshot`. Pivots, vol y beta se calculan **sobre el USD del subyacente**,
   no sobre el precio ARS de BYMA. (`scanner.py::_resolve_underlying`.)
2. **Retorno USD descontando CCL:** la tabla muestra el retorno en dólares
   restando el movimiento del CCL: `((1+vs_1d/100)/(1+ccl_1d/100)-1)*100`. Si el
   CCL no está vivo, la columna USD se muestra `--` sin romper. (`scanner.py`.)
3. **Pulso por sector:** se calcula **en el navegador** sobre la tabla,
   ponderando por `total_money` ($ operado), no por volumen nominal.
4. **Trades inferidos:** el motor infiere los trades del tape por salto de
   volumen nominal (no hay feed de trades directo). `CedearsTimeSales` se vacía
   cada noche (`jobs/cleanup_cedears_timesales.py`) ⚠️ *a confirmar horario*.

---

## 5. Estado SQL

**Verificado por búsqueda directa en `sql/schema.sql` + lectura de los services:**

- `Cedears`, `CedearsSnapshot`, `CedearsTimeSales`, `PreciosAcciones`,
  `AdrSnapshot`, `DayTradingStats` → **0** apariciones en schema ❌
- `scanner.py` y `day_trading.py` leen SQL: **0** (no `get_pool`, no `_sql`).

**Conclusión:** la vista RENTA VARIABLE **no tiene ningún punto de contacto con
SQL**. Completamente fuera de la migración. (El CCL usa `Valuaciones.Dolar`, que
sí tiene tabla `dolar` en SQL, pero el scanner lo lee de Mongo.)

---

## 6. Cache (verificado: `@cached` en los services)

| Función | TTL |
|---|---|
| `get_cedears_scanner` | 2s |
| `get_ccl_live` | 5s |
| `get_ticker_returns` / `get_quant_stats` / `get_pivot_points` | 60s |
| `get_day_trading` | 15s; `_costumbre` 600s; `get_companeros` / correlaciones 300s |

Motor escribe `CedearsSnapshot` cada 1s → cache 2s del scanner balancea frescura.

---

## 7. ⚠️ Pendiente de verificar / medir en prod (NO asumido)

1. **Jobs pobladores exactos** de `AdrSnapshot`, `DayTradingStats` y el cleanup de
   `CedearsTimeSales` — el agente los nombró (`adr_live.py`, `day_trading_stats.py`,
   `cleanup_cedears_timesales.py`) pero no confirmé cada línea. No afecta el mapa
   de datos de la vista.
2. **Gate RBAC vivo:** el default es admin+trader+sales, pero `Manager.RoleMatrix`
   (Mongo) puede pisarlo. Verificar el efectivo si importa.
3. **Conteos reales** → `python -m scripts.diag_inventario_mongo_sql`.

---

## 8. Archivos fuente

- **Frontend:** `acaquant-web/src/app/renta-variable/page.tsx` +
  `renta-variable-shell.tsx`, `scanner-view.tsx`, `cedears-scanner-table.tsx`,
  `cedears-timesales-panel.tsx`, `metricas-panel.tsx`, `pivot-points-panel.tsx`,
  `ticker-chart-panel.tsx`, `lib/types-scanner.ts`.
- **Router:** `api/routers/scanner.py`.
- **Services:** `scanner.py`, `day_trading.py`, `rv_motor.py`.
- **Motor:** `engines/motor_cedears.py`.
- **Jobs:** `precios_acciones_daily.py`, `adr_live.py`, `day_trading_stats.py`,
  `cleanup_cedears_timesales.py`.
- **SQL:** ninguna tabla. (Verificado: 0 colecciones de renta variable en `sql/schema.sql`.)
