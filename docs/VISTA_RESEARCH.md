# Vista RESEARCH — integración 1816 (market data + research diario)

> **REGLA DE ESTE DOCUMENTO (leer antes de tocar nada):** es el doc MADRE de la
> vista Research. Documento VIVO: cada decisión tomada se asienta, cada cosa que
> se termina se mueve a "Hecho", cada credencial/pendiente queda marcado. Si se
> trabaja en algo de acá y no se actualiza este archivo en el mismo commit, el
> trabajo está incompleto. Cross-refs: la ingesta de mails vive en QuantAI P6
> (`docs/QUANTAI.md`), los fundamentals Refinitiv (otra cosa, en Renta Variable)

> **ESTADO (2026-07-17):** DISEÑO CONGELADO, construcción EN ESPERA de credenciales
> (API key de 1816 + creds IMAP — ver §10). Decisiones del user ya tomadas: (a) NO
> snapshot intradía → solo cierre diario; (b) el universo de series se define
> DESPUÉS de la Fase 0, con los créditos reales medidos en la mano. No arrancar a
> codear hasta que estén las credenciales.

---

## 1. Objetivo — qué es la vista Research

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

## 2. Decisiones tomadas (no re-litigar sin el user)

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
   destilado IA y full-text español). La vista los consume. Ver §6.
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
   destilado IA al ingestar se APAGÓ (queda opt-in con `--destilar`). La IA se
   consume SOLO cuando alguien pregunta (el copiloto, Nivel 2) — no "por gastar".

---

## 3. Los dos pilares de un vistazo

```
                            VISTA  RESEARCH  (/research, módulo `research`)
        ┌─────────────────────────────────────────┬──────────────────────────────────────┐
        │  A) MARKET DATA — API 1816                │  B) RESEARCH ESCRITO — mails 1816     │
        │                                           │                                       │
        │  curvas · instrumentos · indicadores      │  "El día en pocas líneas" (diario)    │
        │  SERIES HISTÓRICAS ★ · cálculo teórico     │  + reportes mensuales                 │
        │                                           │                                       │
        │  core/mercado_1816.py (cliente + token)   │  jobs/research_mail.py  (YA existe P6)│
        │  jobs/mercado_1816_series.py (diario)     │  → ia.research (crudo + destilado+FTS)│
        │  → research.mkt_1816_series (SQL)          │                                       │
        │  la vista lee SQL (0 créditos)            │  la vista lee SQL + FTS               │
        └─────────────────────────────────────────┴──────────────────────────────────────┘
```

---

## 4. PILAR A — Market Data (API 1816)

### 4.1 Autenticación

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

### 4.2 Créditos — el recurso escaso (diseño alrededor de esto)

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

### 4.3 Endpoints mapeados

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

### 4.4 Campos (glosario para la UI + el copiloto futuro)

`precioDirty` (precio con intereses corridos) · `precioClean` (sin corridos) ·
`paridad` (precio/valor técnico, decimal 0-1) · `tna`/`tea`/`tem` (tasas, decimal:
0.0925 = 9,25%) · `duration` (Macaulay, años) · `durationMod` (modificada,
sensibilidad a tasa) · `currentYield` · `spread` (margen TNA, decimal) ·
`valorTecnico` · `volumenMontoDiario`/`volumenNominalDiario` · `ultimaOperacion`
(timestamp) · `convencionTna` · `fuente`/`moneda`/`plazo`. **OJO escalas:** tasas y
paridad vienen en **fracción** (0.0925), no en %. Al persistir se guarda tal cual y
la UI/el copiloto formatean (mismo criterio que el resto del sistema).

### 4.4b Cobertura REAL del catálogo 1816 (medida 2026-07-18 — referencia para crecer)

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

### 4.5 Universo inicial (recomendado por 1816)

- **Bonares (USD, ley local):** AL29, AL30, AL35, AL41.
- **Globales (USD, ley NY):** GD29, GD30, GD35, GD38, GD41, GD46.
- **Hard Dollar / otros:** AE38, AO28, AO29.
- **Curvas para descubrir más:** Soberanos, Provinciales, Corporativos, BCRA, USD
  Linked, CER, Badlar, Tasa Fija, Duales, EUR Globales.
- El universo es **editable** (probable: en Manager, como el resto de catálogos) —
  arrancamos con estos ~13 y crece por demanda (cada ticker suma costo de series,
  por eso se cura).

### 4.6 Modelo de datos (tablas SQL nuevas — schema `research`)

Propuesta (a afinar al implementar, `sql/schema.sql` es la fuente):

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

### 4.7 Jobs (cron — `deploy/crontab.txt` es la fuente)

- `jobs/mercado_1816_series.py` — **diario post-cierre** (append del día nuevo,
  incremental por watermark `MAX(fecha)` por ticker/campo; idempotente). Modo
  `--backfill` scopeado (REGLA #4) para la carga inicial de 1 año. Este es el job
  central del pilar A.
- `jobs/mercado_1816_instrumentos.py` — refresco diario del catálogo (curvas +
  instrumentos), barato (1 créd c/u).
- `jobs/mercado_1816_snapshot.py` — **DESCARTADO por ahora** (decisión del user
  2026-07-17: alcanza con el cierre diario, sin intradía). El "hoy" se arma del
  último cierre. Se reevalúa solo si el user pide el vivo (~250k créditos/mes).

### 4.8 Cliente y capa de servicio

- `core/mercado_1816.py` — cliente puro: auth + cache de token + renovación en 401,
  balance de créditos, chequeo de presupuesto, request con retry (429/5xx), parseo.
  Contrato "nunca levanta" (devuelve None/estructura vacía). Patrón de los otros
  feeds de `core/` (eikon_live, finnhub, argentina_datos).
- `api/services/research_sql.py` — lectura SQL para la vista (series por ticker/
  campo/rango, snapshot de hoy, catálogo) + `api/services/research_1816_calc.py`
  para el proxy de la calculadora teórica (endpoint 6, on-demand, con presupuesto).

### 4.9 CASHFLOW (endpoint 8) — MEDIDO 2026-08-15

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
   manual.
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

### 4.9b El diag — cómo se volvió a medir todo esto

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

---

## 5. PILAR B — Research escrito (mails + reportes)

**Reusa P6 — YA construido, no se rehace.** Detalle vivo en `docs/QUANTAI.md` (P6).

### 5.1 Lo que ya existe

- `jobs/research_mail.py` (cron `*/30` 10-14 UTC L-V): lee la casilla por **IMAP
  readonly**, filtra por remitente, persiste el mail **CRUDO** en `ia.research`
  (fuente de verdad, citable; dedup por `Message-ID` → idempotente) + un
  **DESTILADO** del LLM `{resumen, temas[], hechos[]}` (tarea `research_destilar`,
  flash). Mail tratado como DATO hostil (anti prompt-injection). LLM caído → queda
  `destilado NULL` y el próximo run lo reintenta (el crudo nunca se pierde).
- Tabla `ia.research (fecha, fuente, asunto, message_id, cuerpo, destilado jsonb,
  destilado_modelo, created_at)` + **full-text español** (`to_tsvector('spanish',
  cuerpo)`) → "¿qué decía el research sobre el BCRA?" se responde con FTS (ADOPTAR
  YA: full-text antes que vectores, QuantAI).
- Maneja HTML de mail → texto plano, headers MIME, fecha ART.

### 5.2 Lo que falta para el pilar B

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

### 5.3 Qué muestra la vista (pilar B)

- **Timeline** de research por fecha (diario + mensual, separados), con el
  **destilado** arriba (resumen + temas + hechos con su número) y el **crudo**
  expandible (citable).
- **Buscador FTS** ("mostrame lo que dijeron sobre las Lelink / la licitación /
  el superávit") sobre el cuerpo.
- Marca de fuente/fecha; nada de IA presentado como dato verificado (data
  provenance, QuantAI).

---

## 6. Frontend — la vista Research

- **Nueva vista principal** `acaquant-web/src/app/research` + `research-view.tsx`,
  entrada en la nav (gate módulo `research`, oculta sin el módulo).
- **Layout propuesto (a diseñar CON el user, incremental):** dos secciones/tabs:
  - **MARKET DATA:** selector de ticker(s) + campos + rango → **gráfico de series**
    (la estrella) + tabla de indicadores de hoy + la calculadora teórica. Toggles de
    `fuente`/`plazo`/`moneda`.
  - **RESEARCH:** timeline de mails (diario/mensual) + buscador FTS + destilado.
- **Routes Next "live":** las que sirvan snapshot de hoy necesitan
  `dynamic="force-dynamic"` + `revalidate=0` + `Cache-Control:no-store`
  (patrón live-fallback del repo).
- **Gráficos:** reusar la infra de charts existente (curvas-chart / recharts) y la
  skill `dataviz` para el diseño.

---

## 7. RBAC — módulo `research`

Agregar el módulo nuevo (patrón de `api/CLAUDE.md`, igual que `back-office`):

1. Sumar `"research"` a `MODULES` (`core/roles.py`).
2. `("/api/research", "research")` en `ENDPOINT_MODULE_PREFIXES` (`api/auth.py`) →
   todo endpoint bajo `/api/research` nace default-deny.
3. Asignar el módulo a los roles en `manager.role_matrix` (o `DEFAULT_MATRIX`).
   **JAMÁS a `invitado`** (REGLA #8 — research propietario/pago).
4. El frontend filtra la nav por módulo (`/api/me`).

---

## 8. Mapa de archivos (nuevo vs reusado)

| Pieza | Archivo | Estado |
|---|---|---|
| Cliente API 1816 | `core/mercado_1816.py` | **nuevo** |
| Series diarias (writer) | `jobs/mercado_1816_series.py` | **nuevo** (con `--backfill`) |
| Catálogo (curvas/instrumentos) | `jobs/mercado_1816_instrumentos.py` | **nuevo** |
| Snapshot intradía | `jobs/mercado_1816_snapshot.py` | **nuevo, DIFERIDO** |
| Lectura SQL para la vista | `api/services/research_sql.py` | **nuevo** |
| Calculadora teórica (proxy) | `api/services/research_1816_calc.py` | **nuevo** |
| Router HTTP | `api/routers/research.py` | **nuevo** (`/api/research/*`) |
| Tablas SQL | `sql/schema.sql` (`research.mkt_1816_*`) | **nuevo** |
| Ingesta de mails | `jobs/research_mail.py` | **REUSA (P6)** |
| Tabla de mails | `ia.research` (+ columna `tipo`) | **REUSA + 1 columna** |
| Vista frontend | `acaquant-web/src/.../research-view.tsx` | **nuevo** |
| RBAC | `core/roles.py` + `api/auth.py` | **editar** (módulo `research`) |

---

## 9. Roadmap por fases (orden = dependencia)

- **Fase 0 — Fundación del pilar A.** `core/mercado_1816.py` (auth + token + balance
  + presupuesto) + smoke E2E contra la API real (necesita la API key). Tablas SQL.
  Log de créditos. → *entregable: "conectados y midiendo créditos".*
- **Fase 1 — SERIES (la prioridad).** `jobs/mercado_1816_series.py` (backfill 1 año
  scopeado + incremental diario) + `research_sql` + el gráfico de series en la vista.
  → *entregable: "las series históricas de 1816, en la app".*
- **Fase 2 — Catálogo + snapshot de hoy + calculadora teórica.** instrumentos/curvas
  + tabla de indicadores de hoy + el proxy de cálculo teórico on-demand.
- **Fase 3 — Pilar B (mails) en la vista. ✅ HECHO 2026-07-17 (Nivel 1)** — timeline
  + destilado + FTS en la mitad derecha de la vista. Ver Registro de construcción.
  Falta solo el `tipo` mensual real (se deriva del asunto por ahora) y que el user
  corra el ingest + tilde el módulo.
- **Fase 4 — Cruces y copiloto.** El número al lado de la narrativa; y un copiloto
  de la vista Research (el research destilado + las series como contexto) — encaja
  en el patrón del copiloto por vista (`docs/COPILOTO.md`). El paso 2 de P6
  (research → contexto del copiloto) aterriza acá.

---

## 10. Pendientes / credenciales del user (REGLA #6 — se piden UNA vez)

- **`MERCADO_1816_API_KEY`** — generarla en la webapp de 1816 (módulo `mercado`) →
  al `.env` del Droplet. Sin esto, el pilar A no arranca (Fase 0).
- **Credenciales IMAP** (pilar B, P6): `RESEARCH_IMAP_USER` /
  `RESEARCH_IMAP_PASSWORD` (app password) / `RESEARCH_MAIL_FROM`. Sin esto, el
  pilar B no ingesta (pero no bloquea el pilar A). **NOTA (user 2026-07-17): esto
  NUNCA se hizo ni se probó — es setup de cero, no un re-check. Ver la guía paso a
  paso en el Apéndice.**
- **Un mail MENSUAL real** de 1816 para ver si trae PDF adjunto (define §5.2.3).

---

## 11. Preguntas abiertas (para decidir CON el user antes de codear cada fase)

1. **Universo de series:** ~~¿arrancamos con los ~13 recomendados o una curva
   entera?~~ **DECIDIDO (user 2026-07-17): se define DESPUÉS de la Fase 0**, con el
   costo real de créditos medido (`GET /balance`) — se elige con números, no a ojo.
2. **Campos de series:** ¿los 10 sugeridos (precioDirty, paridad, tna, tea, duration,
   durationMod, currentYield, spread, valorTecnico, volumen) o un subset?
3. **Parámetros default:** `fuente` (byma/mae), `plazo` (0/1/2), `moneda` (ars para
   pesos, mep/ccl para HD). ¿Fijamos byma/1/ars y toggles en la UI?
4. **Rango del backfill:** ¿1 año (máximo por call) alcanza, o querés más profundidad
   (varios años = varios calls encadenados)?
5. **Snapshot intradía:** ~~¿lo querés o alcanza con el cierre?~~ **DECIDIDO (user
   2026-07-17): solo cierre diario, sin intradía.**
6. **Reportes mensuales:** ¿vienen en el cuerpo del mail o como PDF adjunto?

---

## 12. Principios que aplican (heredados de QuantAI / arquitectura)

- **Medir antes de afirmar (REGLA #2):** no se puede probar la API sin la key —
  todo número de créditos de acá es HIPÓTESIS hasta correr `GET /balance`. El
  primer smoke de Fase 0 valida shape + costo real.
- **Backfills scopeados/batcheados/idempotentes (REGLA #4):** el backfill de series
  es un scan pago — se hace por lotes (≤10 tickers), con throttle, fuera de rueda,
  midiendo créditos antes.
- **Sync watermark:** el incremental append por `MAX(fecha)` — re-correr no duplica
  ni re-paga (idempotente por PK).
- **Full-text antes que vectores** (mails); **la API es fuente del número**, la app
  no recalcula; **degradar con gracia** en cada capa; **data provenance** (IA marcada
  como IA). Ver `docs/QUANTAI.md` (principios de ingeniería).

---

## Apéndice — Guía práctica para cargar las credenciales (paso a paso)

> Escrito porque el user nunca hizo el app password de Gmail. Todo esto lo hace el
> user (Claude no tiene acceso al Droplet ni a las cuentas — REGLA #0).

### A) Editar el `.env` del Droplet (donde van TODAS las keys)

En la consola del Droplet (o por SSH), el archivo vive en `/root/TradingAV/.env`.
Se abre con el editor `nano`:

```bash
nano /root/TradingAV/.env
```

Al final del archivo, agregar cada credencial en una línea, formato **`CLAVE=valor`
SIN espacios alrededor del `=` y SIN comillas** (una línea mal formada la ignora en
silencio — de hecho hay un warning viejo de "line 33" por esto, conviene revisarla
de paso):

```
MERCADO_1816_API_KEY=pegar_aca_la_api_key_de_1816
RESEARCH_IMAP_USER=lacasilla@gmail.com
RESEARCH_IMAP_PASSWORD=abcdefghijklmnop
RESEARCH_MAIL_FROM=1816.com.ar
```

Guardar y salir de nano: **Ctrl+O** → **Enter** (guarda) → **Ctrl+X** (sale).

Después:
- La **API key** la usan jobs/scripts al correr (`load_dotenv` la lee sola en el
  próximo `python -m ...`). Si algún endpoint de la API la necesita, reiniciar:
  `systemctl restart api.service`.
- Las creds **IMAP** las usa `jobs/research_mail.py` — no hace falta reiniciar
  nada, se leen en la próxima corrida.

### B) App password de Gmail — paso a paso (esto es lo que nunca hiciste)

Un "app password" es una **contraseña de 16 letras que Google genera SOLO para que
un programa entre a tu correo**, sin usar tu contraseña real ni pedirte el código de
2 pasos cada vez. Es más seguro (la podés revocar cuando quieras) y es la ÚNICA
forma de que un script lea Gmail por IMAP.

**Requisito previo:** la cuenta tiene que tener la **Verificación en 2 pasos (2FA)
ACTIVADA** — sin eso, Google no te deja crear app passwords.

Pasos (en la cuenta de Gmail que va a RECIBIR los mails de 1816):

1. Entrá a **myaccount.google.com** → **Seguridad**.
2. Si no está, activá **"Verificación en 2 pasos"** (te pide el celular una vez).
3. Buscá **"Contraseñas de aplicaciones"** (o andá directo a
   **myaccount.google.com/apppasswords**).
4. Poné un nombre cualquiera (ej. `acaquant research`) y **Crear**.
5. Google muestra una clave de **16 letras** en 4 bloques (ej. `abcd efgh ijkl
   mnop`). **Copiala SIN los espacios** → esa es `RESEARCH_IMAP_PASSWORD`.
   (Se muestra UNA sola vez; si la perdés, borrás esa y creás otra.)
6. En **Gmail → Configuración (⚙) → Ver toda la configuración → Reenvío y correo
   POP/IMAP → Habilitar IMAP → Guardar cambios.** (Sin esto, el IMAP no entra.)

**Cómo llegan los mails de 1816 a esa casilla:** los recibís en tu mail corporativo
(`nicolas.mollo@acavalores.com.ar`). Dos opciones:
- **(recomendada, la más simple):** en el corporativo, creá una **regla de reenvío
  automático** de los mails de `research@1816.com.ar` hacia esa cuenta de Gmail.
  Así el script lee Gmail y listo. → `RESEARCH_IMAP_USER` = esa Gmail.
- **(alternativa):** leer el corporativo directo por IMAP. `acavalores.com.ar`
  probablemente es Microsoft 365 (Outlook) → el host sería `outlook.office365.com`
  (`RESEARCH_IMAP_HOST`) y el "app password" se saca del portal de Microsoft, no de
  Google. Es más enroscado — si podés, andá por la de Gmail.

**Probarlo (sin gastar tokens ni escribir nada):**

```bash
cd /root/TradingAV && python -m jobs.research_mail --dry-run
```

Eso lista qué mails de 1816 ve y cuáles ingestaría. Si dice "faltan env vars" →
alguna línea del `.env` quedó mal escrita. Si lista los mails → está andando.

---

## Registro de construcción (con fecha — qué y cómo)

### 2026-08-15 (17) — Corrida real del diag: el universo medido y un bug propio
El user corrió el diag en el Droplet (114 créditos). Los números están en §4.9;
acá va lo que la corrida CAMBIÓ del código:
- **Bug del propio diag**: cruzaba por `fechaPagoTeorica` y nuestro master guarda
  la **efectiva** (1816 manda las dos; difieren cuando la teórica cae en no-hábil).
  En AE38 eso reportaba 8 "divergencias" de 2-3 días que eran puro calendario.
  Ahora MIDE cuál de las dos matchea más y compara por esa (efectiva 21/23 vs
  teórica 13/23) — no se elige una a dedo, se cuenta.
- **El ratio se imprimía solo del interés**, así que un bono con cupón cero
  (AER9O) quedaba sin contraste justo donde había una divergencia real del 3,1%.
  Ahora salen los dos ratios y se comparan TODOS los cupones comunes con
  tolerancia del 1%, reportando cuántos divergen y cuál es el peor.
- **El cruce estaba tapado**: 2.022 "míos" contra 887 de 1816 no compara nada —
  la mayoría de `portafolio.assets` son pagarés `#UAC…`, FCI y cauciones, que no
  son universo de 1816. Ahora separa por ORIGEN y saca la lista que sí importa:
  la renta fija de `mercado.curvas` que 1816 no tiene.
- **La nota de dedup se volvió condicional**: medido, suma por curva = tickers
  únicos = 887, o sea que NINGÚN ticker se publica en dos curvas. El texto que
  explicaba la diferencia ahora solo aparece si la hay.

### 2026-08-15 (16) — Endpoint CASHFLOW: cliente + diag de censo y cruce
Pedido del user: probar el endpoint NUEVO `/v1/mercado/cashflow/{ticker}` y, sobre
todo, **saber de cuántos tickers estamos hablando** (el rango real del catálogo).
- **Cliente** (`core/mercado_1816.py`): `cashflow(ticker, campos)` — el ticker va
  en el PATH y `campos` es OBLIGATORIO para la API (sin él, 400), así que el
  cliente manda los cinco por default y valida el mínimo de 3 caracteres antes de
  gastar el request. `instrumentos()` acepta ahora `solo_performing` — es lo que
  destapa los VENCIDOS (`soloPerforming=false`), que el default de la API esconde.
- **Diag** (`scripts/diag_1816_cashflow.py`, READ-ONLY): censo del universo curva
  por curva (con dedup: un ticker publicado en dos curvas se contaba dos veces —
  las patas de los duales, §4.4b), cruce contra `mercado.curvas` +
  `portafolio.assets`, y prueba del cashflow contrastada cupón a cupón contra
  nuestros `flujos`. Mide el balance de créditos ANTES y DESPUÉS de cada bloque.
  `--json`/`--desde` guardan y reusan el censo para no re-pagarlo.
- **Criterio**: no se persiste nada ni se toca el watch hasta tener medidos los
  cuatro números de §4.9 (cobertura, escala, horizonte, costo). El relevamiento de
  §4.4b (869 instrumentos en 28 curvas) es del 2026-07-18 y **no consta si estaba
  dedupeado**; el diag informa los DOS números (suma por curva y tickers únicos)
  para que la diferencia quede a la vista y §4.4b se pueda actualizar con el dato
  medido, no con el heredado.
- **Tests**: `tests/unit/test_mercado_1816.py` congela el armado del path/params
  del cashflow y el flag `soloPerforming` (6 tests, sin red). Ruff limpio.

### 2026-08-07 (15) — RV Internacional: FUNDAMENTALS en 4 cuadrantes + rubro
Etapa 1 del pedido del user sobre Research (doc completo del cambio y del
discovery de segmentos: **`docs/INTEGRACION_REUTERS.md` §8, entrada v4**).
- **RUBRO como filtro también en FUNDAMENTALS** (el tablero de COTIZACIONES ya
  lo tenía desde 2026-07-18): `tablero_fundamentals()` resuelve el rubro del
  catálogo propio y la sub-vista suma columna + dropdown que filtra los 4
  paneles a la vez.
- **La sub-vista FUNDAMENTALS pasó de una tabla sola a 2×2 de 50%**: SCREENER
  (columnas curadas) · AGREGADO (el universo SUMADO en el tiempo, anual o
  trimestral, endpoint nuevo `GET /reuters/fundamentals/agregado`) · DISPERSIÓN
  (scatter de ejes elegibles, default P/E vs. margen neto) · COMPOSICIÓN POR
  RUBRO. Motivo: la tabla contestaba cómo está UNA empresa, no cómo está el
  conjunto — que es la pregunta de research.
- El agregado se calcula SERVER-SIDE con canasta constante y alineación por
  calendario (ver el detalle y los tests en INTEGRACION_REUTERS §8).
- **Feed**: `FUND_SERIE_USD` suma utilidad bruta / EBIT / capex — hay que
  regenerar la copia del Desktop para que esas 3 series se llenen.

### 2026-07-20 (14) — COPILOTO de Research (el "Nivel 2" de §2.7, HECHO)
La IA on-demand que este doc dejó anotada. Decisión del user: **UN copiloto
para toda /research que ve las 4 fuentes JUNTAS, siempre** ("el research tiene
que saber de todo") — la tab activa viaja solo como señal de prioridad, no
filtra (RV INTERNACIONAL queda con su vista `reuters` propia). Vista `research`
en `api/services/copiloto/research.py` (módulo RBAC `research`, gate `ia`):
- Tabla = concatenación con columna `fuente`: watch 1816 (último TEA%/paridad%/
  precio/duration + cambios de TEA 7/30d en pp) · BCRA/FRED (último valor +
  diferencia 7/30d) · reportes (ficha + comentario; el PDF no se lee). Readers
  EOD con @cached(300).
- **Mails citables con fecha**: el mail más reciente SIEMPRE + FTS determinista
  (`buscar_research`, índice GIN existente) con los términos de la pregunta —
  el modelo solo puede citar fragmentos que el código trajo. Cero tokens extra
  de ingesta: consistente con "la IA no interviene sola".
- Detalle completo y changelog: `docs/COPILOTO.md` v1.58.

### 2026-07-18 (13) — el research del día en el BRIEFING (pedido del user)
El modal del briefing de las 10:00 (HOME) ahora muestra, **solo si HOY llegó el
mail de 1816**, un panel lateral "📰 RESEARCH DEL DÍA" con el texto completo tal
cual (limpio, la misma limpieza de la vista Reportes), con **scroll propio** —
el modal se ensancha solo en ese caso; sin mail de hoy queda EXACTAMENTE como
siempre. Backend: `briefing.py::_research_hoy` (query por fecha=hoy, failing
gracefully) suma la key `research_hoy` al payload; el copiloto NO la consume
(lee campos específicos — cero tokens, consistente con "la IA no interviene
sola"). Frontend: aside en `briefing-modal.tsx` (oculto en pantallas chicas).
Excepción P1 asentada en QUANTAI.

### 2026-07-18 (12) — sondeo con fecha hábil OK → plan por NIVELES + Nivel 1 implementado
El sondeo re-corrido con `fechaOperacion=2026-07-17` (auto): **323/869 con dato
fresco · 263 frescos fuera del watch · 166 frescos que ni están en mercado.curvas**.
- **Caveat REGLA #2:** el sondeo corrió `moneda=ars` — las ONs hard-dollar
  (BACAO, AFCHO, BF39O…) figuran "sin dato" probablemente porque operan en
  MEP/CCL → el diag ganó `--moneda mep|ccl` para re-sondear antes de darlas por
  muertas. El "546 sin dato" está inflado por esto.
- **Plan por NIVELES (asentado con el user):**
  - **Nivel 1 (DECIDIDO por el user y acotado): BOPREALes + GD46** —
    `_EXTRA_WATCH` en el discovery (lista curada versionada en el repo — editar
    + `--apply` = el mecanismo del "día a día"): BPOA7/A8, BPOB7/B8, BPOD7 y
    GD46 (~5k créditos de backfill). **Las 7 ONs nuestras que operan (AER9O,
    AERBO, AFCIO, BACGO, BYCWO, ZPC3O, ZZC1O) quedaron AFUERA por decisión del
    user** — siguen acá como opción para cuando se quiera su historia.
  - **Aclaración conceptual asentada (pregunta del user): el watch de 1816 y
    `mercado.curvas` son RIELES SEPARADOS.** Watch = solo la historia de 1816
    para el laboratorio (sin flujos, sin Manager, sin motor). Renta Fija propia
    = alta en Manager → Títulos → Bonos con cronograma (TEA propia, live,
    acreencias). Sumar a uno NO suma al otro. Estado hoy: BPOA7/B7/B8/C7/D7 ya
    están en curvas; **GD46 y BPOA8 NO están en la Renta Fija propia** —
    si se los quiere ahí, es un alta en Manager (decisión aparte).
  - **Nivel 2 (propuesto, espera OK del user):** ~10 provinciales líquidos
    (BA37D, BB37D, BC37D, BDC33, BDC36, SA24D, PBA27, BAF27, BDC28, BL2S6) →
    spread de crédito subsoberano vs AL30.
  - **Nivel 3 (a demanda):** corporativos masivos y duales por pata.
- **Perlas del relevamiento:** GD46 opera y no estaba NI en mercado.curvas;
  TZXA7/TZXD8/TZXM8/TZXO7 (CER soberanos frescos) tampoco están en la vista RF
  propia — candidato a alta vía Manager (decisión aparte del user).

### 2026-07-18 (11) — perf con números de PROD + cobertura 1816 consolidada
- **diag_sql_perf extendido corrido en prod.** Lectura: RTT ~26ms (session pooler
  OK — el ⚠ era un falso positivo de parseo, arreglado 2×), cache amortiguando
  todo (CACHE≈0), queries nuevas de Research TODAS con índice y sub-ms.
  **Finding real y FIX aplicado:** el tablero Reuters costaba ~68ms SIN cache y
  el front lo pollea cada 5s POR USUARIO → cache compartido `@cached(4s)` en el
  router (+ fundamentals 60s): N usuarios ahora comparten UNA query. Pendiente
  de vigilar: `negocio_movimientos` por cuenta (86-107ms) — se decide con
  pg_stat_statements si aparece en flujos frecuentes.
- **Cobertura 1816 consolidada en §4.4b** (del relevamiento --todas: 869
  instrumentos / 28 curvas; duales desdoblados por pata, BOPREALes, EUR
  globales, nuestras ONs en corporativos, provinciales). Criterio del user:
  MAPA sí, ingesta masiva no — el watch crece a demanda en el día a día.
  (El scratch `docs/DATOS1816.txt` queda consolidado acá — borrable, REGLA #5.)

### 2026-07-18 (10) — cobertura 1816 sin sesgo · forwards siempre-con-par · fixes BCRA · pasada de perf
1. **`scripts/diag_1816_cobertura.py`** (pedido del user: "saber de qué
   instrumentos 1816 devuelve datos, SIN sesgar por los míos"): enumera TODO el
   catálogo de 1816 por curva (default soberanas+BCRA; `--todas` = las 33 con
   confirmación de gasto) y sondea `/indicadores` (ultimaOperacion+precioDirty,
   2 créditos/instrumento) → reporta con dato/fresco/sin dato, cruzado con
   `mercado.curvas` y el watch como INFO (no filtra). El cliente 1816 ganó
   `indicadores()`. Con la salida se decide qué sumar al watch.
2. **Tab renombrada: ARGENTINA → RENTA FIJA ARGENTINA.**
3. **Forwards: siempre hay cruce** — el selector B solo ofrece los pares CON
   datos para el A elegido (`paresConDatos` de la historia cargada; A cambia →
   B se auto-corrige) y el rango default pasó a **Máx**. Chau "sin historia
   para ese par".
4. **BCRA:** eje Y de Reservas ya no dice "k" cuando son millones (M USD/M ARS:
   número completo es-AR; ≥1e6 → "B") · Tipo de cambio SIN abreviar · **CER &
   UVA e INFLACIÓN en split 50/50** (un chart por serie — bases/escalas
   distintas) · **REM (id 29) RETIRADO** de Inflación: `_RETIRADAS` en el job
   se aplica SIEMPRE (desactiva en el watch aunque ya esté sembrado en prod).
5. **Perf (item 4 del user — primera pasada):** `perf_scan --strict` = sin
   findings · `listar_research` ganó `@cached(60s)` (cada visita a /research
   re-leía y re-limpiaba 30 cuerpos por SSR; los mails cambian 1×/día) · lo
   nuevo ya nació frugal (bloques cacheados 300s, series batch por bloque
   indexadas por PK, lazy+keep-alive, la vista BCRA jamás toca la API del
   BCRA). **El "análisis mega-pro" de TODA la página necesita datos de PROD**
   (REGLA #2): pg_stat de OBSERVABILIDAD→BASE + `scripts/diag_sql_perf.py` en
   el Droplet — planificado como próxima sesión con esas salidas en la mano.

### 2026-07-18 (9) — texto legible + FORWARDS en el cuadrante libre + doc BCRA
1. **Color de los reportes:** el cuerpo pasó de `--t-text-muted` (gris ilegible
   en ambos temas — feedback del user) a `--t-text`; el titular de cada item va
   en **bold + acento** (se distingue por peso, no por gris).
2. **Cuadrante libre (superior-derecho de Argentina) = FORWARDS HISTÓRICO**
   (`research-forwards.tsx`): la serie del forward implícito entre dos bonos —
   el gráfico que vive dentro de Forwards en Renta Fija — con el diseño de los
   otros charts (header único: título + curva tasa_fija/CER + par A→B + rango +
   "hoy/media" inline). Fuente: `GET /api/cotizaciones/historico/forwards`
   (endpoint YA existente, `mercado.mercado_hist`; matrix en fracción → ×100,
   misma convención que forwards-panel). Cero backend nuevo.
3. **Doc nuevo `docs/RESEARCH_BCRA.md`** — análisis exhaustivo de la futura tab
   BCRA (pedido del user): las 3 APIs verificadas contra la API VIVA (catálogo
   v4 = 1.581 variables con shape completo; serie id 1 = 7.515 puntos, rezago
   1-2 días hábiles; maestro cambiario = 43 divisas incl. oro), decisión v3
   redundante → solo v4+cambiarias, universo curado ~40 series, 4 cuadrantes
   propuestos con cruces propios (brecha A3500-MEP, tasas reales, IPC vs REM vs
   breakevens, depósitos USD), arquitectura de sync 24/7 (DB-first + cron 4×/día,
   watermark con re-lectura 7d) y fases. NO pisa `jobs/bcra.py` (motores).

### 2026-07-18 (8) — pulido de los cuadrantes + RUBRO/CCL en RV Internacional
Tres pedidos del user:
1. **Header único en los charts de Argentina** (`research-lab.tsx`): la selección
   A−B / chips y las stats (hoy · pct · z · rango · zona) pasaron AL MISMO NIVEL
   que el título "Spread A−B"/"Comparar" (una sola barra flex-wrap, antes eran 3)
   → más alto para el gráfico en los cuadrantes.
2. **Reportes: acordeón TODO CERRADO por defecto** (antes el más reciente arrancaba
   abierto) — solo fecha+título; se abre al click.
3. **RV Internacional:** columna **CCL movida al lado de ÚLTIMO** (grupo PRECIO),
   columna **RUBRO** nueva (del catálogo `mercado.cedears`, join agregado en
   `tablero_reuters()` de `core/eikon_live.py`) + **filtro por rubro** (dropdown
   en la toolbar). El copiloto de la vista también ve `rubro` (COPILOTO v1.56).

### 2026-07-18 (7) — TABS (Argentina · RV Internacional) + cuadrantes + REUTERS movido
Reestructura de la vista (pedido del user):
- **Tabs keep-alive** (patrón trading-shell): **ARGENTINA** y **RENTA VARIABLE
  INTERNACIONAL**.
- **Tab Argentina = 4 cuadrantes 50/50**: superior-izq **Spread A−B** ·
  inferior-izq **Comparar** (el lab se partió en dos paneles vía prop `modoFijo`
  en `research-lab.tsx`) · inferior-der **Reportes** (acordeón 1816) ·
  superior-der **LIBRE** (placeholder "próximo módulo").
- **Tab RV Internacional = el tablero REUTERS movido desde /trading** (el
  `ReutersView` completo con su copiloto in-view; `trading-shell` quedó con
  PIVOTS + INTRADAY, y los usuarios con `trading.tab=reuters` persistido caen a
  pivots). **Nuance RBAC RESUELTA (mismo día, directiva del user: "TRADING solo
  admin; RESEARCH para todos"):** los endpoints del tablero se MUDARON de
  `/api/trading/reuters*` a **`/api/research1816/reuters*`** (gate `research`) y
  la vista `reuters` del copiloto pasó a módulo `research` (COPILOTO v1.55). Los
  4 fetches del frontend actualizados (incluido el poll de trading-view, que es
  admin-only y admin tiene research). Backend del feed intacto.
- **Breakevens (vista Renta Fija) arreglado** (item 3 del pedido, misma tanda):
  el eje Y FORZABA el 3% dentro del dominio → con BEs en 1.5-2% las curvas quedaban
  aplastadas con media pantalla vacía (img_25). Ahora la escala se ajusta a los
  datos (la línea del 3% se dibuja solo si cae en el rango visible) + **leyenda**
  (BE mercado / REM mensual / REM prom. acum. — antes no se sabía qué línea era
  qué) + grilla horizontal + divisor tabla↔gráfico + colores consistentes con el
  lab (#e0803c / #3a9bd5).

### 2026-07-18 (6) — diseño temático (claro/oscuro) del laboratorio y Reportes
Selects con fondo `--t-surface` (se terminó el select BLANCO en modo oscuro),
paneles delineados `--t-panel` + rounded-lg, controles agrupados (segmented con
`--t-surface`), stats bar y tarjetas de reporte con fondo de panel, colores de
línea fijos legibles en ambos temas, tooltip/ejes con vars del tema. Toma el
sistema de `globals.css` (mismo patrón que `_onInput` y las superficies del resto
de la app). Regla: usar SIEMPRE las vars `--t-*`, nunca `bg-transparent` en inputs.

### 2026-07-18 (5) — el 404 del laboratorio: faltaba el route handler de Next
La vista tiraba "Cargando universo… / HTTP 404" aunque el backend tenía las rutas y
la query andaba (diag OK). **Causa (GOTCHA a recordar): acaquant-web NO proxea
`/api/*` con un rewrite general — tiene un route handler de Next POR PREFIJO
(`src/app/api/<x>/[...path]/route.ts`).** Los mails cargaban por SSR (`apiFetch`
server-side, sin handler), pero los fetches del BROWSER (`/universo /series
/spread`) caían en el 404 de Next antes de llegar al backend. Fix:
`src/app/api/research1816/[...path]/route.ts` (catch-all que propaga la identidad
para el gate `require_module("research")`, mismo patrón que `/api/ia/[...path]`).
**Regla para el futuro:** todo endpoint nuevo que el browser consuma bajo un prefijo
nuevo necesita su route handler en acaquant-web.

### 2026-07-18 (4) — Fase 1: LABORATORIO de spreads/valor relativo CONSTRUIDO
Backfill desde 2026 OK (18.408 puntos nuevos, 60 bonos con historia en la base).
Con eso, la mitad izquierda de RESEARCH dejó de ser placeholder y es el laboratorio:
- **Backend `api/services/research_1816_sql.py`** (lee `research.mkt_1816_series`,
  0 créditos): `universo()` (bonos con series, agrupados por curva, para los
  selectores) · `series()` (overlay: N bonos, un campo) · `spread()` (serie A−B en
  el tiempo + **stats de valor relativo**: percentil/z/mín/máx del spread de HOY vs
  su propia historia). Endpoints en `research1816.py`: `/universo /series /spread`.
- **Frontend `research-lab.tsx`** (mitad izquierda): dos modos — **Spread A−B**
  (default: AL30−GD30, la prima de legislación) con la lectura "ancho/angosto vs su
  historia" (percentil), y **Comparar** (overlay de hasta 8 series). Selector de
  bono por curva, campo (TEA/Paridad/Precio/Duration), rango (1M/3M/6M/Máx),
  gráfico recharts con línea de media y cero. tea/paridad se muestran en %.
- import-chain OK (316 rutas), ruff limpio, typecheck OK.
- **Pendiente:** deploy (`git pull` + restart api) + tildar módulo `research`.
  Afinar con el user (colores, defaults, percentil como banda). Ideas siguientes:
  evolución de curva (fecha X vs Y), sumar provinciales/corporativos (spread crédito).

### 2026-07-18 (3) — universo desde TUS bonos (no hardcodeado) + backfill OK + observabilidad
- **Backfill 1 año OK:** 13.790 puntos en `research.mkt_1816_series` (fix del rango
  a 364 días — 1816 rechaza >1 año). Auth+throttle andan (sin 429 con el espaciado).
- **[universo NO hardcodeado — pedido del user]** `jobs/mercado_1816_discovery.py`:
  cruza TUS bonos de `mercado.curvas` (los no-ON, vía `curvas_sql.por_curva_not_like
  ('on%')`) contra el catálogo REAL de 1816 (`/instrumentos` por curva soberana) →
  solo entran los que 1816 tiene, con su ticker canónico (normaliza especie AL30D→
  AL30). Popula `mkt_1816_watch` (el seed hardcodeado queda solo de fallback si el
  watch está vacío). Dry-run muestra match + tuyos-sin-match (transparente).
  **PENDIENTE del user:** `discovery --dry-run` → `--apply` → re-`series --backfill`
  para bajar la historia de TODO el universo real.
  - **Resultado del cruce (2026-07-18): 59/60 de tus bonos matchearon** (de 13
    hardcodeados a 60 reales). El único afuera era BPOC7 (BOPREAL) → se sumó la
    curva BCRA (id 24) al discovery → ahora 60/60.
  - **Ajuste de créditos:** 60 bonos × 6 campos × 364d = 131k > límite diario (100k).
    → campos bajados a **4** (tea/paridad/precioClean/duration; tea es lo que manda
    para spreads).
  - **Ventana del backfill = desde el 1-ene del año en curso (decisión del user
    2026-07-18: "no hace falta un año, desde 2026").** Default `--backfill` arranca
    en enero (`--desde YYYY-MM-DD` para override; cap de 1 año por la API). 60×4×
    ~199d ≈ 48k → entra cómodo. **Resumible por RANGO** (`_ya_backfilleados`:
    salta los tickers cuya historia ya llega a `desde` vía min(fecha) — así los 13
    del primer backfill se saltan y solo baja los ~47 nuevos, sin re-pagar; si se
    corta por el límite, se continúa al otro día).
- **[observabilidad — ya figura sola]** `db_obs.py` (Manager → OBSERVABILIDAD →
  BASE) escanea TODOS los schemas/tablas → `research.mkt_1816_*` aparece
  automáticamente (schema `research` + frescura por `ingestado_en`). Peso: 13.790
  filas ≈ ~2-3 MB (despreciable vs el límite). Nada que agregar.

### 2026-07-18 (2) — Fase 0 + arranque Fase 1: motor de datos 1816 CONSTRUIDO
Alma decidida por el user: **laboratorio de spreads / valor relativo** (series
multi-activo + A−B en el tiempo + percentil histórico). Motor de datos hecho:
- **`core/mercado_1816.py`** — cliente: auth (token 24h cacheado, re-auth en 401),
  **THROTTLE (2,5s entre calls) + BACKOFF exponencial en 429** (la restricción real),
  `balance/curvas/instrumentos/series` + `parse_series()` puro (aplana
  `instrumentos.<tk>.<campo>=[[fecha,valor]]` → tidy). Env `MERCADO_1816_API_KEY`.
- **Tablas `research.mkt_1816_*`** (schema.sql): `series` (tidy EOD, PK por
  ticker×fecha×campo×fuente×moneda×plazo → idempotente), `watch` (universo curado,
  seed soberanos+HD si vacío), `instrumentos` (catálogo). Feed SEPARADO de
  mercado.curvas — no se pisan.
- **`jobs/mercado_1816_series.py`** — `--backfill` (1 año, una vez, batcheado ≤10
  tickers, chequeo de créditos antes — REGLA #4) + diario (últimos 7d, idempotente).
  Cron `0 22 * * 1-5` (post-cierre). SISTEMA.md regenerado.
- **Tests** del parseo puro (`test_mercado_1816.py`). ruff + imports OK.
- **PENDIENTE del user (Droplet):** `apply_schema` (crea las tablas) → correr el
  backfill UNA vez: `python -m jobs.mercado_1816_series --backfill` (o `--dry-run`
  primero para ver el costo). Después el cron mantiene solo. Luego: la lectura SQL
  + la vista (Fase 1 frontend — el laboratorio de spreads).

### 2026-07-18 — Fase 0 arrancada: diag REAL contra la API (verificado, ya no hipótesis)
`scripts/diag_1816.py` corrido con la key real. **Hechos (REGLA #2):**
- **Base URL: `https://api.1816.com.ar` ✓.** Auth OK (token 24h, user
  `acavalores@1816.com.ar`, project mercado). Balance OK: hoy 263/100.000, mes
  264/3.100.000 → créditos SOBRAN.
- **RATE LIMIT DURO (HTTP 429 "Demasiadas solicitudes") al encadenar llamadas.**
  Las 6 llamadas seguidas cayeron; series (espaciada) pasó. **Shapea el diseño:**
  todo pull va con THROTTLE (sleep entre calls), batcheado y por cron — NADA de
  hammering. El header `x-1816-credits` no vino (los créditos se leen del balance).
- **`/series` ANDA y es la joya.** Shape: `instrumentos.<ticker>.<campo> =
  [[fecha, valor], …]` (tidy, EOD diario). TNA/TEA en FRACCIÓN (0.0879). OJO:
  `precioDirty` CAE en la fecha de cupón (AL30: 98.480 el 07-07 → 85.600 el 07-08,
  pagó el 07-09) → para COMPARAR usar paridad/TEA/precioClean, no dirty.
- **Decisión de arquitectura (ver análisis 2026-07-18 abajo / mensaje):** el módulo
  es el LENTE HISTÓRICO/COMPARATIVO (series multi-activo + spreads en el tiempo),
  NO otro "renta fija de hoy" (eso ya existe y NO se pisa). Diferencial de 1816:
  historia profunda y consistente + BREADTH (provinciales/corporativos → spreads
  de crédito que nuestra data no tiene).

### 2026-07-17 (4) — pulido + decisión del layout Market Data
- **[frontend]** Sacado el bloque "Resumen IA" de la card (pedido del user).
- **[backend]** El limpiador ahora saca TODO tipo de link (inline, `[imagen]`,
  `(url)`, `<url>`, `www.`) sin comerse el punto final de la frase (para no fundir
  párrafos). Test con link inline.
- **[layout izquierdo — REVERTIDO, exploramos primero]** Se probó un scaffold de
  dos gráficos comparables en la izquierda; el user lo REVIRTIÓ: primero exploramos
  qué devuelve la API de 1816 y DESPUÉS modelamos el lado izquierdo. Por ahora el
  50/50 simple (Market Data placeholder | Reportes). La idea de "dos series
  comparables" queda anotada como candidata, sin construir.
- **[herramienta]** `scripts/diag_1816.py`: explora TODOS los endpoints de 1816
  (auth → balance → curvas → instrumentos → indicadores → teórico → series),
  imprime status + créditos + respuesta de cada uno. Es el primer paso de la Fase 0
  cuando llegue la API key — con esa salida decidimos qué modelar.

### 2026-07-17 (3) — rediseño de la vista (feedback del user sobre la 1ª versión)
La 1ª versión mostraba todo apilado y con basura del mail. Rediseño:
- **Título derecho = "REPORTES"** (uno solo) con **selector de FUENTE** estilo News
  (1816, y a futuro ACA VALORES, etc.) — `_fuente_label()` detecta la fuente por
  contenido (no por el From, que es el que reenvía). Filtra por la fuente elegida.
- **Acordeón:** cada reporte es una fila [▶ fecha · tipo · título]; se **clickea y
  se abre** el contenido (arranca abierto el más reciente). Se terminó el "todo
  uno abajo del otro". Cabecera con color distinto del cuerpo.
- **Buscador SACADO del título** (pedido del user — quedaba horrible). El endpoint
  de búsqueda queda en el backend para reubicarlo mejor más adelante.
- **Texto MUCHO más prolijo:** `_limpiar_para_mostrar()` ahora saca también las
  imágenes `[https://…png]`, los separadores `____`, el masthead repetido (fecha +
  "EL DÍA EN POCAS LÍNEAS") y líneas duplicadas, y **separa los items en párrafos**
  por su titular en mayúscula. El frontend **resalta el titular** de cada item.
- Tests del limpiador actualizados a la conducta nueva. **Es iteración** — se sigue
  afinando (ej. si un mail viene con html→texto muy roto, mejorar la extracción en
  el ingest y re-ingestar).

### 2026-07-17 (2) — IA OFF por defecto + el texto se muestra LIMPIO
Pedido del user: la IA no interviene por gastar; lo principal es que los textos
aparezcan bien. **Qué se hizo:**
- **`jobs/research_mail.py`:** el destilado IA al ingestar pasó a ser **opt-in**
  (`--destilar`); por defecto guarda SOLO el crudo → **cero tokens** en la corrida
  del cron. La IA queda para on-demand (copiloto, Nivel 2).
- **`api/services/research_sql.py`:** `_limpiar_para_mostrar()` sirve el research
  LIMPIO (saca los headers del reenvío De:/Para:/Asunto: y el pie de 1816
  Copyright/unsubscribe) — el crudo original queda intacto en la DB (FTS/citas). El
  endpoint devuelve `texto` (limpio) en vez del crudo entero.
- **`research-view.tsx`:** el texto del research se muestra DIRECTO, en párrafos
  legibles (los títulos en MAYÚSCULA de 1816 resaltan solos), no escondido tras un
  toggle. El "Resumen IA" aparece solo si algún día se destiló (opt-in).
- Test del limpiador (`test_limpiar_para_mostrar`). Todo verde.
- **Consecuencia para el user:** corré el ingest **sin** `--destilar`
  (`python -m jobs.research_mail`) → guarda los mails sin gastar. La vista los
  muestra lindos. (El cron ya corre sin el flag → correcto.)

### 2026-07-17 — NIVEL 1 (pilar B, research escrito) CONSTRUIDO
La vista **RESEARCH** (nueva vista principal `/research`) con el research diario de
1816 funcionando de punta a punta. **Qué se hizo y cómo:**
- **Ingesta ya andando (P6):** `jobs/research_mail.py` detecta los mails REENVIADOS
  a mano (fix del match From+Asunto+Cuerpo, 2026-07-17) → probado con `--dry-run`:
  ve los 4 reenvíos de la semana. El cron ya existía (`*/30 10-14 UTC L-V`) → apenas
  llega el mail (reenviado a la mañana) se ingesta y destila solo.
- **Módulo RBAC `research`** en `core/roles.py::MODULES` — INTERNO, no está en
  `invitado` (REGLA #8). Default lo tiene `admin` (vía MODULES); el admin lo tilda
  para trader/sales en Manager → ROLES Y PERMISOS (la matriz de prod pisa el
  default, patrón `ia`). **PENDIENTE del user: tildar `research` en el panel.**
- **Backend:** `api/services/research_sql.py` (lee `ia.research`: timeline
  `listar_research` + búsqueda full-text español `buscar_research` con `ts_headline`
  resaltado) + router `api/routers/research1816.py` (`GET /api/research1816/mails`,
  `/mails/buscar`). **Prefijo `/api/research1816` a propósito** — el `/api/research`
  existente es la Análisis Fundamental de RV (gate renta-variable); NO se pisa. Gate
  a nivel router con `require_module("research")`. Montado en `api/main.py`.
- **Frontend:** vista `research-view.tsx` + página `src/app/research/page.tsx`
  (SSR). **Split 50/50:** izquierda = Market Data 1816 (placeholder hasta la API
  key), derecha = research diario (timeline con destilado IA — resumen + temas +
  hechos — + texto crudo expandible + buscador full-text con debounce + "cargar
  más"). Entrada `RESEARCH` en la nav (`header.tsx`, gate módulo). Gating en
  `proxy.ts` (página `/research` + API `/api/research1816`).
- **`tipo` (diario/mensual)** se deriva del asunto al vuelo (`_tipo_de_asunto`) — sin
  columna nueva todavía (cuando llegue un mensual real se evalúa, §5.2).
- **Tests:** `tests/unit/test_research_sql.py` (derivación de tipo + parseo del
  destilado). Backend: import-chain OK (313 rutas), ruff limpio. Frontend: typecheck OK.
- **Qué falta para que el user lo VEA:** (1) correr el ingest real una vez
  (`python -m jobs.research_mail`, sin `--dry-run`) para poblar `ia.research`;
  (2) `git pull` + `systemctl restart api.service` en el Droplet; (3) el frontend
  deploya solo en Vercel; (4) tildar el módulo `research` en el panel de roles.

## Hecho

- **2026-07-17 — Nivel 1 (pilar B) — vista Research con el research diario de 1816**
  (ver Registro de construcción). Pilar A (market data 1816) pendiente de la API key.
