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

## Estructura de carpetas

```
TradingAV/
├── streamlit_app.py          # entrypoint Streamlit (monolítico aún)
├── config.py                 # credenciales .env
│
├── core/                     # infra compartida (importada por todos)
│   ├── mongo.py              # singletons get_mongo_client() / get_mongo_client_read()
│   ├── rofex_session.py      # auth pyRofex
│   ├── websocket.py          # WebSocketManager
│   └── snapshot_writer.py    # writer background genérico
│
├── engines/                  # motores always-on (WS → Mongo)
│   ├── valores.py            # Trading.TimeSales + MarketSnapshot
│   ├── curvas.py             # enriquecimiento TEA/Duration
│   ├── options.py            # Opciones.OptionsSnapshot
│   ├── forwards.py           # Trading.ForwardsLive + Historico
│   ├── breakevens.py         # Trading.BreakevensLive + Historico
│   └── dolar_mep.py          # snapshot MEP intradía
│
├── jobs/                     # batch/cron (sin WebSocket)
│   ├── aunesa_client.py      # cliente API Aunesa
│   ├── carteras.py           # Aunesa → Valuaciones.Carteras
│   ├── aum.py                # snapshot AuM diario
│   ├── aum_backfill.py       # reconstrucción histórica (invocado por Manager)
│   ├── cashflow.py           # movimientos → CashFlow.Movimientos
│   ├── flujo_contrapartes.py # operaciones del día → CashFlow.Flujo
│   ├── segmento_contrapartes.py  # setea Fondos/ALYC/Bancos
│   ├── volatilidad_ggal.py   # VR histórica GGAL al cierre
│   ├── bcra.py               # CER/TAMAR/DOLAR/BADLAR
│   └── dias_habiles.py       # calendario hábil argentino
│
├── quant/                    # cálculo puro (sin I/O de red; lee Mongo para HV)
│   └── black_scholes.py      # bs_price / bs_delta / bs_gamma / bs_vega / bs_theta / find_iv
│
├── dashboard/                # todo Streamlit
│   └── views/
│       └── manager.py        # Vista Manager (sólo admins)
│
├── scripts/                  # one-shot / diagnóstico manual
│   ├── crear_indices.py      # idempotente
│   ├── check_cer.py / check_cer_valuacion.py / check_curvas_pendientes.py
│   ├── check_forwards.py / check_tasa_fija.py / debug_forward.py
│   ├── check_aum_raw.py      # dump Aunesa por keyword
│   └── test_match_contrapartes.py
│
├── deploy/
│   ├── systemd/              # 6 .service (motor_* + streamlit)
│   └── crontab.txt           # fuente de verdad del cron
│
├── assets/logo-header.png
├── docs/                     # AUDIT.md, diccionario_rofex.xlsx
└── logs/                     # git-ignored
```

**Regla de capas**: `core/` no importa a nadie. `engines/` y `jobs/` importan `core/` + `quant/`. `dashboard/` lee Mongo vía `core.mongo.get_mongo_client_read()`; solo el Manager escribe. `scripts/` puede importar lo que necesite.

## Running the Project

Todo se ejecuta desde la raíz del proyecto con `python -m <módulo>`:

```bash
pip install -r requirements.txt        # deps incluyen pyRofex, rich

# Web dashboard
streamlit run streamlit_app.py

# Motores (systemd los corre como `python -m engines.<nombre>`)
python -m engines.valores              # TimeSales + MarketSnapshot
python -m engines.options              # Opciones GGAL headless
python -m engines.curvas               # enriquecimiento TEA/Duration
python -m engines.forwards             # tasas forward cada 30s
python -m engines.breakevens           # breakevens cada 30s
python -m engines.dolar_mep            # snapshot MEP (cron intradía)

# Jobs batch
python -m jobs.aum                     # snapshot AuM (cron 23:00 UTC)
python -m jobs.carteras                # sync carteras (cron 4×/día)
python -m jobs.cashflow --today        # cron 02:00 UTC
python -m jobs.bcra --today            # cron 20:00 UTC diario

# Scripts
python -m scripts.crear_indices
python -m scripts.check_forwards
```

No hay test suite ni linting configurado.

## Architecture

**Event-driven, multi-engine architecture:**

```
ROFEX WebSocket (pyRofex)
        │
        ▼
core.websocket.WebSocketManager
  - Subscribes tickers in 50-ticker chunks
  - Dispatches market data to engine handlers
        │
   ┌────┴────┬──────────┐
   ▼         ▼          ▼
engines.valores  engines.options  engines.curvas
        │
        ▼
MongoDB Atlas (4 databases: Trading, Opciones, Valuaciones, CashFlow)
        │
        ▼
Streamlit Dashboard (www.acaquant.com)
```

### Key Components

- **`config.py`** — Config centralizado; carga `.env` (credenciales ROFEX, Aunesa); `MANAGER_EMAILS` para control de acceso al Manager.
- **`core/rofex_session.py`** — Auth única de pyRofex (`inicializar_sesion`).
- **`core/websocket.py`** — `WebSocketManager`: suscripciones WS; registra handlers `update_price(ticker, data)` por motor.
- **`core/mongo.py`** — Dos clientes singleton thread-safe:
  - `get_mongo_client()` → `MONGO_URI` (read-write). Usado por motores, crons y Manager.
  - `get_mongo_client_read()` → `MONGO_URI_READ` (read-only). Usado por todas las vistas del dashboard. Fallback a `MONGO_URI` si `MONGO_URI_READ` no está definido.
  - **Nunca llamar `client.close()`** — ambos clientes son singletons de larga vida; cerrarlos rompe el pool compartido con Streamlit.

### Trading Engines

Cada motor tiene `update_price(ticker, data)` llamado por el WebSocket en cada tick.

| Motor | Colección MongoDB | Descripción |
|---|---|---|
| `engines/valores.py` | `Trading.TimeSales` + `Trading.MarketSnapshot` | Microestructura bonos/Lecaps/CER: inserta trades en TimeSales, snapshot cada 1s en MarketSnapshot |
| `engines/options.py` | `Opciones.OptionsSnapshot` | Opciones GGAL: Black-Scholes Greeks, IV via Newton-Raphson. Headless (motor_options.service) |
| `engines/curvas.py` | `Trading.TimeSales` (enriquecimiento) | Agrega TEA/TEM/Duration/Paridad. Loop cada 5s, docs sin `duration` ordenados DESC para no bloquear con docs viejos irresolubles |
| `engines/forwards.py` | `Trading.ForwardsLive` + `Trading.ForwardsHistorico` | Matriz NxN de tasas forward por curva cada 30s |
| `engines/breakevens.py` | `Trading.BreakevensLive` + `Trading.BreakevensHistorico` | Breakeven inflación mensual implícita CER/Lecap cada 30s |

### Trading.TimeSales

Trades en tiempo real. Campos base (`engines/valores.py`): `ticker`, `timestamp`, `price`, `size`, `side` (BUY/SELL/MID), `money`

Campos enriquecidos por `engines/curvas.py` (solo tickers en `Trading.Curvas`):

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

### Cálculo cuantitativo (`quant/`)

- `quant/black_scholes.py`: Black-Scholes — `bs_price()`, `bs_delta()`, `bs_gamma()`, `bs_vega()`, `bs_theta()`, `find_iv()` (Newton-Raphson), `calc_intrinseco()`. Además lee `VR-GGal` de Mongo para HV.

### Jobs batch (`jobs/`)

- **`aunesa_client.py`** — cliente Aunesa API (auth + posicionValuada). Importado por el resto.
- **`carteras.py`** — sincroniza posiciones Aunesa → `Valuaciones.Carteras`. Clave upsert: `(id_cuenta, unidad)`. Filtra unidades inválidas antes de guardar (`filtrar_unidades()`): excluye exactas `{ARS, USDL}` y las que contienen `[1] Depósito U$`, `OTC`, `2024`, `2025`, `DLR`. Flag `--clean` para borrar docs ya existentes con esas unidades.
- **`aum.py`** — snapshot AuM de TODAS las cuentas activas → `Valuaciones.AuM`. Clave: `(id_cuenta, unidad, fecha_snapshot)`. Fórmulas: P×Q/100 para renta fija (Títulos Públicos, ONs, Letras, Fideicomisos, CPD); (P+1)×Q para futuros; P×Q para el resto. Retry automático ante timeout Aunesa (3 intentos, 60s). Cron 23:00 UTC.
- **`aum_backfill.py`** — re-ejecutable, reconstruye AuM por fechas. Usado desde el Manager (subprocess: `python -m jobs.aum_backfill <fecha>`).
- **`cashflow.py`** — movimientos de cash desde Aunesa → `CashFlow.Movimientos`. Índice único por `comprobante`. Signo invertido (depósitos positivos). `--today` para cron.
- **`flujo_contrapartes.py`** — operaciones del día desde Aunesa → `CashFlow.Flujo`. Borra docs donde `concertacion == hoy`, fetch por cada contraparte con `cuenta` asignada, filtra 4 tipos excluidos, agrega `moneda` (ARS/USD), deduplica por `boleto`. Cron 22:00 UTC L-V (19:00 ART, en mercado aún abierto).
- **`segmento_contrapartes.py`** — asigna `segmento` ("Fondos"/"ALYC"/"Bancos") en `CashFlow.Contrapartes`. Reglas automáticas + modo interactivo para sin match. Importado por `dashboard/views/manager.py`.
- **`volatilidad_ggal.py`** — VR histórica GGAL al cierre. Cron 20:00 UTC.
- **`bcra.py`** — alimenta CER/TAMAR/DOLAR/BADLAR desde API BCRA. `--today` para cron; sin flag hace backfill desde 2023-01-01. SSL verificado (verify=True).
- **`dias_habiles.py`** — genera calendario de días hábiles argentinos. Ejecutar una vez por año.
- **`options_rollup.py`** — rollup diario `Opciones.Data` → `Opciones.DataHistorica` (una fila por `(fecha, symbol)` con high/low/last/ev + griegas del último tick). Upsert idempotente. Cron 20:15 UTC L-V. `--backfill` procesa todos los días con datos en `Opciones.Data`; `--fecha YYYY-MM-DD` uno puntual.

### Scripts de diagnóstico (`scripts/`)

- **`crear_indices.py`** — crea todos los índices MongoDB necesarios. Idempotente. Ejecutar al agregar colecciones nuevas o en un servidor nuevo. Invocado también desde la tab Setup del Manager.
- **`check_cer_valuacion.py`** — muestra el CER usado en el último trade enriquecido por bono.
- **`check_curvas_pendientes.py`** — cuántos docs sin `duration` hay por ticker en TimeSales.
- **`check_forwards.py`** — diagnóstico completo de forwards por curva.
- **`check_tasa_fija.py`** — diagnóstico de instrumentos tasa_fija en AuM.
- **`check_aum_raw.py`** — consulta directa Aunesa, filtra por keyword. Invocado desde la tab Validaciones del Manager.
- **`test_match_contrapartes.py`** — match de contrapartes con Aunesa. Importado por `dashboard/views/manager.py`.
- **`debug_forward.py`** — walk-through paso a paso del cálculo forward TX26 vs TZX26.

## Deployment

**Servidor**: Droplet de DigitalOcean, `root` en `/root/TradingAV/`, venv local en `/root/TradingAV/venv/`.

**Servicios always-on** (arrancan con el servidor):
- `cloudflared.service` — Cloudflare Tunnel, siempre activo
- `streamlit.service` — dashboard Streamlit, siempre activo

**Servicios de mercado** (lunes a viernes, horario de mercado). Definidos en `deploy/systemd/` (copiar a `/etc/systemd/system/` en el servidor):

- `motor_rofex.service` → `python -m engines.valores`
- `motor_options.service` → `python -m engines.options`
- `motor_curvas.service` → `python -m engines.curvas`
- `motor_forwards.service` → `python -m engines.forwards`
- `motor_breakevens.service` → `python -m engines.breakevens`

Todas las `.service` usan `WorkingDirectory=/root/TradingAV` + `ExecStart=/root/TradingAV/venv/bin/python -m engines.<nombre>`.

### Crontab

Fuente de verdad: **`deploy/crontab.txt`**. Para aplicar en el servidor:

```bash
crontab /root/TradingAV/deploy/crontab.txt
```

Todos los jobs se invocan como `cd /root/TradingAV && /root/TradingAV/venv/bin/python -m <módulo>`. Logs en `/root/TradingAV/logs/`.

Resumen de horarios (ver `deploy/crontab.txt` para el detalle):

| Horario UTC | Job | Frecuencia |
|---|---|---|
| 13:00 / 20:05 | start/stop motores de mercado | L-V |
| 10:00 / 11:30 / 14:00 / 16:00 | `jobs.carteras` | L-V |
| 14:00 / 19:57 | `engines.dolar_mep` | L-V |
| 20:00 | `jobs.volatilidad_ggal` | L-V |
| 20:00 | `jobs.bcra --today` | todos los días |
| 23:00 | `jobs.aum` | L-V |
| 20:15 | `jobs.options_rollup` | L-V |
| 22:00 | `jobs.flujo_contrapartes` | L-V |
| 02:00 | `jobs.cashflow --today` | Mar-Sáb |

## Streamlit Dashboard — Vistas

Nav principal: **Mercado · Opciones · Portfolios · Operaciones · AuM · Manager**

Manager visible solo para emails en `MANAGER_EMAILS`. Determinado por header `Cf-Access-Authenticated-User-Email` de Cloudflare Access.

| Vista | Sub-tabs | Descripción |
|---|---|---|
| Mercado | Mercado · Libro · Curvas · Breakevens · Forwards · Retorno Total · Volúmenes | Microstructure, VWAP, volumen intraday; Libro en tiempo real (run_every=2s); curvas, breakevens (sub-tabs Tiempo Real · Histórico · Gráfico · Simulador), forwards, retorno total. Tab Mercado usa `@st.fragment(run_every=30)`. |
| Opciones | Mercado · Estrategias | Cadena GGAL con SPOT/VR/ADR/Tasa RF + volatility smile. Estrategias: spreads pre-configurados con payoff y costo histórico. |
| Portfolios | Reportes | Informe ejecutivo mensual por cuenta. Lee `Valuaciones.Carteras` (mes actual) y `Valuaciones.CarterasII` (mes anterior). Ver sección abajo. |
| Operaciones | Cash Flow · Contrapartes · Análisis · Flujo vs AuM | Cash Flow: `CashFlow.Movimientos`, filtro "Todas / Sin accionistas / Solo accionistas / Solo cooperativas". Contrapartes: filtros SEGMENTO+MONEDA, flujo mensual + drill-down. Análisis: Individual/Comparativo. Flujo vs AuM: gráfico dual para segmento=Fondos. |
| AuM | FCI · Análisis SG · Tasa Fija · CER | FCI: snapshot por fecha + evolución + detalle por soc. gerente. Análisis SG: Individual o Comparativo base 100. Tasa Fija y CER: toggle "Valor Nominal" alterna columna entre `cantidad` (VN) y `valuacion` (P×Q). |
| Manager | Diagnóstico · Backfills · Validaciones · Logs · Historial · Setup · Latencia | Solo admins. Backfills, upserts a Assets/Contrapartes, flujo inline, audit log en `Manager.ChangeLog`. Tab Latencia: benchmark en tiempo real de todas las queries MongoDB del dashboard (ms, docs, ms/doc). |

### Portfolios → Tab Reportes

Informe ejecutivo mensual por cuenta. Selector de cuenta en el header. Secciones:

#### 1. Resumen Ejecutivo
- KPIs: fecha, valor MEP, valor A3500 (de `Valuaciones.Dolar` y `Trading.DOLAR`), valuación ARS/A3500/USD total.
- Donut chart + tablas mes actual y mes anterior por cartera (ARS / DL / HD / FCI).
- **Mes actual**: de `Valuaciones.Carteras` (último snapshot Aunesa, campo `valuacion`).
- **Mes anterior**: de `Valuaciones.CarterasII` (snapshot manual del primer día hábil del mes anterior, sincronizado al final de `jobs/aum.py` y `jobs/aum_backfill.py`).

#### 2. Carteras vs Benchmarks (4 gráficos — rendimiento acumulado mensual)

Todos los gráficos muestran rendimiento acumulado en eje Y (formato %).

| Gráfico | Fuente cartera | Benchmarks | Estado |
|---|---|---|---|
| Cartera Total ARS vs Benchmarks | `Valuaciones.Rendimientos` campo `rendimiento_ars` | A3500, Inflacion, Badlar (de `Valuaciones.Benchmarks`) | **Pendiente conectar** |
| Cartera Total USD | `Valuaciones.Rendimientos` campo `rendimiento_usd` | Sin benchmarks | **Pendiente conectar** |
| Cartera Pesos vs Benchmarks | `Valuaciones.Rendimientos` campo `rendimiento_carteraars` | Badlar, Inflacion (de `Valuaciones.Benchmarks`) | **Pendiente conectar** |
| Cartera Dolar Linked USD | — | — | **Dummy — pendiente** |

#### Colecciones de soporte (carga manual por ahora)

**`Valuaciones.Benchmarks`** — una fila por `(periodo, benchmark)`:
```
{ periodo: "ago-25", benchmark: "Badlar", mensual: 0.033, acumulado: 0.057 }
{ periodo: "ago-25", benchmark: "A3500",  mensual: 0.132, acumulado: 0.204 }
{ periodo: "ago-25", benchmark: "Inflacion", mensual: 0.027, acumulado: 0.091 }
```
- `benchmark`: valores posibles → `"Badlar"`, `"A3500"`, `"Inflacion"` (la inflación mensual, NO el índice CER).
- `periodo`: formato `"mmm-aa"` (ej. `"ago-25"`). Igual al formato usado en los gráficos.
- `mensual`: variación del mes (decimal). `acumulado`: acumulado desde inicio de serie (decimal).
- Los gráficos usan `acumulado` como eje Y.

**`Valuaciones.Rendimientos`** — una fila por `(id_cuenta, periodo)`:
```
{
  id_cuenta: "1234",
  periodo: "ago-25",
  rendimiento_ars: 0.041,         // variación % de valuación total ARS vs mes anterior
  rendimiento_usd: 0.018,         // variación % de valuación total USD vs mes anterior
  rendimiento_carteraars: 0.052,  // variación % de CARTERA ARS solamente vs mes anterior
}
```
- `rendimiento_ars` y `rendimiento_usd` calculados como `(valor_actual / valor_anterior) - 1`.
- Los gráficos acumulan estos valores mensualmente para construir la serie (igual que benchmarks).
- Carga manual de momento. A futuro: script automático post-cierre mensual.

#### 3. Variaciones del mes — **Dummy (pendiente conectar)**
#### 4–7. Detalle de activos — **Dummy (pendiente conectar)**

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
- Requiere TEA en TimeSales (escrito por `engines/curvas.py`, lag ~5s aceptable).

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
- El BE mensual calculado por `engines/breakevens.py` debería caer entre los dos escenarios donde el P&L cambia de signo (verificación visual).

## Colecciones de referencia

### Trading
- **`CER`** — Serie BCRA id=30. `fecha`, `valor`.
- **`TAMAR`** — BCRA id=44.
- **`DOLAR`** — A3500, BCRA id=5.
- **`BADLAR`** — BCRA id=7.
- **`Curvas`** — Definición estática de renta fija. `ticker`, `ticker_corto`, `tipo`, `curva` (tasa_fija/cer), `fecha_vencimiento`, `fecha_emision`, `flujo_vencimiento`, `valor_nominal`, `cupon_anual`, `cer_emision`, `flujos[]`. Cargada manualmente.
- **`DiasHabiles`** — Calendario hábil argentino. Generado por `jobs/dias_habiles.py` una vez por año.

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
- `segmento`: "Fondos" / "ALYC" / "Bancos". Asignado por `jobs/segmento_contrapartes.py`.

### CashFlow.Accionistas y Cooperativas

- **`CashFlow.Accionistas`**: colección manual con `{cuenta, accionista}`. Permite consolidar múltiples comitentes de un mismo accionista bajo un nombre único (ej. varios comitentes → "LA SEGUNDA"). Usada en el filtro de Cash Flow.
- **Cooperativas**: sin colección propia. Auto-detectadas en runtime por regex `\bcoop` case-insensitive sobre el nombre de cuenta, excluyendo cuentas que estén en `Accionistas`. Filtro "Solo cooperativas" en Cash Flow. Cada cuenta coop tiene 1 solo comitente (no requiere consolidación).

## MongoDB Índices

Definidos en `scripts/crear_indices.py`. Ejecutar en servidor nuevo o al agregar colecciones (`python -m scripts.crear_indices`).

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
| `Opciones.DataHistorica` | `fecha`, `(symbol, fecha)` |

## Notas técnicas importantes

- **MongoClient**: nunca llamar `client.close()`. Es un singleton compartido; cerrarlo mata el pool de Streamlit y tira `InvalidOperation` en todas las queries subsiguientes.
- **Dos clientes MongoDB**: `get_mongo_client()` (read-write) para motores y Manager. `get_mongo_client_read()` (read-only) para todas las vistas del dashboard. Fallback a `MONGO_URI` si `MONGO_URI_READ` no está definido.
- **Cloudflare Access header**: `Cf-Access-Authenticated-User-Email` — solo presente cuando el request pasa por Cloudflare Access. En local el header no existe y `MANAGER_EMAILS` vacío permite acceso.
- **Altair v4 pie labels**: usar `mark_text(radius=N, color="white")` dentro del arco.
- **Altair eje X duplicado en barras mensuales**: usar `strftime` para agrupar como string + encoding `:O` con `sort=` explícito.
- **Altair fontWeight**: entero (`fontWeight=600`), no string.
- **Valuación AuM**: recalculada en la vista. `TIPOS_DIVISOR_100 = {Títulos Públicos, Letras, ONs, Fideicomisos, CPD}`.
- **CashFlow DB**: se llama `CashFlow` (sin espacio). Depósitos positivos, extracciones negativas.
- **Enriquecimiento CER**: el CER usado depende de la fecha de settlement del trade (T-10 días hábiles). Si un bono no opera un día, su último trade enriquecido puede usar el CER de ayer.
- **En el servidor**: siempre usar `/root/TradingAV/venv/bin/python`.
- **Ejecución siempre desde la raíz**: todos los entrypoints usan `python -m <módulo>` con `cwd=/root/TradingAV`. Ejecutar `python engines/valores.py` falla porque `core`, `jobs`, etc. no son discoverables con el working dir en `engines/`.
