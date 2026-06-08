# Acceso OData — Portfolio Acaquant

Documento funcional para conectar la herramienta del proveedor (ej. SAP Datasphere)
al servicio de datos de portfolio de Acaquant vía OData.

---

## 1. Conexión

| Parámetro | Valor |
|---|---|
| URL del servicio | `https://data.acaquant.com/odata/` |
| Protocolo | **OData V2** |
| Tipo de conexión (Datasphere) | **Generic OData** |
| Autenticación | **HTTP Basic** (usuario y contraseña provistos por Acaquant) |
| Transporte | HTTPS únicamente |

---

## 2. Prueba rápida (navegador)

1. **Esquema** (sin credenciales) — abrir:
   `https://data.acaquant.com/odata/$metadata`
   Debe devolver un XML con la entidad `Portfolio`.

2. **Datos** (con credenciales) — abrir:
   `https://data.acaquant.com/odata/Portfolio`
   El navegador pide usuario y contraseña → devuelve los datos en JSON.

Si ambos responden, la conexión está operativa.

---

## 3. Entidad `Portfolio`

Una fila por posición.

| Campo | Tipo | Descripción |
|---|---|---|
| `ID` | texto | Clave única (`fecha\|id_cuenta\|unidad`) |
| `fecha` | texto | Fecha del dato (`YYYY-MM-DD`) |
| `id_cuenta` | texto | Identificador de la cuenta |
| `cuenta` | texto | Nombre de la cuenta |
| `unidad` | texto | Instrumento |
| `cantidad` | número | Nominales |
| `precio` | número | Precio |
| `valuacion` | número | Valuación |

---

## 4. Consultas (opcionales)

| Operación | Ejemplo |
|---|---|
| Filtrar por fecha | `…/odata/Portfolio?$filter=fecha eq '2026-05-16'` |
| Filtrar por cuenta | `…/odata/Portfolio?$filter=id_cuenta eq '101'` |
| Combinar filtros | `…/odata/Portfolio?$filter=fecha eq '2026-05-16' and id_cuenta eq '101'` |
| Paginar | `$top`, `$skip` |
| Seleccionar columnas | `$select=fecha,id_cuenta,valuacion` |
| Conteo de filas | `…/odata/Portfolio/$count` |

---

## 5. Actualización de los datos

- Se actualizan **una vez por día hábil** (lunes a viernes), **durante la noche**.
- El dato de cada jornada queda disponible **a partir de las 02:00 UTC** (≈ 23:00 hora Argentina).
- Se mantiene **histórico por fecha** (las fechas anteriores quedan disponibles).

---

## 6. Soporte

Ante cualquier inconveniente de conexión, indicar la **versión de OData** que requiere
la herramienta (V2 / V4) y el **método de autenticación** que utiliza, junto con el
mensaje de error exacto.
