# Vista RESEARCH — integración 1816 (market data + research diario)

> **REGLA DE ESTE DOCUMENTO (leer antes de tocar nada):** es el doc MADRE de la
> vista Research. Documento VIVO: cada decisión tomada se asienta, cada cosa que
> se termina se mueve a "Hecho", cada credencial/pendiente queda marcado. Si se
> trabaja en algo de acá y no se actualiza este archivo en el mismo commit, el
> trabajo está incompleto. Cross-refs: la ingesta de mails vive en QuantAI P6
> (`docs/QUANTAI.md`), los fundamentals Refinitiv (otra cosa, en Renta Variable)
> en `docs/RESEARCH_REFINITIV.md`, el modelo SQL en `docs/SQL.md`.

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
2. **La vista es INTERNA — jamás para el invitado (REGLA #8).** Es research
   propietario de 1816 (pago, con créditos) + contenido que la empresa recibe
   bajo licencia. El módulo `research` NO entra a `INVITADO_MODULES`. Default
   para admin/trader/sales internos; el admin lo asigna en la matriz.
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

1. **Credenciales IMAP (PENDIENTE del user — REGLA #6):** `RESEARCH_IMAP_USER` /
   `RESEARCH_IMAP_PASSWORD` (app password de Gmail, NO la normal) / `RESEARCH_MAIL_FROM`
   (`research@1816.com.ar` o el dominio `1816.com.ar`). El user reenvía los mails
   a su casilla corporativa → apuntar el IMAP a esa casilla. Probar:
   `python -m jobs.research_mail --dry-run`.
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
- **Fase 3 — Pilar B (mails) en la vista.** Encender P6 (creds), sumar `tipo`
  diario/mensual, timeline + FTS en la vista. (Puede ir en paralelo a Fase 1 — es
  independiente y ya casi todo está.)
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
  pilar B no ingesta (pero no bloquea el pilar A).
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

## Hecho

- *(vacío — el documento nace con el diseño; se va llenando a medida que las fases
  se entregan. Mover acá cada fase cerrada con una línea.)*
