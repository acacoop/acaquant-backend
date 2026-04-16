# TradingAV API

**Version:** 0.1.0  
**Base URL:** `http://localhost:8000`  
**Protocol:** REST over HTTP/1.1  
**Content-Type:** `application/json`

---

## Overview

TradingAV API exposes the core data layer of the TradingAV quantitative trading platform as a set of RESTful endpoints. It provides programmatic access to account data, trade flow, and market operations for Argentine financial markets (MERVAL/ROFEX).

The API is designed to be consumed by internal frontends, scripts, bots, and third-party integrations. It reads from dedicated API-optimized MongoDB collections that mirror and normalize the operational data.

## Quick Start

```bash
# Install dependencies
pip install fastapi "uvicorn[standard]"

# Start the server
uvicorn api.main:app --host 127.0.0.1 --port 8000

# Verify
curl http://localhost:8000/api/health
```

Interactive documentation is auto-generated at:
- **Swagger UI:** `http://localhost:8000/docs`
- **ReDoc:** `http://localhost:8000/redoc`

## Authentication

Not yet implemented. The API currently runs on `127.0.0.1` (localhost only) and is not exposed to the public internet.

Planned: Cloudflare Access (email OTP) for browser access, API Key (`Authorization: Bearer <key>`) for programmatic access.

## Conventions

- All endpoints are prefixed with `/api`.
- Responses are JSON arrays (for list endpoints) or JSON objects.
- Dates use ISO 8601 format: `YYYY-MM-DD`.
- Monetary amounts (`bruto`) are in the currency indicated by the `moneda` field.
- All query parameters are optional unless stated otherwise.
- Empty filters return the full collection.

## Rate Limits

None enforced at this time.

---

## Endpoints

### Health

#### `GET /api/health`

Returns the API status. Use this for uptime monitoring.

**Response**

```json
{
  "status": "ok"
}
```

---

### Cuentas

Account-level data. Accounts are the fundamental entity in TradingAV: both shareholders (accionistas) and counterparties (contrapartes) are accounts with different roles and attributes.

All account records share a common schema:

| Field | Type | Description |
|---|---|---|
| `cuenta` | `string` | Full account identifier (original format) |
| `id_cuenta` | `string` | Numeric account ID extracted from `cuenta` |
| `nombre` | `string` | Account legal name (without ID prefix) |
| `grupo` | `string` | Grouping label (shareholder name or segment) |

---

#### `GET /api/cuentas/accionistas`

Returns all shareholder accounts.

`grupo` groups multiple accounts under a single legal entity (e.g., several accounts belonging to "LA SEGUNDA").

**Example Request**

```
GET /api/cuentas/accionistas
```

**Example Response**

```json
[
  {
    "cuenta": "[163] ACA BIO COOPERATIVA LIMITADA",
    "id_cuenta": "163",
    "nombre": "ACA BIO COOPERATIVA LIMITADA",
    "grupo": "ACA BIO"
  },
  {
    "cuenta": "[139] LA SEGUNDA SEGUROS DE RETIRO SA RVP",
    "id_cuenta": "139",
    "nombre": "LA SEGUNDA SEGUROS DE RETIRO SA RVP",
    "grupo": "LA SEGUNDA"
  }
]
```

---

#### `GET /api/cuentas/contrapartes`

Returns all counterparty accounts. These are external entities we trade against (fund managers, brokers, banks).

`grupo` indicates the counterparty segment: `"Fondos"`, `"ALYC"`, or `"Bancos"`.

**Example Request**

```
GET /api/cuentas/contrapartes
```

**Example Response**

```json
[
  {
    "cuenta": " FCI ADCAP PESOS PLUS",
    "id_cuenta": "362",
    "nombre": "ADCAP",
    "grupo": "Fondos"
  },
  {
    "cuenta": "BANCO DE GALICIA Y BUENOS AIRES S.A.",
    "id_cuenta": "20093",
    "nombre": "GALICIA",
    "grupo": "Bancos"
  }
]
```

---

### Operaciones

Trade operations and cash flow data.

---

#### `GET /api/operaciones/flujo`

Returns trade flow records from the trading desk. Each record represents a single trade (boleto) executed against a counterparty.

**Query Parameters**

| Parameter | Type | Description |
|---|---|---|
| `contraparte` | `string` | Filter by counterparty name (exact match) |
| `moneda` | `string` | Filter by currency: `ARS` or `USD` |
| `segmento` | `string` | Filter by market segment (e.g., `SENEBI`, `MAE`) |
| `desde` | `string` | Start date inclusive (`YYYY-MM-DD`) |
| `hasta` | `string` | End date inclusive (`YYYY-MM-DD`) |

**Response Schema**

| Field | Type | Description |
|---|---|---|
| `unidad` | `string` | Instrument traded (e.g., `[05493] TX24`) |
| `bruto` | `number` | Gross amount in the trade currency |
| `contraparte` | `string` | Counterparty short name |
| `concertacion` | `string` | Trade date (`YYYY-MM-DD`) |
| `boleto` | `string` or `int` | Unique trade ticket ID |
| `id_cuenta` | `string` | Counterparty account number |
| `segmento` | `string` | Market segment (e.g., `SENEBI`, `MAE`) |
| `moneda` | `string` | Currency: `ARS` or `USD` |

**Example Requests**

```
GET /api/operaciones/flujo
GET /api/operaciones/flujo?contraparte=ADCAP&moneda=ARS
GET /api/operaciones/flujo?desde=2024-01-01&hasta=2024-06-30
GET /api/operaciones/flujo?segmento=SENEBI&moneda=USD
```

**Example Response**

```json
[
  {
    "unidad": "[05493] TX24",
    "bruto": 589800000,
    "contraparte": "ADCAP",
    "concertacion": "2023-11-09",
    "boleto": "BOL 2023038769",
    "id_cuenta": "362",
    "segmento": "SENEBI",
    "moneda": "ARS"
  }
]
```

---

#### `GET /api/operaciones/flujos`

Returns cash movement records (deposits, withdrawals, transfers). Each record represents a single accounting entry identified by a unique voucher (boleto).

**Query Parameters**

| Parameter | Type | Description |
|---|---|---|
| `cuenta` | `string` | Filter by account (format `[N] NAME`) |
| `unidad` | `string` | Filter by currency: `ARS` or `USD` |
| `desde` | `string` | Start date inclusive (`YYYY-MM-DD`) |
| `hasta` | `string` | End date inclusive (`YYYY-MM-DD`) |

**Response Schema**

| Field | Type | Description |
|---|---|---|
| `boleto` | `string` | Unique voucher ID (e.g., `CD 2025003117`) |
| `cuenta` | `string` | Account with ID prefix (e.g., `[1005] FIBIGER, BRANCO NAHUEL`) |
| `concertacion` | `string` | Movement date (`YYYY-MM-DD`, converted from `dd/mm/yyyy`) |
| `informacion` | `string` | Description of the movement |
| `bruto` | `number` | Amount (positive = deposit, negative = withdrawal) |
| `unidad` | `string` | Currency: `ARS` or `USD` |

**Example Requests**

```
GET /api/operaciones/flujos
GET /api/operaciones/flujos?unidad=ARS&desde=2025-01-01
GET /api/operaciones/flujos?cuenta=[1005] FIBIGER, BRANCO NAHUEL
```

**Example Response**

```json
[
  {
    "boleto": "CD 2025003117",
    "cuenta": "[1005] FIBIGER, BRANCO NAHUEL",
    "concertacion": "2025-07-02",
    "informacion": "Depósito - TR 20250702124409871",
    "bruto": 100000,
    "unidad": "ARS"
  }
]
```

---

### Portfolio

Portfolio data: current positions (carteras) and historical AuM snapshots.

---

#### `GET /api/portfolio/carteras`

Returns current portfolio positions. Each record is a single holding (account + instrument).

**Query Parameters**

| Parameter | Type | Description |
|---|---|---|
| `id_cuenta` | `string` | Filter by account ID (e.g., `101`) |
| `unidad` | `string` | Filter by instrument/unit |

**Response Schema**

| Field | Type | Description |
|---|---|---|
| `id_cuenta` | `string` | Account ID |
| `unidad` | `string` | Instrument identifier (e.g., `[840] CAFCI577-840 - SBS Ahorro Pesos Clase B`) |
| `cantidad` | `number` | Quantity held |
| `precio` | `number` | Unit price |
| `timestamp` | `datetime` | Snapshot date (date only, no time component) |

**Example Requests**

```
GET /api/portfolio/carteras
GET /api/portfolio/carteras?id_cuenta=101
```

**Example Response**

```json
[
  {
    "id_cuenta": "101",
    "unidad": "[840] CAFCI577-840 - SBS AHORRO PESOS Clase B",
    "cantidad": 0.00006814,
    "precio": 164.067031,
    "timestamp": "2026-04-16T00:00:00"
  }
]
```

---

#### `GET /api/portfolio/aum`

Returns historical AuM (Assets under Management) snapshots. Each record is a daily valuation per account and instrument.

**Query Parameters**

| Parameter | Type | Description |
|---|---|---|
| `id_cuenta` | `string` | Filter by account ID (e.g., `1010`) |
| `unidad` | `string` | Filter by instrument/unit |
| `cuenta` | `string` | Filter by account (format `[N] NAME`) |
| `desde` | `string` | Start date inclusive (`YYYY-MM-DD`) |
| `hasta` | `string` | End date inclusive (`YYYY-MM-DD`) |

**Response Schema**

| Field | Type | Description |
|---|---|---|
| `fecha` | `datetime` | Snapshot date (converted from string to datetime) |
| `id_cuenta` | `string` | Account ID |
| `unidad` | `string` | Instrument identifier |
| `cantidad` | `number` | Quantity held |
| `cuenta` | `string` | Account with ID prefix (e.g., `[1010] FORCINITI, DARIO GUILLERMO`) |
| `precio` | `number` | Unit price |
| `valuacion` | `number` | Total valuation (pre-calculated) |

**Example Requests**

```
GET /api/portfolio/aum
GET /api/portfolio/aum?id_cuenta=1010
GET /api/portfolio/aum?id_cuenta=1010&desde=2026-03-01&hasta=2026-03-31
```

**Example Response**

```json
[
  {
    "fecha": "2026-03-28T00:00:00",
    "id_cuenta": "1010",
    "unidad": "[839] TXAR",
    "cantidad": 608,
    "cuenta": "[1010] FORCINITI, DARIO GUILLERMO",
    "precio": 661.5,
    "valuacion": 402192
  }
]
```

---

### Titulos

Instrument metadata and reference data.

---

#### `GET /api/titulos/assets`

Returns instrument metadata. Each record describes a single financial instrument with its classification.

**Query Parameters**

| Parameter | Type | Description |
|---|---|---|
| `unidad` | `string` | Filter by unit identifier |
| `ticker` | `string` | Filter by ticker symbol |
| `cartera` | `string` | Filter by portfolio category (e.g., `CARTERA FCI`, `OTROS`) |
| `emisor` | `string` | Filter by issuer |
| `clase_activo` | `string` | Filter by asset class (e.g., `MONEDA`, `TITULO`) |

**Response Schema**

| Field | Type | Description |
|---|---|---|
| `unidad` | `string` | Unit identifier (primary key) |
| `calificacion` | `string` | Credit rating or `NO APLICA` |
| `cartera` | `string` | Portfolio category |
| `clase_activo` | `string` | Asset class |
| `emisor` | `string` | Issuer name or `NO APLICA` |
| `ticker` | `string` | Ticker symbol |
| `vencimiento` | `datetime` or `null` | Maturity date (date only) or `null` if not applicable |
| `instrumento` | `string` | Instrument type or `NO APLICA` |

**Example Requests**

```
GET /api/titulos/assets
GET /api/titulos/assets?cartera=CARTERA%20FCI
GET /api/titulos/assets?ticker=TXAR
```

**Example Response**

```json
[
  {
    "unidad": "ARS",
    "calificacion": "NO APLICA",
    "cartera": "OTROS",
    "clase_activo": "MONEDA",
    "emisor": "NO APLICA",
    "ticker": "ARS",
    "vencimiento": null,
    "instrumento": "NO APLICA"
  }
]
```

---

#### `GET /api/titulos/flujos`

Returns cash flow data per instrument. Merges data from `Trading.Curvas` (local bonds: Lecaps, CER) and `Trading.BondMaster` (corporate/sovereign bonds) into a unified schema. Join with `/api/titulos/assets` by `ticker`.

**Query Parameters**

| Parameter | Type | Description |
|---|---|---|
| `ticker` | `string` | Filter by short ticker (e.g., `TX26`, `YMCXO`) |
| `curva` | `string` | Filter by curve: `tasa_fija`, `cer`, or empty for bonds |
| `moneda_flujo` | `string` | Filter by cash flow currency: `ARS`, `USD` |

**Response Schema**

| Field | Type | Description |
|---|---|---|
| `ticker` | `string` | Short ticker (join key with AssetsAPI) |
| `instrumento` | `string` | Full ROFEX ticker |
| `curva` | `string` | `tasa_fija` / `cer` / `""` (bonds, to be filled manually) |
| `moneda_flujo` | `string` | Cash flow currency or `""` if not set |
| `fecha_emision` | `datetime` or `null` | Emission date |
| `fecha_vencimiento` | `datetime` | Maturity date |
| `valor_nominal` | `number` | Face value (typically 100) |
| `cupon_anual` | `number` or `null` | Annual coupon rate (Curvas only) |
| `cer_emision` | `number` or `null` | CER at emission (CER bonds only) |
| `tasa_cupon` | `number` or `null` | Coupon rate (BondMaster only) |
| `flujo_vencimiento` | `number` or `null` | Maturity flow (Lecaps/zero coupon only) |
| `valor_residual_actual_pct` | `number` or `null` | Current residual value % |
| `flujos` | `array` | Normalized cash flows (see below) |

**Flujos array schema:**

| Field | Type | Description |
|---|---|---|
| `fecha` | `datetime` | Payment date |
| `amortizacion` | `number` | Amortization (% of VN for CER, absolute for bonds) |
| `interes` | `number` | Interest (rate on residual for CER, absolute for bonds) |
| `residual` | `number` | Residual after payment |

**Example Requests**

```
GET /api/titulos/flujos
GET /api/titulos/flujos?ticker=TX26
GET /api/titulos/flujos?curva=cer
GET /api/titulos/flujos?moneda_flujo=USD
```

**Example Response**

```json
[
  {
    "ticker": "TX26",
    "instrumento": "MERV - XMEV - TX26 - 24hs",
    "curva": "cer",
    "moneda_flujo": "",
    "fecha_emision": "2020-11-09T00:00:00",
    "fecha_vencimiento": "2026-11-09T00:00:00",
    "valor_nominal": 100,
    "cupon_anual": 0.02,
    "cer_emision": 22.544,
    "tasa_cupon": null,
    "flujo_vencimiento": null,
    "valor_residual_actual_pct": 40,
    "flujos": [
      {
        "fecha": "2026-05-11T00:00:00",
        "amortizacion": 20,
        "interes": 0.01,
        "residual": 40
      },
      {
        "fecha": "2026-11-09T00:00:00",
        "amortizacion": 20,
        "interes": 0.01,
        "residual": 20
      }
    ]
  }
]
```

---

### Cotizaciones

Live and historical market data. Read directly from `Trading.*` collections (no migration needed — always up to date).

---

#### `GET /api/cotizaciones/badlar`

Returns BADLAR interest rate series (BCRA id=7).

**Query Parameters**

| Parameter | Type | Description |
|---|---|---|
| `desde` | `string` | Start date inclusive (`YYYY-MM-DD`) |
| `hasta` | `string` | End date inclusive (`YYYY-MM-DD`) |

**Response Schema:** `{ fecha: string, valor: number }`

---

#### `GET /api/cotizaciones/cer`

Returns CER index series (BCRA id=30).

**Query Parameters:** Same as BADLAR.

**Response Schema:** `{ fecha: string, valor: number }`

---

#### `GET /api/cotizaciones/dolar`

Returns official dollar rate (A3500, BCRA id=5).

**Query Parameters:** Same as BADLAR.

**Response Schema:** `{ fecha: string, valor: number }`

---

#### `GET /api/cotizaciones/mep`

Returns the latest MEP dollar value from `Valuaciones.Dolar`.

**Query Parameters:** None.

**Response Schema:** `{ mep: number, timestamp: datetime }`

**Example Response:**
```json
{ "mep": 1410.8578, "timestamp": "2026-03-25T11:00:02.854000" }
```

---

#### `GET /api/cotizaciones/forwards`

Returns live forward rate matrix by curve.

**Query Parameters**

| Parameter | Type | Description |
|---|---|---|
| `curva` | `string` | Filter by curve: `tasa_fija` or `cer` |

**Response Schema:** `{ curva, matrix, tasas, tickers, updated_at }`

---

#### `GET /api/cotizaciones/renta-fija`

Returns market snapshot per fixed-income instrument (book, key metrics, recent trades).

**Query Parameters**

| Parameter | Type | Description |
|---|---|---|
| `instrumento` | `string` | Filter by full instrument (e.g., `MERV - XMEV - S30A6 - 24hs`) |

**Response Schema**

| Field | Type | Description |
|---|---|---|
| `instrumento` | `string` | Full ROFEX instrument identifier |
| `book` | `object` | Top 5 bids and offers |
| `metrics.total_nominals` | `number` | Total nominals traded |
| `metrics.vwap` | `number` | Volume-weighted average price |
| `metrics.last_price` | `number` | Last trade price |
| `metrics.open_price` | `number` | Opening price |
| `metrics.high_price` | `number` | High of the day |
| `metrics.low_price` | `number` | Low of the day |
| `metrics.closing_price` | `number` | Previous closing price |
| `recent_trades` | `array` | Last 30 trades (timestamp, price, size, side, money) |

---

#### `GET /api/cotizaciones/opciones`

Returns live options snapshot (GGAL options chain with Greeks).

**Query Parameters**

| Parameter | Type | Description |
|---|---|---|
| `instrumento` | `string` | Filter by full instrument (e.g., `MERV - XMEV - GFGC10950A - 24hs`) |
| `tipo` | `string` | Filter by option type: `CALL` or `PUT` |

**Response Schema**

| Field | Type | Description |
|---|---|---|
| `instrumento` | `string` | Full ROFEX instrument identifier (renamed from `symbol`) |
| `bid` | `number` | Best bid price |
| `offer` | `number` | Best offer price |
| `last` | `number` | Last trade price |
| `open` | `number` | Opening price |
| `high` | `number` | High of the day |
| `low` | `number` | Low of the day |
| `ev` | `number` | Expected value |
| `spot` | `number` | Underlying spot price |
| `strike` | `number` | Strike price |
| `tipo` | `string` | Option type (`CALL` / `PUT`) |
| `vence` | `string` | Expiration date (`YYYYMMDD`) |
| `closing_price` | `object` | Previous close: `{ price, date }` |
| `delta` | `number` | Delta (Black-Scholes) |
| `gamma` | `number` | Gamma |
| `iv` | `number` | Implied volatility |
| `theta` | `number` | Theta |
| `vega` | `number` | Vega |
| `updated_at` | `datetime` | Last update timestamp |

---

#### `GET /api/cotizaciones/breakevens`

Returns live breakeven inflation rates (CER vs Lecap pairs).

**Response Schema:** `{ _id, pares: [{ n, lecap, cer, fecha_vencimiento, dias, tem_lecap, tea_cer, paridad_cer, retorno_acumulado, inflacion_acumulada, breakeven_mensual }], updated_at }`

---

### Histórico

Historical market data. Same `Trading.*` collections as live endpoints, with date range filters.

---

#### `GET /api/cotizaciones/historico/forwards`

Returns historical forward rate matrices by date.

**Query Parameters**

| Parameter | Type | Description |
|---|---|---|
| `curva` | `string` | Filter by curve: `tasa_fija` or `cer` |
| `desde` | `string` | Start date inclusive (`YYYY-MM-DD`) |
| `hasta` | `string` | End date inclusive (`YYYY-MM-DD`) |

**Response Schema:** `{ curva, fecha, matrix, tasas, tickers, updated_at }`

---

#### `GET /api/cotizaciones/historico/breakevens`

Returns historical breakeven inflation rates by date.

**Query Parameters**

| Parameter | Type | Description |
|---|---|---|
| `desde` | `string` | Start date inclusive (`YYYY-MM-DD`) |
| `hasta` | `string` | End date inclusive (`YYYY-MM-DD`) |

**Response Schema:** `{ fecha, pares: [{ n, lecap, cer, fecha_vencimiento, dias, tem_lecap, tea_cer, paridad_cer, retorno_acumulado, inflacion_acumulada, breakeven_mensual }], updated_at }`

---

#### `GET /api/cotizaciones/historico/mep`

Returns historical MEP dollar series from `Valuaciones.Dolar`.

**Query Parameters**

| Parameter | Type | Description |
|---|---|---|
| `desde` | `string` | Start date inclusive (`YYYY-MM-DD`) |
| `hasta` | `string` | End date inclusive (`YYYY-MM-DD`) |

**Response Schema:** `{ mep: number, timestamp: datetime }`

---

#### `GET /api/cotizaciones/historico/trades`

Returns trades from `TimeSales` for the **last 15 days** from today. Includes enriched fields (duration, TEA, TEM, paridad) when available. Results sorted by timestamp descending.

**Query Parameters**

| Parameter | Type | Description |
|---|---|---|
| `instrumento` | `string` | Filter by full instrument (e.g., `MERV - XMEV - TX26 - 24hs`) |

**Response Schema**

| Field | Type | Description |
|---|---|---|
| `instrumento` | `string` | Full ROFEX instrument identifier (renamed from `ticker`) |
| `timestamp` | `datetime` | Trade timestamp |
| `price` | `number` | Trade price |
| `size` | `number` | Trade size (nominals) |
| `side` | `string` | `BUY` / `SELL` / `MID` |
| `money` | `number` | Trade money (`price × size / 100`) |
| `duration` | `number` | Macaulay duration in years (if enriched) |
| `TEA` | `number` | Annual effective rate (if enriched, tasa_fija/cer) |
| `TEM` | `number` | Monthly effective rate (if enriched, tasa_fija only) |
| `paridad` | `number` | Parity percentage (if enriched, cer only) |

> **Note:** Not all trades have enriched fields. Only instruments defined in `Trading.Curvas` get duration/TEA/TEM/paridad from `engines/curvas.py`.

---

## Data Architecture

The API reads from dedicated MongoDB databases with normalized schemas, separate from the operational databases used by engines and Streamlit.

| API Database | Collection | Source | Sync |
|---|---|---|---|
| `CuentasAPI` | `AccionistasAPI` | `CashFlow.Accionistas` | Manual via `scripts/api_migrate accionistas` |
| `CuentasAPI` | `ContrapartesAPI` | `CashFlow.Contrapartes` | Manual via `scripts/api_migrate contrapartes` |
| `OperacionesAPI` | `MesaAPI` | `CashFlow.Flujo` | Manual via `scripts/api_migrate flujo` |
| `OperacionesAPI` | `FlujosAPI` | `CashFlow.Movimientos` | Manual via `scripts/api_migrate movimientos` |
| `PortfolioAPI` | `CarterasAPI` | `Valuaciones.Carteras` | Manual via `scripts/api_migrate carteras` |
| `PortfolioAPI` | `AumAPI` | `Valuaciones.AuM` | Manual via `scripts/api_migrate aum` |
| `TitulosAPI` | `AssetsAPI` | `Valuaciones.Assets` | Manual via `scripts/api_migrate assets` |
| `TitulosAPI` | `ValuacionesAPI` | `Trading.Curvas` + `Trading.BondsMaster` | Manual via `scripts/api_migrate flujos-titulos` |
| `Trading` | `BADLAR` | — (lectura directa) | N/A — datos live |
| `Trading` | `CER` | — (lectura directa) | N/A — datos live |
| `Trading` | `DOLAR` | — (lectura directa) | N/A — datos live |
| `Trading` | `ForwardsLive` | — (lectura directa) | N/A — datos live |
| `Trading` | `MarketSnapshot` | — (lectura directa, `ticker`→`instrumento`) | N/A — datos live |
| `Trading` | `BreakevensLive` | — (lectura directa) | N/A — datos live |
| `Opciones` | `OptionsSnapshot` | — (lectura directa, `symbol`→`instrumento`) | N/A — datos live |
| `Valuaciones` | `Dolar` | — (lectura directa, solo `mep`+`timestamp`) | N/A — datos live |
| `Trading` | `ForwardsHistorico` | — (lectura directa) | N/A — datos históricos |
| `Trading` | `BreakevensHistorico` | — (lectura directa) | N/A — datos históricos |
| `Trading` | `TimeSales` | — (lectura directa, `ticker`→`instrumento`, últimos 15 días) | N/A — datos históricos |

Migration scripts normalize field names and extract structured data from legacy formats. Source collections are never modified.

Cotizaciones endpoints read directly from `Trading.*` — no migration needed since data is always up to date (written by engines in real time).

## Project Structure

```
api/
├── main.py              # FastAPI app entrypoint
├── deps.py              # Shared dependencies (DB access)
└── routers/
    ├── carteras.py      # /api/portfolio/*
    ├── cotizaciones.py  # /api/cotizaciones/* (direct reads from Trading.*)
    ├── cuentas.py       # /api/cuentas/*
    ├── operaciones.py   # /api/operaciones/*
    └── titulos.py       # /api/titulos/*
```

## Running Tests

```bash
# Against localhost (default)
python -m scripts.test_api

# Against a specific host
python -m scripts.test_api http://192.168.1.100:8000
```

## Changelog

| Date | Change |
|---|---|
| 2026-04-15 | Initial release: `/api/health`, `/api/cuentas/accionistas`, `/api/cuentas/contrapartes`, `/api/operaciones/flujo` |
| 2026-04-15 | Add `/api/operaciones/flujos` (cash movements from `CashFlow.Movimientos`) |
| 2026-04-16 | Add `/api/portfolio/carteras` and `/api/portfolio/aum` (DB renamed to `PortfolioAPI`) |
| 2026-04-16 | Add `/api/titulos/assets` (instrument metadata from `Valuaciones.Assets`) |
| 2026-04-16 | Add `/api/titulos/flujos` (cash flows from `Trading.Curvas` + `Trading.BondsMaster`) |
| 2026-04-16 | Add `/api/cotizaciones/*` — 6 endpoints (badlar, cer, dolar, forwards, renta-fija, breakevens) reading directly from `Trading.*` |
| 2026-04-16 | Add `/api/cotizaciones/opciones` (live options snapshot from `Opciones.OptionsSnapshot`). Rename `/mercado` → `/renta-fija`, `ticker`/`symbol` → `instrumento` |
| 2026-04-16 | Add `/api/cotizaciones/historico/*` — forwards, breakevens, trades (TimeSales últimos 15 días) |
| 2026-04-16 | Add `/api/cotizaciones/mep` (último MEP) y `/api/cotizaciones/historico/mep` (serie) desde `Valuaciones.Dolar` |
