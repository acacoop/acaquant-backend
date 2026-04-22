# Portfolio API — Especificación para POC

**Scope:** endpoints de `/api/portfolio/*`. Todos los endpoints son de solo lectura (`GET`) y devuelven JSON.

Este documento describe el **contrato funcional** de los endpoints: query params, shape de la respuesta y semántica de los campos. Es suficiente para diseñar la integración del lado del consumidor durante la POC.

Todos los ejemplos usan cuentas **dummy** (ej. `[42] ACME SA`) — los `id_cuenta`, nombres y montos son ilustrativos, no reflejan ninguna cuenta real del operador.

---

## Convenciones

- **Path prefix.** Todos los endpoints bajo `/api/portfolio/*`.
- **Fechas.** `YYYY-MM-DD` (ISO 8601) para días calendario. Campo `timestamp` con offset `+00:00` (UTC) cuando corresponde.
- **Moneda.** Campo `unidad` con valores `ARS` o `USD`. Cuando un instrumento tiene ticker propio, aparece en el campo correspondiente.
- **Filtros.** Los query params son opcionales salvo indicación. Sin filtros devuelve la colección completa.
- **Codificación.** UTF-8.

---

## Valuación

Las respuestas incluyen el campo `valuacion` ya calculado. Fórmula según tipo:

| Tipo | Fórmula |
|---|---|
| Títulos Públicos, Letras, ONs, Fideicomisos, CPD | `cantidad × precio / 100` |
| Futuros | `(precio + 1) × cantidad` |
| FCI / resto | `cantidad × precio` |

El consumidor **no recalcula** — recibe la valuación lista.

---

## Error model

```json
{ "detail": "<mensaje>" }
```

| HTTP | Significado |
|---|---|
| `400` | Query param mal formado (ej. `desde` no es fecha ISO). |
| `404` | Recurso no existe (ej. `id_cuenta` sin posiciones). |
| `422` | Validación: `{"detail": [{"loc": [...], "msg": "..."}]}`. |
| `500`/`502` | Error temporal del servidor — reintentar con backoff. |

---

## Endpoints

### `GET /api/portfolio/carteras`

Posiciones actuales (última foto disponible).

**Query params**

| Param | Tipo | Req | Descripción |
|---|---|---|---|
| `id_cuenta` | string | no | Filtra por ID de cuenta. |
| `unidad` | string | no | Filtra por ticker/instrumento. |

**Response 200**

```json
[
  {
    "id_cuenta": "123456",
    "unidad": "TX26",
    "cantidad": 1000000,
    "precio": 98.55,
    "timestamp": "2026-04-22T13:00:00+00:00"
  }
]
```

---

### `GET /api/portfolio/resumen`

Resumen ejecutivo por cuenta: valuación del mes corriente y del mes anterior, agrupada por segmento de cartera (`CARTERA`).

**Query params**

| Param | Tipo | Req | Descripción |
|---|---|---|---|
| `id_cuenta` | string | no | Si se omite, devuelve solo la lista de cuentas disponibles. |

**Response 200** (sin `id_cuenta`)

```json
{ "cuentas": ["[42] ACME SA", "[43] DUMMY SA"] }
```

**Response 200** (con `id_cuenta`)

```json
{
  "cuentas": ["[42] ACME SA"],
  "mes_actual":   { "Renta Fija": 12500000.0, "FCI": 3400000.0 },
  "mes_anterior": { "Renta Fija": 12100000.0, "FCI": 3200000.0 }
}
```

---

### `GET /api/portfolio/detalle`

Detalle posición-por-posición para una cuenta, con metadatos (emisor, clase de activo, calificación, vencimiento) y porcentaje que representa cada posición sobre el total.

**Query params**

| Param | Tipo | Req | Descripción |
|---|---|---|---|
| `id_cuenta` | string | **sí** | ID de cuenta. |

**Response 200**

```json
{
  "posiciones": [
    {
      "unidad": "TX26",
      "ticker": "TX26",
      "emisor": "Tesoro Nacional",
      "clase_activo": "Títulos Públicos",
      "cartera": "Renta Fija",
      "calificacion": "A",
      "vencimiento": "2026-09-23",
      "cantidad": 1000000,
      "precio": 98.55,
      "valuacion": 985500.0,
      "pct": 7.24
    }
  ],
  "total": 13612000.0
}
```

---

### `GET /api/portfolio/tasa-fija`

Posiciones en instrumentos de tasa fija, agrupadas por ticker, con `cobro_proyectado` (flujo al vencimiento).

**Query params:** ninguno.

**Response 200**

```json
{
  "fecha": "2026-04-21",
  "total_valuacion": 45200000.0,
  "total_cobro": 48300000.0,
  "tickers": [
    {
      "ticker": "S31O6",
      "fecha_vencimiento": "2026-10-31",
      "valuacion": 12500000.0,
      "cobro_proyectado": 13100000.0,
      "cuentas": [
        {
          "id_cuenta": "123456",
          "cuenta": "[42] ACME SA",
          "valuacion": 8500000.0
        }
      ]
    }
  ]
}
```

---

### `GET /api/portfolio/cer`

Idéntico a `/tasa-fija` pero para instrumentos CER (ajustables por inflación). Agrega los campos `paridad` y `tea` del último trade disponible. No calcula `cobro_proyectado` porque depende del CER futuro.

**Query params:** ninguno.

**Response 200**

```json
{
  "fecha": "2026-04-21",
  "total_valuacion": 22800000.0,
  "tickers": [
    {
      "ticker": "TZX26",
      "fecha_vencimiento": "2026-11-15",
      "paridad": 102.3,
      "tea": 0.085,
      "valuacion": 8200000.0,
      "cuentas": [
        {
          "id_cuenta": "123456",
          "cuenta": "[42] ACME SA",
          "valuacion": 8200000.0
        }
      ]
    }
  ]
}
```

---

### `GET /api/portfolio/fci-snapshot`

Detalle por fondo en una fecha específica.

**Query params**

| Param | Tipo | Req | Descripción |
|---|---|---|---|
| `fecha` | date | **sí** | `YYYY-MM-DD`. |

**Response 200**

```json
[
  {
    "unidad": "BALFCI",
    "emisor": "Balanz",
    "ticker": "BALANZ AHORRO",
    "cuenta": "[42] ACME SA",
    "id_cuenta": "123456",
    "cantidad": 850000,
    "valuacion": 6200000.0
  }
]
```

---

## Changelog

| Fecha | Cambio |
|---|---|
| 2026-04-22 | Primera versión para POC del proveedor. |
