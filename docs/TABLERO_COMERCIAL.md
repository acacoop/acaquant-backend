# Tablero de Control Comercial

> Documento vivo. Se va actualizando a medida que avanzamos. El **LOG DE
> AVANCES** (al final) es append-only con fecha.

## Objetivo

Un **tablero de control comercial** dentro de acaquant-web. Permite ver y
gestionar la cartera de **clientes (cuentas comitentes)** de la mesa,
segmentados, con su **operador** asignado, métricas comerciales, etc.

El tablero es **lo último** que se construye. Antes hay que armar la base:
clientes que figuren automáticamente + segmentación + operadores.

## De qué depende (cadena de dependencias, build bottom-up)

```
[5] Tablero de control comercial (vista en acaquant-web)   ← ÚLTIMO
        ▲
[4] Operadores ↔ usuarios de la página (Manager.Users)
        ▲
[3] Segmentación de clientes (campos categóricos, patrón Assets)
        ▲
[2] Master de clientes (DB/colección propia) + alta automática
        ▲
[1] Integración nuevas APIs Aunesa (cuentas comitentes)
```

## Idea clave: es el patrón "Assets" pero para clientes

Hoy `Valuaciones.Assets` es el **master categórico de instrumentos**: cada
asset tiene su `TICKER` + campos (emisor, vencimiento, etc.) y desde ahí se
joinean AuM/Portfolios. Queremos **lo mismo pero para clientes**: un master
de **cuentas comitentes** con campos propios de segmentación.

- Clave de cuenta = **mismo formato que AuM / NegocioMovimientos** (número de
  comitente / `id_cuenta`). Así joinea con todo lo que ya tenemos.
- Database y colección **propias** (no metemos esto en Valuaciones/Manager).
- Los campos de segmentación los **define el usuario** (TBD — ver abajo).

## Modelo de datos (confirmado con cuenta 805 — TBD nombres finales)

**DB nueva: `Clientes`** · colección master: **`Clientes.Comitentes`**.
Mapeo desde `GET /api/cuentas/listadoCuentas`:

```jsonc
{
  "id_cuenta": "805",                       // = response.id
  // ── Aunesa (auto, lo refresca el job; NO editar a mano) ──
  "denominacion": "MOLLO NICOLAS EZEQUIEL",
  "titular": "[DNI 93698623] MOLLO, NICOLAS EZEQUIEL",
  "tipo_titular": "Físico", "tipo": "Comitente", "estado": "Activa",
  "clase": "DMA", "cartera": null, "categoria": null,
  "fecha_alta_legajo": "2023-12-26",        // de fechaAltaLegajo (¡no fechaAlta!)
  "tipo_cliente": "Persona", "perfil_inversion": "Moderado",
  "horizonte_inversion": "Entre uno y tres años",
  "operador_email": "justo.ramirez@acavalores.com.ar",  // → Manager.Users
  "operador_nombre": "Justo Ramirez",
  "email": "...", "telefono": "...", "provincia": "...", "ciudad": "...",
  // ── Segmentación MANUAL (mesa, editable — el sync NO la pisa, patrón $ifNull) ──
  "segmento": null,                         // TBD: el usuario define los campos
  // ── meta ──
  "origen": "aunesa", "created_at": "...", "updated_at": "..."
}
```

- **Idempotente por `id_cuenta`** (upsert). El sync solo `$set` los campos de
  Aunesa; los de segmentación manual se preservan (igual que
  `aum.py::_sincronizar_assets` con `$ifNull`).
- `estado` real = `"Activa"` (confirmado; el doc decía alta/prealta/baja).
- Si el frontend la consume con RBAC, evaluar copia derivada `*API.*API`
  (patrón `jobs/sync_api_copies.py`).

## Piezas existentes que reusamos (no reinventar)

| Necesidad | Pieza existente a usar de modelo | Archivo |
|---|---|---|
| Cliente Aunesa (auth + requests) | `AunesaApiManager` (auth `Irmo/api/login`, `config.AUNESA_*`) | `jobs/aunesa_client.py` |
| Job diario de alta de cuentas | discovery idempotente por cuenta, cron diario, upsert | `jobs/descubrir_cuentas.py` |
| Ingesta Aunesa recurrente | pega a Aunesa, parsea, persiste idempotente | `jobs/negocio_movimientos.py` |
| Master categórico (modelo) | asset + campos editables | `Valuaciones.Assets` |
| Segmentación | reglas de segmento sobre cuentas | `jobs/segmento_contrapartes.py` |
| Operadores = usuarios | usuarios + roles de la página | `Manager.Users`, `core/roles.py` |
| Copia derivada para frontend | sync de colecciones `*API` | `jobs/sync_api_copies.py` |

## [1] Integración Aunesa — endpoint identificado: `GET /api/cuentas/listadoCuentas`

**Ya está integrado a nivel HTTP**: `jobs/aum.py::obtener_cuentas` llama a
`https://aca.aunesa.com/Irmo/api/cuentas/listadoCuentas` (auth Bearer con
`config.AUNESA_*`). El AuM solo usa `id`/`denominacion` y filtra activas,
pero el endpoint **devuelve el doc completo** de cada cuenta.

> Descartado `POST /api/cuentas` (apertura/alta de cuentas) — es escritura,
> no sirve para poblar el master. El que usamos es el **GET listadoCuentas**.

**Params (todos opcionales)**: `idCuenta` (repetible), `tipoCuenta`
(Comitente/Agente…), `estado`, `fechaDesde`/`fechaHasta` (alta de legajo →
ideal para el job diario: traer solo las altas del día).

**Response (campos relevantes para el master):**

```jsonc
{
  "id": "1114",                 // → id_cuenta (clave master)
  "tipo": "...", "cartera": "...", "categoria": "...", "clase": "...",
  "tipoTitular": "...", "estado": "...", "fechaAlta": "...",
  "denominacion": "...", "alias": "...", "titular": "...", "nota": "...",
  "disposicionesGenerales": {   // ← campos de segmentación listos
    "tipoCliente": "...", "perfilInversion": "...",
    "horizonteInversion": "...", "actividadEsperada": "...", ...
  },
  "domicilios": [...], "personasRelacionadas": [...],
  "mediosComunicacion": [...], "cuentasBancarias": [...],
  "administrador": {
    "agente":   { "codigo", "denominacion", "email" },
    "operador": { "nombre", "nombreReal", "idExterno", "email" }, // ← OPERADOR
    "sucursal": { "codigo", "denominacion" }
  }
}
```

- **El operador viene en el mismo endpoint** (`administrador.operador.email`)
  → resuelve gran parte de la Fase 4 sin un endpoint extra. El `email`
  matchea contra `Manager.Users`.
- Test read-only: `scripts/diag_aunesa_listado_cuentas.py` (no escribe nada)
  para confirmar valores reales de `tipo`/`estado` y que `operador` viene
  poblado, antes de codear el job.

**Endpoint complementario** (para después, no v1): `GET
/api/personas/datosPersona?tipoId=&id=` → KYC detallado de una persona
(domicilios, patrimonio, declaraciones PEP/UIF/FATCA, accionistas, grupos
económicos). Sirve para enriquecer un cliente puntual, no para el listado.

## [2] Master de clientes + alta automática

- Job (ej. `jobs/sync_comitentes.py`) que corre **1×/día** (cron, como
  `descubrir_cuentas`): trae comitentes de Aunesa → upsert en
  `Clientes.Comitentes` por `id_cuenta`. Cuentas nuevas se agregan solas;
  las que desaparecen no se borran (quedan con `updated_at` viejo).

## [3] Segmentación

- Campos categóricos propios (los define el usuario), editables — análogo a
  cómo se editan los Assets desde `/manager`. Definir si la segmentación es
  manual (editás el campo) o por reglas (como `segmento_contrapartes`).

## [4] Operadores ↔ usuarios

- **El operador ya viene en `listadoCuentas`** (`administrador.operador` con
  `email`). Lo guardamos como campo en el doc de la cuenta (`operador_email`,
  `operador_nombre`) — no hace falta colección de relación aparte.
- El `operador.email` matchea contra `Manager.Users.email` → cada operador
  (usuario de la página) ve su cartera filtrando por ese campo.

## [5] Tablero de control comercial (vista) — ÚLTIMO

- Vista nueva en acaquant-web. Probable **módulo RBAC nuevo** (sumar a
  `core/roles.py::MODULES` + `ENDPOINT_MODULE_PREFIXES` + matriz). Definir
  quién lo ve (¿rol comercial nuevo? ¿admin/operador?).

## Decisiones abiertas (las vamos cerrando)

- [ ] Nombre definitivo DB + colección (propuesto: `Clientes.Comitentes`).
- [x] **APIs Aunesa**: `GET /api/cuentas/listadoCuentas` (master) +
  `GET /api/personas/datosPersona` (enriquecimiento, fase posterior).
- [ ] Campos de segmentación definitivos (candidatos ya vienen en el response:
  `tipo`, `cartera`, `categoria`, `clase`, `tipoCliente`, `perfilInversion`,
  `horizonteInversion`, `actividadEsperada`). El usuario confirma cuáles.
- [x] **Operador**: campo en la cuenta (`operador_email`), viene en el response.
- [ ] ¿Copia derivada `*API` para el frontend?
- [ ] Módulo RBAC del tablero + quién lo ve.
- [ ] Métricas/columnas del tablero comercial.
- [ ] Confirmar valores reales de `estado` (doc: alta/prealta/baja; aum.py
  filtra `"Activa"`) → lo resuelve el diag.

## LOG DE AVANCES

- **2026-05-22** — Creado este documento. Definida la arquitectura (cadena de
  dependencias, patrón Assets para clientes, piezas a reusar). Pendiente: el
  usuario pasa las APIs de Aunesa para arrancar por la integración [1].
- **2026-05-22** — Analizadas 3 APIs Aunesa (docs/*.pdf): descartado
  `POST /api/cuentas` (apertura, escritura). Confirmado **`GET
  /api/cuentas/listadoCuentas`** como fuente del master — ya integrado a
  nivel HTTP en `jobs/aum.py`. El response trae `id`, campos de segmentación
  y `administrador.operador.email` (resuelve operadores). Creado test
  read-only `scripts/diag_aunesa_listado_cuentas.py`. Pendiente: correr el
  diag en el Droplet y, con el shape confirmado, definir campos de
  segmentación + codear el job de poblado de `Clientes.Comitentes`.
- **2026-05-22** — Test con cuenta 805 (`--cuenta 805`): shape confirmado.
  `estado="Activa"`, `operador.email` poblado (@acavalores.com.ar), campo
  fecha es `fechaAltaLegajo` (no `fechaAlta`). Definido el mapeo del master.
  Pendiente del usuario: (1) campos de segmentación manual a agregar; (2) si
  el sync filtra Comitente+Activa o trae todo. Con eso, codear `jobs/sync_comitentes.py`.
