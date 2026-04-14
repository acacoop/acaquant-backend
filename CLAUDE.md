# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TradingAV is a quantitative trading platform for Argentine financial markets (MERVAL/ROFEX). It streams real-time market data, runs parallel analytical engines (microstructure, options, curvas, forwards, breakevens), persists state to MongoDB Atlas, and exposes a Streamlit dashboard accessible via `www.acaquant.com`.

## Acceso al Dashboard

Dos deploys activos en paralelo. La migración definitiva a acaquant está en curso.

| Deploy | URL | Auth | Estado |
|---|---|---|---|
| Streamlit Cloud | URL privada de streamlit.io | Sin login (URL secreta) | Activo — uso actual de usuarios |
| Droplet + Cloudflare | www.acaquant.com | Cloudflare Access (email OTP) | Activo — nuevo, en transición |

**acaquant.com**: corre en el Droplet de DigitalOcean. `cloudflared.service` (always-on, systemd) establece el tunnel hacia Cloudflare. Cloudflare Access exige autenticación por email OTP antes de llegar al servidor. `streamlit.service` también es always-on.

**Vista Manager restringida**: solo emails en `MANAGER_EMAILS` (`.env`) pueden ver y acceder al Manager. El email autenticado lo lee Streamlit del header HTTP `Cf-Access-Authenticated-User-Email` que inyecta Cloudflare. En local (sin Cloudflare) el Manager es accesible para todos.

## Variables de entorno requeridas (`.env`)

```
ROFEX_USER / ROFEX_PASSWORD / ROFEX_ACCOUNT / ROFEX_API_URL / ROFEX_WS_URL
MONGO_URI          ← usuario read-write (motores + Manager)
MONGO_URI_READ     ← usuario read-only (dashboard Streamlit)
AUNESA_CLIENT_ID / AUNESA_USERNAME / AUNESA_PASSWORD
MANAGER_EMAILS     ← emails separados por coma con acceso al Manager
```

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
python main_curvas.py           # Enriquecimiento TEA/Duration TimeSales (motor_curvas.service)
python main_forwards.py         # Tasas forward en tiempo real (motor_forwards.service)
python main_breakevens.py       # Breakevens CER/Lecap en tiempo real (motor_breakevens.service)

# Production deployment (systemd services start/stop via crontab)
# See crontab section below
```

No hay test suite ni linting configurado.

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
   ┌────┴────┬──────────┐
   ▼         ▼          ▼
main_valores  main_options  main_curvas
        │
        ▼
MongoDB Atlas (4 databases: Trading, Opciones, Valuaciones, CashFlow)
        │
        ▼
Streamlit Dashboard (www.acaquant.com)
```

### Key Components

- **`config.py`** — Config centralizado; carga `.env` (credenciales ROFEX, Aunesa); `MANAGER_EMAILS` para control de acceso al Manager.
- **`session_manager.py`** — Auth única de pyRofex.
- **`websocket_manager.py`** — Suscripciones WebSocket; registra handlers `update_price(ticker, data)` por motor.
- **`mongo_manager.py`** — Dos clientes singleton thread-safe:
  - `get_mongo_client()` → `MONGO_URI` (read-write). Usado por motores, crons y Manager.
  - `get_mongo_client_read()` → `MONGO_URI_READ` (read-only). Usado por todas las vistas del dashboard. Fallback a `MONGO_URI` si `MONGO_URI_READ` no está definido.
  - **Nunca llamar `client.close()`** — ambos clientes son singletons de larga vida; cerrarlos rompe el pool compartido con Streamlit.

### Trading Engines

Cada motor tiene `update_price(ticker, data)` llamado por el WebSocket en cada tick.

| Motor | Colección MongoDB | Descripción |
|---|---|---|
| `main_valores.py` | `Trading.TimeSales` + `Trading.MarketSnapshot` | Microestructura bonos/Lecaps/CER: inserta trades en TimeSales, snapshot cada 1s en MarketSnapshot |
| `main_options_service.py` | `Opciones.OptionsSnapshot` | Opciones GGAL: Black-Scholes Greeks, IV via Newton-Raphson. Headless (motor_options.service) |
| `main_curvas.py` | `Trading.TimeSales` (enriquecimiento) | Agrega TEA/TEM/Duration/Paridad. Loop cada 5s, docs sin `duration` ordenados DESC para no bloquear con docs viejos irresolubles |
| `main_forwards.py` | `Trading.ForwardsLive` + `Trading.ForwardsHistorico` | Matriz NxN de tasas forward por curva cada 30s |
| `main_breakevens.py` | `Trading.BreakevensLive` + `Trading.BreakevensHistorico` | Breakeven inflación mensual implícita CER/Lecap cada 30s |

### Trading.TimeSales

Trades en tiempo real. Campos base (`main_valores.py`): `ticker`, `timestamp`, `price`, `size`, `side` (BUY/SELL/MID), `money`

Campos enriquecidos por `main_curvas.py` (solo tickers en `Trading.Curvas`):

| Campo | Instrumentos | Descripción |
|---|---|---|
| `duration` | todos | Macaulay duration en años |
| `TEA` | tasa_fija + cer | Tasa efectiva anual |
| `TEM` | tasa_fija | Tasa efectiva mensual |
| `paridad` | cer | precio / (VN × CER_trade/CER_emision) × 100 |

### Trading.MarketSnapshot

Un doc por ticker, reemplazado cada 1s. Campos: `ticker`, `updated_at`, `book` (bids/offers top 5), `metrics` (micro_price, spread, imbalance, VWAP, VPIN, total_nominals, total_money, buy_money, sell_money, last/open/high/low/closing_price), `hourly_stats` (por hora 10-17), `top_trades` (top 15 por size), `recent_trades` (últimos 30).

### Trading.Curvas — estructura de flujos

Flujos CER usan campos porcentuales (NO valores absolutos):
- `amortizacion_pct`: % del VN que se amortiza
- `cupon_sobre_residual`: tasa × `residual_previo_pct` / 100 × VN
- `cupon_anual`: solo zero coupon (= 0)

Flujos tasa_fija usan valores absolutos: `amortizacion` + `interes`.

### Options Module (`Opciones/`)

- `calculos_cuantitativos.py`: Black-Scholes — `bs_price()`, `bs_delta()`, `bs_gamma()`, `bs_vega()`, `bs_theta()`, `find_iv()` (Newton-Raphson), `calc_intrinseco()`
- `VolatilidadGGAL.py`: calcula volatilidad realizada histórica al cierre. Cron 20:00 UTC.
- Options target GGAL; volatilidad histórica leída de `VR-GGal`.

### Excel / Portfolio Sync (`Excel/`)

- **`aunesa_api_manager.py`** — cliente Aunesa API (auth + posicionValuada).
- **`main_carteras.py`** — sincroniza posiciones Aunesa → `Valuaciones.Carteras`. Clave upsert: `(id_cuenta, unidad)`. Filtra unidades inválidas antes de guardar (`filtrar_unidades()`): excluye exactas `{ARS, USDL}` y las que contienen `[1] Depósito U$`, `OTC`, `2024`, `2025`, `DLR`. Flag `--clean` para borrar docs ya existentes con esas unidades.
- **`main_aum.py`** — snapshot AuM de TODAS las cuentas activas → `Valuaciones.AuM`. Clave: `(id_cuenta, unidad, fecha_snapshot)`. Fórmulas: P×Q/100 para renta fija (Títulos Públicos, ONs, Letras, Fideicomisos, CPD); (P+1)×Q para futuros; P×Q para el resto. Retry automático ante timeout Aunesa (3 intentos, 60s). Cron 23:00 UTC.
- **`main_cashflow.py`** — movimientos de cash desde Aunesa → `CashFlow.Movimientos`. Índice único por `comprobante`. Signo invertido (depósitos positivos). `--today` para cron.
- **`main_flujo_contrapartes.py`** — operaciones del día desde Aunesa → `CashFlow.Flujo`. Borra docs donde `concertacion == hoy`, fetch por cada contraparte con `cuenta` asignada, filtra 4 tipos excluidos, agrega `moneda` (ARS/USD), deduplica por `boleto`. Cron 02:00 UTC martes-sábado.
- **`set_segmento_contrapartes.py`** — asigna `segmento` ("Fondos"/"ALYC"/"Bancos") en `CashFlow.Contrapartes`. Reglas automáticas + modo interactivo para sin match. Importado por `views/data_manager.py`.
- **`backfill_aum.py`** — re-ejecutable, reconstruye AuM por fechas. Usado desde el Manager (subprocess).
- **`test_match_contrapartes.py`** — match de contrapartes con Aunesa. Importado por `views/data_manager.py`.

### Scripts de datos y diagnóstico

- **`data_bcra.py`** — alimenta CER/TAMAR/DOLAR/BADLAR desde API BCRA. `--today` para cron; sin flag hace backfill desde 2023-01-01. SSL verificado (verify=True).
- **`data_diashabiles.py`** — genera calendario de días hábiles argentinos. Ejecutar una vez por año.
- **`crear_indices.py`** — crea todos los índices MongoDB necesarios. Idempotente. Ejecutar al agregar colecciones nuevas o en un servidor nuevo.
- **`check_cer_valuacion.py`** — muestra el CER usado en el último trade enriquecido por bono.
- **`check_curvas_pendientes.py`** — cuántos docs sin `duration` hay por ticker en TimeSales.
- **`check_forwards.py`** — diagnóstico completo de forwards por curva.
- **`check_tasa_fija.py`** — diagnóstico de instrumentos tasa_fija en AuM.

## Deployment

**Servidor**: Droplet de DigitalOcean, `root` en `/root/TradingAV/`, venv local en `/root/TradingAV/venv/`.

**Servicios always-on** (arrancan con el servidor):
- `cloudflared.service` — Cloudflare Tunnel, siempre activo
- `streamlit.service` — dashboard Streamlit, siempre activo

**Servicios de mercado** (lunes a viernes, horario de mercado):
- `motor_rofex.service` → `main_valores.py` (archivo .service solo existe en el servidor, no en el repo)
- `motor_options.service` → `main_options_service.py`
- `motor_curvas.service` → `main_curvas.py`
- `motor_forwards.service` → `main_forwards.py`
- `motor_breakevens.service` → `main_breakevens.py`

### Crontab del servidor (actualizado 2026-04-14)

```cron
# Motores de mercado: Lunes a Viernes
# 13:00 UTC = 10:00 ART | 20:05 UTC = 17:05 ART
0 13 * * 1-5 systemctl start motor_rofex.service
5 20 * * 1-5 systemctl stop motor_rofex.service
0 13 * * 1-5 systemctl start motor_options.service
5 20 * * 1-5 systemctl stop motor_options.service
0 13 * * 1-5 systemctl start motor_curvas.service
5 20 * * 1-5 systemctl stop motor_curvas.service
0 13 * * 1-5 systemctl start motor_forwards.service
5 20 * * 1-5 systemctl stop motor_forwards.service
0 13 * * 1-5 systemctl start motor_breakevens.service
5 20 * * 1-5 systemctl stop motor_breakevens.service

# main_carteras.py — sincronización Aunesa → MongoDB, 4 veces por día hábil
0 10 * * 1-5 /root/TradingAV/venv/bin/python /root/TradingAV/Excel/main_carteras.py >> /root/TradingAV/logs/carteras.log 2>&1
30 11 * * 1-5 /root/TradingAV/venv/bin/python /root/TradingAV/Excel/main_carteras.py >> /root/TradingAV/logs/carteras.log 2>&1
0 14 * * 1-5 /root/TradingAV/venv/bin/python /root/TradingAV/Excel/main_carteras.py >> /root/TradingAV/logs/carteras.log 2>&1
0 16 * * 1-5 /root/TradingAV/venv/bin/python /root/TradingAV/Excel/main_carteras.py >> /root/TradingAV/logs/carteras.log 2>&1

# VolatilidadGGAL.py — calcula VR histórica al cierre
0 20 * * 1-5 /root/TradingAV/venv/bin/python /root/TradingAV/Opciones/VolatilidadGGAL.py >> /root/TradingAV/logs/vr_ggal.log 2>&1

# main_dolar_mep.py — snapshot dólar MEP
0 14 * * 1-5 /root/TradingAV/venv/bin/python /root/TradingAV/main_dolar_mep.py >> /root/TradingAV/logs/dolar_mep.log 2>&1
57 19 * * 1-5 /root/TradingAV/venv/bin/python /root/TradingAV/main_dolar_mep.py >> /root/TradingAV/logs/dolar_mep.log 2>&1

# main_cashflow.py — movimientos de dinero (02:00 UTC = 23:00 ART)
0 2 * * 2-6 /root/TradingAV/venv/bin/python /root/TradingAV/Excel/main_cashflow.py --today >> /root/TradingAV/logs/cashflow.log 2>&1

# main_flujo_contrapartes.py — operaciones del día
0 2 * * 2-6 /root/TradingAV/venv/bin/python /root/TradingAV/Excel/main_flujo_contrapartes.py >> /root/TradingAV/logs/flujo_contrapartes.log 2>&1

# data_bcra.py — CER, TAMAR, DOLAR, BADLAR (20:00 UTC, todos los días)
0 20 * * * /root/TradingAV/venv/bin/python /root/TradingAV/data_bcra.py --today >> /root/TradingAV/logs/bcra.log 2>&1

# main_aum.py — snapshot AuM al cierre (23:00 UTC = 20:00 ART)
0 23 * * 1-5 /root/TradingAV/venv/bin/python /root/TradingAV/Excel/main_aum.py >> /root/TradingAV/logs/aum.log 2>&1

```

Logs en `/root/TradingAV/logs/`. Python siempre via `/root/TradingAV/venv/bin/python`.

## Streamlit Dashboard — Vistas

Nav principal: **Mercado · Opciones · Portfolios · Operaciones · AuM · Manager**

Manager visible solo para emails en `MANAGER_EMAILS`. Determinado por header `Cf-Access-Authenticated-User-Email` de Cloudflare Access.

| Vista | Sub-tabs | Descripción |
|---|---|---|
| Mercado | Mercado · Libro · Curvas · Breakevens · Forwards · Retorno Total · Volúmenes | Microstructure, VWAP, volumen intraday; Libro en tiempo real (run_every=2s); curvas, breakevens (sub-tabs Tiempo Real · Histórico · Gráfico · Simulador), forwards, retorno total. Tab Mercado usa `@st.fragment(run_every=30)`. |
| Opciones | Mercado · Estrategias | Cadena GGAL con SPOT/VR/ADR/Tasa RF + volatility smile. Estrategias: spreads pre-configurados con payoff y costo histórico. |
| Portfolios | una tab por cuenta | Posiciones por cuenta desde Aunesa (`Valuaciones.Carteras`). Dólar oficial de `Trading.DOLAR`. Tab por `id_cuenta`; filtro cartera dentro de cada tab. |
| Operaciones | Cash Flow · Contrapartes · Análisis · Flujo vs AuM | Cash Flow: `CashFlow.Movimientos`, filtro "Todas / Sin accionistas / Solo accionistas / Solo cooperativas". Contrapartes: filtros SEGMENTO+MONEDA, flujo mensual + drill-down. Análisis: Individual/Comparativo. Flujo vs AuM: gráfico dual para segmento=Fondos. |
| AuM | FCI · Análisis SG · Tasa Fija · CER | FCI: snapshot por fecha + evolución + detalle por soc. gerente. Análisis SG: Individual o Comparativo base 100. Tasa Fija y CER: toggle "Valor Nominal" alterna columna entre `cantidad` (VN) y `valuacion` (P×Q). |
| Manager | Diagnóstico · Backfills · Validaciones · Logs · Historial · Setup · Latencia | Solo admins. Backfills, upserts a Assets/Contrapartes, flujo inline, audit log en `Manager.ChangeLog`. Tab Latencia: benchmark en tiempo real de todas las queries MongoDB del dashboard (ms, docs, ms/doc). |

### Mercado → Tab Libro

Auto-refresh cada 2s via `@st.fragment(run_every=2)`.

- **Header**: selector de ticker (izq) | última actualización (der)
- **Fila 1**: Depth (book top 5) + Hourly Vol · Tape · Quant Analytics
- **Fila 2**: Last Minutes chart · Volume Profile

**Last Minutes chart**: eje X temporal real (`:T`, `%H:%M:%S`). VWAP horizontal verde (`strokeDash=[6,3]`). Toggle TEA alterna eje Y entre Precio y TEA (oculta VWAP en modo TEA).

**Volume Profile**: query desde medianoche UTC. Tick size dinámico (~25 barras). Solo niveles con operaciones reales.

### AuM → Tab Tasa Fija

Join chain: `Trading.Curvas` (curva=tasa_fija) → `ticker_corto` → `Valuaciones.Assets` (TICKER==ticker_corto) → `unidad` → `Valuaciones.AuM`.

Layout: tabla Ticker/Vencimiento/Valuación (izq) | tabla Cuenta/Valuación al clickear (der) | gráfico cobros al vencimiento (ancho completo).

Para agregar instrumentos: insertar doc en `Trading.Curvas` con `curva: "tasa_fija"` + doc en `Valuaciones.Assets` con `TICKER == ticker_corto`.

### Operaciones → Tab Flujo vs AuM

Join chain: `CashFlow.Contrapartes` (segmento=Fondos) → `Valuaciones.Assets` (CARTERA FCI, EMISOR in fondos) → `Valuaciones.AuM` (valuacion diaria) + `CashFlow.Flujo` (contraparte in fondos, moneda=ARS).

Gráfico dual: barras verde/rojo (flujo, eje izq) + línea naranja con forward-fill (AuM, eje der, `zero=False`).

### Trading.ForwardsLive y Trading.ForwardsHistorico

- `ForwardsLive`: 1 doc por curva. Campos: `curva`, `updated_at`, `tickers`, `tasas`, `matrix`.
- `ForwardsHistorico`: 1 doc por `(fecha, curva)`.
- Forward formula: `((1 + TEA_B)^t_B / (1 + TEA_A)^t_A)^(1/(t_B - t_A)) - 1`
- Requiere TEA en TimeSales (escrito por `main_curvas.py`, lag ~5s aceptable).

### Trading.BreakevensLive y Trading.BreakevensHistorico

- `BreakevensLive`: 1 doc global (`_id: "breakevens"`).
- `BreakevensHistorico`: 1 doc por `fecha`.
- Schema por par: `lecap`, `cer`, `fecha_vencimiento`, `dias`, `tem_lecap`, `paridad_cer`, `retorno_acumulado`, `inflacion_acumulada`, `breakeven_mensual`.
- Fórmulas: `retorno = (1+TEM)^(días/30) - 1` | `inflacion = (1+retorno)*(paridad/100) - 1` | `breakeven = (1+inflacion)^(30/días) - 1`
- Emparejamiento: Lecap con CER de vencimiento más cercano (máx 60 días de diferencia).

### Mercado → Breakevens → Simulador

Calcula P&L relativo **CER vs Lecap** por par, bajo escenarios de inflación mensual flat.

- **Settlement T+1** (próximo día hábil); **CER liq** = settlement − 10 días hábiles.
- `ret_lecap = flujo_vencimiento / precio_lecap − 1` (tasa fija, cierto).
- Para cada flujo pendiente del CER: `CER_proy = cer_liq × (1 + infl)^meses` → `flujo_pesos = monto_VN × CER_proy / cer_emision`.
- `ret_cer = Σ flujo_pesos / precio_cer − 1` → `P&L = ret_cer − ret_lecap` (en bps, verde/rojo).
- El BE mensual calculado por `main_breakevens.py` debería caer entre los dos escenarios donde el P&L cambia de signo (verificación visual).

## Colecciones de referencia

### Trading
- **`CER`** — Serie BCRA id=30. `fecha`, `valor`.
- **`TAMAR`** — BCRA id=44.
- **`DOLAR`** — A3500, BCRA id=5.
- **`BADLAR`** — BCRA id=7.
- **`Curvas`** — Definición estática de renta fija. `ticker`, `ticker_corto`, `tipo`, `curva` (tasa_fija/cer), `fecha_vencimiento`, `fecha_emision`, `flujo_vencimiento`, `valor_nominal`, `cupon_anual`, `cer_emision`, `flujos[]`. Cargada manualmente.
- **`DiasHabiles`** — Calendario hábil argentino. Generado por `data_diashabiles.py` una vez por año.

### CashFlow.Flujo

Campos: `boleto`, `concertacion`, `tipoOperacion`, `cuenta`, `denominacion`, `instrumento`, `condiciones`, `bruto`, `segmento`, `contraparte`, `moneda`.

- `segmento` = segmento de mercado Aunesa (ej: "SENEBI", "MAE"). Distinto del `segmento` de Contrapartes.
- `contraparte` sobreescrito con nombre de `CashFlow.Contrapartes`.
- `boleto` = clave única, deduplicado en cada corrida.
- Tipos excluidos: "Concurrencia - Caución colocadora (Apertura/Cierre)", "Futuros Financieros - Compra/Venta".

### CashFlow.Contrapartes

- `contraparte`: clave de join con `Flujo.contraparte`.
- `cuenta`: número en Aunesa (int, string, o CUIT). Puede haber múltiples docs por contraparte.
- `denominacion`: nombre legal.
- `segmento`: "Fondos" / "ALYC" / "Bancos". Asignado por `set_segmento_contrapartes.py`.

### CashFlow.Accionistas y Cooperativas

- **`CashFlow.Accionistas`**: colección manual con `{cuenta, accionista}`. Permite consolidar múltiples comitentes de un mismo accionista bajo un nombre único (ej. varios comitentes → "LA SEGUNDA"). Usada en el filtro de Cash Flow.
- **Cooperativas**: sin colección propia. Auto-detectadas en runtime por regex `\bcoop` case-insensitive sobre el nombre de cuenta, excluyendo cuentas que estén en `Accionistas`. Filtro "Solo cooperativas" en Cash Flow. Cada cuenta coop tiene 1 solo comitente (no requiere consolidación).

## MongoDB Índices

Definidos en `crear_indices.py`. Ejecutar en servidor nuevo o al agregar colecciones.

| Colección | Índice |
|---|---|
| `Trading.TimeSales` | `(ticker, timestamp)`, `(ticker, duration, timestamp)`, `(ticker, TEA, timestamp)`, `(ticker, TEM, timestamp)`, `(ticker, paridad, timestamp)` |
| `Trading.MarketSnapshot` | `ticker` |
| `Trading.ForwardsHistorico` | `(curva, fecha)` |
| `Trading.BreakevensHistorico` | `fecha` |
| `Trading.CER` | `fecha` |
| `Trading.Curvas` | `curva`, `ticker_corto` |
| `Valuaciones.AuM` | `fecha_snapshot`, `(unidad, fecha_snapshot)`, `(id_cuenta, fecha_snapshot)` |
| `Valuaciones.Carteras` | `(id_cuenta, unidad)` |
| `Valuaciones.Assets` | `unidad`, `(EMISOR, CARTERA)` |
| `CashFlow.Flujo` | `(contraparte, moneda)`, `concertacion`, `boleto` |
| `CashFlow.Movimientos` | `fecha` |

## Notas técnicas importantes

- **MongoClient**: nunca llamar `client.close()`. Es un singleton compartido; cerrarlo mata el pool de Streamlit y tira `InvalidOperation` en todas las queries subsiguientes.
- **Dos clientes MongoDB**: `get_mongo_client()` (read-write) para motores y Manager. `get_mongo_client_read()` (read-only) para todas las vistas del dashboard. Fallback a `MONGO_URI` si `MONGO_URI_READ` no está definido.
- **Cloudflare Access header**: `Cf-Access-Authenticated-User-Email` — solo presente cuando el request pasa por Cloudflare Access. En local el header no existe y `MANAGER_EMAILS` vacío permite acceso.
- **Altair v4 pie labels**: usar `mark_text(radius=N, color="white")` dentro del arco.
- **Altair eje X duplicado en barras mensuales**: usar `strftime` para agrupar como string + encoding `:O` con `sort=` explícito.
- **Altair fontWeight**: entero (`fontWeight=600`), no string.
- **Dólar Oficial en Portfolios**: leído de `Trading.DOLAR` (sort por `fecha` desc). No hay input manual.
- **Valuación AuM**: recalculada en la vista. `TIPOS_DIVISOR_100 = {Títulos Públicos, Letras, ONs, Fideicomisos, CPD}`.
- **CashFlow DB**: se llama `CashFlow` (sin espacio). Depósitos positivos, extracciones negativas.
- **Enriquecimiento CER**: el CER usado depende de la fecha de settlement del trade (T-10 días hábiles). Si un bono no opera un día, su último trade enriquecido puede usar el CER de ayer.
- **En el servidor**: siempre usar `/root/TradingAV/venv/bin/python`.
