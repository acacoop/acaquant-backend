# Vista BCRA (tab de Research) — integración 100% a las APIs del BCRA

> **DOC VIVO** de la tab BCRA de la vista Research (doc madre de la vista:
> `docs/VISTA_RESEARCH.md`). Misma regla: todo avance/decisión se asienta acá
> en el mismo commit. Estado: **CONSTRUIDO (2026-07-18) — pendiente del user:
> apply_schema + backfill + restart (ver Registro).**
>
> **Decisiones del user (2026-07-18, posteriores al análisis):** (a) la tab BCRA
> se organiza en **SUB-TABS por BLOQUE, cada una con su propia lógica** (no
> cuadrantes); (b) **CAMBIARIAS DESCARTADAS** (la canasta USD/EUR/BRL/CNY/XAU no
> interesa) — solo Monetarias v4; (c) eficiencia primero: universo curado,
> incremental, una request batch por bloque, nada de consumir al pedo.

---

## 1. Objetivo

Una **tab nueva "BCRA" dentro de `/research`** que integre las APIs públicas de
estadísticas del BCRA: el pulso monetario/cambiario oficial (reservas, base
monetaria, tasas, inflación, CER, depósitos, préstamos, multi-divisas) con
**series históricas graficables y comparables**, actualizándose sola. El mismo
espíritu del laboratorio 1816: **leer de la API → persistir en Postgres → la
vista sirve de la DB** (rápida, sin depender del BCRA en cada carga).

---

## 2. Las 3 APIs — qué son y qué VERIFICAMOS contra la API viva (2026-07-18)

Base: `https://api.bcra.gob.ar` — **públicas, sin autenticación, sin API key.**
La doc oficial (bcra.gob.ar/documentacion-apis) es una SPA que no renderiza sin
browser; lo de abajo se verificó **pegándole a la API real** (REGLA #2).

### 2.1 Estadísticas Monetarias v4 — LA PRINCIPAL ✅ verificada

- **Catálogo:** `GET /estadisticas/v4.0/monetarias` → **1.581 variables** (sí,
  mil quinientas ochenta y una). Cada una con: `idVariable`, `descripcion`,
  **`categoria`**, `tipoSerie`, `periodicidad`, `unidadExpresion`, `moneda`,
  `primerFechaInformada`, `ultFechaInformada`, `ultValorInformado`.
  Paginado: `metadata.resultset {count, offset, limit}` — limit máx 1000 →
  el catálogo entero sale en 2 páginas.
- **Serie:** `GET /estadisticas/v4.0/monetarias/{idVariable}` →
  `results: [{idVariable, detalle: [{fecha: "YYYY-MM-DD", valor: number}]}]`.
  Verificado con id 1 (Reservas): **7.515 puntos** históricos, último dato
  2026-07-15 (publica con rezago de ~1-2 días hábiles). Acepta `limit` (probado);
  `desde`/`hasta`/`offset` = patrón documentado de v3 — **verificar en Fase 0**.
- **Categorías detectadas:** "Principales Variables" (ids ~1-45: reservas, TC
  minorista/mayorista, BADLAR, TM20, tasas, agregados, inflación), grupos de la
  planilla "Series.xlsm" (factores de explicación de la base monetaria, M2, ids
  46-198), y préstamos/depósitos por titular (ids 199-318), entre otras.

### 2.2 Estadísticas Cambiarias v1 ✅ verificada (maestro)

- **Maestro de divisas:** `GET /estadisticascambiarias/v1.0/Maestros/Divisas` →
  **43 divisas** (USD, EUR, BRL, CNY, JPY, GBP, CLP, UYU… e incluye **XAU = oro
  onza** y divisas históricas DEM/ESP/ITL).
- **Cotizaciones (hipótesis, patrón documentado — verificar en Fase 0):**
  `GET /estadisticascambiarias/v1.0/Cotizaciones?fechaCotizacion=` (todas las
  divisas de un día) y `GET /Cotizaciones/{codMoneda}?fechaDesde&fechaHasta`
  (serie por divisa, con tipoPase y tipoCotizacion).

### 2.3 Principales Variables v3 — REDUNDANTE con v4 → NO se integra

v3 (`/estadisticas/v3.0/monetarias`) es la versión anterior del mismo servicio;
**v4 la contiene** (las "principales variables" son la categoría homónima del
catálogo v4). Decisión: **integrar SOLO v4 + cambiarias v1** — una versión menos
que mantener. (El día que v4 deprecie algo, se revisa.)

---

## 3. ⚠️ Lo que YA existe en el sistema (no duplicar, no romper)

`jobs/bcra.py` (cron 22 UTC L-V) **ya le pega al BCRA** y alimenta
`macro.series_macro` (claves DOLAR/CER/BADLAR/TAMAR…) — es el insumo de los
MOTORES (CER forward de breakevens, valuaciones). **Ese pipeline NO SE TOCA**:
es crítico y estrecho a propósito. La integración nueva es un feed **ancho y
separado** (schema `research.bcra_*`, jobs propios) para ANÁLISIS, no para
motores. Si algún día conviene unificar, se decide con los dos andando.

---

## 4. Qué construir — datos y gráficos, en detalle

### 4.1 El principio de diseño

**No se bajan 1.581 variables.** Se cura un **universo de ~35-40 series** (tabla
`research.bcra_watch`, editable — mismo patrón que el watch de 1816) elegidas por
valor de mesa, y la vista ofrece dos niveles:

- **Dashboards curados** (cuadrantes con lecturas armadas — lo que abrís todos
  los días).
- **Explorador de series** (elegís cualquier variable del watch y la graficás /
  comparás — el patrón Spread/Comparar del lab 1816, reutilizado).

### 4.2 Universo curado propuesto (el seed del watch)

| Bloque | Series (idVariable a confirmar en Fase 0 con el catálogo) |
|---|---|
| **Reservas** | Reservas internacionales (id 1) |
| **Tipo de cambio** | Mayorista A3500 (id 5) · Minorista (id 4) |
| **Tasas** | Política monetaria · BADLAR privados · TM20 · TAMAR · pases/REPO · plazo fijo minorista · adelantos |
| **Agregados** | Base monetaria · Circulante · M2 privado · M3 · factores de variación de la base (compras de divisas / Tesoro / pases — los que comenta 1816 a diario) |
| **Inflación** | IPC mensual · IPC interanual · inflación esperada (REM mediana) |
| **Indexación** | CER · UVA |
| **Depósitos** | Depósitos privados ARS (vista, plazo fijo) · **depósitos en USD del sector privado y del sector PÚBLICO** (los que el research de 1816 usa para inferir ventas de ANSES/provincias — cruce directo con los Reportes) |
| **Préstamos** | Préstamos privados ARS · préstamos en USD |
| **Cambiarias** | USD · EUR · BRL · CNY · **XAU (oro)** — canasta comparable |

### 4.3 Los gráficos (por cuadrante de la tab BCRA)

**Layout propuesto: 4 cuadrantes 50/50, como Argentina.**

1. **RESERVAS & FX** — serie de reservas (con Δ diario en barras) + overlay del
   A3500. La lectura que abre el día: ¿el Central compra o vende? *Cruce propio:
   línea del MEP/CCL nuestro sobre el A3500 = la BRECHA en el tiempo (dato que
   el BCRA no te da y nosotros sí tenemos).*
2. **TASAS** — multi-serie comparable (política monetaria, BADLAR, TAMAR, pase,
   plazo fijo) con el mismo header único de los otros charts. *Cruce propio:
   tasas reales = tasa − inflación esperada (REM), computado por código.*
3. **DINERO** — base monetaria y agregados, con toggle **nominal / real**
   (deflactado por CER — el nominal solo siempre "sube", el real es la señal) y
   los **factores de explicación** de la base (el "por qué" oficial del que
   habla 1816: compras en el MLC, Tesoro, pases).
4. **EXPLORADOR** — el genérico: elegís serie(s) del watch, rango, overlay o
   spread A−B con percentil/z **reutilizando el motor del lab 1816** (misma
   mecánica de valor relativo, otra fuente).

**Segunda tanda (cuando la base esté poblada):** panel de DEPÓSITOS USD
(privados vs públicos — el radar de "¿quién le vendió al Central?"), canasta de
divisas + oro, y el cruce inflación: IPC efectivo vs REM vs breakevens nuestros
(tres curvas, un gráfico — nadie de la mesa tiene eso hoy).

### 4.4 Por qué esto es valioso (y no otra tabla de números)

- Las series del BCRA son **el contexto oficial** de todo lo que la mesa opera;
  hoy se miran en la web del BCRA o en Excel ajeno.
- Los **cruces con datos propios** (brecha A3500-MEP, tasas reales, IPC vs REM
  vs breakevens, depósitos USD vs los comentarios de 1816) son imposibles fuera
  de nuestra plataforma — ahí está el diferencial, no en re-mostrar la serie.

---

## 5. Arquitectura — cómo se actualiza solo "24/7"

**La verdad del dato primero:** el BCRA publica **una vez por día hábil, con
rezago de 1-2 días** (verificado: Reservas al 15-jul un 18-jul). "Actualizado
24/7" real = **la vista sirve SIEMPRE desde nuestra DB** (cero dependencia del
BCRA al navegar, 24/7 de verdad) + **un cron que sincroniza varias veces al día**
para capturar la publicación apenas sale, sin martillar la API.

### 5.1 Piezas

- **`core/bcra_api.py`** — cliente (patrón `mercado_1816`): throttle suave +
  retry/backoff, paginación del catálogo, parseo de series. Sin key. Contrato
  "nunca levanta". *(Nota: WebFetch entró hoy con TLS OK — el viejo problema de
  certificado del BCRA parece resuelto; el diag de Fase 0 lo confirma desde el
  Droplet.)*
- **Tablas (`sql/schema.sql`):**
  - `research.bcra_variables` — espejo del catálogo (las 1.581 filas de
    metadata: es chico y sirve para explorar/curar). Refresh diario.
  - `research.bcra_watch` — el universo curado (seed §4.2, editable).
  - `research.bcra_series (id_variable, fecha, valor, PK(id_variable, fecha))`
    — los puntos, solo del watch. Tidy, idempotente.
  - `research.bcra_cambiarias (cod_moneda, fecha, tipo_pase, tipo_cotizacion,
    PK(cod_moneda, fecha))` — divisas del watch cambiario.
- **`jobs/bcra_research.py`** — el sincronizador:
  - `--backfill`: historia completa de cada serie del watch (una vez; las series
    del BCRA son livianas — miles de puntos por variable, no millones).
  - default (cron): **incremental por watermark** (`desde = max(fecha) − 7d` por
    variable → captura revisiones retroactivas del BCRA, que existen). Un run
    sin datos nuevos = ~40 requests baratos y 0 escrituras.
  - `JobRunLogger` (estándar de la casa) — si el BCRA cambia algo o se cae,
    se entera el triage.
- **Cron (`deploy/crontab.txt`):** `0 12,16,20,23 * * 1-6` UTC (≈ 9/13/17/20
  ART, lunes a sábado) — 4 pasadas diarias sobre una publicación que ocurre una
  vez: apenas el BCRA publica, a lo sumo horas después está en la vista. Domingo
  no corre (no hay publicación); la vista sigue sirviendo de la DB.
- **API interna:** endpoints bajo `/api/research1816`… no — prefijo propio
  **`/api/research-bcra`** (mismo gate `research`, entrada en proxy.ts +
  **route handler de Next** — el gotcha aprendido). Lectura SQL pura
  (`api/services/research_bcra_sql.py`), reusa la mecánica de
  series/spread/percentil del lab.

### 5.2 Costos y límites

Sin créditos ni key: el costo es cortesía de uso. Con el diseño incremental son
**~40-45 requests × 4 corridas/día ≈ 180 requests diarios** — irrelevante para
una API pública nacional. El backfill inicial (~40 series completas) se corre
una vez, throttled, fuera de horario pico.

---

## 6. Fases

- **Fase 0 — Verificación (1 diag, sin infra):** relevar el catálogo
  completo → confirmar `desde/hasta/offset` en series v4, shape de
  `Cotizaciones/{moneda}`, ids exactos del seed §4.2, y TLS desde el Droplet.
  Con esa salida se congela el watch inicial CON el user.
- **Fase 1 — Ingesta:** cliente + tablas + `bcra_research.py` (backfill + cron).
  Entregable: las ~40 series en la DB actualizándose solas.
- **Fase 2 — La tab BCRA:** 4 cuadrantes (§4.3) reutilizando el diseño del lab
  (header único + chart). Entregable: la vista viva.
- **Fase 3 — Cruces:** brecha A3500-MEP, tasas reales, IPC vs REM vs breakevens,
  depósitos USD públicos/privados.
- **Fase 4 — Copiloto:** vista `bcra` del copiloto (registro + reglas de dominio
  — qué es cada serie, qué NO inferir) — encaja directo en el patrón por vista.

## 7. Decisiones tomadas / pendientes

- ✅ Solo v4 + cambiarias v1 (v3 redundante). Feed separado de `jobs/bcra.py`
  (motores) — no se toca lo existente. Universo CURADO, jamás las 1.581.
  Prefijo API propio + route handler de Next desde el día 1.
- ⏳ **Del user:** validar el seed del watch (§4.2) y el layout (§4.3) antes de
  la Fase 2; prioridad de esta tab vs el resto del roadmap Research.

## Registro (con fecha)

- **2026-07-18 (3) — backfill OK en prod + `--desde`/`--purgar-antes`.**
  Backfill real: **23/23 series, 121.818 puntos** (catálogo 1.581 refrescado).
  Aclaración asentada: el catálogo (1.581) es SOLO metadata (~200 KB, el "menú"
  para curar); los datos son las 23 series (~6 MB — no es problema real).
  Feedback del user ("el TODO es al pedo, dejemos un desde") → el job ganó:
  `--backfill --desde YYYY-MM-DD` (historia acotada) y `--purgar-antes
  YYYY-MM-DD` (limpieza one-off de lo ya bajado, con dry-run). La limpieza quedó
  como SECUNDARIA a decisión del user — cuando quiera:
  `python -m jobs.bcra_research --purgar-antes 2020-01-01 --dry-run` → sin
  `--dry-run` para ejecutarla. El incremental del cron no re-baja lo purgado
  (watermark = max(fecha), que queda intacta).
  - **IDs VERIFICADOS contra el catálogo vivo** (2º fetch): 1 reservas · 4/5 TC
    minorista/mayorista · 7 BADLAR · 8 TM20 · 11 BAIBAR · 12 PF 30d · 13
    adelantos · 14 personales · 15/16/17 base/circulación/billetes · 26 préstamos
    priv. · 21/24 depósitos ARS · 27/28 IPC m/i.a. · 29 REM 12m · 30 CER · 31
    UVA · **103/104 depósitos USD púb.+priv./priv.** (el bloque estrella del
    research 1816). Nota: 35 y 45 duplican BADLAR/TAMAR en el catálogo — se usan
    7 y 44; el diag del Droplet puede refinar.
  - **Backend:** `core/bcra_api.py` (throttle 1,5s + backoff; catálogo paginado;
    `serie()` con desde/hasta MÁS re-filtro client-side defensivo — el
    comportamiento exacto de esos params se confirma con la primera corrida
    real) · tablas `research.bcra_{variables,watch,series}` (watch con columna
    **`bloque`** = la sub-tab; seed de 23 series en `jobs/bcra_research.py`) ·
    job con `--dry-run`/`--backfill`/incremental (watermark −7d, captura
    revisiones del BCRA) + refresh del catálogo 1×/corrida + JobRunLogger ·
    endpoints `GET /api/research-bcra/{bloques,series}` (gate `research`;
    `series` es batch: 1 request por bloque) · cron `0 12,16,20,23 * * 1-6` ·
    SISTEMA.md regenerado · cron registrado en el test del Diagnóstico.
  - **Frontend:** `research-bcra.tsx` — tab **BCRA** en Research (ARGENTINA ·
    BCRA · RV INTERNACIONAL), con **sub-tabs por bloque** (RESERVAS · TIPO DE
    CAMBIO · TASAS · DINERO · INFLACIÓN · CER & UVA · DEPÓSITOS), cada una:
    header único (chips por serie con toggle + valor de HOY inline + rango
    3M/6M/1A/5A) + chart multi-serie con el diseño existente. Lazy por sub-tab
    (keep-alive), formato por unidad (%, M ARS/M USD compactos). Route handler
    Next `research-bcra/[...path]` + gating en proxy.ts (el gotcha, cubierto de
    entrada).
  - **PENDIENTE del user (Droplet):** `git pull` → `python -m scripts.apply_schema`
    (crea las tablas) → `python -m jobs.bcra_research --dry-run` (ver el plan) →
    `--backfill` (historia completa, una vez) → `systemctl restart api.service`.
    El cron después mantiene solo. La 1ª corrida confirma el comportamiento de
    `desde/hasta` (si la API los ignorara, el filtro client-side protege pero el
    incremental bajaría series enteras → se revisa el log de puntos y se ajusta).
  - **Fuera del alcance ahora:** cambiarias (descartadas), cruces Fase 3 (brecha
    A3500-MEP, tasas reales, IPC vs REM vs breakevens) y copiloto Fase 4.

- **2026-07-18 — Análisis exhaustivo + verificación contra la API viva.** Catálogo
  v4 (1.581 variables, shape completo), serie id 1 (7.515 puntos, rezago 1-2 días
  hábiles), maestro cambiario (43 divisas, incl. XAU). Diseño de la tab, universo
  curado, arquitectura de sync y fases asentados.
