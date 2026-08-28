# RENTA VARIABLE — mapa de datos (SQL)

> **Qué es este documento.** Mapa verificado **desde el código** de la vista
> **RENTA VARIABLE** (`/renta-variable` en acaquant-web) = **Scanner de CEDEARs +
> CCL + day-trading**. Qué muestra, de qué tabla SQL sale, quién la llena,
> relaciones y arquitectura de datos.
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

📋 **De dónde sale todo:** SQL (Postgres/Supabase). Tablas del schema `mercado`
(6) + el CCL del schema `valuaciones`. Conexión vía `core.postgres.get_pool()`.

📋 **Estado SQL (lo importante):** **RENTA VARIABLE es SQL-native desde el cutover
2026-06-24.** Los motores/jobs escriben SQL directo (vía `core.pg_mirror`) y
`scanner.py` / `day_trading.py` leen Postgres vía services `*_sql.py`
(`scanner_sql`) sobre `core.postgres.get_pool()` (verificado).

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
| **Day-trading** (costumbre/vueltas) | `GET /api/scanner/day-trading`, `/companeros/{t}` | — | `day_trading.py` |
| **Mesa de Estrategia** — solo queda la CORRELACIÓN | — (sin HTTP desde 2026-07-13). `get_trade_analysis`/`get_book_analysis` se borraron con el MCP el 2026-08-28; `get_correlation_matrix` sobrevive porque la usa `day_trading` para `/companeros` | — | `rv_motor.py` |

---

## 3. Tablas SQL — quién las lee y quién las llena (verificado)

| Tabla | Schema | Qué es | La lee | La llena (verificado) |
|---|---|---|---|---|
| **cedears** | `mercado` | Maestro categórico (ticker, underlying, sector, ratio) | scanner, day_trading, rv_motor | seed/manual (master) |
| **cedears_snapshot** | `mercado` | Estado live por CEDEAR (precio/book/vol, ~1s) | scanner, day_trading | **`engines/motor_cedears.py`** (SQL-native cada 1s — `motor_cedears.py:122,296`) |
| **cedears_time_sales** | `mercado` | Trades intradía (tape) — se vacía al cierre | scanner, day_trading | `engines/motor_cedears.py` (`motor_cedears.py:126,312`) |
| **precios_acciones** | `mercado` | Cierres EOD del subyacente US (+ SPY/QQQ) | scanner (returns/quant/pivot), rv_motor | **`jobs/precios_acciones_daily.py`** (post-cierre US, Yahoo) |
| **adr_snapshot** | `mercado` | ADR live (cada ~15 min en hs US) | scanner (pivot, adr) | `jobs/adr_live.py` (Finnhub) ⚠️ *job a confirmar* |
| **day_trading_stats** | `mercado` | "Costumbre" de vueltas (~20 ruedas) | day_trading | `jobs/day_trading_stats.py` ⚠️ *a confirmar* |
| **cedears_ohlc_daily** | `mercado` | OHLC diario ARS del CEDEAR (ventana 60 ruedas) + `atr` (ATR-20 en ARS) | pivots ARS, estrategia | `jobs/cedears_ohlc_daily.py` (post-cierre 20:15, calcula el ATR) |
| **cedears_bars_1m** | `mercado` | Archivo permanente de barras 1-min ARS (OHLCV) — fuente del Efficiency Ratio intradía | `core/bars_sql.py` | `jobs/cedears_bars_1m.py` (20:20, resamplea el tape antes del cleanup) |
| **dolar_snapshot / dolar** | `valuaciones` | CCL live / cierre | scanner (`/ccl`) | motores dólares (`engines/dolares.py` / `dolar_mep`) |

---

## 4. Relaciones / lógica clave (verificado)

1. **CEDEAR (ARS) ↔ subyacente (USD):** `mercado.cedears.underlying` resuelve el
   ticker BYMA corto (ej. `YPFD`→`YPF`) al símbolo US para leer
   `mercado.precios_acciones`/`mercado.adr_snapshot`. Pivots, vol y beta se
   calculan **sobre el USD del subyacente**,
   no sobre el precio ARS de BYMA. (`scanner.py::_resolve_underlying`.)
2. **Retorno USD descontando CCL:** la tabla muestra el retorno en dólares
   restando el movimiento del CCL: `((1+vs_1d/100)/(1+ccl_1d/100)-1)*100`. Si el
   CCL no está vivo, la columna USD se muestra `--` sin romper. (`scanner.py`.)
3. **Pulso por sector:** se calcula **en el navegador** sobre la tabla,
   ponderando por `total_money` ($ operado), no por volumen nominal.
4. **Trades inferidos:** el motor infiere los trades del tape por salto de
   volumen nominal (no hay feed de trades directo). `mercado.cedears_time_sales`
   se vacía cada noche (`jobs/cleanup_cedears_timesales.py`) ⚠️ *a confirmar horario*.

---

## 5. Estado SQL

**RENTA VARIABLE es SQL-native (cutover 2026-06-24):**

- `mercado.cedears`, `mercado.cedears_snapshot`, `mercado.cedears_time_sales`,
  `mercado.precios_acciones`, `mercado.adr_snapshot`, `mercado.day_trading_stats`
  → todas en SQL ✅
- `scanner.py` y `day_trading.py` leen SQL vía services `*_sql.py` (`scanner_sql`)
  sobre `core.postgres.get_pool()`.

**Conclusión:** la vista RENTA VARIABLE es **100% SQL** (escritura SQL-native vía
`core.pg_mirror`, lectura vía `scanner_sql`). El CCL sale de `valuaciones.dolar` /
`valuaciones.dolar_snapshot`.

---

## 6. Cache (verificado: `@cached` en los services)

| Función | TTL |
|---|---|
| `get_cedears_scanner` | 2s |
| `get_ccl_live` | 5s |
| `get_ticker_returns` / `get_quant_stats` / `get_pivot_points` | 60s |
| `get_day_trading` | 15s; `_costumbre` 600s; `get_companeros` / correlaciones 300s |

Motor escribe `mercado.cedears_snapshot` cada 1s → cache 2s del scanner balancea frescura.

---

## 7. ⚠️ Pendiente de verificar / medir en prod (NO asumido)

1. **Jobs pobladores exactos** de `mercado.adr_snapshot`, `mercado.day_trading_stats`
   y el cleanup de `mercado.cedears_time_sales` — el agente los nombró
   (`adr_live.py`, `day_trading_stats.py`, `cleanup_cedears_timesales.py`) pero no
   confirmé cada línea. No afecta el mapa de datos de la vista.
2. **Gate RBAC vivo:** el default es admin+trader+sales, pero `manager.role_matrix`
   (SQL) puede pisarlo. Verificar el efectivo si importa.
3. **Conteos reales** → consultar las tablas SQL del schema `mercado`.

---

## 8. Archivos fuente

- **Frontend:** `acaquant-web/src/app/renta-variable/page.tsx` +
  `renta-variable-shell.tsx`, `scanner-view.tsx`, `cedears-scanner-table.tsx`,
  `cedears-timesales-panel.tsx`, `metricas-panel.tsx`, `pivot-points-panel.tsx`,
  `ticker-chart-panel.tsx`, `lib/types-scanner.ts`.
- **Router:** `api/routers/scanner.py`.
- **Services:** `scanner.py`, `day_trading.py`, `rv_motor.py` (+ `scanner_sql`).
- **Motor:** `engines/motor_cedears.py` (escribe SQL-native vía `core.pg_mirror`).
- **Jobs:** `precios_acciones_daily.py`, `adr_live.py`, `day_trading_stats.py`,
  `cleanup_cedears_timesales.py`.
- **SQL:** schema `mercado` (`cedears`, `cedears_snapshot`, `cedears_time_sales`,
  `precios_acciones`, `adr_snapshot`, `day_trading_stats`) + `valuaciones.dolar` /
  `valuaciones.dolar_snapshot` (CCL). Conexión `core.postgres.get_pool()`.
