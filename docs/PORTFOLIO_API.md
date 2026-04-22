# Portfolio API

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

Idéntico a `/tasa-fija` pero para instrumentos CER (ajustables por inflación). Agrega los campos `paridad` y `tea` del último trade disponible.

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
