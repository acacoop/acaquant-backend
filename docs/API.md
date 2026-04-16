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

Migration scripts normalize field names and extract structured data from legacy formats. Source collections are never modified.

## Project Structure

```
api/
├── main.py              # FastAPI app entrypoint
├── deps.py              # Shared dependencies (DB access)
└── routers/
    ├── carteras.py      # /api/portfolio/*
    ├── cuentas.py       # /api/cuentas/*
    └── operaciones.py   # /api/operaciones/*
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
