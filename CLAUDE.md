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

# Individual engines (run as background daemons)
python main_ts.py         # Microstructure engine (30+ tickers)
python main_options.py    # Options pricing/Greeks
python main_fx.py         # FX arbitrage detection
python main_arbitrage.py  # Plazo/term arbitrage
python main_valores.py    # Equity tracking

# Production deployment
./start_all.sh
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
MongoDB Atlas (3 databases: Trading, Opciones, Valuaciones)
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
| `main_ts.py` | `Trading.Data` | Microstructure: order book, VWAP, volume bucketing, trade tape |
| `main_values.py` | `Trading.Data` | Equity tracking (GGAL, VSCJO, BVCOO, DHSGO) |
| `main_options.py` | `Opciones.OptionsSnapshot` | GGAL options: Black-Scholes Greeks, IV via Newton-Raphson |
| `main_fx.py` | `Trading.FXArbitrage` | USD pair cross-currency arbitrage (fee: 0.0847%) |
| `main_arbitrage.py` | `Trading.CI24` | CI/24hs cash+carry repo arbitrage (headless daemon) |

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
- `main_aum.py` — snapshot de posiciones valuadas de TODAS las cuentas activas desde Aunesa a `Valuaciones.AuM`. Modelo time series: clave `(id_cuenta, unidad, fecha_snapshot)`. Fórmulas de valuación: P×Q/100 para renta fija (Títulos Públicos, ONs, Letras, Fideicomisos, CPD); (P+1)×Q para futuros; P×Q para el resto. Filtros: excluye OTC y cash negativo.
- `fix_aum_valuacion.py` — script one-off que divide por 100 las valuaciones de ONs/Fideicomisos/CPD mal calculadas en Mongo (se ejecutó una vez tras el fix).
- `fix_sign_movimientos.py` — script one-off que invirtió signos de movimientos ya insertados en Mongo (se ejecutó una vez).

## Deployment

Systemd services en `motor_rofex.service`, `streamlit.service` y `services/motor_options.service`. Producción corre en un **Droplet de Digital Ocean** como `root` en `/root/TradingAV/` con un venv local.

### Crontab del servidor (actualizado 2026-03-23)

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
```

Logs en `/root/TradingAV/logs/`.

## Streamlit Dashboard — Vistas

| Vista | Descripción |
|---|---|
| Libro | Order book en tiempo real de ROFEX |
| Mercado | Microstructure, VWAP, volumen intraday |
| Opciones | Greeks GGAL, Black-Scholes, IV |
| Estrategias Opciones | Spreads pre-configurados |
| Carteras | Posiciones por cuenta/cartera desde Aunesa; MEP editable guardado en `Valuaciones.Dolar` |
| Operaciones | Cash Flow (depósitos/transferencias/extracciones) desde `CashFlow.Movimientos`; filtros por fecha, moneda, accionista; gráficos ARS y USD independientes |
| AuM | Posiciones valuadas desde `Valuaciones.AuM`; modos Total/Por cuenta; moneda ARS o USD MEP; tabla Instrumento+Tipo+Valuación + torta por tipo de activo |

### Notas técnicas importantes
- **Altair v4 pie labels**: usar `mark_text(radius=N, color="white")` dentro del arco. Labels fuera del arco se cortan.
- **Altair eje X duplicado en barras mensuales**: usar `strftime` para agrupar como string + encoding `:O` con `sort=` explícito, nunca `:T`.
- **Altair fontWeight**: usar entero (`fontWeight=600`), no string.
- **MEP**: guardado en `Valuaciones.Dolar` con `{"type": "config", "mep": valor}`. Editable desde vista Carteras.
- **Valuación AuM**: recalculada en la vista (no solo leída de Mongo) para corregir datos históricos. TIPOS_DIVISOR_100 = {Títulos Públicos, Letras, ONs, Fideicomisos, CPD}.
- **CashFlow**: DB se llama `CashFlow` (sin espacio). Signo: depósitos positivos, extracciones negativas.
- **En el servidor**: siempre usar `/root/TradingAV/venv/bin/python`, no `python3`.
