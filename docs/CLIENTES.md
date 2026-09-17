# CLIENTES — grupos, segmentación patrimonial y tablero comercial

> **Un doc por dominio.** Consolidó a `GRUPOS.md`, `SEGMENTACION_PATRIMONIAL.md` y
> `AGREGADO_COMERCIAL.md` el 2026-08-31. Los tres cruzan por `id_cuenta` contra
> `clientes.comitentes` y los tres hablan de la misma pregunta —quién es el
> cliente y cuánto pesa—, así que tenerlos separados obligaba a abrir tres
> archivos para entender una fila del tablero.


---

# PARTE A — Grupos económicos

Scoping multi-tenant: restringir qué cuentas comitentes ve cada usuario.
El admin lo gestiona 100% desde `/manager → GRUPOS`.

### Modelo

Tabla **`manager.grupos`** (SQL). Shape conceptual de un grupo:

```jsonc
{
  nombre:     "Mesa Rosario",
  emails:     ["user1@x.com", "user2@x.com"],   // lowercased
  id_cuentas: ["805", "1207", ...],             // id_cuenta (string)
  creado_por: "admin@x.com",
  creado_at:  <timestamptz>,
  updated_at: <timestamptz>,
  updated_por:"admin@x.com",
}
```

Un grupo agrupa **usuarios** (por email) + **cuentas** (por `id_cuenta`).
Las cuentas asignables son SOLO las que ya existen (último snapshot AuM) —
el selector del panel se puebla con `portfolio.listar_cuentas()`.

### Regla de visibilidad

`core/grupos.py::cuentas_visibles(email) -> set[str] | None`:

| Caso | Resultado |
|---|---|
| admin | `None` → ve TODO |
| usuario en NINGÚN grupo | `None` → ve TODO (transición: nada se rompe) |
| usuario en ≥1 grupo | `set` = unión de las `id_cuentas` de sus grupos |

`None` = sin restricción. Cache TTL 60s; `invalidate_cache()` tras cada
mutación. Fail-open ante error de DB (devuelve `None`) — los grupos no
deben tumbar la app; el peor caso es "ve de más", igual al estado actual.

### Plan en fases

- **Fase 1 (HECHA)** — modelo + CRUD.
  - `core/grupos.py`: resolver + CRUD + cache.
  - `api/routers/manager/grupos.py`: `GET/POST/PATCH/DELETE
    /api/manager/grupos` (admin-only, hereda el gate de `manager`).
  - Frontend: tab GRUPOS en el panel Manager (`grupos-panel.tsx`).
  - **Todavía NO enforcea** — solo se pueden crear/editar grupos.

- **Fase 2 (HECHA)** — enforcement backend para el namespace `id_cuenta`
  (valuaciones + portfolio/AuM + PnL).
  - `api/deps.py`: dependencies `scope_cuentas`
    (inyecta `tuple[str,...] | None`) y `verificar_id_cuenta` (403).
  - Los services de `portfolio.py`, `pnl.py` y `valuaciones.py` aceptan
    `scope` y lo aplican al filtro SQL (`WHERE id_cuenta IN ...`) / de filas.
  - Routers `carteras.py` y `valuaciones.py` resuelven el scope y lo
    pasan; los endpoints `/{id_cuenta}/*` quedan gateados.
  - El cron (`pnl_todas_cuentas_compute`) corre sin scope.

- **Fase 2b (HECHA)** — enforcement del namespace `cuenta`
  (string "[<id>] NOMBRE") que usan `FlujosAPI` y `NegocioMovimientos`
  (no tienen `id_cuenta` directo). `verificar_cuenta_str` en `api/deps.py`
  (regex sobre el id bracketed); el filtro SQL equivalente vive en cada
  service (`cashflow_sql`). Los helpers Mongo (`scope_cuenta_match` /
  `aplicar_scope_cuenta`) se borraron con el decomiso de Mongo.
  Aplicado en `/flujos` y `/negocio/{serie,cuentas,cuentas-matrix,
  boletos,cuentas-list}`. `operar` sigue admin-only.

- **Fase 3 (HECHA — sin cambios de código)** — frontend. Se revisaron
  todos los selectores de cuenta de `acaquant-web`: todos sourcean de
  endpoints ya scopeados por Fase 2/2b.
  - `/aum` (+ VALUACIONES, PNL TÍTULOS): `aum-view` → `/api/portfolio-cuentas`.
    `valuaciones-view` / `pnl-titulos-view` reciben `idCuenta` como prop.
  - POR CUENTA / TOTALES: tablas de `consolidado` / `pnl-todas`.
  - `/operaciones/negocio`: `negocio-view` → `/negocio/cuentas-list`.
  - Manager (AUNESA, debug XIRR, GRUPOS): admin-only → scope None.
  - Caso borde: URL bookmarkeada con `?cuenta=<ajena>` → backend 403.

### Notas

- `operar` quedó admin-only (commit `7478405`) como medida previa —
  independiente de grupos.
- No es RBAC: RBAC (`core/roles.py`) decide qué **módulos** ve un usuario;
  grupos decide qué **cuentas** ve dentro de esos módulos.

---

# PARTE B — Segmentación patrimonial

> Documento vivo del feature. El **LOG DE AVANCES** (al final) es append-only
> con fecha. Cada vez que toquemos algo de esta feature, agregar una entrada.
>
> ## ⚠️ ESTADO REAL (verificado contra el código, 2026-08-31)
>
> **El plan de 7 fases de §5 se ejecutó, pero NO con los nombres que dice.** Se
> deja el plan porque explica el criterio; lo que hay que creerle es esta tabla:
>
> | Lo que el doc nombra | Qué existe de verdad |
> |---|---|
> | `api/routers/manager/comercial.py` | **`api/routers/manager/clientes.py`** — ahí vive la carga de cupos y la re-clasificación |
> | `api/services/comercial.py` (upsert de cupo) | **`api/services/segmentacion.py`** |
> | `jobs/uva.py` / serie UVA propia | **no se creó**: el motor lee la UVA de `api.services.macro.get_ultimo_uva` |
> | `clientes.limites_fondeo_historico` | **no existe.** No hay tabla de histórico: la Fase 6 era opcional y no se hizo |
> | `scripts/cargar_cupos_fondeo.py`, `cargar_limites_fondeo.py`, `rename_limite_fondeo_a_cupo.py`, `backfill_segmentos_upper.py`, `diag_segmentacion_patrimonial.py` | **ninguno existe.** Los tres primeros eran fallbacks opcionales que la UI volvió innecesarios; los otros dos eran one-shot y ya cumplieron |
> | `GET /api/manager/comercial/debug-segmento` | **no existe** |
>
> **Sí existe y corre**: `jobs/segmentar_patrimonial.py` (el motor, Fase 4) —
> ⚠️ **pero NO tiene cron**: no está en `deploy/crontab.txt`, así que hoy se
> corre a mano. Es la única diferencia con el diseño que puede sorprender en
> producción.
>
> Sub-feature del **Tablero Comercial** (vista por operador; ver `CLAUDE.md` →
> "Tablero Comercial") — esto es
> "Segmentación de clientes" (capa [3] en la cadena de dependencias del tablero),
> pero con un criterio **patrimonial objetivo** (límite de fondeo) en vez de
> categórico-manual.

### Objetivo

Clasificar cada cuenta comitente en uno de **6 segmentos patrimoniales** (3 para
Personas Humanas, 3 para Personas Jurídicas) a partir del **límite de fondeo
disponible** que reporta el custodio. El segmento es el insumo central del CRM
para métricas comerciales, campañas y priorización de la mesa.

**Por qué este criterio y no el `nivel_3` manual de hoy**: `nivel_3` se carga a
mano por la mesa y se revirtió la derivación automática previa
(commit `7d71bc4`, derivaba de `tipo_cliente` y daba mal). El límite de fondeo
es un dato duro, comparable entre cuentas, y refleja el patrimonio real que el
custodio le reconoce al cliente — es el proxy correcto para "cuán grande" es
un cliente desde lo comercial.

**Decisión clave**: `segmento_patrimonial` (derivado, automático) **convive**
con `nivel_3` (manual, lo que ya hay) — son **dos campos separados**. No se
pisa el manual. Esto evita repetir el bug del revert y deja a la mesa la
opción de overrides puntuales si el algoritmo no les cierra para una cuenta.

### Reglas de clasificación (6 segmentos)

El criterio depende de si la cuenta es **Persona Humana (PH)** o **Persona
Jurídica (PJ)** y del **límite disponible para fondear** convertido a la
moneda/unidad correspondiente.

#### Personas Humanas — umbral en **USD**

| Segmento              | Límite disponible (USD) |
|-----------------------|-------------------------|
| `PH_RETAIL`           | < 50.000                |
| `PH_MEDIO_RETAIL`     | ≥ 50.000 y ≤ 100.000    |
| `PH_ALTO_PATRIMONIO`  | > 100.000               |

#### Personas Jurídicas — umbral en **UVAs**

| Segmento       | Límite disponible (UVAs) |
|----------------|--------------------------|
| `PJ_PEQUENA`   | ≤ 350.000                |
| `PJ_MEDIANA`   | > 350.000 y ≤ 700.000    |
| `PJ_GRANDE`    | > 700.000                |

> **Convención de bordes** (para que no haya ambigüedad en el código):
> los valores exactos del límite superior caen en el segmento inferior
> (intervalos `(−∞, A]`, `(A, B]`, `(B, +∞)` para PJ; `[0, 50k)`, `[50k, 100k]`,
> `(100k, +∞)` para PH — leído tal cual del texto del usuario).

#### Distinguir PH vs PJ

Source of truth: campo **`tipo_cliente`** de `Comitentes`. Mapping confirmado
contra los valores reales (corrida 2026-05-28 sobre 1773 cuentas):

| `tipo_cliente`              | Clasificación | n    |
|-----------------------------|---------------|------|
| `Persona`                   | **PH**        | 900  |
| `Empleado`                  | **PH**        | 3    |
| `Empresa`                   | **PJ**        | 495  |
| `Fondo Común de Inversión`  | **PJ**        | 309  |
| `Compañía de seguros`       | **PJ**        | 8    |
| `Fideicomiso`               | **PJ**        | 3    |
| `Institucional`             | **PJ**        | 2    |
| *null*                      | sin clasificar (53 cuentas, se ignoran) | 53 |

Regla en código:
- `tipo_cliente ∈ {"Persona", "Empleado"}` → PH.
- `tipo_cliente` en el resto de valores no-null → PJ.
- `tipo_cliente` null → `segmento_patrimonial = null` (no se clasifica; queda
  el límite cargado sin segmento).

Si a futuro aparecen valores nuevos en `tipo_cliente` (Aunesa los puede
agregar), el motor los loguea como "sin mapear" y no asigna segmento — hay
que actualizar el mapping a mano. El diag detecta eso re-corriéndolo.

### Conversiones de moneda/unidad

El **input siempre viene en ARS** (es lo que reporta el custodio en el Excel).
Para clasificar PH se convierte a USD; para clasificar PJ se convierte a UVAs.

#### ARS → USD (para PH)

**Decisión recomendada (a confirmar)**: usar **MEP** del momento del cálculo
(no del momento de la carga). Justificación: el "patrimonio del cliente" en
términos relevantes para la mesa es lo que puede dolarizarse en el mercado,
no el oficial. Fuente live: `get_ultimo_mep` (TTL 5s), igual que el resto de
la API.

Alternativas si se descarta MEP: dólar oficial mayorista (BCRA A3500) desde la
serie macro DOLAR (`series_macro`, fixing diario) o desde
`valuaciones.dolar_oficial_live` (feed MAE intradiario).

#### ARS → UVAs (para PJ)

**Pendiente de implementar**: en el repo no hay serie UVA todavía. Hay que
sumarla a `jobs/bcra.py` (es una serie estadística pública igual que CER /
Riesgo País). Decisión abierta sobre dónde persistirla — probablemente como una
serie UVA más en SQL (timeseries diaria, mismo shape que la serie DOLAR).

Una vez ingestada: el motor toma el último valor UVA publicado al momento
del cálculo y hace `limite_uvas = limite_ars / uva`.

### Modelo de datos

Todo vive en **`clientes.comitentes`** (la tabla ya existente) como
**campos/objetos propios de la cuenta** — no se crea tabla nueva en esta fase.

```jsonc
{
  // ... campos existentes (id_cuenta, denominacion, operador, nivel_1..5, etc.) ...

  // ── Input de la carga masiva (Excel) ────────────────────────────────
  "cupo": {
    "transaccional_ars": 12345678.90,
    "usado_ars":          2345678.90,
    "utilizacion_pct":   19.01,             // derivado, persistido para indexar
    "cargado_en":  "2026-05-28T13:00:00Z",
    "fuente":      "excel:carga_2026-05"    // nombre del archivo o etiqueta
  },

  // ── Output del motor de segmentación ────────────────────────────────
  "segmento_patrimonial": "PH_RETAIL",    // uno de los 6, o null si falta input
  "segmento_patrimonial_calc": {           // auditabilidad
    "tipo": "PH",                          // "PH" | "PJ"
    "limite_convertido": 38420.15,         // USD para PH, UVAs para PJ
    "unidad": "USD",                       // "USD" | "UVA"
    "tc": {                                // sólo PH
      "valor": 1320.50,
      "fuente": "MEP",                     // o "oficial_a3500", etc.
      "fecha":  "2026-05-28"
    },
    "uva": {                               // sólo PJ
      "valor": 1450.32,
      "fecha": "2026-05-28"
    },
    "calculado_en": "2026-05-28T13:05:00Z"
  }
}
```

**Contrato con `sync_comitentes`**: estos campos NO los maneja Aunesa, los
escribe nuestra carga masiva / nuestro motor. Por lo tanto van a `MANUAL_FIELDS`
(o equivalente) en `jobs/sync_comitentes.py` para que el sync diario NO los
pise. Mismo patrón que `nivel_1..5` y `operador_email/operador_nombre` hoy.

**Histórico (FASE 2, fuera de scope inicial)**: una tabla
`clientes.limites_fondeo_historico` append-only (una fila por carga) habilita
métricas temporales (evolución del segmento del cliente mes a mes) sin migrar
el modelo actual. No se construye ahora; se deja la puerta abierta.

### Componentes a construir

#### 1. Carga desde Manager — endpoint bulk + tab "Fondeos"

**No es un script suelto, es parte del manager** (mismo patrón que la carga
de segmentación de Clientes hoy). Espejo de `POST /api/manager/clientes/bulk`
(`api/routers/manager/clientes.py:177`) que ya implementa exactamente la
semántica que pidió el usuario: itera fila por fila, solo setea los campos
no vacíos, **no toca las cuentas ausentes del payload**, re-subir reemplaza
vía `$set` (idempotente).

**Backend** — dos opciones (TBD):

- **(a) Extender `/clientes/bulk` existente** para que reconozca dos columnas
  más (`cupo_transaccional`, `cupo_usado`). Si vienen, las escribe en el
  subdoc via dot-notation (`"cupo.transaccional_ars": ...`) + computa
  `utilizacion_pct` + setea `cargado_en` y `fuente`. Pro: un solo endpoint,
  un solo grid.
- **(b) Endpoint hermano `POST /api/manager/clientes/bulk-fondeo`** dedicado.
  Pro: separación clara, validación numérica propia, libra de complicar el
  bulk de segmentación. **Recomendado** — el subdoc + los cómputos derivados
  justifican un endpoint propio.

En ambos casos, contrato **idéntico al bulk existente**:
- Recibe `rows: list[dict]` parseadas en el frontend desde CSV/XLSX.
- Filas sin `id_cuenta` → contadas en `sin_id`, no escriben.
- Filas con `id_cuenta` pero sin ninguna columna de cupo → `sin_campos`.
- `update_one` por `id_cuenta` (NO upsert; no se crean cuentas — vienen del
  sync diario).
- Devuelve `{actualizadas, matched, filas_validas, sin_id, sin_campos,
  n_no_encontradas, no_encontradas[:50]}`.
- Stamp de `actualizado_por` (email del actor) + `actualizado_at`.

**Frontend** (`acaquant-web/`) — tab nueva **"Fondeos"** dentro de
`/manager → CLIENTES` (sub-tabs internas, junto al editor de segmentación
que ya está). Misma UX que el bulk de segmentación:
- Grid con 3 columnas: `id_cuenta`, `cupo_transaccional`, `cupo_usado`.
- Botón "Cargar archivo" (CSV/XLSX) → parsea en cliente, muestra preview
  de N filas, valida tipos, envía al endpoint.
- Soporte de **paste** desde Excel directo a la grilla (el patrón actual ya
  lo soporta) para cargar 5-10 filas a mano sin armar archivo.
- Tras el POST: toast con `{actualizadas, no_encontradas}` y refresh.

**Bulk script como fallback** (`scripts/cargar_cupos_fondeo.py`) — queda
disponible solo para una **carga inicial masiva** de un archivo histórico
muy grande, o para batch ad-hoc desde el Droplet. **No es el flujo
principal**. Internamente reusa el mismo helper que el endpoint para que la
lógica viva en un solo lugar (en `api/services/`, no duplicada).

#### 2. Motor de segmentación — `jobs/segmentar_patrimonial.py`

- Toma todas las cuentas de `clientes.comitentes` con `cupo.transaccional_ars` poblado.
- Para cada cuenta: detecta PH/PJ, obtiene TC (MEP) o UVA según corresponda,
  convierte el límite, aplica las reglas → escribe `segmento_patrimonial` +
  `segmento_patrimonial_calc`.
- Idempotente. Corre standalone o encadenado.
- Cron: a definir (probablemente mensual, post-carga). Si UVA o MEP cambian
  mucho podría correr semanalmente — TBD.

#### 3. Vista en `/comercial`

- Servicio: `api/services/comercial.py` → agregaciones por `segmento_patrimonial`
  (counts + AuM agregado por banda + % utilización promedio), filtrable por
  operador.
- Endpoint: `api/routers/manager/comercial.py` (manager-only, sigue el patrón
  existente — ver TABLERO_COMERCIAL).
- Frontend: card/tab nueva dentro del tablero comercial en `acaquant-web/`
  (cross-repo, ver feedback `acaquant_web_companion`).

#### 4. Diagnóstico — `scripts/diag_segmentacion_patrimonial.py`

Read-only. Imprime la distribución actual por segmento, qué cuentas tienen
`cupo` pero no `segmento_patrimonial` (debería estar vacío post-motor),
qué cuentas con AuM > 0 no tienen cupo cargado (gap del Excel), etc.

### Plan de implementación (orden sugerido)

1. **Fase 1 — Backend de carga (endpoint manager + protección del subdoc).**
   - `POST /api/manager/clientes/bulk-fondeo` en
     `api/routers/manager/clientes.py` (o ampliación del bulk existente
     según decisión).
   - Service en `api/services/` con la lógica del upsert (reusable desde el
     script fallback).
   - Agregar `cupo` y `segmento_patrimonial*` a `MANUAL_FIELDS` (o
     `MANUAL_SUBDOCS` para el subdoc) en `jobs/sync_comitentes.py` para que
     el sync diario NO los pise.
   - Validar imports antes de pushear (REGLA #1 en `api/CLAUDE.md`).
2. **Fase 2 — Tab "Fondeos" en `/manager → CLIENTES` (`acaquant-web`).**
   - Sub-tab nueva con grid de 3 columnas + paste + upload CSV/XLSX.
   - Reusar el componente de bulk de segmentación (mismo patrón).
3. **Fase 3 — Ingesta UVA al repo** (bloqueante para clasificar PJ, pero NO
   para cargar los límites).
   - Sumar serie UVA a `jobs/bcra.py` o crear `jobs/uva.py`.
   - Persistir como serie UVA en SQL (TBD shape exacto).
4. **Fase 4 — Motor de segmentación**.
   - `jobs/segmentar_patrimonial.py`. Idempotente.
   - Cron en `deploy/crontab.txt` + regenerar `docs/ACAQUANT.md`
     (`python -m scripts.gen_sistema`).
5. **Fase 5 — Vista de segmentación en `/comercial`**.
   - Service + endpoint manager-only en `api/services/comercial.py` /
     `api/routers/manager/comercial.py`.
   - Frontend en `acaquant-web/`.
6. **Fase 6 (opcional)** — Histórico (`LimitesFondeoHistorico`) si la mesa
   quiere ver evolución mes a mes.
7. **Fase 7 (opcional)** — Script fallback `scripts/cargar_limites_fondeo.py`
   para una carga inicial masiva desde el Droplet, si llega un Excel
   histórico muy grande. Reusa el service de Fase 1.

> **Por qué este orden**: las Fases 1 y 2 permiten al usuario empezar a
> cargar fondeos hoy desde la UI sin esperar nada más. Las Fases 3 y 4
> habilitan la clasificación automática. La Fase 5 es la vista. Carga UVA
> y motor pueden ir en paralelo con la UI si se quiere paralelizar.

### Decisiones tomadas

- **Campos propios en `clientes.comitentes`** (no tabla nueva en esta fase).
- **`segmento_patrimonial` convive con `nivel_3` manual** (no lo pisa).
- **`PH_RETAIL` / `PH_MEDIO_RETAIL` / `PH_ALTO_PATRIMONIO` / `PJ_PEQUENA` / `PJ_MEDIANA` / `PJ_GRANDE`** como enum de string (snake_case mayúscula, sin acentos para evitar bugs en queries).
- **Inputs en ARS, segmentación en USD (PH) o UVAs (PJ)**.
- **Carga principal desde Manager → CLIENTES → Fondeos** (endpoint bulk +
  UI tab nueva en `acaquant-web`), espejo del bulk de segmentación que ya
  existe. Garantiza la semántica pedida: cargar 10 filas toca solo esas 10,
  re-subir reemplaza, las 990 restantes intactas. Script CLI queda como
  fallback opcional para bulk inicial.
- **`cupo` (subdoc) y `segmento_patrimonial*` van a `MANUAL_SUBDOCS` /
  `MANUAL_FIELDS`** en `jobs/sync_comitentes.py` → el sync diario de Aunesa
  NO los pisa.
- **Sin histórico en el MVP**; modelo deja la puerta abierta para fase 2.

### Decisiones abiertas

- [ ] **Endpoint dedicado vs extender el bulk existente**: opción (a)
      extender `POST /api/manager/clientes/bulk` con dos columnas más, o (b)
      crear `POST /api/manager/clientes/bulk-fondeo` (recomendado por
      separación de validación numérica y subdoc). (Bloquea Fase 1.)
- [ ] **UX de la tab**: ¿sub-tab "Fondeos" dentro del editor existente de
      CLIENTES, o tab nueva al mismo nivel "FONDEOS"? Recomendado: sub-tab
      interna, mismo URL, menos ruido en el nav. (Bloquea Fase 2.)
- [ ] **TC a usar para PH**: MEP (recomendado) vs oficial A3500 vs mayorista
      MAE. (Bloquea Fase 4.)
- [ ] **Dónde persistir UVA**: serie UVA nueva en SQL (recomendado) vs sumarla
      a una serie existente. Cron de ingesta (probablemente 12 UTC L-V,
      como `argentina_datos`). (Bloquea Fase 4 para PJ.)
- [ ] **Periodicidad del motor**: post-carga + cron semanal vs mensual.
- [ ] **Estado inicial post-carga**: ¿qué hacemos con cuentas Activas sin
      límite cargado? Opciones: `segmento_patrimonial = null`, o
      `"SIN_DATOS"` explícito.

### Estado / TODO

- [x] Diseño documentado.
- [ ] Confirmar decisiones abiertas con la mesa.
- [x] Fase 1 — Backend de carga (endpoint manager + `MANUAL_SUBDOCS`).
- [x] Fase 2 — Tab "Fondeos" en `/manager → CLIENTES` (acaquant-web).
- [ ] Fase 3 — Ingesta UVA (serie UVA en SQL).
- [ ] Fase 4 — Motor de segmentación.
- [ ] Fase 5 — Vista de segmentación en `/comercial`.
- [ ] Fase 6 — Histórico (opcional).
- [ ] Fase 7 — Script CLI fallback (opcional).

---

### LOG DE AVANCES

- **2026-05-28** — **Doc inicial.** Se definió el modelo (subdoc en
  `Clientes.Comitentes`, separado de `nivel_3` manual), los 6 segmentos con
  umbrales (3 PH en USD: 50k/100k; 3 PJ en UVAs: 350k/700k), las conversiones
  (input ARS → USD/UVAs), y el plan inicial. Ver "Decisiones abiertas".
- **2026-05-28** — **Cambio de approach en la carga.** El usuario pidió que
  el insert sea desde Manager → CLIENTES (no script desde Droplet), espejo
  del bulk de segmentación que ya existe en
  `POST /api/manager/clientes/bulk` (`api/routers/manager/clientes.py:177`).
  Ese endpoint ya implementa exactamente la semántica pedida: itera fila
  por fila, solo setea los campos no vacíos, no toca cuentas ausentes del
  payload, idempotente al re-subir. Plan reordenado: ahora Fase 1 = endpoint,
  Fase 2 = tab "Fondeos" en acaquant-web. UVA/motor/vista se corren a
  Fases 3-5. Script CLI baja a Fase 7 opcional (fallback). Sumadas decisiones
  abiertas: endpoint dedicado vs extender, UX de la tab.
- **2026-05-28** — **Fase 2 deployada (frontend).** Sub-tab "FONDEOS"
  agregada dentro de `/manager → CLIENTES` en acaquant-web (commit
  `635614c` en repo hermano). Patrón espejo del bulk de segmentación:
  tabla read-only con `limite_fondeo` (disponible/usado/% util/fecha) +
  botón "📁 Importar archivo" que parsea CSV/XLSX con headers
  `id_cuenta`, `limite_disponible`, `limite_utilizado` y pega al endpoint
  `bulk-fondeo`. Toggle "solo con fondeo cargado" para focalizar la
  vista. Refactor mínimo: `TabClientes` original → `TabClientesSegmentacion`;
  nuevo `TabClientes` es wrapper con switch de sub-tabs. Cero cambios
  funcionales en segmentación. Endpoint `GET /api/manager/clientes` ya
  incluye el subdoc `limite_fondeo` en la proyección (commit `6dd2e51`).
- **2026-05-28** — **Fase 1 deployada (backend).** Endpoint
  `POST /api/manager/clientes/bulk-fondeo` en `api/routers/manager/clientes.py`:
  recibe `rows = [{id_cuenta, limite_disponible, limite_utilizado}, ...]` +
  `fuente` opcional. Parser numérico tolerante (AR `1.234.567,89` o US
  `1234567.89`). Escribe `limite_fondeo.{disponible_ars, utilizado_ars,
  utilizacion_pct, cargado_en, fuente}` via dot-notation. Si vienen los dos
  montos computa pct; si viene uno solo lo `$unset` (mejor stale-removed que
  stale-confuso). Devuelve `{actualizadas, matched, sin_id, sin_campos,
  sin_numeros, n_no_encontradas, no_encontradas[:50]}`. NO crea cuentas
  (matched-only). `jobs/sync_comitentes.py` actualizado: nuevo tuple
  `MANUAL_SUBDOCS = ("limite_fondeo",)` que se inicializa como `{}` en
  `$setOnInsert` para que dot-notation funcione desde el primer write.
  Imports validados (REGLA #1) — 205 routes OK.
- **2026-05-28** — **Mapping PH/PJ confirmado contra datos reales.** Un diag
  corrido sobre 1773 cuentas reveló 7 valores
  de `tipo_cliente` + 53 nulls. Mapping fijado: PH = `{Persona, Empleado}`,
  PJ = `{Empresa, Fondo Común de Inversión, Compañía de seguros, Fideicomiso,
  Institucional}`, null = sin clasificar. Se cerró la decisión abierta de
  cómo distinguir PH/PJ — no hace falta fallback al CUIT. Tabla con counts
  documentada en "Distinguir PH vs PJ".
- **2026-05-28** — **Labels con prefijo PH/PJ + UVA manual + endpoint debug.**
  - Labels finales del motor: `PH RETAIL` / `PH MEDIO RETAIL` /
    `PH ALTO PATRIMONIO` / `PJ PEQUEÑA` / `PJ MEDIANA` / `PJ GRANDE`. El
    prefijo aclara la lente PH vs PJ sin tener que cruzar con tipo_cliente.
    Migración: re-correr `jobs.segmentar_patrimonial --apply` actualiza
    los nivel_3 viejos (sin prefijo) a los nuevos.
  - `Trading.UVA` minimal: doc `{valor_uva: <float>}`, se inserta manual
    desde Compass. `api/services/macro.py::get_ultimo_uva()` toma el más
    reciente por `_id` desc (cache TTL 5min). Habilita la clasificación PJ
    (antes quedaban en `null` por falta de fuente UVA).
  - `GET /api/manager/comercial/debug-segmento?id_cuenta=X` — audit endpoint:
    devuelve cupo raw + tc usado (MEP/UVA) + cálculo + nivel_3 actual vs
    recalculado + umbrales. Lo consume `/manager → VALIDACIONES → DEBUG
    SEGMENTO` para que la mesa pueda ver paso a paso cómo se segmentó una
    cuenta.
- **2026-05-28** — **Convención de MAYÚSCULAS para nivel_1..5.** El usuario
  reportó duplicados en la distribución por nivel ("Productores" + "PRODUCTORES"
  como dos categorías distintas). Decisión: TODOS los segmentos viven en
  MAYÚSCULAS. Cambios:
  - Labels del motor de segmentación pasados a uppercase: `RETAIL`,
    `MEDIO RETAIL`, `ALTO PATRIMONIO`, `PEQUEÑA`, `MEDIANA`, `GRANDE`.
  - `scripts/backfill_segmentos_upper.py` — normaliza los 5 niveles en
    `Clientes.Comitentes` vía aggregation pipeline + `$toUpper`. Idempotente.
  - `api/routers/manager/clientes.py` — bulk y PATCH aplican `.upper()` a
    nivel_1..5 antes de escribir (`_UPPERCASE_FIELDS` + `_normalize_value`).
    Otros campos (observaciones, operador_email) no se normalizan.
  Orden de despliegue: deploy backend → correr backfill → re-correr motor de
  segmentación (re-clasifica los que cambiaron de label).
- **2026-05-28** — **Fase A deployada: motor de segmentación PH + escribe a `nivel_3`.**
  Decisión del usuario: el campo target NO es `segmento_patrimonial` sino el
  ya existente `nivel_3` (queda derivado, no manual). Labels en español:
  `Retail` / `Medio Retail` / `Alto Patrimonio` (PH) y `Pequeña` / `Mediana`
  / `Grande` (PJ).
  - `api/services/segmentacion.py::clasificar_nivel_3()` — función pura,
    PH umbrales USD 50k/100k vía MEP, PJ umbrales UVAs 350k/700k. Devuelve
    `None` si falta input (cupo / tipo_cliente / TC).
  - `jobs/segmentar_patrimonial.py` — re-clasifica todas las Comitentes
    activas. `--dry-run` default + `--apply` + `--ids` para batch parcial.
    Idempotente (solo escribe cuando el label cambia).
  - `api/routers/manager/clientes.py::bulk_clientes_fondeo` — encadena la
    re-clasificación de las cuentas tocadas en el mismo request (visible
    al instante en la UI). Si falla la clasificación (ej. sin MEP), el
    cupo igual queda cargado y el cron lo arregla.
  - PJ queda en `nivel_3 = null` hasta sumar `Trading.UVA` (Fase B).
  REGLA #1: imports OK (205 routes). Cron post-deploy a definir.
- **2026-05-28** — **Rename vocabulario: `limite_fondeo` → `cupo`.** Los
  headers del Excel y el modelo Mongo se renombraron para alinearse con el
  vocabulario del custodio (cupo transaccional / cupo usado, no
  límite disponible / utilizado). Cambios:
  - Excel headers: `limite_disponible`/`limite_utilizado` →
    `cupo_transaccional`/`cupo_usado`.
  - Mongo subdoc: `limite_fondeo.{disponible_ars, utilizado_ars}` →
    `cupo.{transaccional_ars, usado_ars}` (otros campos del subdoc —
    `utilizacion_pct`, `cargado_en`, `fuente` — conservan nombre).
  - `MANUAL_SUBDOCS = ("cupo",)` en `jobs/sync_comitentes.py`.
  - TS type `LimiteFondeo` → `Cupo` en `acaquant-web/manager-view.tsx`.
  - Endpoint URL `POST /api/manager/clientes/bulk-fondeo` se mantiene
    (la sub-tab se sigue llamando "FONDEOS" como concepto general).
  - Migración: `scripts/rename_limite_fondeo_a_cupo.py` con `$rename`
    idempotente (subdoc + inner fields). Default `--dry-run`; correr con
    `--apply` ANTES del deploy del backend nuevo para no romper la sub-tab.

---

# PARTE C — Agregado del Tablero Comercial (DISEÑO, sin implementar)

**Estado: DISEÑO APROBADO, sin implementar.** Este doc es el paso previo al código.
Cuando se implemente, se pliega a la sección "Tablero Comercial" del `CLAUDE.md` raíz
y este archivo se borra (REGLA #5).

### 1. Por qué, con números medidos (2026-08-13)

`/api/operaciones/comercial/operador` y `/comercial/serie` suman **~3.100 s por
semana** de tiempo de API. Del lado de la base son el mayor bloque de LECTURA:

| query | llamadas | media | total acumulado |
|---|---|---|---|
| `SUM(valuacion)` por fecha sobre `portafolio.tenencia` | 48.420 | 35,0 ms | 1.696 s |
| `SUM(CASE WHEN moneda…)` por fecha sobre `negocio_movimientos` | 40.703 | 39,0 ms | 1.589 s |
| ídem agrupado por `id_cuenta` | 39.588 | 38,2 ms | 1.512 s |
| ídem, otra ventana | 22.295 | 39,0 ms | 870 s |
| ídem | 22.265 | 38,1 ms | 848 s |
| `SUM(valuacion) AS aum` sobre `tenencia` | 40.156 | 14,3 ms | 573 s |

El perfil del endpoint (cProfile) da **8 viajes a la base de ~56 ms cada uno** y
Python en el 11%. O sea: **no es N+1** (ese fue el caso de `/api/derivados/agro`, que
se arregló agrupando y salió gratis) — son agregaciones caras de verdad.

Y **crece solo**: `negocio_movimientos` va por 413.919 filas y suma todos los días.

### 2. Lo que YA se descartó, para no re-proponerlo

- **Índices.** `index_advisor` (con `hypopg`) sobre las 6 queries: mejoras de **0% a
  3%**. La mejor sugerencia (`btree(categoria)`) no justifica el costo de escritura en
  una tabla que el cron toca cada 30'. **Descartado con datos.**
- **Cache.** Cambia frescura (el user pidió explícitamente no cambiar funcionalidad) y
  **no escala**: la query sigue engordando y el primer usuario después de cada
  expiración paga el precio completo. **Descartado por criterio.**

### 3. El diseño

Mismo patrón que `operaciones.ops_agregado_diario` (HOT/COLD), que ya funciona en
esta casa:

- **Días CERRADOS** → pre-agregados en tabla. No cambian nunca (salvo corrección, ver §4).
- **HOY** → se calcula en vivo, como ahora.
- La lectura hace `UNION` de las dos partes.

**Grano propuesto:**

```
operaciones.comercial_agregado_diario
  fecha, id_cuenta, moneda  →  volumen_ars, volumen_usd, n_boletos
portafolio.tenencia_agregado_diario
  fecha, id_cuenta          →  aum
```

**Por qué ese grano alcanza (la clave de todo):** el tablero filtra por operador,
`nivel_1..5`, `referido` y `division` — y **todos esos son atributos de la CUENTA**
(viven en `clientes.comitentes`), no del boleto. Agregando a `(fecha, id_cuenta)` no se
pierde ninguna combinación de filtros: se agrega el hecho y se sigue joineando la
dimensión en la lectura. Si algún filtro futuro fuera atributo del BOLETO (ej. mercado
o especie), habría que sumarlo al grano o quedaría fuera del agregado.

**Ganancia esperada:** leer ~200 filas de agregado por cuenta-mes en vez de sumar
413.919. Los mismos números exactos.

### 4. ⚠️ La trampa de corrección (verificada en el código, 2026-08-13)

`ops_agregado_diario` detecta días sucios con `ingestado_en`. Acá **eso no alcanza**:

```python
## jobs/negocio_movimientos.py — marcar_anulados()
"UPDATE negocio_movimientos SET anulado_en = now() "
" WHERE fecha = %(fecha)s AND anulado_en IS NULL AND comprobante <> ALL(%(vivos)s)"
```

**La anulación NO toca `ingestado_en`.** Un boleto que Aunesa deja de devolver se marca
anulado y su día **no se vería sucio** → el agregado seguiría contando un boleto
anulado y el tablero mostraría de más, **en silencio y para siempre**.

Es exactamente el incidente de los movimientos de tesorería que el back office detectó
por un faltante clavado: un error silencioso de plata es peor que un endpoint lento.

**Solución elegida:** el detector de días sucios mira **las dos** columnas —

```sql
WHERE GREATEST(max(ingestado_en), max(COALESCE(anulado_en, 'epoch'))) > <último recompute>
```

No se toca el writer (`ingestado_en` conserva su significado: "cuándo se ingestó").

### 4-bis. Ratios MEDIDOS (2026-08-13) → el proyecto se parte en dos

un diag en prod (ya cumplido y borrado; los números quedan acá):

| | filas hoy | filas agregado | ratio |
|---|---|---|---|
| **AuM** (`portafolio.tenencia`) | 371.103 | 67.867 | **5,5×** |
| **Volumen** (`negocio_movimientos`) | 414.219 | 117.838 | **3,5×** |

Proyección del endpoint: ~535 ms → **~220 ms**. No baja más porque queda el piso
de ~68 ms de peaje de red (8 viajes) + ~60 ms de Python.

**Decisión: NO se hacen las dos mitades juntas.** La de AuM es netamente mejor
negocio y, sobre todo, mucho más simple:

| | AuM (`tenencia`) | Volumen (`negocio_movimientos`) |
|---|---|---|
| Ratio | 5,5× | 3,5× |
| La query que ahorra | la #1: 48.420 llamadas · 1.696 s | 40.703 · 1.589 s |
| Quién escribe | **un cron diario** (11:00 UTC L-V) | cron **cada 30'** |
| Cómo se invalida | **`portafolio.backfill_log` dice exactamente qué (fecha, cuenta) se escribió y cuándo** → invalidación EXACTA | heurística sobre `ingestado_en` + la trampa de `anulado_en` (§4) |

**FASE 1 — solo AuM.** Se recomputa después del backfill diario, leyendo de
`backfill_log` los pares tocados. Sin heurísticas, sin la trampa de la anulación,
con el mejor ratio y sobre la query más llamada del tablero. Es cuestión de un día,
no de varios.

**FASE 2 — volumen.** Se decide DESPUÉS, con la fase 1 medida en prod. Si la
mejora real se acerca a lo proyectado, se hace; si no, se descarta y no se pagó
la complejidad de la invalidación por día sucio.

### 5. Plan de implementación

1. **Tabla + writer**, sin leerla todavía. Job que recomputa por día sucio.
2. **Backfill** de días cerrados: scopeado, batcheado, fuera de rueda (REGLA #4).
3. **Verificación ANTES de leerla**: un diag a escribir junto con el agregado, que compare
   agregado vs live para N cuentas × N fechas y exige **diferencia 0.000000** — el
   mismo patrón que `diag_tesoreria_front_vs_back`, que ya se usó para validar el saldo
   final de Tesorería (48 filas, diferencia 0).
4. **Recién ahí**, cambiar la lectura a agregado+hoy.
5. Medir con `diag_costo_real` y comparar contra los números de §1.

### 6. Riesgos

- **Drift** (el agregado deja de coincidir con la realidad). Mitigado por §4 + el diag
  de comparación, que puede quedar como control periódico.
- **Filtro nuevo sobre el boleto** rompería el grano (§3). Documentado arriba.
- **Doble fuente de verdad**: la fórmula de volumen (con conversión por `mep`) tiene que
  vivir **una sola vez**. Si el writer y la lectura live la escriben por separado, van a
  divergir — es el mismo error que se corrigió sacando la fórmula del saldo final del
  frontend.
