# ACA VALORES — API de Datos de Portfolio

Documento funcional de la API que expone, a un proveedor externo autorizado,
las posiciones de portfolio de un conjunto acotado de cuentas.

---

## 1. Descripción general

La API permite consultar, vía HTTPS, las **posiciones de portfolio** de las
cuentas habilitadas para el proveedor. Es de **solo lectura**: no se puede
crear, modificar ni borrar nada.

- Los datos se actualizan **dos veces por día hábil** (lunes a viernes): a
  las **18:30** y a las **23:00** hs (hora de Argentina).
- Cada consulta requiere un **token de acceso** que se obtiene con usuario y
  contraseña.
- Las credenciales (usuario y contraseña) las entrega ACA VALORES por un canal
  seguro.

---

## 2. URL base

```
https://data.acaquant.com
```

Todos los endpoints cuelgan de esa URL. La comunicación es siempre por HTTPS.

---

## 3. Autenticación

El acceso funciona en **dos pasos**:

1. **Obtener un token** — se hace `POST /v1/token` con el usuario y la
   contraseña. La respuesta incluye un `access_token`.
2. **Usar el token** — en cada consulta de datos se envía el header:
   ```
   Authorization: Bearer <access_token>
   ```

El token **vence a los 60 minutos**. Cuando vence, las consultas devuelven
`401`; en ese caso hay que pedir un token nuevo repitiendo el paso 1.

---

## 4. Endpoints

### 4.1 `POST /v1/token` — Obtener token

Login con usuario y contraseña. El cuerpo se envía como formulario
(`application/x-www-form-urlencoded`).

**Parámetros (form):**

| Campo      | Tipo   | Requerido | Descripción              |
|------------|--------|-----------|--------------------------|
| `username` | string | Sí        | Usuario del proveedor    |
| `password` | string | Sí        | Contraseña del proveedor |

**Respuesta `200`:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6...",
  "token_type": "bearer",
  "expires_in": 3600
}
```

| Campo          | Descripción                                 |
|----------------|---------------------------------------------|
| `access_token` | Token a usar en el header `Authorization`   |
| `token_type`   | Siempre `bearer`                            |
| `expires_in`   | Segundos de validez del token (3600 = 1 h)  |

**Errores:** `401` si el usuario o la contraseña son inválidos.

---

### 4.2 `GET /v1/portfolio` — Posiciones de portfolio

Devuelve las posiciones de las cuentas habilitadas para una fecha dada.
Requiere token.

**Parámetros (query string, opcionales):**

| Parámetro   | Tipo   | Descripción                                                        |
|-------------|--------|--------------------------------------------------------------------|
| `fecha`     | string | Fecha a consultar, formato `YYYY-MM-DD`. Si se omite, usa la más reciente. |
| `id_cuenta` | string | Filtra por una cuenta puntual (ej. `101`). Si se omite, trae todas. |

**Respuesta `200`:**
```json
{
  "fecha": "2026-05-18",
  "posiciones": [
    {
      "fecha": "2026-05-18",
      "id_cuenta": "101",
      "cuenta": "[101] ASOCIACION DE COOPERATIVAS ARGENTINAS COOP LTDA",
      "unidad": "AL30",
      "cantidad": 1500000.0,
      "precio": 98.5,
      "valuacion": 1477500.0
    }
  ],
  "n": 1
}
```

| Campo        | Descripción                                            |
|--------------|--------------------------------------------------------|
| `fecha`      | Fecha efectivamente devuelta (`YYYY-MM-DD`)            |
| `posiciones` | Lista de posiciones (ver campos en la sección 5)       |
| `n`          | Cantidad de posiciones en la lista                     |

Si no hay datos para la fecha pedida, `posiciones` viene vacía y `n` es `0`.

---

### 4.3 `GET /v1/fechas` — Fechas disponibles

Lista las fechas que tienen datos cargados, de la más reciente a la más
antigua. Útil para saber qué se puede pedir en `fecha`. Requiere token.

**Respuesta `200`:**
```json
{
  "fechas": ["2026-05-18", "2026-05-16", "2026-05-15"],
  "n": 3
}
```

---

### 4.4 `GET /health` — Estado del servicio

Verifica que la API esté activa. **No requiere token.** Útil para monitoreo.

**Respuesta `200`:**
```json
{"status": "ok"}
```

---

## 5. Campos de una posición

Cada elemento de `posiciones` representa la tenencia de un instrumento en
una cuenta, para una fecha.

| Campo       | Tipo   | Descripción                                                              |
|-------------|--------|--------------------------------------------------------------------------|
| `fecha`     | string | Fecha del snapshot (`YYYY-MM-DD`).                                       |
| `id_cuenta` | string | Identificador numérico de la cuenta.                                     |
| `cuenta`    | string | Nombre de la cuenta, con formato `[id] DENOMINACIÓN`.                    |
| `unidad`    | string | Instrumento / especie de la posición (ej. `AL30`, `ARS` para efectivo).  |
| `cantidad`  | número | Cantidad de nominales / unidades de la posición.                         |
| `precio`    | número | Precio unitario del instrumento.                                         |
| `valuacion` | número | Valuación de la posición, en pesos argentinos (ARS).                     |

Notas:
- El efectivo aparece como una posición más, con `unidad` = `ARS` (o la
  moneda correspondiente), `precio` = 1 y `valuacion` = `cantidad`.
- `valuacion` ya viene calculada según el tipo de instrumento — el proveedor
  no necesita recalcular nada.

---

## 6. Códigos de error

| Código | Significado                                                         |
|--------|---------------------------------------------------------------------|
| `200`  | OK.                                                                 |
| `401`  | No autenticado: falta el token, es inválido o venció. Pedir uno nuevo. |
| `422`  | Parámetros mal formados (ej. `fecha` con formato inválido).         |
| `429`  | Demasiadas solicitudes — se superó el límite de uso (ver sección 7).|
| `500`  | Error interno del servicio. Reintentar más tarde.                   |

---

## 7. Límites de uso

Para proteger el servicio hay límites de cantidad de solicitudes:

| Endpoint          | Límite                          |
|-------------------|---------------------------------|
| `POST /v1/token`  | 10 por minuto / 100 por hora    |
| `GET /v1/portfolio` | 60 por hora                   |
| `GET /v1/fechas`  | 60 por hora                     |

Superar un límite devuelve `429`. Como los datos se actualizan dos veces
por día (18:30 y 23:00 hs Argentina), no hace falta consultar seguido.

---

## 8. Ejemplos de consultas

### 8.1 Paso 1 — Obtener el token

**Solicitud:**
```
curl -X POST https://data.acaquant.com/v1/token \
  -d "username=USUARIO" \
  -d "password=CONTRASEÑA"
```

**Respuesta:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c3Vhcmlv...",
  "token_type": "bearer",
  "expires_in": 3600
}
```
Se copia el valor de `access_token` para usarlo en las consultas siguientes.

### 8.2 Consultar el portfolio completo

Trae todas las posiciones de todas las cuentas habilitadas, para la fecha
más reciente (no se pasa `fecha`).

**Solicitud:**
```
curl https://data.acaquant.com/v1/portfolio \
  -H "Authorization: Bearer ACCESS_TOKEN"
```

**Respuesta:**
```json
{
  "fecha": "2026-05-18",
  "posiciones": [
    {
      "fecha": "2026-05-18",
      "id_cuenta": "101",
      "cuenta": "[101] ASOCIACION DE COOPERATIVAS ARGENTINAS COOP LTDA",
      "unidad": "ARS",
      "cantidad": 994712575.11,
      "precio": 1.0,
      "valuacion": 994712575.11
    },
    {
      "fecha": "2026-05-18",
      "id_cuenta": "101",
      "cuenta": "[101] ASOCIACION DE COOPERATIVAS ARGENTINAS COOP LTDA",
      "unidad": "AL30",
      "cantidad": 2500000.0,
      "precio": 98.45,
      "valuacion": 2461250.0
    },
    {
      "fecha": "2026-05-18",
      "id_cuenta": "463",
      "cuenta": "[463] NOMBRE DE LA CUENTA",
      "unidad": "FCI ADCAP RENTA FIJA",
      "cantidad": 45000.0,
      "precio": 1320.55,
      "valuacion": 59424750.0
    }
  ],
  "n": 3
}
```

### 8.3 Consultar una fecha puntual

**Solicitud:**
```
curl "https://data.acaquant.com/v1/portfolio?fecha=2026-05-16" \
  -H "Authorization: Bearer ACCESS_TOKEN"
```
Devuelve el portfolio de esa fecha. Si no hay datos, `posiciones` viene
vacía y `n` es `0`.

### 8.4 Consultar una sola cuenta

**Solicitud:**
```
curl "https://data.acaquant.com/v1/portfolio?id_cuenta=101" \
  -H "Authorization: Bearer ACCESS_TOKEN"
```
Devuelve solo las posiciones de la cuenta `101`. Se puede combinar con
`fecha`:
```
curl "https://data.acaquant.com/v1/portfolio?fecha=2026-05-18&id_cuenta=101" \
  -H "Authorization: Bearer ACCESS_TOKEN"
```

### 8.5 Ver las fechas disponibles

**Solicitud:**
```
curl https://data.acaquant.com/v1/fechas \
  -H "Authorization: Bearer ACCESS_TOKEN"
```

**Respuesta:**
```json
{"fechas": ["2026-05-18", "2026-05-16", "2026-05-15"], "n": 3}
```

### 8.6 Ejemplo de integración en Python

```python
import requests

BASE = "https://data.acaquant.com"

# 1. Login → token
r = requests.post(f"{BASE}/v1/token",
                   data={"username": "USUARIO", "password": "CONTRASEÑA"})
r.raise_for_status()
token = r.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

# 2. Consultar el portfolio
r = requests.get(f"{BASE}/v1/portfolio", headers=headers)
r.raise_for_status()
data = r.json()

print(f"Portfolio al {data['fecha']} — {data['n']} posiciones")
for p in data["posiciones"]:
    print(f"  {p['id_cuenta']:>6}  {p['unidad']:<20}  "
          f"cant={p['cantidad']:>16,.2f}  valuacion={p['valuacion']:>18,.2f}")
```

---

## 9. Resumen del flujo de integración

1. ACA VALORES entrega al proveedor un **usuario** y una **contraseña**.
2. El proveedor obtiene un **token** con `POST /v1/token`.
3. Con ese token consulta `GET /v1/portfolio` (y opcionalmente `/v1/fechas`).
4. El token se renueva cuando vence (cada 60 minutos).
5. Los datos se refrescan dos veces por día hábil — a las **18:30** y a las
   **23:00** hs (hora de Argentina).
