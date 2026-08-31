# RESEARCH — la vista `/research` completa

> **Un doc por vista.** Consolidó a `VISTA_RESEARCH.md` (77 kB), `RESEARCH_FRED.md`
> (44 kB) y `RESEARCH_BCRA.md` (15 kB) el **2026-08-31**: los tres describían tabs
> de la MISMA pantalla y los tres arrastraban lo mismo — un roadmap por fases ya
> construido, una lista de credenciales ya cargadas, preguntas abiertas ya
> decididas, y un **registro de construcción** que era el 40% del texto. Nada de
> eso se lee para trabajar; git lo tiene.
>
> Lo que quedó: **cómo funciona hoy cada tab, con qué la alimenta y qué NO hay que
> re-litigar.** Doc `[VIVO]` — si tocás una tab, un filtro o un job de research,
> actualizá su sección en el mismo commit.

## 0. Las SEIS tabs — verificado contra `src/components/research-view.tsx`

| Tab | Componente | De dónde saca el dato |
|---|---|---|
| **RENTA FIJA ARGENTINA** | `research-lab.tsx` · `research-forwards.tsx` · `research-retorno-total.tsx` | `/api/research1816/{series,spread,universo}` · `/api/cotizaciones/historico/forwards` · `/api/analitica/retorno-total` |
| **ANÁLISIS SENSIBILIDAD** | (el de la difunta vista ESTRATEGIA) | `/api/analitica/sensibilidad-retorno` |
| **REPORTES FINANCIEROS** | `research-documentos.tsx` | `/api/research1816/mails` · `/api/research-docs/*` |
| **BCRA** | `research-bcra.tsx` | `/api/research-bcra/{bloques,series}` |
| **DATOS INTERNACIONALES** | `research-fred.tsx` | `/api/research-fred/{bloques,series}` |
| **RENTA VARIABLE INTERNACIONAL** | (vista `reuters`) | ver `docs/INTEGRACION_REUTERS.md` |

⚠️ **ANÁLISIS SENSIBILIDAD llegó el 2026-08-30** desde `/retorno` (vista MERCADO →
ESTRATEGIA, eliminada): mismo componente y **mismo endpoint**, sin cambio de RBAC.

⚠️ **NO hay copiloto.** Se dio de baja el 2026-08-19 con todos los copilotos por
vista. Cualquier mención a «el copiloto de Research» en el texto de abajo es
histórica — quedó porque explica por qué una decisión de diseño es como es.

---

# PARTE A — Tabs de 1816 (RENTA FIJA ARGENTINA + REPORTES FINANCIEROS)

### 1. Objetivo — qué es la vista Research

Una **vista principal nueva** (`/research`, módulo RBAC `research`) que concentra
en un solo lugar todo el research de mercado de **1816 | Economía y Estrategia**.
Tiene **dos pilares**:

- **A) MARKET DATA (API de 1816).** Datos duros de renta fija soberana/corporativa
  argentina: curvas, instrumentos, indicadores a precio de mercado y **series
  históricas** (precio dirty/clean, paridad, TNA/TEA/TEM, duration, current yield,
  spread, valor técnico, volumen). **Foco declarado del user: las SERIES
  HISTÓRICAS.** Es lo que más va a mirar.
- **B) RESEARCH ESCRITO (mails + reportes).** El research narrativo que 1816 manda
  por mail: el diario **"El día en pocas líneas"** (research@1816.com.ar, todas las
  mañanas) y los **reportes mensuales**. El user se los reenvía a su casilla
  corporativa. Se ingestan solos, se destilan y se muestran acá — texto navegable
  y buscable.

La vista cruza los dos mundos: el **número** (series/indicadores) al lado de la
**narrativa** (qué decía el research ese día). Es una de las piezas de mayor valor
del producto — la mesa lee a 1816 todas las mañanas.

---

### 2. Decisiones tomadas (no re-litigar sin el user)

1. **Todo va a Postgres/Supabase, NUNCA Mongo.** El doc funcional de 1816 dice
   "persistir en MongoDB" — eso está **decomisado** en este sistema (CLAUDE.md).
   Series, snapshots y catálogo van a tablas SQL nuevas (schema `research`, ya
   existe para Refinitiv → se le suman tablas `research.mkt_1816_*`).
2. **ABIERTA al invitado desde 2026-07-21** (decisión user, SUPERADA la
   restricción original): los "invitados" del portal www son OTRO SECTOR de la
   MISMA empresa (grupo ACA), no terceros → el research licenciado (1816/
   Reuters) no sale de la compañía. `research` está en `INVITADO_MODULES`.
   Para la mesa: default admin/trader/sales; el admin lo asigna en la matriz.
3. **Los créditos son el recurso escaso — se administran como el presupuesto de
   IA.** La API cobra por request (`x-1816-credits`); series cuesta
   `tickers × campos × días`. La estrategia es **leer una vez, persistir, servir
   desde la DB** (la vista lee SQL, 0 créditos por carga). Ver §5.2.
4. **La ingesta de mails NO se reescribe: se reusa P6.** `jobs/research_mail.py`
   + tabla `ia.research` ya están construidos y probados (idempotentes, con
   full-text español). La vista los consume. Ver §6.
5. **La API es la fuente de la verdad de los números; nada se recalcula a mano.**
   TNA/TEA/duration/paridad salen de 1816 tal cual (tienen su motor de cálculo con
   convenciones por curva). Nuestro `mercado.curvas` (renta fija propia) es OTRA
   cosa y no se mezcla — 1816 es un feed independiente, más ancho (provinciales,
   corporativos, BCRA) y con histórico servido.
6. **Degrada con gracia.** Sin API key / sin créditos (HTTP 402) / 429 rate limit
   → el pilar A muestra lo último persistido y avisa; el pilar B es 100%
   independiente (mails) y sigue andando. Ninguna falla rompe la vista.
7. **La IA NO interviene por defecto — solo on-demand (decisión del user
   2026-07-17).** LO PRINCIPAL es que los textos del research aparezcan BIEN en
   acaquant (el crudo, limpio y legible). NADA de gasto automático de tokens: el
   destilado IA al ingestar se APAGÓ (2026-07-17) y se BORRÓ (2026-08-28): nunca
   corrió —el flag no estaba en el cron— y ninguna pantalla lo dibujaba. **Hoy
   esta vista no tiene una sola línea de IA.**

---

### 3. Los dos pilares de un vistazo

```
                            VISTA  RESEARCH  (/research, módulo `research`)
        ┌─────────────────────────────────────────┬──────────────────────────────────────┐
        │  A) MARKET DATA — API 1816                │  B) RESEARCH ESCRITO — mails 1816     │
        │                                           │                                       │
        │  curvas · instrumentos · indicadores      │  "El día en pocas líneas" (diario)    │
        │  SERIES HISTÓRICAS ★ · cálculo teórico     │  + reportes mensuales                 │
        │                                           │                                       │
        │  core/mercado_1816.py (cliente + token)   │  jobs/research_mail.py  (YA existe P6)│
        │  jobs/mercado_1816_series.py (diario)     │  → ia.research (crudo + FTS)          │
        │  → research.mkt_1816_series (SQL)          │                                       │
        │  la vista lee SQL (0 créditos)            │  la vista lee SQL + FTS               │
        └─────────────────────────────────────────┴──────────────────────────────────────┘
```

---

### 4. PILAR A — Market Data (API 1816)

#### A.4.1 Autenticación

- **Flujo:** `POST /v1/auth/token` con `{apiKey, module:"mercado"}` → `{token,
  expiresIn:86400, module}`. El token (JWT) va como `Authorization: Bearer <token>`
  en TODO lo demás. Expira a las 24 h.
- **Cliente:** `core/mercado_1816.py` cachea el token en memoria del proceso y lo
  **renueva solo** ante `401` (patrón: 1 reintento tras re-auth). Nunca guarda el
  token en DB (efímero, por proceso — mismo criterio que otros feeds).
- **Env vars (van al `.env` del Droplet — REGLA #6, ver `docs/SECRETS.md`):**
  - `MERCADO_1816_API_KEY` — la API Key (se genera en la webapp de 1816).
  - `MERCADO_1816_BASE_URL` — opcional, default la base productiva de 1816.
  - Sin la key, el cliente queda apagado (el pilar A muestra lo persistido; nunca
    levanta excepción — contrato "degrada con gracia").

#### A.4.2 Créditos — el recurso escaso (diseño alrededor de esto)

- **Límites (del plan):** `100.000/día` · `3.100.000/mes`. `GET /v1/creditos/balance`
  → `{daily:{used,limit}, monthly:{used,limit}}`. Créditos insuficientes = **HTTP
  402**; rate limit = **429**.
- **Fórmula de consumo por endpoint:**
  | Endpoint | Costo |
  |---|---|
  | `/v1/mercado/curvas` | 1 |
  | `/v1/mercado/instrumentos` | 1 |
  | `/v1/mercado/indicadores` (batch, ≤50 tickers) | `tickers × campos` |
  | `/v1/mercado/indicadores/{ticker}` (teórico) | `campos` |
  | `/v1/mercado/series` (≤10 tickers, ≤1 año) | `tickers × campos × días` ★ |
  | `/v1/mercado/cashflow/{ticker}` | `cupones` (1 por cupón devuelto) |
  | `/v1/creditos/balance` | 0 (control) |
- **Presupuesto en el cliente (igual que `core/ai`):** antes de un pull grande
  (backfill de series) el cliente consulta el balance y **corta si el costo
  estimado supera el margen** — kill switch. Se registra cada request y su costo en
  una tabla de auditoría (`research.mkt_1816_creditos_log`) → panel de
  Observabilidad, mismo espíritu que `ia.trazas`.
- **La cuenta real (estimada — HIPÓTESIS sin correr, verificar con el balance):**
  - Universo inicial ~13 tickers (§4.5) × ~10 campos.
  - **Backfill 1 año de series:** `13 × 10 × 365 ≈ 47.500` créditos, **una sola
    vez**. Entra holgado en el límite diario (100k). Se hace scopeado + batcheado
    (REGLA #4): series topea en 10 tickers/call → 2 lotes, con throttle.
  - **Incremental diario** (append del día nuevo): `13 × 10 × 1 = 130` créditos/día
    → ~2.700/mes. Trivial vs 3,1M.
  - **Snapshot intradía** (opcional, `/indicadores` cada 5 min): `13 × 10 = 130`
    por snapshot × ~96 snapshots/rueda ≈ `12.500`/día → ~250k/mes. Feasible pero
    NO es la prioridad (el user quiere series) → diferido, se enciende si se pide.
  - **Conclusión:** con la estrategia "leer una vez + incremental + servir de la
    DB", el consumo de créditos es cómodo. El riesgo es un backfill a lo bruto sin
    scopear → por eso la disciplina REGLA #4.

#### A.4.3 Endpoints mapeados

| # | Endpoint | Para qué en la vista | Persistencia |
|---|---|---|---|
| 1 | `POST /v1/auth/token` | token (interno del cliente) | — |
| 2 | `GET /v1/creditos/balance` | control de consumo + panel | log |
| 3 | `GET /v1/mercado/curvas?texto=` | catálogo de curvas (33 IDs: soberanos, provinciales, corporativos, BCRA) | `research.mkt_1816_curvas` (refresco semanal, 1 créd) |
| 4 | `GET /v1/mercado/instrumentos?texto=&curvaId=` | catálogo de tickers + ISIN + vencimientos; validar ticker antes de pedir series | `research.mkt_1816_instrumentos` (refresco diario) |
| 5 | `GET /v1/mercado/indicadores` (batch ≤50) | **snapshot** del día (precio/tna/tea/duration/…) por ticker | `research.mkt_1816_snapshot` |
| 6 | `GET /v1/mercado/indicadores/{ticker}` | **calculadora teórica** on-demand (mandás UNO de precio/tna/tea/paridad/… y te devuelve el resto) | no se persiste (on-the-fly) |
| 7 | `GET /v1/mercado/series` (≤10 tickers, ≤1 año) | **★ SERIES HISTÓRICAS** — el corazón de la vista | `research.mkt_1816_series` |
| 8 | `GET /v1/mercado/cashflow/{ticker}` | **cupones del instrumento** (fecha teórica/efectiva, amortización, interés, total) — endpoint NUEVO, en evaluación | sin decidir (ver §4.9) |

**Parámetros transversales** (indicadores/series): `fuente` (byma/mae/homo-1816,
default byma), `plazo` (0/1/2, default 1), `moneda` (ars/mep/ccl, default ars),
`convencionTna` (default la de la curva), `fechaOperacion`/`fechaInicial`/`fechaFinal`.
**Decidir y fijar** una combinación default por vista (probable: `fuente=byma`,
`plazo=1`, `moneda=ars` para pesos y `mep`/`ccl` para HD) y exponer los toggles
en la UI. Estos parámetros cambian el número → se guardan junto al dato.

#### A.4.4 Campos (glosario para la UI + el copiloto futuro)

`precioDirty` (precio con intereses corridos) · `precioClean` (sin corridos) ·
`paridad` (precio/valor técnico, decimal 0-1) · `tna`/`tea`/`tem` (tasas, decimal:
0.0925 = 9,25%) · `duration` (Macaulay, años) · `durationMod` (modificada,
sensibilidad a tasa) · `currentYield` · `spread` (margen TNA, decimal) ·
`valorTecnico` · `volumenMontoDiario`/`volumenNominalDiario` · `ultimaOperacion`
(timestamp) · `convencionTna` · `fuente`/`moneda`/`plazo`. **OJO escalas:** tasas y
paridad vienen en **fracción** (0.0925), no en %. Al persistir se guarda tal cual y
la UI/el copiloto formatean (mismo criterio que el resto del sistema).

#### A.4.4b Cobertura REAL del catálogo 1816 (medida 2026-07-18 — referencia para crecer)

Relevada el 2026-07-18 (salida completa capturada
ese día): **869 instrumentos en 28 curvas**. **RE-MEDIDO el 2026-08-15 con
`scripts/diag_1816_cashflow`: 887 tickers únicos, mismas 28 curvas** — el
desglose por bloque y el cruce contra lo nuestro están en §4.9, que es el número
vigente. Esta sección queda por el MAPA CUALITATIVO (qué tipo de cosas hay),
que no cambió. El mapa grueso:

- **Soberanos**: todas las curvas conocidas (bonares, globales, CER, tasa fija,
  duales, DLK/Lelink, TAMAR, Badlar, Botes) + **EUR Globales** (GE29→GE46, que
  no tenemos) + rezagos (CUAP/PARP/DICP variantes, PR17, TO26, TY30P…).
- **Duales DESDOBLADOS**: 1816 publica cada dual también por pata —
  `TTD26 @TAMAR` / `@TASA FIJA`, `TXMD8 @CER` / `@TAMAR`, `TY30P @PUT` —
  valuaciones por componente que nosotros no calculamos. Interesante para
  análisis de la opcionalidad del dual.
- **BCRA**: BOPREALes completos (BPO27/28, BPOA7/8, BPOB7/8, BPOC7, BPOD7) +
  variantes `@AFIP`. Varios NO están en nuestro Curvas (solo teníamos BPOC7 en
  el watch).
- **Corporativos** (la masa grande): USD / USD Linked / ARS Badlar / ARS Tamar /
  ARS Inflación / ARS Fijo / Caución. **Varias de NUESTRAS ONs figuran ahí**
  (AER9O, AERBO, AFCHO/AFCIO/AFCJO, BACAO/BACGO, BF39O…) → 1816 puede darnos
  la SERIE histórica de ONs que hoy solo tenemos live.
- **Provinciales**: USD / ARS Tamar / Badlar / Fijo (BA37D, BDC28/31/33/36…) —
  el bloque de spreads de crédito subsoberano que no tenemos de ninguna fuente.

**Criterio asentado (decisión del user):** esto es un MAPA, no un plan de
ingesta — "no armar una base de datos brutal al pedo". El watch crece **a
demanda, en el día a día**: cuando un análisis pida un instrumento (una ON con
historia, un provincial contra AL30, la pata de un dual), se agrega ESE al
watch y listo. La herramienta para decidir es el diag de cobertura (con
fechaOperacion hábil — la corrida de sábado dio 0 por el default =hoy, ya
arreglado).

#### A.4.5 Universo inicial (recomendado por 1816)

- **Bonares (USD, ley local):** AL29, AL30, AL35, AL41.
- **Globales (USD, ley NY):** GD29, GD30, GD35, GD38, GD41, GD46.
- **Hard Dollar / otros:** AE38, AO28, AO29.
- **Curvas para descubrir más:** Soberanos, Provinciales, Corporativos, BCRA, USD
  Linked, CER, Badlar, Tasa Fija, Duales, EUR Globales.
- El universo es **editable** (probable: en Manager, como el resto de catálogos) —
  arrancamos con estos ~13 y crece por demanda (cada ticker suma costo de series,
  por eso se cura).

#### A.4.6 Modelo de datos (tablas SQL — schema `research`)

> ⚠️ **Esto era la PROPUESTA. Lo implementado es menos** (verificado contra
> `sql/schema.sql`, 2026-08-31). Del schema `research` existen: `mkt_1816_series`,
> `mkt_1816_instrumentos`, `mkt_1816_watch`, `bcra_{series,variables,watch}`,
> `fred_{series,observations,watch}` y `documentos`.
> **NO se crearon `mkt_1816_snapshot`** (el intradía se descartó — ver §A.4.7),
> **`mkt_1816_curvas`** (las 33 curvas viven en la constante de
> `jobs/mercado_1816_discovery.py`, no en tabla) ni **`mkt_1816_creditos_log`**
> (el consumo se audita por el log del job, no por tabla).

- `research.mkt_1816_series (ticker, fecha, campo, valor, fuente, moneda, plazo,
  convencion_tna, ingestado_en)` — **long/tidy** (una fila por ticker×fecha×campo):
  flexible para agregar campos sin migrar, ideal para graficar. PK
  `(ticker, fecha, campo, fuente, moneda, plazo)`. Índice por `(ticker, campo, fecha)`.
- `research.mkt_1816_snapshot (ticker, fecha_operacion, <campos...>, fuente, moneda,
  plazo, ingestado_en)` — snapshot del día (wide, 1 fila por ticker), para la tabla
  de "hoy".
- `research.mkt_1816_instrumentos (ticker, denominacion, curva, curva_id, isin,
  fecha_emision, fecha_vencimiento, moneda_denom, moneda_pago, emisor, activo)`.
- `research.mkt_1816_curvas (id, name, convencion_tna)`.
- `research.mkt_1816_creditos_log (ts, endpoint, tickers, campos, dias, costo,
  daily_used, monthly_used, ok, error)` — auditoría de consumo.
- El universo curado: `research.mkt_1816_watch (ticker, activo, orden)` (o reusar
  un patrón de catálogo existente).

#### A.4.7 Jobs (cron — `deploy/crontab.txt` es la fuente)

- `jobs/mercado_1816_series.py` — **diario post-cierre** (append del día nuevo,
  incremental por watermark `MAX(fecha)` por ticker/campo; idempotente). Modo
  `--backfill` scopeado (REGLA #4) para la carga inicial de 1 año. Este es el job
  central del pilar A.
- **`jobs/mercado_1816_discovery.py`** (el doc lo llamaba `mercado_1816_instrumentos`) —
  refresco del catálogo (curvas + instrumentos), barato (1 créd c/u).
- ~~`jobs/mercado_1816_snapshot.py`~~ — **NUNCA SE ESCRIBIÓ** (decisión del user
  2026-07-17: alcanza con el cierre diario, sin intradía). El "hoy" se arma del
  último cierre. Se reevalúa solo si el user pide el vivo (~250k créditos/mes).

#### A.4.8 Cliente y capa de servicio

- `core/mercado_1816.py` — cliente puro: auth + cache de token + renovación en 401,
  balance de créditos, chequeo de presupuesto, request con retry (429/5xx), parseo.
  Contrato "nunca levanta" (devuelve None/estructura vacía). Patrón de los otros
  feeds de `core/` (eikon_live, finnhub, argentina_datos).
- `api/services/research_sql.py` — lectura SQL para la vista (series por ticker/
  campo/rango, snapshot de hoy, catálogo). ⚠️ **`api/services/research_1816_calc.py`
  (el proxy de la calculadora teórica, endpoint 6) nunca se escribió** — la
  calculadora on-demand quedó sin implementar.

#### A.4.9 CASHFLOW (endpoint 8) — MEDIDO 2026-08-15

**Corrida real en el Droplet** (`python -m scripts.diag_1816_cashflow`, 114
créditos: 29 el censo + 85 cinco cashflows). Todo lo de acá es HECHO MEDIDO;
lo que sigue siendo hipótesis está marcado como tal.

**El universo: 887 tickers únicos vigentes en 28 curvas** (eran 869 el
2026-07-18 → creció 18). La suma por curva da exactamente 887, o sea que
**ningún ticker se publica en dos curvas**: el catálogo es una partición limpia
y no hay nada que deduplicar. La masa está en corporativos:

| Bloque | Vigentes | Curvas más gordas |
|---|---:|---|
| **Corporativos** | **667** | USD 318 · ARS Tamar 145 · USD Linked 107 · Badlar 59 |
| **Provinciales** | 106 | ARS Tamar 28 · USD 28 · Badlar 21 |
| **Soberanos** | 104 | CER 25 · Duales 24 · tasa fija 11 |
| **BCRA** | 10 | BOPREALes |

Cuatro curvas vienen VACÍAS (Corporativos ARS TPM, Provinciales Duales,
Soberanos ARS Letras CER, Soberanos USD Linked Lelink): existen en el catálogo
pero hoy no tienen instrumentos vigentes.

**El cruce: 251 tickers están en los DOS lados** (1816 ∩ Manager) — contra los
13 del watch actual, o sea que hay **~19× más historia disponible de la que
estamos bajando**. El total crudo de "míos" (2.022) NO es comparable con los 887:
la mayoría de `portafolio.assets` no son títulos listados (pagarés de
FINANCIAMIENTO `#UAC…`/`#MAV…`, FCI, cauciones). Por eso el diag separa por
ORIGEN y la lista que importa es la de `mercado.curvas`.

**Las cuatro preguntas, contestadas:**

1. **Cobertura — cubre ONs, no solo soberanos.** 5/5 cashflows OK, y cuatro de
   los cinco eran ONs nuestras (AEC3O, AER5O, AER9O, AERBO). Esto es lo que
   habilita todo lo demás: el cuadro de flujos de las ONs es hoy carga 100%
   manual. **OJO: 5 de 887 es el 0,6% — NO prueba que todos tengan flujo.**
   Para eso está el barrido `--cobertura` (§4.9c); el número de acá es "el
   endpoint anda también para corporativos", no "cubre el universo".
2. **Escala — NO hay una sola, y coincide con la nuestra instrumento por
   instrumento.** Los bonos por paridad vienen por VN 100 (Σ amortización =
   100.0000 clavado en AE38, AEC3O, AERBO) y otras ONs vienen en NOMINALES
   (AER5O Σ 148.869,84 · AER9O Σ 142.713,36). Nuestro master usa la MISMA
   convención en cada caso. **Conclusión: no hay que normalizar nada, pero
   tampoco se puede asumir un divisor global** — quien consuma esto tiene que
   mirar la escala por instrumento, igual que hace la valuación del AuM.
3. **Horizonte — el cuadro COMPLETO desde emisión.** AE38 devuelve los 34
   cupones, el #1 con fecha 2021-07-12. Sirve para reconstruir historia, no solo
   para proyectar.
4. **Costo — ~17 créditos por instrumento** (85/5 medidos; muestra chica, así
   que es promedio, no techo). Bajar los 251 compartidos ≈ **4.300 créditos**;
   los 887 enteros ≈ **15.000**. Contra el tope de 100k/día, las dos cosas
   entran holgadas y de una sola vez.

**Dos hallazgos que el manual de la API no dice:**

- **Devuelve `cuponNumero`**, un sexto campo que no está en la lista de `campos`
  documentada. No se pide y viene igual.
- **Manda las DOS fechas**, teórica y efectiva, que difieren cuando la teórica
  cae en no-hábil (AE38: teórica 2027-01-09, efectiva 2027-01-11). **Nuestro
  master guarda la EFECTIVA** — medido: por efectiva matchean 21/23 cupones
  futuros y por teórica solo 13/23. Cruzar por la fecha equivocada inventa
  divergencias de 2-3 días que no existen; el diag mide cuál de las dos matchea
  y compara por esa.

**Una divergencia REAL encontrada:** AER9O, cupón del 2026-08-19 — nuestro
master dice amortización **48.215,20** y 1816 dice **49.710,88**, un **3,1%**
de diferencia (no es redondeo: los otros cuatro cierran dentro del 1%).
*Hipótesis sin verificar*: es una ON de amortización indexada (los tres cupones
de 1816 crecen 46.587 → 49.710) y cada fuente la ajusta con el índice a una
fecha distinta; si es así, el nuestro está viejo. **Hay que mirarlo con la mesa
antes de tocar nada.**

**Decisión pendiente del user** (las tres opciones siguen en pie, ahora con
números): (a) no usarlo; (b) **control cruzado** — un guardrail que compare
nuestros `flujos` contra 1816 y avise cuando difieren más del 1%, que es
exactamente lo que acaba de encontrar el AER9O y cuesta ~4.300 créditos por
pasada sobre los 251 compartidos; (c) persistirlo como fuente de flujos. La (b)
es la recomendación: no duplica una segunda verdad y ataca el problema real, que
es que la carga manual se desactualiza sin que nadie se entere.

#### A.4.9b El diag — cómo se volvió a medir todo esto

**Herramienta**: `python -m scripts.diag_1816_cashflow` — censa el universo
completo (curvas → instrumentos), lo cruza contra `mercado.curvas` +
`portafolio.assets`, prueba el endpoint sobre una muestra y contrasta cupón a
cupón contra nuestros `flujos`. Es READ-ONLY: no escribe en la base ni en el
watch. Tres decisiones de diseño que hacen que el número no mienta:

- **Mide con qué fecha comparar** (efectiva vs teórica) en vez de elegir una:
  cuenta cuál de las dos matchea más contra nuestro master y usa esa. Sin eso
  reportaba divergencias de 2-3 días que eran puro calendario.
- **La escala se MUESTRA, no se normaliza**: Σ amortización + el ratio 1816/mío
  de amortización e interés. Si alguna vez llega un instrumento con otra
  convención, salta a la vista en lugar de corregirse en silencio.
- **Compara TODOS los cupones comunes**, no solo el primero, con tolerancia
  relativa del 1% (nuestro master está redondeado a 2 decimales; exigir igualdad
  exacta marcaría todo). Reporta cuántos divergen y cuál es el peor.

`--json`/`--desde` guardan y reusan el censo para no re-pagarlo; `--vencidos`
suma los no-performing; `--ticker` prueba uno puntual.

#### A.4.9c ¿TODOS los tickers tienen flujo? — el barrido (`--cobertura`)

Pregunta del user 2026-08-15, y la respuesta honesta es que **con 5 sondeos no
se sabe**: es el 0,6% del universo. `--cobertura` la contesta midiendo, con dos
modos porque el barrido completo no es gratis:

```bash
python -m scripts.diag_1816_cashflow --cobertura            # 3 por curva (~84 tickers)
python -m scripts.diag_1816_cashflow --cobertura --todos    # los 887, ~37 min
```

- **Muestreo ESTRATIFICADO, no los primeros N.** Toma tickers repartidos a lo
  largo de cada curva ordenada. Los N primeros alfabéticamente caerían todos en
  la misma familia de emisores (todos los `AER…`, todos los `BAC…`) y una
  familia entera sin flujo daría 0% o 100% por puro azar del alfabeto. Es
  determinista: dos corridas dan la misma muestra y son comparables.
- **Clasifica en tres**, que no son lo mismo: `con_flujo` · `sin_flujo` (HTTP
  200 con array vacío — el instrumento existe y 1816 no publica su cuadro) ·
  `error` (404/400 — el endpoint rechaza el ticker). Lista cuáles cayeron en
  cada uno.
- **Extrapola el costo por curva** (promedio de cupones × instrumentos de esa
  curva) — así se sabe cuánto saldría bajar el cuadro completo ANTES de bajarlo,
  y se ve si el gasto se concentra en una curva.
- **Dos frenos (REGLA #4)**: `--max-creditos` (default 20.000) corta el barrido
  a mitad de camino y **igual reporta lo medido**, marcándolo como PARCIAL; y el
  throttle de 2,5 s del cliente hace que el barrido completo tarde ~37 min → no
  se corre en rueda.
- `--cobertura-json` guarda el resultado ticker por ticker, para no re-pagarlo.

**MEDIDO 2026-08-15 (muestra de 70/887, 3 por curva, 947 créditos): 98,6% de
cobertura** — 69 de 70 devolvieron cuadro, 0 vacíos, **1 error**: `TTS26 @TASA
FIJA`, o sea la PATA de un dual (ver §4.10 — las patas son vistas de valuación,
no instrumentos con cuadro propio; el ticker base sí responde). Promedio de 13,5
cupones por instrumento → **bajar el cuadro completo de los 887 saldría ~10.100
créditos** (extrapolado desde la muestra). No hizo falta el `--todos`: con 0
vacíos en las 24 curvas con instrumentos, el hueco que se buscaba no existe.

#### A.4.10 EL OBJETIVO REAL — automatizar `mercado.curvas` con 1816

**Declarado por el user el 2026-08-15, y cambia el encuadre**: esto no es una
feature de Research, es **subirle el nivel al sistema de RENTA FIJA**. Lo que
persigue, en sus palabras:

1. Que un bono **nuevo** (licitación, emisión) aparezca solo, sin estar él
   pendiente de darlo de alta.
2. Que el **cuadro de flujos** de ese bono nuevo venga cargado.
3. **Mejorar/agregar curvas** — la de DUALES hoy no la usa.
4. **Renombrar sus curvas a las de 1816**, para que la automatización tenga a
   qué agarrarse.
5. **NO gastar créditos en precios** (last price, TEA, TNA): esa lógica ya la
   tiene aceitada y la sigue calculando el sistema.

> **Dato que ordena todo**: los flujos que hoy están en `mercado.curvas` **salieron
> de 1816** (los cargó a mano desde ahí). O sea que esto no es adoptar una fuente
> nueva: es **conectar la que ya se venía usando a mano**.

**El punto 5 sale gratis, y está medido.** El diseño usa SOLO `/curvas` +
`/instrumentos` + `/cashflow`; **nunca** `/indicadores` ni `/series`, que son los
caros. Con los números medidos el 2026-08-15:

| Pieza del job | Costo | Frecuencia |
|---|---:|---|
| Detectar novedades (1 `/curvas` + 28 `/instrumentos`) | **29 créditos** | diaria |
| Bajar el cuadro de UN bono nuevo | ~14 créditos | por alta |
| **Total mensual estimado** | **~600-800** | contra **3.100.000** |

Es **0,02% del plan mensual**. El recurso escaso deja de serlo cuando no se
piden precios.

**Lo que YA está medido y sostiene el diseño** (§4.9): 212 de nuestros 222 bonos
de `mercado.curvas` están en 1816 (95,5%), la cobertura de cashflow es 98,6%, el
cuadro viene COMPLETO desde emisión, y la escala coincide instrumento por
instrumento con la nuestra.

**Lo que falta decidir, y por qué hace falta medirlo antes** — `scripts/diag_1816_mapeo.py`:

- **El mapeo de curvas NO se inventa, se deriva.** El diag muestra, para cada
  valor de `mercado.curvas.curva`, en qué curvas de 1816 cayeron sus tickers.
  Donde una curva de 1816 se lleva ≥80% de una familia nuestra, el job puede
  vigilarla sin ambigüedad. Donde se parte (sospecha, sin medir: `tasa_fija`
  entre «Soberanos ARS tasa fija» y «Soberanos ARS Botes»), hay una **decisión
  del user**: parto mi curva para seguir a 1816, o el job vigila varias.
- **Renombrar la curva NO es cosmético**: `mercado.curvas.curva` es lo que
  agrupa las vistas de renta fija, decide el `fit` de fair value y arma los
  z-scores. Cambiar un nombre mueve las vistas. Por eso el mapeo se propone como
  **tabla de equivalencia** (mi curva ↔ curva de 1816) y no como un rename a lo
  bruto: la tabla deja automatizar sin tocar lo que ya funciona, y el rename se
  puede hacer después con el mapa en la mano.
- **Las patas de los duales NO tienen cuadro propio.** Medido: `TTS26 @TASA
  FIJA` da 404 en `/cashflow` aunque figure en `/instrumentos`. Son **vistas de
  valuación por componente**, no instrumentos: para el cuadro hay que pedir el
  ticker base. Esto toca directo el punto 3 (duales).
- **Divergencias reales**: AER9O tiene 3,1% de diferencia entre nuestro cuadro y
  el de 1816. Antes de automatizar hay que saber si nuestro dato quedó viejo
  (probable si la amortización es indexada) o si 1816 proyecta distinto.

**Arquitectura propuesta (a validar con los números del diag, NO codeada):**

```
   jobs/curvas_1816_sync.py  (diario, post-cierre, ~29 créditos)
      │
      ├─ 1. lee las curvas de 1816 que están MAPEADAS a una curva mía
      ├─ 2. compara el listado contra mercado.curvas  → ¿ticker nuevo?
      ├─ 3. por cada nuevo: /cashflow (~14 créditos) → PROPUESTA de alta
      └─ 4. NO escribe en mercado.curvas: deja la propuesta para APROBAR
             en Manager → BONOS (que hoy está desactualizado y sin uso)
```

**La decisión de diseño que importa: el job PROPONE, no da de alta solo.** Un
alta automática en `mercado.curvas` entra directo a la valuación, al fair value
y al AuM (vía el join con `portafolio.assets`); un flujo mal escalado rompe el
chart entero — que es exactamente el catálogo de fallas de
`docs/RENTA_FIJA.md` §9. Con una bandeja de aprobación, el trabajo manual pasa de
*"buscar el bono, copiar el cuadro, tipearlo"* a *"mirar y aceptar"*, sin que
una emisión rara se cuele sola. Manager → BONOS ya existe (`/bonos`,
`/bonos/sin-flujo`, `/bonos/sin-tasa`, `/bonos/parse-flujos`) y está
desactualizado: **es el lugar natural para esa bandeja**, y le devuelve sentido.

> **⚡ ESTE DISEÑO SE MUDÓ — doc vivo: `docs/AV_AGENT.md` (QuantAI P8).**
> Lo de acá arriba queda como el **registro de la medición** (los números de §4.9
> son la evidencia que sostiene el proyecto y no se duplican). El plan ejecutable,
> las etapas, las decisiones abiertas y el estado viven en el doc del agente — si
> los dos dicen cosas distintas, manda el del agente.

**Estado: E0 (doc + decisiones abiertas) hecho el 2026-08-16.** El diseño creció
de "job que propone altas" a **agente de integridad de datos**, porque las tres
tareas que pidió el user (altas, flujos faltantes, tasas mal) son el mismo verbo y
solo la tercera necesita IA.

---

### 5. PILAR B — Research escrito (mails + reportes)

**Reusa P6 — YA construido, no se rehace.**

#### A.5.1 Lo que ya existe

- `jobs/research_mail.py` (cron `*/30` 10-14 UTC L-V): lee la casilla por **IMAP
  readonly**, filtra por remitente, persiste el mail **CRUDO** en `ia.research`
  (fuente de verdad, citable; dedup por `Message-ID` → idempotente). **No usa
  IA**: tenía un DESTILADO opcional del LLM `{resumen, temas[], hechos[]}` que se
  borró el 2026-08-28 (solo 4 mails llegaron a tenerlo, y nadie lo mostraba).
- Tabla `ia.research (fecha, fuente, asunto, message_id, cuerpo, created_at)`
  + **full-text español** (`to_tsvector('spanish',
  cuerpo)`) → "¿qué decía el research sobre el BCRA?" se responde con FTS (ADOPTAR
  YA: full-text antes que vectores, QuantAI).
- Maneja HTML de mail → texto plano, headers MIME, fecha ART.

#### A.5.2 Lo que falta para el pilar B

1. **Credenciales IMAP (CONFIGURADAS 2026-07-17 por el user):** `RESEARCH_IMAP_USER`
   (la Gmail que recibe los reenvíos, `mollonicolas95@gmail.com`) /
   `RESEARCH_IMAP_PASSWORD` (app password de Gmail) / `RESEARCH_MAIL_FROM=1816.com.ar`.
   **Flujo real del user:** 1816 → su corporativo → él **REENVÍA a mano** a la Gmail.
   El reenvío reescribe el `From` (pasa a ser su corporativo), pero el remitente
   original (`research@1816.com.ar`) queda en el CUERPO → por eso el job matchea el
   remitente en **From + Asunto + Cuerpo** (fix 2026-07-17), no solo en From. Probar:
   `python -m jobs.research_mail --dry-run`. **Nuance conocida:** para reenviados, la
   `fecha` sale del header Date (día del reenvío) — si el user reenvía el mismo día
   (su caso), coincide con la fecha del research; si reenvía tarde, habría que parsear
   la fecha del cuerpo (diferido hasta que sea un problema real).
2. **Distinguir DIARIO vs MENSUAL:** hoy `ia.research` no separa el tipo. Sumar una
   columna `tipo` ('diario'/'mensual'/'otro') derivada del asunto ("El día en pocas
   líneas" = diario; los reportes mensuales por su asunto) — así la vista los
   agrupa en dos secciones. Cambio chico en el job + un backfill del campo.
3. **Reportes mensuales pueden traer adjuntos (PDF):** el job hoy solo toma el
   cuerpo. Si los mensuales vienen como PDF adjunto, extender la extracción
   (guardar el PDF + su texto). **A confirmar con un mail mensual real.**

#### A.5.3 Qué muestra la vista (pilar B)

- **Timeline** de research por fecha (diario + mensual, separados) con el
  **crudo** limpio y legible. (El plan original ponía un destilado del LLM arriba;
  se construyó, nunca se prendió y se borró el 2026-08-28 — ver §changelog.)
- **Buscador FTS** ("mostrame lo que dijeron sobre las Lelink / la licitación /
  el superávit") sobre el cuerpo.
- Marca de fuente/fecha; nada de IA presentado como dato verificado (data
  provenance, QuantAI).

---

### 6. Frontend — la vista Research

- **Nueva vista principal** `acaquant-web/src/app/research` + `research-view.tsx`,
  entrada en la nav (gate módulo `research`, oculta sin el módulo).
- **Tabs actuales (2026-08-30, keep-alive)**: RENTA FIJA ARGENTINA ·
  **ANÁLISIS SENSIBILIDAD** (mudada desde la vista ESTRATEGIA `/retorno`,
  eliminada — mismo componente `sensibilidad-table.tsx`, mismo endpoint
  `/api/analitica/sensibilidad-retorno`) · REPORTES FINANCIEROS · BCRA ·
  DATOS INTERNACIONALES · RENTA VARIABLE INTERNACIONAL.
- **Layout propuesto (a diseñar CON el user, incremental):** dos secciones/tabs:
  - **MARKET DATA:** selector de ticker(s) + campos + rango → **gráfico de series**
    (la estrella) + tabla de indicadores de hoy + la calculadora teórica. Toggles de
    `fuente`/`plazo`/`moneda`.
  - **RESEARCH:** timeline de mails (diario/mensual) + buscador FTS.
- **Routes Next "live":** las que sirvan snapshot de hoy necesitan
  `dynamic="force-dynamic"` + `revalidate=0` + `Cache-Control:no-store`
  (patrón live-fallback del repo).
- **Gráficos:** reusar la infra de charts existente (curvas-chart / recharts) y la
  skill `dataviz` para el diseño.

---

### 7. RBAC — módulo `research`

Agregar el módulo nuevo (patrón de `api/CLAUDE.md`, igual que `back-office`):

1. Sumar `"research"` a `MODULES` (`core/roles.py`).
2. `("/api/research", "research")` en `ENDPOINT_MODULE_PREFIXES` (`api/auth.py`) →
   todo endpoint bajo `/api/research` nace default-deny.
3. Asignar el módulo a los roles en `manager.role_matrix` (o `DEFAULT_MATRIX`).
   **JAMÁS a `invitado`** (REGLA #8 — research propietario/pago).
4. El frontend filtra la nav por módulo (`/api/me`).

---

### 8. Mapa de archivos (nuevo vs reusado)

| Pieza | Archivo | Estado |
|---|---|---|
| Cliente API 1816 | `core/mercado_1816.py` | **nuevo** |
| Series diarias (writer) | `jobs/mercado_1816_series.py` | **nuevo** (con `--backfill`) |
| Catálogo (curvas/instrumentos) | `jobs/mercado_1816_discovery.py` | **hecho** (se llamó distinto al plan) |
| Snapshot intradía | ~~`jobs/mercado_1816_snapshot.py`~~ | **NO EXISTE** — descartado |
| Lectura SQL para la vista | `api/services/research_sql.py` | **nuevo** |
| Calculadora teórica (proxy) | ~~`api/services/research_1816_calc.py`~~ | **NO EXISTE** — sin implementar |
| Router HTTP | `api/routers/research1816.py` | **hecho** (`/api/research1816/*`, no `/api/research/*`) |
| Tablas SQL | `sql/schema.sql` (`research.mkt_1816_{series,instrumentos,watch}`) | **hecho** (3 de las 6 propuestas — ver §A.4.6) |
| Ingesta de mails | `jobs/research_mail.py` | **REUSA (P6)** |
| Tabla de mails | `ia.research` (+ columna `tipo`) | **REUSA + 1 columna** |
| Vista frontend | `acaquant-web/src/.../research-view.tsx` | **nuevo** |
| Tab sensibilidad | `acaquant-web/src/.../sensibilidad-table.tsx` | **REUSA** (mudado de `/retorno` 2026-08-30; backend `api/services/sensibilidad.py`) |
| RBAC | `core/roles.py` + `api/auth.py` | **editar** (módulo `research`) |

---

---

# PARTE B — Tab BCRA

### 1. Objetivo

Una **tab nueva "BCRA" dentro de `/research`** que integre las APIs públicas de
estadísticas del BCRA: el pulso monetario/cambiario oficial (reservas, base
monetaria, tasas, inflación, CER, depósitos, préstamos, multi-divisas) con
**series históricas graficables y comparables**, actualizándose sola. El mismo
espíritu del laboratorio 1816: **leer de la API → persistir en Postgres → la
vista sirve de la DB** (rápida, sin depender del BCRA en cada carga).

---

### 2. Las 3 APIs — qué son y qué VERIFICAMOS contra la API viva (2026-07-18)

Base: `https://api.bcra.gob.ar` — **públicas, sin autenticación, sin API key.**
La doc oficial (bcra.gob.ar/documentacion-apis) es una SPA que no renderiza sin
browser; lo de abajo se verificó **pegándole a la API real** (REGLA #2).

#### B.2.1 Estadísticas Monetarias v4 — LA PRINCIPAL ✅ verificada

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

#### B.2.2 Estadísticas Cambiarias v1 ✅ verificada (maestro)

- **Maestro de divisas:** `GET /estadisticascambiarias/v1.0/Maestros/Divisas` →
  **43 divisas** (USD, EUR, BRL, CNY, JPY, GBP, CLP, UYU… e incluye **XAU = oro
  onza** y divisas históricas DEM/ESP/ITL).
- **Cotizaciones (hipótesis, patrón documentado — verificar en Fase 0):**
  `GET /estadisticascambiarias/v1.0/Cotizaciones?fechaCotizacion=` (todas las
  divisas de un día) y `GET /Cotizaciones/{codMoneda}?fechaDesde&fechaHasta`
  (serie por divisa, con tipoPase y tipoCotizacion).

#### B.2.3 Principales Variables v3 — REDUNDANTE con v4 → NO se integra

v3 (`/estadisticas/v3.0/monetarias`) es la versión anterior del mismo servicio;
**v4 la contiene** (las "principales variables" son la categoría homónima del
catálogo v4). Decisión: **integrar SOLO v4 + cambiarias v1** — una versión menos
que mantener. (El día que v4 deprecie algo, se revisa.)

---

### 3. ⚠️ Lo que YA existe en el sistema (no duplicar, no romper)

`jobs/bcra.py` (cron 22 UTC L-V) **ya le pega al BCRA** y alimenta
`macro.series_macro` (claves DOLAR/CER/BADLAR/TAMAR…) — es el insumo de los
MOTORES (CER forward de breakevens, valuaciones). **Ese pipeline NO SE TOCA**:
es crítico y estrecho a propósito. La integración nueva es un feed **ancho y
separado** (schema `research.bcra_*`, jobs propios) para ANÁLISIS, no para
motores. Si algún día conviene unificar, se decide con los dos andando.

---

### 4. Qué construir — datos y gráficos, en detalle

#### B.4.1 El principio de diseño

**No se bajan 1.581 variables.** Se cura un **universo de ~35-40 series** (tabla
`research.bcra_watch`, editable — mismo patrón que el watch de 1816) elegidas por
valor de mesa, y la vista ofrece dos niveles:

- **Dashboards curados** (cuadrantes con lecturas armadas — lo que abrís todos
  los días).
- **Explorador de series** (elegís cualquier variable del watch y la graficás /
  comparás — el patrón Spread/Comparar del lab 1816, reutilizado).

#### B.4.2 Universo curado propuesto (el seed del watch)

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

#### B.4.3 Los gráficos (por cuadrante de la tab BCRA)

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

#### B.4.4 Por qué esto es valioso (y no otra tabla de números)

- Las series del BCRA son **el contexto oficial** de todo lo que la mesa opera;
  hoy se miran en la web del BCRA o en Excel ajeno.
- Los **cruces con datos propios** (brecha A3500-MEP, tasas reales, IPC vs REM
  vs breakevens, depósitos USD vs los comentarios de 1816) son imposibles fuera
  de nuestra plataforma — ahí está el diferencial, no en re-mostrar la serie.

---

### 5. Arquitectura — cómo se actualiza solo "24/7"

**La verdad del dato primero:** el BCRA publica **una vez por día hábil, con
rezago de 1-2 días** (verificado: Reservas al 15-jul un 18-jul). "Actualizado
24/7" real = **la vista sirve SIEMPRE desde nuestra DB** (cero dependencia del
BCRA al navegar, 24/7 de verdad) + **un cron que sincroniza varias veces al día**
para capturar la publicación apenas sale, sin martillar la API.

#### B.5.1 Piezas

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

#### B.5.2 Costos y límites

Sin créditos ni key: el costo es cortesía de uso. Con el diseño incremental son
**~40-45 requests × 4 corridas/día ≈ 180 requests diarios** — irrelevante para
una API pública nacional. El backfill inicial (~40 series completas) se corre
una vez, throttled, fuera de horario pico.

---

### 7. Decisiones tomadas / pendientes

- ✅ Solo v4 + cambiarias v1 (v3 redundante). Feed separado de `jobs/bcra.py`
  (motores) — no se toca lo existente. Universo CURADO, jamás las 1.581.
  Prefijo API propio + route handler de Next desde el día 1.
- ⏳ **Del user:** validar el seed del watch (§4.2) y el layout (§4.3) antes de
  la Fase 2; prioridad de esta tab vs el resto del roadmap Research.

---

# PARTE C — Tab DATOS INTERNACIONALES (FRED)

### 1. Objetivo

Una **tab nueva "Datos Internacionales" dentro de `/research`** que integre la
**FRED API** (Federal Reserve Bank of St. Louis) con el contexto macro-financiero
global que hoy la mesa mira en Bloomberg ajeno, TradingView o Excel: **tasas del
Tesoro USA, commodities (soja/maíz/trigo/petróleo/oro), dólar global, spreads de
crédito EM, inflación y actividad USA, China, Brasil, Latam y liquidez global** —
series históricas graficables, comparables y actualizándose solas.

- **Para quién:** la mesa (renta fija + agro + FX). Módulo interno `research`
  — **JAMÁS invitado** (REGLA #8): esto no es "mercado ARG read-only", es research
  con cruces de datos privados. Default-deny en el portal www.
- **Por qué (el diferencial):** no es re-mostrar una serie que ya está gratis en
  fred.stlouisfed.org. **El valor es el CRUCE con datos propios** que solo existen
  en esta plataforma: MEP/CCL live, breakevens de inflación AR, curva de bonos AR,
  riesgo país, agro/pizarra local, tasas locales. Un `UST10Y` sobre la paridad de
  GD30 responde la primera pregunta de la mañana — *"¿el soberano se movió por el
  mundo o por nosotros?"* — algo que ninguna herramienta externa puede hacer (§5).

---

### 2. La FRED API — mecánica VERIFICADA

Base: `https://api.stlouisfed.org/fred/` — REST sobre HTTPS, todo por GET con query
params. Existe una `v2` en desarrollo; **usamos la estable `/fred/` (v1)**.

#### C.2.1 Autenticación — la diferencia CLAVE vs BCRA

**FRED EXIGE API key; el BCRA no.** Este es el único cambio estructural respecto
del molde BCRA:

- **Key:** string alfanumérico de 32 chars en minúscula. Se obtiene gratis en
  `fredaccount.stlouisfed.org/apikeys` (registro + login, se entrega el mismo día,
  sin aprobación manual). Requiere aceptar los Terms of Use.
- **Cómo se manda:** **query param `api_key=<32chars>` en la URL** (NO header),
  **obligatorio en TODOS los endpoints**. Sin él o inválido → 400.
- **Costo: gratis.** No hay tiers, ni suscripción, ni costo por request. Es un
  secreto **de bajo riesgo** (solo lectura de data pública, sin cargo) — pero
  secreto igual: va como **env var `FRED_API_KEY`** en el `.env` del Droplet,
  **nunca hardcodeada, nunca al front, nunca al repo**. Sin la env, el feed queda
  deshabilitado (mismo patrón que el resto de las integraciones por API key).

#### C.2.2 Rate limit (verificado)

- **120 requests/min por API key** (documentado y replicado por todas las libs).
  Al exceder → **HTTP 429**.
- **Nuestro throttle:** ~1 req cada 0.6s (holgado bajo el techo) + backoff en
  429/5xx (patrón `fredr`: espera creciente, máx ~6 reintentos). Con el diseño
  incremental **nunca nos acercamos** al techo: ~45 series × 1 req = 45 requests
  por corrida (§6.5). El límite es **por key** → si varios procesos comparten la
  key, se suman: **el throttle vive centralizado en `core/fred_api.py`** (un solo
  carril de salida), no en cada job.

#### C.2.3 Endpoints que usamos (verificados)

| Endpoint | Uso nuestro |
|---|---|
| `/fred/series/observations` | **EL central.** La serie de tiempo (`date`/`value`). Acá viven units, cambio de frecuencia y vintages. |
| `/fred/series` | Metadata de UNA serie (title, frequency, units, seasonal_adjustment, observation_start/end, last_updated). Para poblar `fred_series` del watch. |
| `/fred/series/updates` | Series actualizadas recientemente — para refrescar solo lo que cambió sin barrer. (Opcional, optimización.) |
| `/fred/series/search` | **Solo para curar** (descubrir el `series_id` correcto). NO en runtime de producción. |
| `/fred/releases/dates`, `/fred/release/dates` | Calendario de publicaciones (Fase futura: calendario económico). |
| `/fred/series/vintagedates`, `observations?vintage_dates=` | ALFRED / backtesting sin look-ahead (Fase futura, §2.5). |

`observations` params clave: `series_id` (obligatorio), `observation_start`/`_end`
(YYYY-MM-DD; defaults absurdos 1776/9999 → **siempre acotar**), `units`
(`lin`|`chg`|`ch1`|`pch`|`pc1`|`pca`|`cch`|`cca`|`log`, default `lin`), `frequency`
+ `aggregation_method` (`avg`|`sum`|`eop`) para agregar a menor frecuencia,
`realtime_start`/`_end` y `vintage_dates` (ALFRED), `limit` (1–100000, default
100000), `offset`, `sort_order`. **`file_type=json` SIEMPRE** (default es XML — la
trampa #1).

#### C.2.4 Gotchas verificados (los que rompen código)

1. **`file_type` default = XML.** En código pasar **siempre `file_type=json`** o
   parseás XML sin querer.
2. **`api_key` en query param (no header), obligatorio en todos los endpoints.**
3. **Missing values vienen como el string `"."` (punto)**, NO null ni vacío.
   Convertir a `None`/NaN **antes** de castear a float o revienta. (Igual espíritu
   que el `valor is None` del cliente BCRA.)
4. **`units` (transformaciones) las calcula el SERVIDOR**, y se aplican **DESPUÉS**
   de recortar por `observation_start/end` → un `pc1` (YoY) al inicio del rango
   viene vacío por falta de 12 meses previos. **Pedir colchón hacia atrás.**
   (Decisión: guardamos `lin` crudo y transformamos on-the-fly — §6.)
5. **Cambio de frecuencia solo AGREGA de mayor→menor** (daily→monthly OK;
   monthly→daily NO interpola). `aggregation_method` mal elegido distorsiona:
   `avg` (default), `sum` (flujos: ventas), `eop` (stocks/precios de cierre).
   Sumar un índice de precios o promediar un stock = basura.
6. **Ajuste estacional (SA vs NSA):** muchas series existen en dos versiones con
   **`series_id` DISTINTO**. El campo `seasonal_adjustment_short` (SA/NSA) lo dice.
   **No mezclar SA con NSA** en comparaciones ni en interanuales. (Nuestro seed usa
   SA donde importa: CPI, PCE, PAYEMS.)
7. **Series diarias tienen HUECOS reales** (fines de semana/feriados). Alinear por
   **fecha**, nunca por índice posicional.
8. **Revisiones retroactivas:** por default FRED devuelve la **última revisión de
   TODO el histórico**, no lo publicado en su momento. El valor de una serie puede
   **cambiar entre dos requests** aunque no salga dato nuevo. → watermark con
   colchón (re-leemos N días para capturar revisiones, §6.4). Para "as-published"
   real hace falta ALFRED (§2.5).
9. **Paginación:** `observations` acepta `limit` hasta 100000 (suficiente para casi
   todo); los demás endpoints, hasta 1000. Iterar `offset += limit` si supera.
10. **Series discontinuadas** siguen accesibles pero no se actualizan (tag
    `discontinued`, `observation_end` viejo). Chequear `last_updated` antes de
    confiar. (Ninguna del seed está discontinuada — verificado.)

#### C.2.5 ALFRED / vintages — qué es y por qué lo DIFERIMOS

**ALFRED = Archival FRED.** Cada observación tiene 3 fechas: `date` (a qué período
se refiere), `realtime_start`/`realtime_end` (ventana en que ese valor fue el
vigente antes de ser revisado). `output_type`: 1 = por realtime period (default),
2 = todas las vintages, 3 = new/revised only, **4 = INITIAL RELEASE ONLY** (el
primer valor publicado, sin revisiones — **oro para backtesting sin look-ahead
bias**).

**Decisión v1: guardamos SOLO `latest` (última revisión), NO vintages.** Razón:
(a) el 90% del valor de mesa es ver la serie hoy y cruzarla; (b) vintages
multiplican el almacenamiento y la complejidad (un carril de cache/PK por
`vintage_date`); (c) los gotchas de "el pasado se reescribe" los mitigamos con el
colchón de watermark. **ALFRED queda como Fase futura** (backtesting honesto de
sorpresas: initial release vs final, `output_type=4`). Se asienta acá cuando se
decida — no se implementa a ciegas.

---

### 3. ⚠️ Lo que YA existe en el repo (no duplicar, no romper)

- **`jobs/bcra.py`** (cron 22 UTC) alimenta `macro.series_macro` (DOLAR/CER/BADLAR/
  TAMAR) → insumo de los **MOTORES**. **NO se toca.**
- **`jobs/argentina_datos.py`** (12 UTC) trae RiesgoPais/IPC/REM. **NO se toca.**
- **La tab BCRA** (`core/bcra_api.py`, `research.bcra_*`, `jobs/bcra_research.py`,
  `/api/research-bcra`, `research-bcra.tsx`) es el **MOLDE EXACTO** de esta tab. Se
  **copia el patrón**, no se modifica.

**FRED es un feed ANCHO y SEPARADO** — schema `research.fred_*`, cliente propio,
job propio, router propio, tab propia. Cero acoplamiento con los pipelines de
motores. Mismo principio que BCRA: si algún día conviene unificar, se decide con
los dos andando.

**Cliente propio, NO una lib externa** (`fredapi`/`fedfred`/`pyfredapi`). Razones:
(1) el repo ya tiene "un módulo por fuente" (`bcra_api`, `argentina_datos`, `byma`,
`mae`) con `requests` + pool SQL — meter una lib pesada agrega deps y estilos
ajenos; (2) FRED es un REST plano con key, trivial de envolver; (3) necesitamos
control fino del rate-limit centralizado y del upsert incremental a Postgres, que
ninguna lib da. **Se roban 3 patrones** de `fedfred`/`fredr` como referencia: token
bucket de 120/min, cache por `(series_id, units, realtime)`, y (si algún día)
carril de cache de vintage separado del latest.

---

### 4. Universo CURADO por bloque (el seed de `research.fred_watch`)

**El principio (idéntico a BCRA/1816):** FRED tiene ~800k+ series. **JAMÁS se baja
el catálogo.** Se cura un universo de **~48 series** de valor de mesa, cada una con
su `bloque` (= sub-tab). Editable en `research.fred_watch`.

**Leyenda:** ✅ = **ID VERIFICADO** existe en FRED (fase de verificación completada);
⏳ = **a confirmar en Fase 0** (mencionada en cruces/prioridad pero no en la
verificación — se valida contra `/fred/series` antes de sembrar). Freq: D/W/M/Q.

#### Bloque `tasas_usa` — Tasas USA (bloque #1 para una mesa de renta fija)
| series_id | Etiqueta | Freq | Unidad | ✓ |
|---|---|---|---|---|
| FEDFUNDS | Fed Funds Effective (mensual) | M | % | ✅ |
| DFF | Fed Funds Effective (diaria) | D | % | ✅ |
| DGS2 | UST 2Y | D | % | ✅ |
| DGS5 | UST 5Y | D | % | ✅ |
| DGS10 | UST 10Y | D | % | ✅ |
| DGS30 | UST 30Y | D | % | ✅ |
| T10Y2Y | Spread 10Y−2Y | D | pp | ✅ |
| T10Y3M | Spread 10Y−3M | D | pp | ✅ |
| SOFR | SOFR | D | % | ✅ |
| DFII10 | UST 10Y real (TIPS) | D | % | ✅ |
| DFEDTARU | Fed target range (upper) | D | % | ⏳ |

#### Bloque `commodities` — Commodities (el corazón del modelo AR: soja + energía)
| series_id | Etiqueta | Freq | Unidad | ✓ |
|---|---|---|---|---|
| PSOYBUSDM | Soja (FMI) | M | USD/t | ✅ |
| PSMEAUSDM | Harina de soja (AR #1 mundial) | M | USD/t | ✅ |
| PSOILUSDM | Aceite de soja (AR líder) | M | USD/t | ✅ |
| PMAIZMTUSDM | Maíz | M | USD/t | ✅ |
| PWHEAMTUSDM | Trigo | M | USD/t | ✅ |
| DCOILWTICO | Petróleo WTI | D | USD/bbl | ✅ |
| DCOILBRENTEU | Petróleo Brent | D | USD/bbl | ✅ |
| ~~GOLDPMGBD228NLBM~~ | Oro (LBMA PM fixing) — **FRED lo ELIMINÓ 2022-01-31** (licencia ICE); NO existe. Sin reemplazo spot diario bueno en FRED. | — | — | ❌ |
| PCOPPUSDM | Cobre (Dr. Copper) | M | USD/t | ✅ |
| DHHNGSP | Gas natural (Henry Hub) | D | USD/MMBtu | ✅ |
| PIORECRUSDM | Hierro (cadena China→Brasil) | M | USD/t | ⏳ |

#### Bloque `dolar_fx` — Dólar / FX global
| series_id | Etiqueta | Freq | Unidad | ✓ |
|---|---|---|---|---|
| DTWEXBGS | Dólar amplio (≈DXY) | D | índice | ✅ |
| DTWEXAFEGS | Dólar vs avanzadas (G10) | D | índice | ✅ |
| DEXUSEU | USD por EUR | D | USD/EUR | ✅ |
| DEXJPUS | JPY por USD (funding carry) | D | JPY/USD | ✅ |
| DEXCHUS | CNY por USD | D | CNY/USD | ✅ |
| DEXBZUS | BRL por USD (comparable del peso) | D | BRL/USD | ✅ |

#### Bloque `riesgo_credito` — Riesgo / crédito / condiciones financieras
| series_id | Etiqueta | Freq | Unidad | ✓ |
|---|---|---|---|---|
| BAMLEMCBPIOAS | EM Corp OAS | D | pp | ✅ |
| BAMLEMHBHYCRPIOAS | EM HY Corp OAS (donde cae AR) | D | pp | ✅ |
| BAMLH0A0HYM2 | US HY OAS | D | pp | ✅ |
| BAMLC0A0CM | US IG OAS | D | pp | ✅ |
| VIXCLS | VIX | D | pts | ✅ |
| NFCI | Chicago Fed Financial Conditions | W | índice | ✅ |
| STLFSI4 | St. Louis Fed Financial Stress | W | índice | ✅ |

#### Bloque `eeuu_inflacion` — EEUU inflación
| series_id | Etiqueta | Freq | Unidad | ✓ |
|---|---|---|---|---|
| CPIAUCSL | CPI headline (SA) | M | índice | ✅ |
| CPILFESL | Core CPI (SA) | M | índice | ✅ |
| PCEPI | PCE price index | M | índice | ✅ |
| PCEPILFE | Core PCE (target de la Fed) | M | índice | ✅ |
| T10YIE | Breakeven 10Y | D | % | ✅ |
| T5YIE | Breakeven 5Y | D | % | ✅ |
| T5YIFR | 5y5y forward (anclaje) | D | % | ✅ |

#### Bloque `eeuu_actividad` — EEUU actividad
| series_id | Etiqueta | Freq | Unidad | ✓ |
|---|---|---|---|---|
| PAYEMS | Nonfarm payrolls (SA) | M | miles | ✅ |
| UNRATE | Desempleo (SA) | M | % | ✅ |
| ICSA | Initial claims (SA) | W | solicitudes | ✅ |
| GDPC1 | PBI real | Q | USD 2017 | ✅ |
| INDPRO | Producción industrial | M | índice | ✅ |
| CFNAI | Chicago Fed Activity (proxy PMI) | M | índice | ✅ |
| RSAFS | Ventas minoristas | M | USD | ✅ |
| NEWORDER | Órdenes bienes de capital core | M | USD | ✅ |
| UMCSENT | Sentimiento U. Michigan | M | índice | ✅ |
| HOUST | Housing starts | M | miles SAAR | ✅ |

> **Nota (verificado):** ISM/PMI de Markit **ya no están en FRED** (quitados por
> copyright). Por eso el bloque usa CFNAI/INDPRO/NEWORDER como proxies de actividad.

#### Bloque `china` — China
| series_id | Etiqueta | Freq | Unidad | ✓ |
|---|---|---|---|---|
| CHNCPIALLMINMEI | CPI China (deflación = ojo commodities) | M | índice NSA | ✅ |
| XTEXVA01CNM667S | Exportaciones China | M | USD | ✅ |
| TRESEGCNM052N | Reservas ex-oro China | M | M DEG | ✅ |

> **Verificado:** el PBI de China en FRED (OECD) está desactualizado (termina
> Q3-2023) y M2 termina 2019 → **no van al seed**. Se priorizan exportaciones,
> reservas y CPI, que sí están al día.

#### Bloque `brasil` — Brasil (principal socio comercial + comparable de riesgo)
| series_id | Etiqueta | Freq | Unidad | ✓ |
|---|---|---|---|---|
| IRSTCB01BRM156N | Selic (tasa BCB) | M | % | ✅ |
| IRSTCI01BRM156N | CDI / interbancaria | M | % | ✅ |
| BRACPIALLMINMEI | CPI Brasil (IPCA) | M | índice NSA | ✅ |

#### Bloque `latam_em` — Latam / Emergentes (benchmarks de fair value AR)
| series_id | Etiqueta | Freq | Unidad | ✓ |
|---|---|---|---|---|
| DEXMXUS | MXN por USD | D | MXN/USD | ✅ |
| CCUSMA02CLM618N | CLP por USD (atado al cobre) | M | CLP/USD | ✅ |
| MEXCPIALLMINMEI | CPI México | M | índice NSA | ✅ |
| CHLCPIALLMINMEI | CPI Chile | M | índice NSA | ✅ |

#### Bloque `liquidez_global` — Liquidez global (la marea que mueve TODO el riesgo)
| series_id | Etiqueta | Freq | Unidad | ✓ |
|---|---|---|---|---|
| WALCL | Balance de la Fed (QT/QE) | W | M USD | ✅ |
| RRPONTSYD | Reverse repo overnight | D | mil M USD | ✅ |
| WRESBAL | Reservas bancarias en la Fed | W | M USD | ✅ |
| WTREGEN | TGA (cuenta del Tesoro) | W | M USD | ✅ |
| M2SL | M2 (SA) | M | mil M USD | ✅ |

> **Opcional (a confirmar Fase 0):** `USREC`/`USRECD` (NBER recession indicator,
> mensual/diario) para el **recession shading** (§7); `CBBTCUSD` (Bitcoin Coinbase,
> ⏳) si el user lo quiere como termómetro de risk-appetite. No son "series de mesa"
> — van como series **auxiliares** (bloque técnico oculto o flag `activo=false`).

**Total seed activo: ~48 series** (11+11+6+7+7+10+3+3+4+5, menos los ⏳ que se
confirmen). Todas D/W/M/Q — **frecuencias mixtas**, el punto técnico #1 de esta tab
vs BCRA (§6.2).

---

### 5. Los CRUCES con datos propios — el diferencial (desarrollados)

**Esto es lo que ninguna herramienta externa (TradingEconomics, TradingView, FRED)
puede hacer:** cruzar la serie global con nuestros datos privados. Son la Fase 3 y
la razón de ser del módulo. Ocho cruces priorizados:

1. **Beta del soberano AR a tasas globales.** `DGS10`+`DFII10` vs paridad/TIR de
   AL/GD + riesgo país. Regresión del Δ diario del riesgo país contra el real yield
   US separa cuánto del drawdown de GD es **"del mundo"** (suben tasas → EM entero
   sufre) y cuánto es **alpha idiosincrático local**. → **no vender en pánico**
   cuando GD cae por un selloff global; dimensionar hedge de duration.

2. **¿El peso va con sus pares EM o hay ruido local?** `DEXBZUS`+`DEXMXUS`+`DTWEXBGS`
   vs **MEP/CCL propio (live)**. Regresión construye un "CCL teórico EM"; el
   **residuo** (CCL real − teórico) es el componente argentino (intervención,
   dólar-soja, fiscal, cepo). Un residuo que se abre **anticipa presión sobre la
   brecha** antes de que se note en el nivel.

3. **Timing de la liquidación del campo.** `PSOYBUSDM`+`PMAIZMTUSDM`+`PWHEAMTUSDM`
   vs **pizarra/cámara agro local + MEP/CCL**. Si Chicago sube pero la liquidación
   local no acompaña (expectativa de devaluación > carry en pesos) → **oferta
   reprimida de dólares**. Predice la ventana de liquidación y la presión futura
   sobre CCL. Cruce con `mercado.agro_pizarra` / `mercado.camara_cereales`.

4. **Beta de crédito EM del riesgo país (rally prestado vs propio).** `BAMLH0A0HYM2`
   +`BAMLEMCBPIOAS` vs **riesgo país AR**. Si la compresión del riesgo país coincide
   con compresión del HY global → rally **"prestado"** (revierte con el mundo). Si
   comprime con spreads globales estables → **mérito local** (más sostenible). →
   decidir tomar ganancia o mantener.

5. **Credibilidad del plan de desinflación.** `T10YIE`/`T5YIE` vs **breakevens de
   inflación AR propios** (`engines/breakevens.py`, curva CER vs Lecap). El
   diferencial (breakeven AR − breakeven US) **aísla la inflación AR esperada por
   encima del mundo** — termómetro de credibilidad. Se comprime → creerle a la
   desinflación → rotar CER→tasa fija. Se abre → premio por CER/dólar-linked.
   **Paralelismo pedagógico:** mismo concepto de breakeven que la plataforma ya
   calcula, distinto país.

6. **Colchón de carry en pesos vs tasa USD libre de riesgo.** `DFEDTARU`/`DGS2` vs
   **tasas locales (TAMAR/caución) + MEP/CCL**. Carry en pesos en USD = tasa local −
   devaluación esperada, compite contra la tasa USD sin riesgo. Fed alta → el
   colchón se achica → menos incentivo a pesos. → **¿realmente paga quedarse en
   pesos?**

7. **Semáforo risk-off global → high-beta AR.** `VIXCLS`+`T10Y2Y` vs **paridad
   soberano AR**. Picos de VIX / inversión de curva son gatillo **anticipado** para
   reducir exposición antes del golpe en GD. Gestión top-down.

8. **Cadena China → Brasil → dólares de exportación AR.** `PCOPPUSDM`+`PIORECRUSDM`+
   `DEXBZUS` vs **soja/agro local + reservas**. Cobre/hierro y BRL débiles anticipan
   **con semanas** menos dólares de exportación AR → presión sobre CCL y reservas.

**Los 8 "reads" de la mañana** (widget de briefing, §7): real yield vs riesgo país ·
CCL vs pares EM · riesgo país vs crédito global · soja Chicago vs pizarra · VIX+curva ·
breakeven US vs AR · cobre+hierro+BRL · Fed vs carry en pesos.

**Nota de arquitectura de los cruces:** los datos propios (MEP/CCL, breakevens,
riesgo país, agro) **ya viven en la DB** (`valuaciones.dolar_snapshot`,
`macro.series_macro` clave RiesgoPais, `mercado.agro_*`, motor de breakevens). El
cruce es **un JOIN por fecha en el service SQL** — no requiere pegarle a nada nuevo.
Empezar por los cruces #1, #2 y #5 (los de mayor uso diario).

---

### 6. Arquitectura de sync

**La verdad del dato primero (REGLA #2):** FRED publica en **días hábiles USA**, con
frecuencias mixtas (diarias intradía-EOD, mensuales/trimestrales con rezago y
**revisiones retroactivas**). "Actualizado 24/7" real = **la vista sirve SIEMPRE de
nuestra DB** + un cron que sincroniza varias veces al día.

#### C.6.1 `core/fred_api.py` — el cliente (patrón `bcra_api`, +API key)

Copia exacta del molde `core/bcra_api.py`, con estas diferencias:

- `_BASE = "https://api.stlouisfed.org/fred"`, `file_type=json` inyectado en todo
  request, `api_key` leído de `os.environ["FRED_API_KEY"]` e inyectado en `_get`
  (nunca logueado — enmascarar en logs de error).
- `_MIN_INTERVALO_S = 0.6` (respeta 120/min con margen; centralizado con `threading.Lock`).
- Backoff en 429/5xx (idéntico a BCRA).
- **`"."` → `None`** al parsear observaciones (el gotcha #3). Contrato "nunca
  levanta" (`ErrorFRED`).
- Funciones:
  - `metadata(series_id) -> dict` (`/series` → title, frequency, units,
    seasonal_adjustment_short, observation_start/end, last_updated).
  - `observaciones(series_id, desde=None, hasta=None, units="lin") -> list[{fecha, valor}]`
    (`/series/observations`, pagina por offset, re-filtra client-side por fecha
    igual que BCRA). **Pide `units="lin"` (crudo)** — las transformaciones se hacen
    on-the-fly (§6.6).
  - `buscar(texto) -> list[dict]` (`/series/search`) — **solo para curar**, no
    en runtime.

#### C.6.2 Tablas (`sql/schema.sql`, schema `research`)

**OJO frecuencias mixtas:** `series_id` es **texto** (no `int` como el `idVariable`
del BCRA). La PK es `(series_id, fecha)`; la `fecha` de una mensual es el primer día
del período (convención FRED). La frecuencia se guarda en `fred_watch.freq` y en la
metadata → la UI sabe si graficar escalonado (M/Q) o continuo (D).

```sql
-- research.fred_series — espejo de metadata SOLO del watch (NO las 800k).
-- A diferencia de BCRA (catálogo chico de 1.581 que sí espejamos entero), el
-- catálogo FRED es inmanejable → esta tabla solo tiene metadata de las series
-- curadas, poblada vía /fred/series por cada id del watch.
CREATE TABLE IF NOT EXISTS research.fred_series (
    series_id        text PRIMARY KEY,
    title            text,
    frequency        text,        -- Daily / Weekly / Monthly / Quarterly
    units            text,        -- units de origen (ej. "Percent", "Index 2017=100")
    seasonal_adj     text,        -- SA / NSA
    observation_start date,
    observation_end   date,
    last_updated     timestamptz, -- de FRED (para detectar cambios / calendario)
    notes            text,
    actualizado_en   timestamptz NOT NULL DEFAULT now()
);

-- research.fred_watch — universo CURADO (bloque = sub-tab, orden, activo). Seed §4.
CREATE TABLE IF NOT EXISTS research.fred_watch (
    series_id text PRIMARY KEY,
    bloque    text NOT NULL,   -- tasas_usa / commodities / dolar_fx / riesgo_credito / ...
    etiqueta  text NOT NULL,   -- label corto para la UI ("UST 10Y", "Soja")
    unidad    text,            -- unidad DISPLAY nuestra ("%", "USD/t", "índice", "pp")
    freq      text,            -- D / W / M / Q (clave por frecuencias mixtas)
    pais      text,            -- EEUU / Global / China / Brasil / Mexico / Chile / EM
    orden     int,
    activo    boolean NOT NULL DEFAULT true
);

-- research.fred_observations — los puntos del watch (tidy, idempotente por PK).
CREATE TABLE IF NOT EXISTS research.fred_observations (
    series_id    text NOT NULL,
    fecha        date NOT NULL,
    valor        double precision,   -- NULL permitido (el "." de FRED)
    ingestado_en timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (series_id, fecha)
);
CREATE INDEX IF NOT EXISTS ix_fred_obs_id_fecha
    ON research.fred_observations (series_id, fecha DESC);
```

**Decisión "latest-only" (§2.5):** `fred_observations` guarda la **última revisión**
(sin `realtime_start/end`). Si se implementa ALFRED, se agrega una tabla aparte
`research.fred_vintages(series_id, fecha, vintage_date, valor, PK(series_id, fecha,
vintage_date))` — **carril separado**, no se mezcla con el latest (mezclarlos
corrompe el histórico).

#### C.6.3 `jobs/fred_research.py` — el sincronizador (patrón `bcra_research`)

Copia exacta del molde, con el `_SEED` de §4 (lista de tuplas `(bloque, series_id,
etiqueta, unidad, freq, pais, orden)`). Modos:

- `--dry-run`: universo + watermarks + qué haría (0 requests de series).
- `--backfill [--desde YYYY-MM-DD]`: historia de cada serie del watch (una vez;
  throttled; `--desde` para acotar — las diarias con 30 años son grandes).
- **(default) incremental por watermark:** `desde = max(fecha) − N días` por serie.
- Refresca `fred_series` (metadata, 1 request por serie del watch) 1×/corrida.
- `JobRunLogger` (estándar de la casa).
- `--purgar-antes YYYY-MM-DD` (limpieza one-off scopeada, con `--dry-run`).

#### C.6.4 El watermark con colchón (revisiones retroactivas)

Como FRED **revisa hacia atrás** (gotcha #8), el `desde` incremental resta un
colchón. **Colchón por frecuencia** (más fino que el −7d fijo de BCRA):

- Series **diarias** (tasas, FX, commodities D): `−10 días` (capturan la última
  semana hábil + revisiones menores).
- Series **mensuales/trimestrales** (CPI, PCE, GDP, payrolls): `−95 días` (≈3 meses
  / 1 trimestre — GDP y payrolls se revisan 2–3 publicaciones después).

Esto se lee de `fred_watch.freq`. Un run sin datos nuevos re-lee esas ventanas y
hace `ON CONFLICT DO UPDATE` (idempotente: si el valor no cambió, no pasa nada; si
FRED lo revisó, se corrige). Costo: ~48 requests, escrituras solo de lo que cambió.

#### C.6.5 Cron y costos reales

```
## deploy/crontab.txt — FRED research (tab Datos Internacionales)
0 12,16,20,23 * * 1-5   python -m jobs.fred_research   # ≈9/13/17/20 ART, L-V
```

4 pasadas L-V (FRED no publica findes). **Costo por corrida: ~48 requests de series
+ ~48 de metadata ≈ 100 requests**, muy por debajo del techo de 120/**min** (los
100 se reparten en ~60s por el throttle de 0.6s → ~1 min de corrida). **~400
requests/día** — irrelevante. El **backfill inicial** (~48 series, algunas diarias
con 30+ años) se corre una vez, throttled, **fuera de rueda** (REGLA #4) — con
`--desde 2010-01-01` alcanza para casi todo el valor de mesa y baja el peso.

**Regenerar `deploy/SISTEMA.md`** (`python -m scripts.gen_sistema`) y registrar el
cron en el test del Diagnóstico, en el mismo commit (igual que BCRA).

#### C.6.6 API interna + route handler Next

- **Router `api/routers/research_fred.py`**, prefijo **`/api/research-fred`**, gate
  `Depends(require_module("research"))`. Read-only, SQL puro. Endpoints:
  - `GET /bloques` → el watch agrupado por bloque (sub-tabs) con etiqueta/unidad/
    freq/país + rango de historia. Cacheado TTL 300.
  - `GET /series?ids=DGS10&ids=DGS2&desde=&hasta=&transform=` → batch (1 request por
    bloque). `transform` ∈ `nivel|yoy|idx100` (on-the-fly, §7). Default 12 meses.
  - `GET /cruces/{nombre}` (Fase 3) → cada cruce del §5 pre-armado (JOIN por fecha
    con la data propia). Ej. `/cruces/beta-soberano`, `/cruces/ccl-vs-pares`.
- **Service `api/services/research_fred_sql.py`** (patrón `research_bcra_sql.py`):
  `bloques()` cacheado + `series()` batch en UNA query (`WHERE series_id = ANY(%s)
  AND fecha BETWEEN ...`). **`series_id` es texto** → `ids: list[str]`.
- **Gotcha aprendido (BCRA):** prefijo nuevo ⇒ **route handler de Next**
  (`src/app/api/research-fred/[...path]/route.ts`) + entrada en `src/proxy.ts` +
  `dynamic="force-dynamic"` / `revalidate=0` / `Cache-Control:no-store`. Cubrir de
  entrada.
- **REGLA #1:** validar `python -c "from api.main import app"` antes de pushear.

---

### 7. Diseño de la tab frontend (`research-fred.tsx`)

Tab **"DATOS INTERNACIONALES"** en Research (junto a ARGENTINA · BCRA · RV
INTERNACIONAL). **Sub-tabs por bloque** (TASAS USA · COMMODITIES · DÓLAR/FX ·
RIESGO/CRÉDITO · INFLACIÓN USA · ACTIVIDAD USA · CHINA · BRASIL · LATAM · LIQUIDEZ),
lazy por sub-tab (keep-alive). Cada sub-tab reutiliza el patrón BCRA/1816:

- **Header único:** chips por serie (toggle on/off) + **valor de HOY inline** + selector
  de rango **3M / 6M / 1A / 5A / MAX** + **selector de transformación** (§ abajo).
- **Chart multi-serie** con el diseño existente; **escalonado** para M/Q, continuo
  para D (lo dice `freq`). Eje secundario cuando las unidades difieren (ej. VIX vs
  spread).

**Transformaciones on-the-fly (robadas de FRED graph):**
- **Nivel** (default, la serie cruda que guardamos).
- **YoY / % var. interanual** (`pc1`): la más usada para CPI/PCE/commodities.
  Computada en el service/cliente sobre el nivel guardado (no re-pedimos a FRED).
- **Índice base 100** en fecha t0 elegida: re-normaliza para **comparar
  trayectorias de escalas distintas** (ej. soja vs maíz vs trigo en un gráfico; o
  DXY vs BRL vs CLP). **Feature #1 para comparación visual.**

**Recession shading (NBER):** toggle default en los charts de EEUU — bandas grises
entre pico y valle usando `USREC`/`USRECD` (series auxiliares del watch). Para AR no
hay NBER pero se puede replicar el concepto con bandas de eventos locales
(cambios de gobierno, defaults, cepo) en Fase futura.

**Comparación país-vs-país / serie-vs-serie:** el bloque LATAM y CHINA/BRASIL
habilitan overlay directo (CPI México vs Chile vs Brasil; CNY vs BRL vs MXN). El
**Explorador** genérico (última sub-tab, opcional) permite elegir cualquier serie
del watch de cualquier bloque y compararlas — reutiliza el motor spread/percentil
del lab 1816.

**KPI tiles (briefing/HOME, Fase futura):** "single-value widget" tipo FRED
Dashboards — último UST10Y, VIX, soja, DXY como tarjetas para el briefing del día.
Encaja con el patrón `home.market_quotes`.

---

### 9. Decisiones tomadas / pendientes del user

**Tomadas (arquitectura):**
- ✅ Cliente propio `core/fred_api.py` (no lib externa), feed **separado** de
  `jobs/bcra.py`/`argentina_datos.py` — nada existente se toca.
- ✅ Universo **CURADO** (~48 series), jamás el catálogo (~800k). `fred_series` =
  metadata **solo del watch** (no espejo del catálogo entero, a diferencia de BCRA).
- ✅ **Latest-only** en v1 (vintages/ALFRED diferido, carril separado si se hace).
- ✅ `series_id` **texto** en la PK; **`freq` en el watch** por frecuencias mixtas;
  watermark con **colchón por frecuencia** (−10d diarias / −95d mensuales).
- ✅ Transformaciones (YoY / idx100) **on-the-fly** sobre nivel guardado; recession
  shading NBER como toggle. Gate `research` — **jamás invitado** (REGLA #8).
- ✅ Prefijo `/api/research-fred` + route handler Next + proxy desde el día 1.

**⏳ Pendientes del user (bloqueantes / de decisión):**
1. **`FRED_API_KEY` (BLOQUEANTE):** solo el user puede crear la cuenta en
   `fredaccount.stlouisfed.org/apikeys` y poner la key como **env var en el `.env`
   del Droplet**. Sin esto, Fase 0 en adelante no arranca. (Se pide UNA vez —
   REGLA #6 — y queda marcado PENDIENTE; el diseño avanza en lo que no depende de
   la key.)
2. **Validar el seed del watch (§4)** y los bloques/sub-tabs antes de Fase 1 (¿se
   quieren las ~48? ¿alguna afuera, alguna más? ¿`CBBTCUSD` sí/no?).
3. **Prioridad de los cruces (§5):** confirmar arrancar por #1/#2/#5 y **el orden
   de esta tab vs el resto del roadmap Research**.
4. **Ventana del backfill:** ¿historia completa o `--desde 2010-01-01` (más liviano,
   cubre el valor de mesa)?

---
