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
  // ── Segmentación MANUAL (mesa, editable — el sync NO la pisa) ──
  "nivel_1": null, "nivel_2": null, "nivel_3": null, "nivel_4": null, "nivel_5": null,
  "primer_contacto_comercial": null, "riesgo_la_ft": null, "division": null,
  "adc": null, "dma": null, "observaciones": null,
  "sucursal": null, "referido": null,
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

## [2] Master de clientes + alta automática — ✅ job creado

- **`jobs/sync_comitentes.py`** (creado): trae comitentes de
  `listadoCuentas` → upsert en `Clientes.Comitentes` por `id_cuenta`.
  Idempotente, pensado para cron 1×/día. Campos auto via `$set`; los 13
  manuales via `$setOnInsert` (no se pisan). Filtra Comitente+Activa
  (`--include-all` para todos). `--dry-run` para probar sin escribir.
- Campos auto (12): id_cuenta, denominacion, tipo_titular, tipo, estado,
  clase, fecha_alta_legajo, tipo_cliente, perfil_inversion, operador_email,
  operador_nombre, provincia.
- Pendiente: validar con `--dry-run` en el Droplet → agregar línea al cron
  (`deploy/crontab.txt`).

## [3] Segmentación — ✅ editor en Manager → CLIENTES

- **Editor in-place tipo Assets**: tab **CLIENTES** en `/manager` (admin).
  Backend `api/routers/manager/clientes.py` (espejo de `assets.py`):
  `GET /clientes` (filtrable por operador/nivel_1/campo_vacio/q),
  `GET /clientes/values` (datalists), `PATCH /clientes` (edita los 13
  manuales por `id_cuenta`). Frontend `TabClientes` en `manager-view.tsx`.
- Campos de Aunesa = read-only (contexto); se editan solo los 13 manuales.
- **Carga masiva por archivo** desde la misma tab: botón "Importar archivo"
  (.csv/.xlsx, parse SheetJS client-side) → valida que las columnas sean
  `id_cuenta` + campos manuales (mismo nombre que la base; columna no
  reconocida = error) → `POST /clientes/bulk` rellena solo esas columnas.
  Reemplaza al `scripts/backfill_*` (que queda como fallback CLI).

## [4] Operadores ↔ usuarios

- **El operador ya viene en `listadoCuentas`** (`administrador.operador` con
  `email`). Lo guardamos como campo en el doc de la cuenta (`operador_email`,
  `operador_nombre`) — no hace falta colección de relación aparte.
- El `operador.email` matchea contra `Manager.Users.email` → cada operador
  (usuario de la página) ve su cartera filtrando por ese campo.

## [5] Vista COMERCIAL — diseño (en curso, 2026-05-23)

> Empieza **solo para manager (admin)**. Después se abre a operadores con scope.

### La idea central: cruzar QUIÉN + ACTIVIDAD + TAMAÑO
Todo joinea por `id_cuenta` (la misma clave de siempre):

| Fuente | Qué aporta | Join |
|---|---|---|
| `Clientes.Comitentes` | QUIÉN: operador, segmentación (nivel_1..5), estado legal, clase, perfil, fecha_alta | `id_cuenta` (master) |
| `CashFlow.NegocioMovimientos` | ACTIVIDAD: última operación, # ops, volumen, por categoría/ticker | campo `cuenta` `[id] NOMBRE` → extraer id (`_RE_ID_BRACKET`) |
| `Valuaciones.AuM` / `ConsolidadoCuentas` | TAMAÑO: AuM por cuenta | `id_cuenta` |
| `Manager.Users` | operador = usuario (nombre, scoping) | `operador_email` = `email` |

### Dos "estados" que NO son lo mismo (distinción clave)
- **Estado legal** (`estado` de Aunesa: Activa/baja) — administrativo.
- **Estado comercial** (derivado de `NegocioMovimientos`) — ¿opera o no?
  Propuesta de ciclo de vida:
  `NUEVA` (alta reciente, nunca operó) → `ACTIVA` (operó hace ≤ N días) →
  `ENFRIÁNDOSE` (operaba, hace N–M sin operar) → `DORMIDA` (hace > M sin operar)
  → `BAJA` (estado legal). Umbral N/M configurable (default tentativo 30/90 días).
  **Esto es el corazón del pedido: "saber qué cuentas están activas y cuáles no".**

### Las dos lentes (lo que pediste)
**A) Por CLIENTE** — tabla, fila = cuenta. Columnas: id · denominación · operador ·
segmento (nivel_1) · AuM · última op · estado comercial · flujo neto del período ·
perfil. Filtros: operador, segmento, estado comercial, clase, con/sin segmentar.
Click → **ficha del cliente** (drill-down, abajo).

**B) Por OPERADOR** — tabla, fila = operador. Columnas: # cuentas · # activas /
dormidas · AuM total de su cartera · flujo neto captado · # operaciones del período ·
antigüedad promedio · % cartera segmentada. Click → lente A filtrada por ese operador.

### KPIs (header, responden de un vistazo)
AuM total · cuentas activas / total · flujo neto del período · # operadores ·
⚠️ cuentas sin operador · ⚠️ cuentas sin segmentar.

### Accionables / alertas — IDEAS NUEVAS (lo que NO dijiste, valor agregado)
- **Riesgo de churn:** cuentas que estaban activas y se enfriaron, priorizadas por
  AuM alto → "llamá a estos antes de perderlos".
- **Grandes sin contacto:** AuM alto + `primer_contacto_comercial` viejo/null.
- **Onboarding incompleto:** cuentas nuevas sin segmentar (`nivel_1` null) o sin operador.
- **Cuentas huérfanas:** AuM > 0 pero `operador_email` no existe en `Manager.Users`
  (operador que se fue) → hay que reasignarlas.
- **Concentración:** top cuentas que son X% del AuM de un operador (riesgo si se va una).

### Comparativas / tendencia — IDEAS NUEVAS
- **Ranking de operadores** por AuM, por activas, por flujo neto captado, por altas nuevas.
- **Cross-tab operador × segmento** (heatmap de AuM o de # cuentas).
- **Cohortes por `fecha_alta`** (retención: % que sigue activa según antigüedad).
- **Tendencia de AuM por operador** (AuM ya tiene snapshots históricos → serie temporal).

### Salud del master (data quality) — IDEA NUEVA
% segmentadas · % con operador · huecos por campo. Doble función: mide la calidad
del dato Y empuja a la mesa a completar la segmentación (que el editor CLIENTES ya permite).

### Drill-down: FICHA DEL CLIENTE (combina todo)
Header (denominación, operador, segmento, perfil, alta) · AuM actual + mini-serie
histórica · **timeline de movimientos** (`NegocioMovimientos`) · flujo neto · link KYC
(`/api/personas/datosPersona`, fase posterior). Es "el lugar donde ver al cliente".

### Arquitectura propuesta
- **Precompute (cron) `comercial_rollup`** que cruza Comitentes + última-op/volumen de
  `NegocioMovimientos` + AuM por cuenta → colección **`Clientes.ComercialCache`**
  (patrón `PnLTotalesCache` / `ConsolidadoCuentas`). El frontend lee eso (rápido).
  Agregar 1770 cuentas × movimientos en cada request sería caro → precompute 1×/día
  (o más seguido si hace falta). El estado comercial se recalcula contra "hoy".
- **Backend:** `api/services/comercial.py` (puro) + `api/routers/comercial.py` (patrón add-endpoint).
- **RBAC:** módulo nuevo **`comercial`** (`core/roles.py::MODULES` + `ENDPOINT_MODULE_PREFIXES` + matriz).
  v1 = admin only. Después: rol/acceso comercial + **scope por operador** (un operador ve
  solo su cartera filtrando `operador_email` = su email — reusar idea de `_grupos_scope`).
- **Frontend:** vista nueva en acaquant-web (nav item "Comercial"). v1 puede vivir
  como tab/grupo, o vista propia.

### Decisiones abiertas de la vista
- [ ] Umbrales de estado comercial (N/M días). ¿Fijos o configurables por la mesa?
- [ ] Precompute vs on-the-fly (recomiendo precompute → `ComercialCache`).
- [ ] Módulo RBAC `comercial` + quién lo ve en v1 (admin) y después (rol comercial / operadores con scope).
- [ ] "Período" de los KPIs de flujo/actividad: mes corriente, 30d móvil, configurable.
- [ ] MVP: ¿arrancamos por la **lente por operador** (resumen) o por la **tabla de clientes**?

## Decisiones abiertas (las vamos cerrando)

- [ ] Nombre definitivo DB + colección (propuesto: `Clientes.Comitentes`).
- [x] **APIs Aunesa**: `GET /api/cuentas/listadoCuentas` (master) +
  `GET /api/personas/datosPersona` (enriquecimiento, fase posterior).
- [x] **Campos auto** (12): id_cuenta, denominacion, tipo_titular, tipo,
  estado, clase, fecha_alta_legajo, tipo_cliente, perfil_inversion,
  operador_email, operador_nombre, provincia.
- [x] **Campos de segmentación manual** (13): nivel_1, nivel_2, nivel_3,
  nivel_4, nivel_5, primer_contacto_comercial, riesgo_la_ft, division, adc,
  dma, observaciones, sucursal, referido. (nivel_1..5 = árbol de segmentación;
  reemplazan a los viejos segmento/sub_segmento/sub_sub_segmento.)
- [x] **Filtro del sync**: Comitente + Activa (default; `--include-all` todo).
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
- **2026-05-22** — Usuario definió campos: 12 auto + 5 manuales (segmento,
  sub_segmento, sub_sub_segmento, sucursal, referido). Creado
  **`jobs/sync_comitentes.py`** (upsert idempotente, manuales preservados via
  `$setOnInsert`, filtro Comitente+Activa, `--dry-run`/`--include-all`).
  Pendiente: correr `--dry-run` en el Droplet, después agregar al cron.
- **2026-05-22** — Job validado en prod (poblado OK). Agregadas 3 corridas
  diarias al cron (`deploy/crontab.txt`): 14/17/21 UTC = 11/14/18 ART, L-V.
  Creado `scripts/backfill_comitentes_segmentacion.py` para cargar los campos
  manuales (segmento/…) en bloque desde un CSV (`id_cuenta` + columnas;
  actualiza solo manuales, no crea cuentas). Pendiente: aplicar cron en el
  Droplet (`crontab deploy/crontab.txt`) y cargar el CSV de segmentación.
  Próximo: vista/tablero comercial.
- **2026-05-22** — Migración aplicada en prod (1770 cuentas → nivel_1..5 +
  nuevos en null). Creado **editor CLIENTES en Manager** (espejo de Assets):
  `api/routers/manager/clientes.py` + `TabClientes` en manager-view.tsx →
  editar los 13 manuales por pantalla (datalists, filtro por operador/nivel_1,
  búsqueda). Es la vía principal de carga de segmentación; el CSV queda como
  opción de carga masiva. Pendiente: aplicar cron + (después) tablero comercial.
- **2026-05-22** — Revisión de campos manuales (de 5 a 13): segmento/
  sub_segmento/sub_sub_segmento → nivel_1/2/3; +nivel_4, nivel_5,
  primer_contacto_comercial, riesgo_la_ft, division, adc, dma, observaciones
  (sucursal y referido quedan). Actualizados job + backfill. Como el master
  ya estaba poblado (manuales en null), migrar docs existentes con `$rename`
  + `$set null` (Mongo Shell, ver pasos). Backfill por CSV EN PAUSA hasta que
  el usuario tenga el archivo.
- **2026-05-23** — Arranca el diseño de **[5] la vista COMERCIAL** (solo manager
  por ahora). Idea central: cruzar QUIÉN (`Clientes.Comitentes`: operador +
  segmentación) + ACTIVIDAD (`NegocioMovimientos`: última op, volumen) + TAMAÑO
  (`AuM`), todo por `id_cuenta`. Definido el concepto de **estado comercial**
  (distinto del legal): NUEVA→ACTIVA→ENFRIÁNDOSE→DORMIDA→BAJA por umbral de días
  sin operar — el corazón del "qué cuentas están activas". Dos lentes: por cliente
  y por operador. Documentadas ideas nuevas (churn, grandes sin contacto, cuentas
  huérfanas, concentración, ranking de operadores, cohortes, salud del master,
  ficha de cliente). Arquitectura propuesta: precompute `comercial_rollup` →
  `Clientes.ComercialCache`, módulo RBAC `comercial`, scope por operador.
  Pendiente del usuario: cerrar decisiones abiertas + elegir MVP (lente operador
  vs tabla clientes).
- **2026-05-23** — MVP construido (lente por OPERADOR, umbrales 30/90, decididos
  por el user). Backend: `api/services/comercial.py::resumen_por_operador` (cruce
  Comitentes × NegocioMovimientos × AuM × Users, on-the-fly cacheado TTL 300) +
  `GET /api/manager/comercial/operadores` (gate _MANAGER) + getters
  `get_db_clientes`/`get_db_manager`. Frontend: tab **COMERCIAL** en Manager
  (`comercial-panel.tsx`) con KPIs + tabla por operador (activas/dormidas/AuM/
  flag huérfana). Test del estado comercial. v1 = solo manager (admin); on-the-fly
  (no precompute todavía). Pendiente: restart api.service en el Droplet; validar
  números reales; después evaluar precompute si pesa + lente por cliente + ficha.
- **2026-05-23** — Vista COMERCIAL **en OPERACIONES** (distinta del MVP de Manager;
  la ven admin+trader). Calcada de NEGOCIO: selector de operador + KPIs (AuM
  gestionado, # clientes, Volumen MTD/YTD) + tabla de clientes (cuenta+nombre /
  AuM / Vol YTD) + gráfico de líneas con toggle Volumen/AuM. Backend:
  comercial.py (listar_operadores_comercial, resumen_comercial, clientes_comercial,
  serie_comercial) + 4 endpoints /api/operaciones/comercial/*. Volumen =
  sum(abs(importe)) mismas categorías que NEGOCIO; MTD/YTD calendario ART.
  Frontend comercial-operaciones-view.tsx. v1 — iterar con datos reales.
  Pendiente: restart api.service; validar números.
- **2026-05-24** — **Rediseño layout + interactividad + optimización** (es de las
  vistas más usadas). Layout estilo NEGOCIO: izq = bloque compacto "Resumen
  operador" (selector + 4 KPIs juntos, ya no cards grandes) sobre el gráfico de
  evolución (más chico); der = tabla de clientes 60% + **ficha del cliente 40%**.
  Interactivo: clickear un cliente re-scopea el gráfico a esa cuenta (toggle ×
  vuelve al operador) y llena la ficha. **Backend optimizado**: fusionados
  `resumen_comercial`+`clientes_comercial` → **`operador_comercial`** (una pasada:
  1 lookup de cuentas + 1 AuM + 2 aggregates vs 3; Vol YTD total = suma del group
  por cuenta). La **ficha** (nivel_1..5, provincia, sucursal, perfil, riesgo_la_ft,
  etc. — `_FICHA_FIELDS` de `Clientes.Comitentes`) viaja **embebida** en cada fila
  → seleccionar un cliente NO pega otra query. `serie_comercial` acepta `id_cuenta`
  opcional. Endpoints: `/comercial/operador` (reemplaza /resumen + /clientes) y
  `/comercial/serie?...&id_cuenta=`. Diag: `scripts/diag_comercial.py`. Pendiente:
  restart api.service; validar números (correr el diag).
- **2026-05-24 (v2 layout)** — Reacomodo de espacio. **KPIs + selector** pasan a un
  **header slim** arriba (métricas generales, no interactivas, ya no bloque grande).
  Izq: gráfico de evolución **más chico** (flex-2) + **ficha con tabs** (flex-3);
  tab "Datos" muestra SOLO `nivel_1..5`, `primer_contacto_comercial`, `riesgo_la_ft`,
  `division`, `adc`, `dma` en grilla 3-4 col (se sacó tipo_cliente/tipo_titular y el
  AuM/Vol de la ficha — se repetían). `_FICHA_FIELDS` recortado a ese set.
  Der: tabla de clientes 60% + **PORTAFOLIO/tenencia del cliente 40%** (master-detail
  estilo AUM): nuevo `portafolio_cliente(id_cuenta)` → posiciones de `Valuaciones.AuM`
  (último snapshot) `{unidad, valuacion, pct}`, endpoint `/comercial/portafolio`.
  La ficha queda preparada para más tabs (hoy solo "Datos"). Pendiente: restart +
  validar en pantalla.
- **2026-05-24 (v3 gráfico)** — Solo frontend. Ficha alineada con el portafolio
  (ambas 40%) → el gráfico crece a 60%. Header de gráfico estilo NEGOCIO/AUM:
  título (Volumen operado / AUM · ARS), rango de fechas visible, Total período
  (volumen=suma) / Último (aum=stock), toggle DIARIO/SEMANAL/MENSUAL y selector
  de rango 1W/1M/3M/6M/YTD/1A/ALL con pan ◀▶. Todo client-side (la serie ya viene
  completa). Agregación metric-aware: volumen suma por bucket, AuM toma el último
  del bucket. Sin "Foco día" (es de las barras de NEGOCIO, no aplica a la línea).
- **2026-05-24 (v4)** — Selector de operador movido a la **barra de tabs** de
  OPERACIONES (margen sup. derecho, solo en COMERCIAL): estado sube a
  `operaciones-view.tsx`, llega a la vista como prop `operador`. El panel de
  **Portafolio** pasa a tener **tabs Tenencia / Operaciones**: nueva
  `operaciones_cliente(id_cuenta, limite=300)` → boletos operativos del cliente
  (`_CATS_OPERACIONES` = volumen + rescates FCI), recientes primero, desde
  `CashFlow.NegocioMovimientos` scopeado por id bracketed. Endpoint
  `/comercial/operaciones`. Columnas: fecha · categoría · ticker · cant · precio ·
  importe (u$s si USD). Así el operador ve qué operó el cliente.
- **2026-05-24 (v5 perf/QA)** — Optimización de queries + QA, pensando en escala.
  Cuello de botella detectado: el volumen por operador filtraba `NegocioMovimientos`
  con un **regex de alternación** sobre `cuenta` (`^\[(id1|id2|…)\]`) → no usa índice,
  escanea. **Fix estructural**: se denormaliza **`id_cuenta`** en cada boleto (ingesta
  `negocio_movimientos.py` lo extrae de `cuenta`) + índices `(id_cuenta, fecha)` y
  `(id_cuenta, categoria, fecha)`. Todo `comercial.py` (incl. `resumen_por_operador`
  del MVP) pasa de regex a `{id_cuenta: {$in: ...}}` / `{id_cuenta: id}` → IXSCAN.
  Backfill de docs viejos: `scripts/backfill_id_cuenta_negocio.py` (update_many con
  pipeline `$regexFind`, server-side). AuM y Comitentes ya estaban bien indexados.
  **QA**: `scripts/diag_comercial.py` (timings + explain IXSCAN/COLLSCAN + invariante
  Σ=total); tests unit (`_extract_id_cuenta`, `_match_volumen` sin regex); tests de
  integración `tests/integration/test_comercial_integration.py` (`-m integration`:
  invariantes + índice + 0 docs sin id_cuenta). Precompute (`ComercialCache`) NO se
  hizo — queda para Fase 2 solo si el diag muestra que pesa.
  **Orden de deploy**: git pull → `python -m scripts.backfill_id_cuenta_negocio`
  → restart api.service → `python -m scripts.diag_comercial` para validar.
- **2026-05-24 (v5.1)** — Diag en prod: todo IXSCAN, 0 sin id_cuenta. Único outlier
  `serie_comercial(metric=aum)` ~504ms — la query suma `valuacion` sobre toda la
  historia de AuM del operador y el índice `(id_cuenta, fecha_snapshot)` no incluía
  `valuacion` → FETCH por doc. Fix: **índice covering** `(id_cuenta, fecha_snapshot,
  valuacion)` en `crear_indices.py` → el `$group` lee del índice sin FETCH. Diag
  ahora muestra la cadena de stages (PROJECTION_COVERED→IXSCAN = covered).
  Correr `python -m scripts.crear_indices` en el Droplet y re-diag para confirmar.
