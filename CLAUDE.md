# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TradingAV is a quantitative trading platform for Argentine financial markets (MERVAL/ROFEX). It streams real-time market data, runs parallel analytical engines (microstructure, options, FX arbitrage, plazo arbitrage), persists state to MongoDB Atlas, and exposes a Streamlit dashboard.

## Running the Project

```bash
# Install dependencies
pip install -r requirements.txt
# Also required: pip install pyRofex rich

# Web dashboard
streamlit run streamlit_app.py

# Individual engines (run as background daemons via systemd)
python main_valores.py          # Microstructure: bonos/Lecaps/CER (TimeSales + MarketSnapshot)
python main_options_service.py  # Options pricing/Greeks headless (motor_options.service)
python main_fx.py               # FX arbitrage detection
python main_on.py --headless    # Yield screener ONs (motor_on.service)
python main_curvas.py           # Enriquecimiento TEA/Duration TimeSales (motor_curvas.service)
python main_forwards.py         # Tasas forward en tiempo real (motor_forwards.service)
python main_breakevens.py       # Breakevens CER/Lecap en tiempo real (motor_breakevens.service)

# Production deployment (systemd services start/stop via crontab)
# See crontab section below
```

Requires a `.env` file with: `ROFEX_USER`, `ROFEX_PASSWORD`, `ROFEX_ACCOUNT`, `ROFEX_API_URL`, `ROFEX_WS_URL`, `MONGO_URI`, `AUNESA_CLIENT_ID`, `AUNESA_USERNAME`, `AUNESA_PASSWORD`.

There is no test suite or linting configuration.

## Architecture

**Event-driven, multi-engine architecture:**

```
ROFEX WebSocket (pyRofex)
        │
        ▼
WebSocketManager (websocket_manager.py)
  - Subscribes tickers in 50-ticker chunks
  - Dispatches market data to engine handlers
        │
   ┌────┴────┬──────────┬──────────┬──────────┐
   ▼         ▼          ▼          ▼          ▼
main_ts   main_fx   main_options main_arb  main_valores
   │         │          │          │          │
   └────┬────┴──────────┴──────────┴──────────┘
        ▼
SnapshotWriter (background thread, ~0.5s interval)
  - Hash-based change detection
  - Bulk writes to MongoDB Atlas
        │
        ▼
MongoDB Atlas (4 databases: Trading, Opciones, Valuaciones, CashFlow)
        │
        ▼
Streamlit Dashboard (3 pages: Libro, Mercado, Opciones)
```

### Key Components

- **`config.py`** — Centralized config; loads `.env` credentials; defines the master ticker list (63 instruments: futures, equities, commodities)
- **`session_manager.py`** — Single pyRofex auth initialization
- **`websocket_manager.py`** — WebSocket subscriptions; registers per-engine `update_price(ticker, data)` handlers
- **`mongo_manager.py`** — Singleton `get_mongo_client()` for Atlas; `MongoManager` class for upserts and inserts
- **`snapshot_writer.py`** — Background thread writing engine state to MongoDB; uses hash diffing to skip unchanged data
- **`oms_manager.py`** — Order Management System; wraps pyRofex order placement

### Trading Engines

Each engine has an `update_price(ticker, data)` callback called by the WebSocket handler on each tick. They maintain in-memory state and delegate persistence to `SnapshotWriter`.

| Engine | MongoDB collection | Description |
|---|---|---|
| `main_valores.py` | `Trading.TimeSales` + `Trading.MarketSnapshot` | Microestructura para bonos/Lecaps/CER: inserta trades en TimeSales, snapshot cada 1s en MarketSnapshot |
| `main_options_service.py` | `Opciones.OptionsSnapshot` | GGAL options: Black-Scholes Greeks, IV via Newton-Raphson. Servicio headless (motor_options.service). |
| `main_fx.py` | `Trading.FXArbitrage` | USD pair cross-currency arbitrage (fee: 0.0847%) |
| `main_on.py` | `Trading.ONs` (live) | Yield screener ONs: TIR y duration en tiempo real vía WebSocket. Corre vía motor_on.service (`--headless`). |
| `main_curvas.py` | `Trading.TimeSales` (enrichment) | Enriquece trades de Curvas con TEA/TEM/Duration/Paridad. Loop cada 5s, busca docs sin `duration` ordenados por timestamp DESC (más recientes primero) para no bloquear trades nuevos con docs viejos irresolubles. |
| `main_forwards.py` | `Trading.ForwardsLive` + `Trading.ForwardsHistorico` | Calcula matriz NxN de tasas forward por curva cada 30s. ForwardsLive = 1 doc por curva (tiempo real). ForwardsHistorico = 1 doc por (fecha, curva). |
| `main_breakevens.py` | `Trading.BreakevensLive` + `Trading.BreakevensHistorico` | Calcula breakeven de inflación mensual implícita CER/Lecap cada 30s. Empareja cada Lecap con el CER de vencimiento más cercano (≤60d). BreakevensLive = 1 doc global. BreakevensHistorico = 1 doc por fecha. |

### Trading.TimeSales

Colección de trades en tiempo real. Campos base (escritos por `main_valores.py`):
`ticker`, `timestamp`, `price`, `size`, `side` (BUY/SELL/MID), `money`

Campos enriquecidos por `main_curvas.py` (solo tickers en `Trading.Curvas`):

| Campo | Instrumentos | Descripción |
|---|---|---|
| `duration` | todos | Macaulay duration en años |
| `TEA` | tasa_fija + cer | Tasa efectiva anual |
| `TEM` | tasa_fija | Tasa efectiva mensual |
| `paridad` | cer | precio / (VN × CER_trade/CER_emision) × 100 |

### Trading.MarketSnapshot

Un doc por ticker, reemplazado cada 1 segundo por `main_valores.py`. Campos:
`ticker`, `updated_at`, `book` (bids/offers top 5), `metrics` (micro_price, spread, imbalance, VWAP, VPIN, total_nominals, total_money, buy_money, sell_money, last/open/high/low/closing_price), `hourly_stats` (por hora 10-17), `top_trades` (top 15 por size), `recent_trades` (últimos 30).

### Scripts de datos BCRA y Curvas

- **`data_bcra.py`** — alimenta CER/TAMAR/DOLAR/BADLAR desde API BCRA. `--today` para cron, sin flag hace backfill desde 2023-01-01.
- **`data_diashabiles.py`** — genera calendario de días hábiles argentinos (año fijo `YEAR`) y los carga en `Trading.DiasHabiles`. Requiere ejecutarse una vez por año. Usado por `main_curvas.py` y `backfill_curvas.py`.
- **`backfill_curvas.py`** — script one-off que recorre todo TimeSales y agrega TEA/TEM/Duration/Paridad a docs históricos de Curvas. Ya ejecutado (97k docs procesados).
- **`backfill_forwards.py`** — script one-off que construye ForwardsHistorico recorriendo las TEAs ya existentes en TimeSales. Ejecutar si hay que reconstruir el histórico.
- **`backfill_breakevens.py`** — script one-off que construye BreakevensHistorico recorriendo TEM/paridad de TimeSales. Ejecutar tras backfill_curvas o cuando se quiera reconstruir el histórico de breakevens.

### Trading.Curvas — estructura de flujos

Los flujos CER usan campos porcentuales (NO valores absolutos):
- `amortizacion_pct`: % del VN que se amortiza
- `cupon_sobre_residual`: tasa × `residual_previo_pct` / 100 × VN
- `cupon_anual`: solo zero coupon (= 0)

Los flujos tasa_fija usan valores absolutos: `amortizacion` + `interes`.

### Options Module (`Opciones/`)

- `calculos_cuantitativos.py`: Black-Scholes engine — `bs_price()`, `bs_delta()`, `bs_gamma()`, `bs_vega()`, `bs_theta()`, `find_iv()` (Newton-Raphson), `calc_intrinseco()`
- `estrategias_opciones.py`: Pre-configured spreads (bull calls, bear puts, ratio spreads) as (long, short) leg tuples
- Options target GGAL April expiration; historical volatility read from MongoDB collection `VR-GGal`

### FX Arbitrage Module (`arbitraje_fx/`)

- `calculadora.py`: Cross-currency buy/sell price math
- `calculadora_tasas.py`: Interest rate calculations
- `mongo_assets.py`: FX pair definitions in MongoDB
- `buscador_caucion.py`: Finds shortest-term overnight repo
- `trade_logger.py`: Logs matched trades to file

### Excel / Portfolio Sync (`Excel/`)

- Syncs Rofex account positions to MongoDB and exports to Google Sheets
- Uses `google_sheets_manager.py` with OAuth2 credentials in `ons-fx.json`
- `aunesa_api_manager.py` connects to Aunesa broker for additional data
- `main_cashflow.py` — carga movimientos de cash (depósitos, transferencias, extracciones) desde Aunesa API a `CashFlow.Movimientos`. Índice único por `comprobante`. Signo invertido respecto a API (depósitos positivos). Soporta `--today` para cron diario.
- `main_aum.py` — snapshot de posiciones valuadas de TODAS las cuentas activas desde Aunesa a `Valuaciones.AuM`. Modelo time series: clave `(id_cuenta, unidad, fecha_snapshot)`. Fórmulas de valuación: P×Q/100 para renta fija (Títulos Públicos, ONs, Letras, Fideicomisos, CPD); (P+1)×Q para futuros; P×Q para el resto. Filtros: excluye OTC y cash negativo. Tiene retry automático ante timeout de Aunesa (3 intentos, 60s entre intentos). Timeouts: `obtener_cuentas` y `consultar_posicion` = 60s.
- `main_flujo_contrapartes.py` — cron diario que carga operaciones de hoy desde Aunesa API a `CashFlow.Flujo`. Lógica: borra docs donde `concertacion == hoy`, fetch por cada contraparte con `cuenta` asignada (`fechaConcDesde/Hasta = hoy`), filtra 4 tipos excluidos, agrega campo `moneda` (ARS/USD desde `condiciones`), deduplica por `boleto`, inserta. Al final verifica duplicados en toda la colección y borra extras. Cron: 02:00 UTC martes-sábado.
- `fix_aum_valuacion.py` — script one-off que divide por 100 las valuaciones de ONs/Fideicomisos/CPD mal calculadas en Mongo (se ejecutó una vez tras el fix).
- `fix_sign_movimientos.py` — script one-off que invirtió signos de movimientos ya insertados en Mongo (se ejecutó una vez).
- `fix_flujo_borrar_tipos.py` — script one-off que borró de `CashFlow.Flujo` los tipos: "Concurrencia - Caución colocadora (Apertura/Cierre)" y "Futuros Financieros - Compra/Venta". Ya ejecutado.
- `backfill_moneda_flujo.py` — script one-off que agrega campo `moneda` (ARS/USD) a todos los docs existentes en `CashFlow.Flujo` inferido de `condiciones`. Ejecutar una vez si hay docs sin ese campo.
- `fix_contraparte_names.py` — script one-off (re-ejecutable) que actualiza el campo `contraparte` en todos los docs de `CashFlow.Flujo` usando `CashFlow.Contrapartes` como fuente de verdad. Join por `cuenta` con match flexible: string, int, float y variantes con/sin ceros adelante (ej: "20" == "020"). Correr tras cambiar nombres en Contrapartes.
- `set_segmento_contrapartes.py` — asigna campo `segmento` ("Fondos"/"ALYC"/"Bancos") en `CashFlow.Contrapartes`. Reglas automáticas: `denominacion` contiene "FCI" → Fondos; `contraparte` contiene "ALYC" → ALYC; `denominacion` contiene "BANCO" → Bancos. Para los sin match, modo interactivo: tecleás 1/2/3 o Enter para saltear.

## Deployment

Systemd services en `motor_rofex.service`, `streamlit.service`, `services/motor_options.service`, `services/motor_curvas.service` y `services/motor_forwards.service`. Producción corre en un **Droplet de Digital Ocean** como `root` en `/root/TradingAV/` con un venv local.

### Colecciones de referencia en Trading

- **`Trading.CER`** — Serie histórica del CER desde BCRA (variable id=30). Campos: `fecha`, `valor`. Upsert diario por `fecha`.
- **`Trading.TAMAR`** — Tasa TAMAR desde BCRA (variable id=44). Campos: `fecha`, `valor`.
- **`Trading.DOLAR`** — Tipo de cambio A3500 desde BCRA (variable id=5). Campos: `fecha`, `valor`.
- **`Trading.BADLAR`** — Tasa BADLAR desde BCRA (variable id=7). Campos: `fecha`, `valor`.
- **`Trading.Curvas`** — Definición estática de instrumentos de renta fija para pricing de curvas. Campos: `ticker`, `ticker_corto`, `tipo` (boncap/cer), `curva` (tasa_fija/cer), `fecha_vencimiento`, `fecha_emision`, `flujo_vencimiento`, `valor_nominal`, `cupon_anual`, `cer_emision`, `flujos[]`. Cargada manualmente en MongoDB.

`data_bcra.py` — script que alimenta CER/TAMAR/DOLAR/BADLAR. Sin `--today` hace backfill desde 2023-01-01; con `--today` pide solo el día actual (modo cron). La API puede devolver el último día hábil disponible si no hay dato para hoy — el upsert por `fecha` evita duplicados en cualquier caso.

### Scripts de diagnóstico

- **`check_cer_valuacion.py`** — muestra por bono CER cuál fecha/valor de CER se usó en el último trade enriquecido (settlement − 10 días hábiles). Útil para verificar que el motor de curvas está usando el CER correcto.
- **`check_curvas_pendientes.py`** — muestra cuántos docs sin `duration` hay por ticker en TimeSales. Útil para detectar si hay tickers bloqueando el batch del motor de curvas (ej: bonos vencidos con miles de trades sin enriquecer).
- **`check_forwards.py`** — diagnóstico completo de forwards por curva: lista todos los instrumentos en `Trading.Curvas`, cuáles tienen TEA disponible en TimeSales (aparecerían en la matriz) y cuáles no (ausentes), y compara el orden actual en `ForwardsLive` vs orden esperado por `fecha_vencimiento`.

### Notas sobre enriquecimiento CER

El CER usado para valuar depende del contexto:
- **TimeSales histórico**: cada trade usa el CER de su propia fecha de settlement (correcto por definición).
- **Vista de Mercado presente**: como todos los bonos operan diariamente, el último trade enriquecido es siempre de hoy → todos usan el CER de hoy automáticamente.
- **Problema potencial**: si un bono no opera un día, su último trade enriquecido puede ser de ayer con el CER de ayer. El fix sistémico sería recalcular on-the-fly con el CER de hoy (no implementado aún).

### CashFlow.Flujo

Operaciones de contrapartes cargadas desde Aunesa API. Campos guardados (definidos en `CAMPOS` de `main_flujo_contrapartes.py`):
`boleto`, `concertacion`, `tipoOperacion`, `cuenta`, `denominacion`, `instrumento`, `condiciones`, `bruto`, `segmento`, `contraparte`

Campo agregado por el script (NO viene de la API):
- `moneda`: "ARS" o "USD" inferido de `condiciones` (contiene "ARS" o "USD"). Agregado por `inferir_moneda()` en el cron y por `backfill_moneda_flujo.py` para históricos.

**Notas importantes sobre CAMPOS:**
- `segmento` aquí es el segmento de mercado que devuelve Aunesa (ej: "SENEBI", "MAE"). Es distinto del campo `segmento` de `CashFlow.Contrapartes` (que es "Fondos"/"ALYC"/"Bancos").
- `contraparte` se sobreescribe con el nombre que tenemos en `CashFlow.Contrapartes` (no el que devuelve Aunesa).
- `boleto` es la clave única. El cron deduplica al final de cada corrida.
- Tipos excluidos permanentemente: "Concurrencia - Caución colocadora (Apertura)", "Concurrencia - Caución colocadora (Cierre)", "Futuros Financieros - Compra", "Futuros Financieros - Venta".
- Las contrapartes deben tener el campo `cuenta` seteado en `CashFlow.Contrapartes` (ejecutar `test_match_contrapartes.py` para hacer el match con Aunesa si hace falta).

### CashFlow.Contrapartes

Catálogo de contrapartes. Campos relevantes:
- `contraparte`: nombre corto (ej: "MAX", "IEB"). Es la clave de join con `CashFlow.Flujo.contraparte`.
- `cuenta`: número de cuenta en Aunesa. Puede ser int, string, o CUIT ("30-67724257-0"). El cron usa este campo para hacer el fetch por cuenta. Puede haber múltiples docs para la misma contraparte (una por cuenta).
- `denominacion`: nombre legal completo (ej: "MAX VALORES S.A.").
- `segmento`: tipo de contraparte — "Fondos", "ALYC" o "Bancos". Asignado por `set_segmento_contrapartes.py`. Usado para filtrar en la vista Operaciones → Contrapartes y para la vista Flujo vs AuM (solo segmento=Fondos).

### Crontab del servidor (actualizado 2026-04-10)

```cron
# Prender/apagar motores y Streamlit: Lunes a Viernes
# 13:00 UTC = 10:00 AM ARG | 20:05 UTC = 17:05 PM ARG
0 13 * * 1-5 systemctl start motor_rofex.service
5 20 * * 1-5 systemctl stop motor_rofex.service
0 13 * * 1-5 systemctl start streamlit.service
5 20 * * 1-5 systemctl stop streamlit.service
0 13 * * 1-5 systemctl start motor_options.service
5 20 * * 1-5 systemctl stop motor_options.service

# main_carteras.py — sincronización Aunesa → MongoDB, 4 veces por día hábil
0 10 * * 1-5 /root/TradingAV/venv/bin/python /root/TradingAV/Excel/main_carteras.py >> /root/TradingAV/logs/carteras.log 2>&1
30 11 * * 1-5 /root/TradingAV/venv/bin/python /root/TradingAV/Excel/main_carteras.py >> /root/TradingAV/logs/carteras.log 2>&1
0 14 * * 1-5 /root/TradingAV/venv/bin/python /root/TradingAV/Excel/main_carteras.py >> /root/TradingAV/logs/carteras.log 2>&1
0 16 * * 1-5 /root/TradingAV/venv/bin/python /root/TradingAV/Excel/main_carteras.py >> /root/TradingAV/logs/carteras.log 2>&1

# VolatilidadGGAL.py — calcula VR histórica al cierre (20:00 UTC)
0 20 * * 1-5 /root/TradingAV/venv/bin/python /root/TradingAV/Opciones/VolatilidadGGAL.py >> /root/TradingAV/logs/vr_ggal.log 2>&1

# main_dolar_mep.py — snapshot dólar MEP (14:00 y 19:57 UTC)
0 14 * * 1-5 /root/TradingAV/venv/bin/python /root/TradingAV/main_dolar_mep.py >> /root/TradingAV/logs/dolar_mep.log 2>&1
57 19 * * 1-5 /root/TradingAV/venv/bin/python /root/TradingAV/main_dolar_mep.py >> /root/TradingAV/logs/dolar_mep.log 2>&1

# main_cashflow.py — carga diaria de movimientos de dinero a CashFlow.Movimientos
# 02:00 UTC = 23:00 ART (lunes a viernes ARG = martes a sábado UTC)
0 2 * * 2-6 /root/TradingAV/venv/bin/python /root/TradingAV/Excel/main_cashflow.py --today >> /root/TradingAV/logs/cashflow.log 2>&1

# main_flujo_contrapartes.py — carga operaciones de hoy a CashFlow.Flujo
0 2 * * 2-6 /root/TradingAV/venv/bin/python /root/TradingAV/Excel/main_flujo_contrapartes.py >> /root/TradingAV/logs/flujo_contrapartes.log 2>&1

# data_bcra.py — CER, TAMAR, DOLAR, BADLAR diario (17:00 ART = 20:00 UTC, todos los días)
0 20 * * * /root/TradingAV/venv/bin/python /root/TradingAV/data_bcra.py --today >> /root/TradingAV/logs/bcra.log 2>&1

# motor_curvas.service — enriquecimiento TEA/Duration TimeSales (lunes a viernes)
0 13 * * 1-5 systemctl start motor_curvas.service
5 20 * * 1-5 systemctl stop motor_curvas.service

# motor_forwards.service — tasas forward en tiempo real (lunes a viernes)
0 13 * * 1-5 systemctl start motor_forwards.service
5 20 * * 1-5 systemctl stop motor_forwards.service

# motor_breakevens.service — breakevens CER/Lecap en tiempo real (lunes a viernes)
0 13 * * 1-5 systemctl start motor_breakevens.service
5 20 * * 1-5 systemctl stop motor_breakevens.service

# main_aum.py — snapshot AuM al cierre (20:00 ART = 23:00 UTC, lunes a viernes)
0 23 * * 1-5 /root/TradingAV/venv/bin/python /root/TradingAV/Excel/main_aum.py >> /root/TradingAV/logs/aum.log 2>&1

# motor_on.service — Yield Screener ONs (lunes a viernes)
0 13 * * 1-5 systemctl start motor_on.service
0 20 * * 1-5 systemctl stop motor_on.service
```

Logs en `/root/TradingAV/logs/`.

## Streamlit Dashboard — Vistas

Nav principal: **Mercado · Opciones · Portfolios · Operaciones · AuM**

> **ONs pausado desde 2026-04-08**: la vista ONs fue removida del dashboard y el `motor_on.service` queda pausado. Todo lo relacionado a ONs (`main_on.py`, `vista_ons()`, `Trading.ONSnapshot`) está en el código pero desactivado hasta nuevo aviso.

| Vista | Sub-tabs | Descripción |
|---|---|---|
| Mercado | Mercado · Libro · Curvas · Breakevens · Forwards · Retorno Total · Volúmenes | Microstructure, VWAP, volumen intraday; Libro en tiempo real (run_every=2s); curvas de rendimiento, breakevens CER/Lecap, forwards, retorno total, volúmenes. Tab Mercado usa `@st.fragment(run_every=30)`. |
| Opciones | Mercado · Estrategias | Mercado: cadena GGAL con SPOT/VR/ADR/Tasa RF + volatility smile. Estrategias: spreads pre-configurados con payoff y costo histórico |
| Portfolios | una tab por cuenta | Posiciones por cuenta desde Aunesa (`Valuaciones.Carteras`). Dólar oficial leído automáticamente de `Trading.DOLAR` (último valor). Tab por cada `id_cuenta` único; filtro cartera dentro de cada tab |
| Operaciones | Cash Flow · Contrapartes · Análisis · Flujo vs AuM | Cash Flow: depósitos/transferencias/extracciones desde `CashFlow.Movimientos`. Contrapartes: filtros SEGMENTO+MONEDA en una fila (checkboxes compactos), flujo acumulado mensual + tabla consolidada + drill-down. Análisis: evolución Individual/Comparativo. Flujo vs AuM: gráfico dual para contrapartes segmento=Fondos. |
| AuM | FCI · Análisis SG · Tasa Fija | FCI: snapshot por fecha + gráfico evolución + detalle fondos por soc. gerente al clickear. Análisis SG: evolución AuM por sociedad gerente — modo Individual o Comparativo base 100. Tasa Fija: posiciones en instrumentos de `Trading.Curvas` (curva=tasa_fija); tabla Ticker/Vencimiento/Valuación + tabla cuentas al clickear ticker + gráfico cobros al vencimiento a ancho completo |
| ~~ONs~~ | — | ~~Yield screener ONs en tiempo real~~ — **pausado desde 2026-04-08** |

### Mercado → Tab Libro

Vista de order book en tiempo real para traders. Auto-refresh cada 2s via `@st.fragment(run_every=2)`.

**Layout:**
- **Header**: selector de ticker (izq) | última actualización (der, alineada sobre Quant)
- **Fila 1**: Depth (book top 5) + Hourly Vol · Tape · Quant Analytics
- **Fila 2**: Last Minutes chart · Volume Profile

**Last Minutes chart**: line chart con eje X temporal real (`:T`, formato `%H:%M:%S`) — escala proporcional, trades del mismo minuto no colapsan. Línea VWAP horizontal verde (`mark_rule`, `strokeDash=[6,3]`). Toggle `st.toggle("TEA")` para alternar eje Y entre Precio y TEA (formato `%`). Cuando está en modo TEA el VWAP se oculta. Mensajes vacíos usan `st.caption` (no `st.info`).

**Volume Profile**: query `Trading.TimeSales` desde medianoche UTC. Tick size dinámico: calcula rango real de precios, apunta a ~25 barras, redondea a número "lindo" (0.01/0.05/0.10/0.25/0.50/1.0...). Usa `groupby` sobre precio redondeado — solo aparecen niveles donde realmente se operó (sin buckets vacíos). Interactivo (zoom/pan).

**Tabla Whales eliminada** (2026-04-10). Reemplazada por Volume Profile en fila 2.

### AuM → Tab Tasa Fija

Muestra posiciones de instrumentos cuyo `ticker_corto` está en `Trading.Curvas` con `curva=tasa_fija`.

**Flujo de joins:**
1. `Trading.Curvas` (filtro `curva=tasa_fija`) → `ticker_corto`, `fecha_vencimiento`, `flujo_vencimiento`
2. `Valuaciones.Assets` → match `TICKER == ticker_corto` → obtiene `unidad`
3. `Valuaciones.AuM` → filtra por esas `unidad` → `cantidad` (nominales) y `valuacion`

**Layout:**
- Fila 1: tabla Ticker/Vencimiento/Valuación (izq) | tabla Cuenta/Valuación al clickear ticker (der) — misma altura
- Fila 2: gráfico a ancho completo — barras apiladas por ticker, solo cobros al vencimiento (`cantidad × flujo_vencimiento / 100`)

**Para agregar instrumentos:** insertar doc en `Trading.Curvas` con `curva: "tasa_fija"` + doc en `Valuaciones.Assets` con `TICKER == ticker_corto`. Sin esos dos docs el instrumento no aparece aunque haya posición en AuM.

### Operaciones → Tab Flujo vs AuM

Vista para analizar reciprocidad con fondos (contrapartes con `segmento=Fondos`).

**Join chain:**
1. `CashFlow.Contrapartes` (segmento=Fondos) → nombres de emisores (deduplicados)
2. `Valuaciones.Assets` (CARTERA=CARTERA FCI, EMISOR in fondos) → unidades FCI del emisor
3. `Valuaciones.AuM` (unidad in esas unidades) → valuacion diaria por emisor
4. `CashFlow.Flujo` (contraparte in fondos, moneda=ARS) → flujo diario

**Gráfico:** dual eje Y independiente. Eje X temporal dinámico (día/semana/mes según rango).
- **Barras verde/rojo** (eje izq): flujo ARS por día de operación. Verde = entra plata, rojo = sale.
- **Línea naranja** (`#f4a261`, eje der): AuM con **forward-fill** diario (último valor conocido se arrastra). Escala `zero=False` con dominio explícito ±15% del rango real.
- **Leyenda** sobre el gráfico: ■ AuM actual (valor) + ■ Flujo acumulado (valor) en formato M/B.

**Inicio del gráfico**: primer `fecha_snapshot` disponible para ese emisor en AuM.

### Trading.ForwardsLive y Trading.ForwardsHistorico

- Vista Forwards tiene **auto-refresh cada 30s** (`@st.fragment(run_every=30)`), tanto en tab de Mercado como en página dedicada. Matriz se muestra como "MATRIZ DE TASAS FORWARD (TEA)".
- **`ForwardsLive`**: 1 doc por curva, upsert en cada corrida del motor. Campos: `curva`, `updated_at`, `tickers` (lista ordenada por maturity), `tasas` (spot TEA por ticker), `matrix` (dict anidado `matrix[ticker_largo][ticker_corto] = forward_rate`).
- **`ForwardsHistorico`**: 1 doc por `(fecha, curva)`. Mismo schema que ForwardsLive + campo `fecha` (string ISO). El motor lo actualiza durante la rueda; el valor final del día queda como cierre.
- **Forward formula**: `((1 + TEA_B)^t_B / (1 + TEA_A)^t_A)^(1/(t_B - t_A)) - 1` donde `t` = días a vencimiento desde hoy / 365.
- **Dependencia**: `main_forwards.py` requiere que `main_curvas.py` haya enriquecido TimeSales con TEA (lag ~5s aceptable dado que forwards corre cada 30s).

### Trading.BreakevensLive y Trading.BreakevensHistorico

- **`BreakevensLive`**: 1 doc global (`_id: "breakevens"`), upsert en cada corrida. Campos: `updated_at`, `pares` (lista de pares Lecap/CER, ordenados por vencimiento).
- **`BreakevensHistorico`**: 1 doc por `fecha` (string ISO). Mismo schema + `fecha`. El motor lo actualiza durante la rueda.
- **Schema de cada par**: `n`, `lecap` (ticker_corto), `cer` (ticker_corto), `fecha_vencimiento`, `dias`, `tem_lecap` (decimal), `paridad_cer` (ej: 101.0), `retorno_acumulado`, `inflacion_acumulada`, `breakeven_mensual` (decimal).
- **Fórmulas**: `retorno = (1+TEM)^(días/30) - 1` | `inflacion = (1+retorno) * (paridad/100) - 1` | `breakeven = (1+inflacion)^(30/días) - 1`
- **Emparejamiento**: cada Lecap (`curva=tasa_fija`) se empareja con el CER (`curva=cer`) de vencimiento más cercano (máx 60 días de diferencia).
- **Dependencia**: requiere TEM en TimeSales (escrito por `main_curvas.py`) y paridad en TimeSales (también `main_curvas.py`).

### Notas técnicas importantes
- **Altair v4 pie labels**: usar `mark_text(radius=N, color="white")` dentro del arco. Labels fuera del arco se cortan.
- **Altair eje X duplicado en barras mensuales**: usar `strftime` para agrupar como string + encoding `:O` con `sort=` explícito, nunca `:T`.
- **Altair fontWeight**: usar entero (`fontWeight=600`), no string.
- **Dólar Oficial en Portfolios**: leído automáticamente de `Trading.DOLAR` (sort por `fecha` desc, campo `valor`). Ya no hay input manual de MEP en esa vista.
- **Valuación AuM**: recalculada en la vista (no solo leída de Mongo) para corregir datos históricos. TIPOS_DIVISOR_100 = {Títulos Públicos, Letras, ONs, Fideicomisos, CPD}.
- **CashFlow**: DB se llama `CashFlow` (sin espacio). Signo: depósitos positivos, extracciones negativas.
- **En el servidor**: siempre usar `/root/TradingAV/venv/bin/python`, no `python3`.
