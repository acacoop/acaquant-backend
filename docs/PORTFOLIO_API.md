# Portfolio API

**Version:** 1.0
**Base URL:** `https://api.acaquant.com`
**Protocol:** REST over HTTPS · JSON
**Scope:** `/api/portfolio/*` — lectura de carteras, AuM y detalle de posiciones.

Este documento es la referencia funcional completa para integrarse con la API de Portfolios. No describe el resto del backend: solo los endpoints que este proveedor está autorizado a consumir.

---

## 1. Resumen

La Portfolio API expone el estado patrimonial diario de las cuentas administradas: posiciones actuales, snapshots históricos de AuM, detalle por activo y agregados de FCI. Todos los endpoints son **de solo lectura** (`GET`).

Los datos se calculan y valúan desde fuentes internas (proveedor de posición + datos de mercado). El cliente recibe el resultado ya valuado; no necesita recomputar precios ni flujos.

---

## 2. Autenticación

Toda request pasa por **tres** controles independientes. Sin los tres, el tráfico se rechaza antes de llegar al servicio.

### 2.1 Cloudflare Access — service token

La API está detrás de Cloudflare Access (Zero Trust). El proveedor recibe de nosotros un par de credenciales de máquina (**service token**) que deben enviarse en cada request:

```http
CF-Access-Client-Id: <client-id>.access
CF-Access-Client-Secret: <client-secret>
```

Estas credenciales:
- No caducan automáticamente, pero pueden ser rotadas con aviso previo.
- Se entregan por canal seguro (1Password / Bitwarden share link) junto con el `API_KEY`.
- **No** deben quedar expuestas en código cliente público, logs, ni en un repo.

### 2.2 Bearer API key

Además del service token, todo endpoint (excepto `/api/health`) requiere un header `Authorization`:

```http
Authorization: Bearer <API_KEY>
```

El `API_KEY` es emitido por nosotros junto con el service token. Es un secreto compartido de larga vida y se rota bajo coordinación.

### 2.3 Scope / permisos

El service token provisto solo habilita las rutas `/api/health` y `/api/portfolio/*`. Cualquier intento a otra ruta devuelve `403 no autorizado`.

### 2.4 Ejemplo completo

```bash
curl -H "Authorization: Bearer $API_KEY" \
     -H "CF-Access-Client-Id: $CF_CLIENT_ID" \
     -H "CF-Access-Client-Secret: $CF_CLIENT_SECRET" \
     https://api.acaquant.com/api/portfolio/carteras
```

---

## 3. Rate limits

No hay cuotas explícitas por endpoint dentro de este scope, pero el proveedor debe respetar:

- **Máximo recomendado:** 60 requests / minuto.
- **Concurrencia:** hasta 10 conexiones simultáneas.
- **Caché del lado consumidor:** los snapshots de AuM se actualizan una vez al día (23:00 UTC, L-V). Las posiciones actuales (`/carteras`) se refrescan hasta 3 veces al día hábil. No tiene sentido pollear cada pocos segundos.

Superar el límite puede derivar en `429 Too Many Requests`. La cuota real se afina según patrón de uso real durante las primeras semanas de integración.

---

## 4. Convenciones

- **Prefijo.** Todas las rutas bajo `/api/portfolio/*`. Health: `/api/health`.
- **Fechas.** `YYYY-MM-DD` (ISO 8601) para días calendario. Campo `timestamp` con `+00:00` (UTC) para datetimes.
- **Moneda.** Campo `unidad` con valores `ARS` o `USD`. Cuando un activo tiene denominación propia (ej. ticker), aparece en el campo correspondiente.
- **Filtros.** Todos los query params son opcionales salvo indicación en contrario. Sin filtros, el endpoint devuelve la colección completa.
- **Proyección.** La API recorta el documento del lado servidor. No se expone `_id` ni campos internos. Nuevos campos pueden aparecer sin romper compatibilidad; el cliente debe tolerarlos.
- **Compresión.** Respuestas ≥ 1 KiB vienen con `Content-Encoding: gzip` si el cliente anuncia `Accept-Encoding: gzip`.
- **Codificación.** UTF-8.
- **CORS.** No habilitado. La API se consume server-to-server.

---

## 5. Valuación

Todas las posiciones devueltas incluyen el campo `valuacion` ya calculado. Para transparencia, la fórmula aplicada según tipo de activo:

| Tipo | Fórmula |
|---|---|
| Títulos Públicos, Letras, ONs, Fideicomisos, CPD | `cantidad × precio / 100` |
| Futuros | `(precio + 1) × cantidad` |
| FCI / resto | `cantidad × precio` |

El cliente **no necesita** recomputar estos valores.

---

## 6. Error model

Errores son devueltos con la siguiente forma:

```json
{ "detail": "<mensaje>" }
```

| HTTP | Significado |
|---|---|
| `400` | Query param mal formado (ej. `desde` no es ISO date). |
| `401` | Falta o es inválido el Bearer / service token. |
| `403` | Credenciales válidas pero fuera del scope autorizado. |
| `404` | Recurso no existe (ej. `id_cuenta` sin posiciones). |
| `422` | Validación FastAPI: `{"detail": [{"loc": [...], "msg": "..."}]}`. |
| `429` | Rate limit excedido. Respetar `Retry-After` si viene. |
| `502/503` | Problema temporal upstream; reintentar con backoff exponencial. |
| `500` | Error no manejado. Reportar con `request_id` (si se provee) y timestamp. |

---

## 7. Endpoints

### 7.1 `GET /api/health`

Liveness probe. No requiere auth.

**Response 200**

```json
{ "status": "ok" }
```

---

### 7.2 `GET /api/portfolio/carteras`

Posiciones actuales (última foto disponible).

**Query params**

| Param | Tipo | Req | Descripción |
|---|---|---|---|
| `id_cuenta` | string | no | Filtra por ID de cuenta. |
| `unidad` | string | no | Filtra por ticker/instrumento. |

**Response 200** — array de posiciones

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

**Ejemplo**

```bash
curl -H "Authorization: Bearer $API_KEY" \
     -H "CF-Access-Client-Id: $CF_CLIENT_ID" \
     -H "CF-Access-Client-Secret: $CF_CLIENT_SECRET" \
     "https://api.acaquant.com/api/portfolio/carteras?id_cuenta=123456"
```

---

### 7.3 `GET /api/portfolio/aum`

Serie histórica de snapshots de AuM (Assets under Management). Un snapshot por cuenta / unidad / fecha.

**Query params**

| Param | Tipo | Req | Descripción |
|---|---|---|---|
| `id_cuenta` | string | no | Filtra por ID de cuenta. |
| `unidad` | string | no | Filtra por ticker. |
| `cuenta` | string | no | Filtra por nombre de cuenta (`[N] NOMBRE`). |
| `desde` | date | no | `YYYY-MM-DD` inclusive. |
| `hasta` | date | no | `YYYY-MM-DD` inclusive. |
| `ultimo` | bool | no | Si `true`, devuelve solo el snapshot más reciente e ignora `desde`/`hasta`. |

**Response 200**

```json
[
  {
    "fecha": "2026-04-21",
    "id_cuenta": "123456",
    "unidad": "TX26",
    "cuenta": "[42] ACME SA",
    "cantidad": 1000000,
    "precio": 98.55,
    "valuacion": 985500.0
  }
]
```

---

### 7.4 `GET /api/portfolio/resumen`

Resumen ejecutivo por cuenta: valuación del mes corriente y del mes anterior, agrupada por segmento de cartera (`CARTERA`).

**Query params**

| Param | Tipo | Req | Descripción |
|---|---|---|---|
| `id_cuenta` | string | no | Si se omite, devuelve solo la lista de cuentas disponibles. |

**Response 200** (sin `id_cuenta`)

```json
{ "cuentas": ["[42] ACME SA", "[43] …"] }
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

### 7.5 `GET /api/portfolio/detalle`

Detalle posición-por-posición para una cuenta, con metadatos (emisor, clase de activo, calificación, vencimiento) y el porcentaje que representa cada posición sobre el total.

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

### 7.6 `GET /api/portfolio/tasa-fija`

Posiciones en instrumentos de tasa fija del último snapshot de AuM, agrupadas por ticker, con `cobro_proyectado` (flujo al vencimiento).

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
      "cuentas": [ { "id_cuenta": "123456", "cuenta": "[42] ACME SA", "valuacion": 8500000.0 } ]
    }
  ]
}
```

---

### 7.7 `GET /api/portfolio/cer`

Idéntico a `tasa-fija` pero para instrumentos CER (ajustables por inflación). Agrega los campos `paridad` y `tea` del último trade disponible.

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
      "cuentas": [ { "id_cuenta": "123456", "cuenta": "[42] ACME SA", "valuacion": 8200000.0 } ]
    }
  ]
}
```

---

### 7.8 `GET /api/portfolio/fci-serie`

Serie histórica diaria de tenencias en Fondos Comunes de Inversión (FCI), con apertura por emisor del fondo.

**Query params**

| Param | Tipo | Req | Descripción |
|---|---|---|---|
| `desde` | date | no | `YYYY-MM-DD`. |
| `hasta` | date | no | `YYYY-MM-DD`. |

**Response 200**

```json
[
  {
    "fecha": "2026-04-21",
    "total": 15200000.0,
    "por_emisor": { "Balanz": 6200000.0, "Schroders": 4500000.0, "Consultatio": 4500000.0 }
  }
]
```

Límite: 730 filas por respuesta.

---

### 7.9 `GET /api/portfolio/fci-snapshot`

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

## 8. Ejemplo de integración

```python
import os
import requests

BASE = "https://api.acaquant.com"
HEADERS = {
    "Authorization":        f"Bearer {os.environ['API_KEY']}",
    "CF-Access-Client-Id":  os.environ["CF_CLIENT_ID"],
    "CF-Access-Client-Secret": os.environ["CF_CLIENT_SECRET"],
}

# Last AuM for all accounts
r = requests.get(f"{BASE}/api/portfolio/aum", params={"ultimo": True}, headers=HEADERS, timeout=30)
r.raise_for_status()
snapshots = r.json()

# Full detail for one account
r = requests.get(f"{BASE}/api/portfolio/detalle", params={"id_cuenta": "123456"}, headers=HEADERS, timeout=30)
r.raise_for_status()
detalle = r.json()
print(f"Total: {detalle['total']:,.2f}")
for pos in detalle["posiciones"]:
    print(f"  {pos['ticker']:<10} {pos['valuacion']:>15,.2f}  ({pos['pct']:.2f}%)")
```

---

## 9. Operación y soporte

- **Disponibilidad.** 24/7 con ventana de mantenimiento programada 04:00–11:30 UTC (01:00–08:30 ART). En esa franja el endpoint puede responder con `503`.
- **Cambios breaking.** Cualquier cambio incompatible se comunica con al menos 10 días hábiles de anticipación y se versiona.
- **SLA informal.** Latencia p95 < 800 ms para endpoints agregados (`/resumen`, `/tasa-fija`, `/cer`, `/fci-*`). Para `/carteras` y `/aum` raw: p95 < 300 ms.
- **Contacto técnico.** Coordinar incidencias o pedidos de cambio con el equipo interno (canal pactado en el onboarding).

---

## 10. Changelog

| Fecha | Cambio |
|---|---|
| 2026-04-22 | Primera versión del contrato compartida con proveedor externo. |
