# Segmentación Patrimonial de Clientes

> Documento vivo del feature. El **LOG DE AVANCES** (al final) es append-only
> con fecha. Cada vez que toquemos algo de esta feature, agregar una entrada.
>
> Sub-feature de **[TABLERO_COMERCIAL.md](TABLERO_COMERCIAL.md)** — esto es
> "Segmentación de clientes" (capa [3] en la cadena de dependencias del tablero),
> pero con un criterio **patrimonial objetivo** (límite de fondeo) en vez de
> categórico-manual.

## Objetivo

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

## Reglas de clasificación (6 segmentos)

El criterio depende de si la cuenta es **Persona Humana (PH)** o **Persona
Jurídica (PJ)** y del **límite disponible para fondear** convertido a la
moneda/unidad correspondiente.

### Personas Humanas — umbral en **USD**

| Segmento              | Límite disponible (USD) |
|-----------------------|-------------------------|
| `PH_RETAIL`           | < 50.000                |
| `PH_MEDIO_RETAIL`     | ≥ 50.000 y ≤ 100.000    |
| `PH_ALTO_PATRIMONIO`  | > 100.000               |

### Personas Jurídicas — umbral en **UVAs**

| Segmento       | Límite disponible (UVAs) |
|----------------|--------------------------|
| `PJ_PEQUENA`   | ≤ 350.000                |
| `PJ_MEDIANA`   | > 350.000 y ≤ 700.000    |
| `PJ_GRANDE`    | > 700.000                |

> **Convención de bordes** (para que no haya ambigüedad en el código):
> los valores exactos del límite superior caen en el segmento inferior
> (intervalos `(−∞, A]`, `(A, B]`, `(B, +∞)` para PJ; `[0, 50k)`, `[50k, 100k]`,
> `(100k, +∞)` para PH — leído tal cual del texto del usuario).

### Distinguir PH vs PJ

Source of truth: campo **`tipo_cliente`** de `Comitentes`. Mapping confirmado
contra los valores reales (ver `scripts/diag_tipo_cliente.py`, corrida
2026-05-28 sobre 1773 cuentas):

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

## Conversiones de moneda/unidad

El **input siempre viene en ARS** (es lo que reporta el custodio en el Excel).
Para clasificar PH se convierte a USD; para clasificar PJ se convierte a UVAs.

### ARS → USD (para PH)

**Decisión recomendada (a confirmar)**: usar **MEP** del momento del cálculo
(no del momento de la carga). Justificación: el "patrimonio del cliente" en
términos relevantes para la mesa es lo que puede dolarizarse en el mercado,
no el oficial. Fuente live: `get_ultimo_mep` (TTL 5s), igual que el resto de
la API.

Alternativas si se descarta MEP: dólar oficial mayorista (BCRA A3500) desde
`Trading.DOLAR` (fixing diario) o desde `Valuaciones.DolarOficialLive` (feed
MAE intradiario).

### ARS → UVAs (para PJ)

**Pendiente de implementar**: en el repo no hay serie UVA todavía. Hay que
sumarla a `jobs/bcra.py` (es una serie estadística pública igual que CER /
Riesgo País). Decisión abierta sobre dónde persistirla — probablemente nueva
colección `Trading.UVA` (timeseries diaria, mismo shape que `Trading.DOLAR`).

Una vez ingestada: el motor toma el último valor UVA publicado al momento
del cálculo y hace `limite_uvas = limite_ars / uva`.

## Modelo de datos

Todo vive en **`Clientes.Comitentes`** (la colección ya existente) como
**subdocumentos atómicos** — no se crea colección nueva en esta fase.

```jsonc
{
  // ... campos existentes (id_cuenta, denominacion, operador, nivel_1..5, etc.) ...

  // ── Input de la carga masiva (Excel) ────────────────────────────────
  "limite_fondeo": {
    "disponible_ars": 12345678.90,
    "utilizado_ars":   2345678.90,
    "utilizacion_pct": 19.01,             // derivado, persistido para indexar
    "cargado_en":  "2026-05-28T13:00:00Z",
    "fuente":      "excel:carga_2026-05"  // nombre del archivo o etiqueta
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

**Histórico (FASE 2, fuera de scope inicial)**: una colección
`Clientes.LimitesFondeoHistorico` append-only (un doc por carga) habilita
métricas temporales (evolución del segmento del cliente mes a mes) sin migrar
el modelo actual. No se construye ahora; se deja la puerta abierta.

## Componentes a construir

### 1. Carga desde Manager — endpoint bulk + tab "Fondeos"

**No es un script suelto, es parte del manager** (mismo patrón que la carga
de segmentación de Clientes hoy). Espejo de `POST /api/manager/clientes/bulk`
(`api/routers/manager/clientes.py:177`) que ya implementa exactamente la
semántica que pidió el usuario: itera fila por fila, solo setea los campos
no vacíos, **no toca las cuentas ausentes del payload**, re-subir reemplaza
vía `$set` (idempotente).

**Backend** — dos opciones (TBD):

- **(a) Extender `/clientes/bulk` existente** para que reconozca dos columnas
  más (`limite_fondeo_disponible_ars`, `limite_fondeo_utilizado_ars`). Si
  vienen, las escribe en el subdoc via dot-notation
  (`"limite_fondeo.disponible_ars": ...`) + computa `utilizacion_pct` +
  setea `cargado_en` y `fuente`. Pro: un solo endpoint, un solo grid.
- **(b) Endpoint hermano `POST /api/manager/clientes/bulk-fondeo`** dedicado.
  Pro: separación clara, validación numérica propia, libra de complicar el
  bulk de segmentación. **Recomendado** — el subdoc + los cómputos derivados
  justifican un endpoint propio.

En ambos casos, contrato **idéntico al bulk existente**:
- Recibe `rows: list[dict]` parseadas en el frontend desde CSV/XLSX.
- Filas sin `id_cuenta` → contadas en `sin_id`, no escriben.
- Filas con `id_cuenta` pero sin ninguna columna de límite → `sin_campos`.
- `update_one` por `id_cuenta` (NO upsert; no se crean cuentas — vienen del
  sync diario).
- Devuelve `{actualizadas, matched, filas_validas, sin_id, sin_campos,
  n_no_encontradas, no_encontradas[:50]}`.
- Stamp de `actualizado_por` (email del actor) + `actualizado_at`.

**Frontend** (`acaquant-web/`) — tab nueva **"Fondeos"** dentro de
`/manager → CLIENTES` (sub-tabs internas, junto al editor de segmentación
que ya está). Misma UX que el bulk de segmentación:
- Grid con 3 columnas: `id_cuenta`, `limite_disponible`, `limite_utilizado`.
- Botón "Cargar archivo" (CSV/XLSX) → parsea en cliente, muestra preview
  de N filas, valida tipos, envía al endpoint.
- Soporte de **paste** desde Excel directo a la grilla (el patrón actual ya
  lo soporta) para cargar 5-10 filas a mano sin armar archivo.
- Tras el POST: toast con `{actualizadas, no_encontradas}` y refresh.

**Bulk script como fallback** (`scripts/cargar_limites_fondeo.py`) — queda
disponible solo para una **carga inicial masiva** de un archivo histórico
muy grande, o para batch ad-hoc desde el Droplet. **No es el flujo
principal**. Internamente reusa el mismo helper que el endpoint para que la
lógica viva en un solo lugar (en `api/services/`, no duplicada).

### 2. Motor de segmentación — `jobs/segmentar_patrimonial.py`

- Toma todas las `Comitentes` con `limite_fondeo.disponible_ars` poblado.
- Para cada cuenta: detecta PH/PJ, obtiene TC (MEP) o UVA según corresponda,
  convierte el límite, aplica las reglas → escribe `segmento_patrimonial` +
  `segmento_patrimonial_calc`.
- Idempotente. Corre standalone o encadenado.
- Cron: a definir (probablemente mensual, post-carga). Si UVA o MEP cambian
  mucho podría correr semanalmente — TBD.

### 3. Vista en `/comercial`

- Servicio: `api/services/comercial.py` → agregaciones por `segmento_patrimonial`
  (counts + AuM agregado por banda + % utilización promedio), filtrable por
  operador.
- Endpoint: `api/routers/manager/comercial.py` (manager-only, sigue el patrón
  existente — ver TABLERO_COMERCIAL).
- Frontend: card/tab nueva dentro del tablero comercial en `acaquant-web/`
  (cross-repo, ver feedback `acaquant_web_companion`).

### 4. Diagnóstico — `scripts/diag_segmentacion_patrimonial.py`

Read-only. Imprime la distribución actual por segmento, qué cuentas tienen
`limite_fondeo` pero no `segmento_patrimonial` (debería estar vacío post-motor),
qué cuentas con AuM > 0 no tienen límite cargado (gap del Excel), etc.

## Plan de implementación (orden sugerido)

1. **Fase 1 — Backend de carga (endpoint manager + protección del subdoc).**
   - `POST /api/manager/clientes/bulk-fondeo` en
     `api/routers/manager/clientes.py` (o ampliación del bulk existente
     según decisión).
   - Service en `api/services/` con la lógica del upsert (reusable desde el
     script fallback).
   - Agregar `limite_fondeo` y `segmento_patrimonial*` a `MANUAL_FIELDS` en
     `jobs/sync_comitentes.py` para que el sync diario NO los pise.
   - Validar imports antes de pushear (REGLA #1 en `api/CLAUDE.md`).
2. **Fase 2 — Tab "Fondeos" en `/manager → CLIENTES` (`acaquant-web`).**
   - Sub-tab nueva con grid de 3 columnas + paste + upload CSV/XLSX.
   - Reusar el componente de bulk de segmentación (mismo patrón).
3. **Fase 3 — Ingesta UVA al repo** (bloqueante para clasificar PJ, pero NO
   para cargar los límites).
   - Sumar serie UVA a `jobs/bcra.py` o crear `jobs/uva.py`.
   - Persistir en `Trading.UVA` (TBD shape exacto).
4. **Fase 4 — Motor de segmentación**.
   - `jobs/segmentar_patrimonial.py`. Idempotente.
   - Cron en `deploy/crontab.txt` + regenerar `deploy/SISTEMA.md`
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

## Decisiones tomadas

- **Subdoc en `Clientes.Comitentes`** (no colección nueva en esta fase).
- **`segmento_patrimonial` convive con `nivel_3` manual** (no lo pisa).
- **`PH_RETAIL` / `PH_MEDIO_RETAIL` / `PH_ALTO_PATRIMONIO` / `PJ_PEQUENA` / `PJ_MEDIANA` / `PJ_GRANDE`** como enum de string (snake_case mayúscula, sin acentos para evitar bugs en queries).
- **Inputs en ARS, segmentación en USD (PH) o UVAs (PJ)**.
- **Carga principal desde Manager → CLIENTES → Fondeos** (endpoint bulk +
  UI tab nueva en `acaquant-web`), espejo del bulk de segmentación que ya
  existe. Garantiza la semántica pedida: cargar 10 filas toca solo esas 10,
  re-subir reemplaza, las 990 restantes intactas. Script CLI queda como
  fallback opcional para bulk inicial.
- **`limite_fondeo` y `segmento_patrimonial*` van a `MANUAL_FIELDS`** en
  `jobs/sync_comitentes.py` → el sync diario de Aunesa NO los pisa.
- **Sin histórico en el MVP**; modelo deja la puerta abierta para fase 2.

## Decisiones abiertas

- [ ] **Endpoint dedicado vs extender el bulk existente**: opción (a)
      extender `POST /api/manager/clientes/bulk` con dos columnas más, o (b)
      crear `POST /api/manager/clientes/bulk-fondeo` (recomendado por
      separación de validación numérica y subdoc). (Bloquea Fase 1.)
- [ ] **UX de la tab**: ¿sub-tab "Fondeos" dentro del editor existente de
      CLIENTES, o tab nueva al mismo nivel "FONDEOS"? Recomendado: sub-tab
      interna, mismo URL, menos ruido en el nav. (Bloquea Fase 2.)
- [ ] **TC a usar para PH**: MEP (recomendado) vs oficial A3500 vs mayorista
      MAE. (Bloquea Fase 4.)
- [ ] **Dónde persistir UVA**: nueva `Trading.UVA` (recomendado) vs sumarla
      a otra colección existente. Cron de ingesta (probablemente 12 UTC L-V,
      como `argentina_datos`). (Bloquea Fase 4 para PJ.)
- [ ] **Periodicidad del motor**: post-carga + cron semanal vs mensual.
- [ ] **Estado inicial post-carga**: ¿qué hacemos con cuentas Activas sin
      límite cargado? Opciones: `segmento_patrimonial = null`, o
      `"SIN_DATOS"` explícito.

## Estado / TODO

- [x] Diseño documentado.
- [ ] Confirmar decisiones abiertas con la mesa.
- [x] Fase 1 — Backend de carga (endpoint manager + `MANUAL_SUBDOCS`).
- [ ] Fase 2 — Tab "Fondeos" en `/manager → CLIENTES` (acaquant-web).
- [ ] Fase 3 — Ingesta UVA (`Trading.UVA`).
- [ ] Fase 4 — Motor de segmentación.
- [ ] Fase 5 — Vista de segmentación en `/comercial`.
- [ ] Fase 6 — Histórico (opcional).
- [ ] Fase 7 — Script CLI fallback (opcional).

---

## LOG DE AVANCES

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
- **2026-05-28** — **Mapping PH/PJ confirmado contra datos reales.** Diag
  `scripts/diag_tipo_cliente.py` corrido sobre 1773 cuentas reveló 7 valores
  de `tipo_cliente` + 53 nulls. Mapping fijado: PH = `{Persona, Empleado}`,
  PJ = `{Empresa, Fondo Común de Inversión, Compañía de seguros, Fideicomiso,
  Institucional}`, null = sin clasificar. Se cerró la decisión abierta de
  cómo distinguir PH/PJ — no hace falta fallback al CUIT. Tabla con counts
  documentada en "Distinguir PH vs PJ".
