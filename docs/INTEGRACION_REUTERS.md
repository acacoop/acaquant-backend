# Integración a Reuters (Eikon/LSEG Workspace)

> **Doc VIVO con changelog obligatorio** — igual que `docs/COPILOTO.md`: todo avance,
> cambio o descarte de esta integración se asienta ACÁ en el mismo commit.
> Nacimiento: 2026-07-16. Dueño: mesa (Nicolás).

## 1. Qué es

Precios **en tiempo real desde Reuters** (Eikon/LSEG Workspace) para los subyacentes
US de los CEDEARs, integrados a la plataforma con el mismo patrón que el dólar
mayorista MAE: un script local en una PC con Workspace logueado transmite hacia la
API cuando se lo prende; la plataforma los muestra en **TRADING → REUTERS**.

Objetivo final: **CCL implícito en vivo por activo** —
`ccl = precio_cedear_ars × ratio / precio_adr_usd` (el precio ARS ya lo da
`motor_cedears` vía pyRofex; el USD lo trae este feed). El cálculo está PENDIENTE
a pedido de la mesa; el `ratio` ya está modelado.

## 2. Arquitectura

```
Notebook con Workspace ABIERTO y logueado (hoy: la del user)
   │  Desktop\feed.py  ←  copia local de scripts/eikon_feed_simple.py
   │                       con las keys pegadas (NUNCA commitear esa copia)
   │  loop cada 20s: ek.get_data(RICs, CAMPOS) → POST solo lo que cambió
   ▼
POST https://api.acaquant.com/api/ingest/eikon/quotes
   │  auth: X-Ingest-Token (el mismo del feed MAE) + service token CF Access
   ▼
core/eikon_live.upsert_quotes → SQL mercado.eikon_snapshot (1 fila por ticker,
   │                            jsonb passthrough, updated_at lo pone el server)
   ▼
GET /api/trading/reuters (módulo `trading`, admin-only — INVITADO JAMÁS)
   ▼
acaquant-web → TRADING → tab REUTERS (reuters-view.tsx, poll 5s, tabla 60% izq)
```

- **La PC no toca la base** (misma postura de seguridad que el feed MAE: si el
  token se filtra, solo permite escribir estas tablas de mercado).
- **Camino SEPARADO de Finnhub**: `jobs/adr_live.py` → `mercado.adr_snapshot`
  sigue intacto alimentando el Scanner. Conviven; nadie más lee `eikon_snapshot`.

## 3. Piezas

| Pieza | Dónde |
|---|---|
| Feed local (ÚNICO) | `scripts/eikon_feed_simple.py` → copia con keys en `Desktop\feed.py` |
| Ingesta | `api/routers/ingest.py` → `/api/ingest/eikon/{universo,rics,quotes}` |
| Lógica SQL | `core/eikon_live.py` (universo, upsert, tablero) |
| Tabla | `mercado.eikon_snapshot` (ticker PK, ric, data jsonb, updated_at) |
| Catálogo | `mercado.cedears.ric` + `mercado.cedears.ratio` — carga MANUAL |
| Editor | Manager → TÍTULOS → RENTA VARIABLE (columnas RIC y RATIO) |
| Endpoint vista | `GET /api/trading/reuters` (`api/routers/trading.py`) |
| Vista | `acaquant-web/src/components/reuters-view.tsx` (tab en `trading-shell`) |
| Diag (temporal) | `scripts/diag_eikon_snapshot.py` — se borra al cerrar la prueba |

**Decisión clave (2026-07-16): los RICs se cargan SOLO a mano.** La primera
versión los resolvía por symbology y los persistía sola → guardó 99 tickers
pelados (solo NYSE) y ensució el catálogo. Se eliminó la auto-resolución
(`scripts/fix_limpiar_rics.py` limpió eso). Convención de RICs: NASDAQ = `.O`
(`AAPL.O`, `NVDA.O`), NYSE = `.N` (`KO.N`, `JPM.N`).

## 4. Operación (runbook)

**Prender el feed** (en la notebook, con Workspace abierto y logueado):
```
py C:\Users\nicolas.mollo\Desktop\feed.py
```
Consola esperada: `suscribiendo N RICs…` → `✅ N precios actualizados` /
`🔄 sin cambios` cada 20s. Ctrl+C corta. La ventana nunca se cierra sin mostrar
el motivo.

**Agregar un activo**: Manager → TÍTULOS → RENTA VARIABLE → cargar RIC (y ratio)
→ cortar y volver a correr el feed (toma los RICs al arrancar).

**Verificar del lado del server** (Droplet): `python -m scripts.diag_eikon_snapshot`.

**Si se toca `scripts/eikon_feed_simple.py`**: regenerar la copia del Desktop
(mismo archivo con las 4 keys pegadas). Claude lo hace con un `sed` en un paso.

## 5. Campos — validados EN VIVO (2026-07-16, RKLB.O)

Live (se mueven intradía): `CF_LAST` (last), `CF_BID`, `CF_ASK`, `CF_OPEN`
(⚠️ puede venir vacío — RKLB no lo publica), `CF_HIGH`, `CF_LOW`, `CF_CLOSE`
(cierre anterior), `CF_VOLUME`, `PCTCHNG` (var %), `NETCHNG_1` (var neta),
**`AFTMKT_PRC` / `AFTMKT_VOL` (after market)**, **`PREMKT_PRC` (pre market)**.

EOD (cambian 1 vez por día, al cierre de la rueda anterior): `TR.PriceClose/Open/
High/Low`, `TR.Volume`, y los retornos `TR.PricePctChg{1D,5D,WTD,MTD,QTD,YTD,1M,3M,1Y,5Y}`.

Hallazgos que costaron caro (no re-descubrir):
- **El after hours NO es un RIC `.PP`** (`RKLB.PP` → "record could not be found").
  Es un CAMPO (`AFTMKT_PRC`) sobre el RIC normal. Var % del after = vs `last`
  (cierre de hoy); var % del pre = vs `prev_close` (cierre anterior) — las calcula
  el server (`core/eikon_live._var_pct`).
- **`get_data(..., field_name=True)` SIEMPRE**: sin eso los `TR.*` vuelven con
  display name ("5-day Price PCT Change") que no se puede mapear. Con eso vuelven
  como el código en MAYÚSCULA (`TR.PRICEPCTCHG5D`).
- **`PRIMACT_1` es el last de FUTUROS** (por eso andaba en el viejo script de
  commodities) — para acciones es `CF_LAST`. El feed pide ambos y usa el que venga.
- El aviso `Field 'X' was not found for instrument 'Y'` es por-campo/instrumento
  y NO invalida el resto de la respuesta.

## 6. ⚡ Testeo EN VIVO desde Claude Code

**Workspace corre en la MISMA notebook donde corre Claude Code** → Claude puede
validar campos, RICs y comportamientos de Eikon **en vivo, en segundos**, sin
tocar el feed ni pedirle nada al user: escribe un script one-shot en su scratchpad
(`ek.set_app_key(...)` + `ek.get_data(...)`) y lo corre con `py`. Así se validaron
los 24 campos, se descubrió `AFTMKT_PRC`/`PREMKT_PRC`, se descartó `.PP` y se
encontró `field_name=True`. **Ante cualquier duda sobre un campo/RIC: probarlo,
no adivinar** (REGLA #2 con superpoderes). Requisito: Workspace abierto y logueado.

## 6b. Noticias Reuters — ESTUDIO (validado en vivo 2026-07-16, sin implementar)

Pedido de la mesa: sumar noticias de Reuters a la plataforma. Se estudió y se
**probó en vivo** con la lib `eikon` actual (misma sesión Workspace):

- **`ek.get_news_headlines(query, count)` FUNCIONA** → DataFrame con
  `versionCreated`, `text`, `storyId`, `sourceCode`. Probado:
  `R:RKLB.O L:EN` devolvió los titulares REALES del selloff de space stocks de
  hoy; multi-RIC `R:AAPL.O OR R:NVDA.O L:EN` también OK.
- **`ek.get_news_story(storyId)` FUNCIONA** → HTML string con el texto completo
  (`<div class="storyContent">…`). Parsear con BeautifulSoup si se quiere texto
  plano; el storyId es un URN (`urn:newsml:newswire.refinitiv.com:...`).
- ⚠️ La query en español `L:ES AND "argentina"` devolvió **503 Backend error**
  (también hubo un 503 transitorio al abrir sesión) — la sintaxis de queries
  combinadas hay que refinarla probando (el operador va sin `AND` entre filtros:
  `R:AAPL.O L:EN` funciona así, yuxtapuesto).
- Operadores de query: `R:` RIC · `L:` idioma · `IN:` región · `NS:` fuente ·
  `T:` tópico RCS (`T:MERG` M&A) · `"frase exacta"` · `AND/OR/NOT`.
- Rate limit noticias: ~5.000 requests/hora. Para producción SIN Workspace
  haría falta LSEG Data Platform (RDP) — hoy no lo tenemos: mientras tanto,
  cualquier feed de noticias corre en la PC de oficina como el de precios.
- Lib nueva `lseg-data` (`ld.news.get_headlines/get_story`): misma
  funcionalidad, recomendada para proyecto nuevo — pero `eikon` ya está
  instalada y probada en la notebook; decidir al implementar.

**Camino sugerido cuando se decida implementar** (mismo patrón que precios):
el feed local agrega un poll de titulares por RIC suscripto (cada N min, cache
por storyId para no repetir) → `POST /api/ingest/eikon/news` → tabla
`mercado.eikon_news` → panel derecho del tab REUTERS (el 40% reservado) y/o
insumo del copiloto. **Sin implementar hasta que el user lo pida.**

## 7. Requisitos de entorno (la PC del feed)

- Python con `eikon` (1.1.18, la última — lib deprecada pero funcional) y
  **`pandas<3`** ← CRÍTICO: eikon + pandas≥3 revienta `get_data` con
  `ValueError: invalid error value specified` (pandas 3 eliminó
  `errors='ignore'` de `to_numeric`). Fix: `py -m pip install "pandas<3"`.
- `requests`.
- **El archivo local NO puede llamarse `eikon.py`** — `import eikon` se importaría
  a sí mismo. (El script lo detecta y avisa.)
- Workspace abierto y logueado (Desktop Session; sin la app no hay datos).

## 8. Changelog

- **2026-07-24 — el feed suma BONOS OFF (soberanos offshore → watchlist HOME + briefing).**
  Precio en USD de la pata que operan los extranjeros (páginas contribuidas
  MarketAxess, RICs "=1M" provistos por el user): 6 Globales (GD29/30/35/38/41/46)
  + 5 Bonares (AL29/30/35, AE38, AL41). Mismo patrón que Chicago:
  - Constante `core/eikon_bonos.py::BONOS_OFF` (RIC→bono) + tabla
    `mercado.eikon_bonos_snapshot` + `/api/ingest/eikon/bonos/{universo,quotes}`.
  - Feed: grupo `[bonos]` en el mismo loop, heartbeat sin diff. ⚠️ Los campos de
    esas páginas NO están validados en vivo → set tolerante (CF_LAST/PRIMACT_1/
    CF_BID/CF_ASK/PCTCHNG/…) y el server resuelve el precio con fallback
    last → primact → mid(bid,ask). Mirar los avisos de la 1ra pasada.
  - Consumers: watchlist HOME sección ARGENTINA (filas "GD30 OFF" agregadas por
    `api/services/argy.py` — el front las renderiza solo, %día del feed,
    7d/MTD/YTD sin anchor por ahora) y modal de briefing (bloque `bonos_off`,
    sección "SOBERANOS EXTERIOR (OFF)" bajo la curva DLR).
  - Regenerar la copia del Desktop del feed.

- **2026-07-24 — el feed suma CHICAGO (futuros CBOT → AGRO → tab CHICAGO).**
  El MISMO `scripts/eikon_feed_simple.py` (decisión del user: un solo file de
  feed) ahora también:
  - Pide `GET /api/ingest/eikon/chicago/universo` al arrancar (tolerante: si la
    API vieja no tiene el endpoint, sigue solo con acciones) → RICs de
    continuación CBOT de 5 familias (constante `core/eikon_chicago.py::FAMILIAS`,
    viene del script de commodities original del user; sin catálogo editable).
  - En el mismo loop de 20s: `ek.get_data(rics, [CONTR_MNTH, PRIMACT_1,
    SEC_ACT_1])` (PRIMACT_1 = last de futuros, §5) → POST
    `/api/ingest/eikon/chicago/quotes` con valores CRUDOS + cache-diff propio.
  - Server: `mercado.eikon_chicago_snapshot` (1 fila por RIC, jsonb) y los
    factores a USD/tonelada se aplican AL LEER (`tablero_chicago`) — la PC no
    conoce el modelo. Vista: `GET /api/derivados/agro/chicago` → AGRO → tab
    CHICAGO (`agro-chicago.tsx`, grilla de 5 tablas, poll 10s).
  - **Regenerar la copia del Desktop** (`feed.py` con las keys) para que tome
    Chicago — hasta entonces el feed viejo sigue andando igual (solo acciones).
  - **Semáforo EN LÍNEA** (feedback del user, mismo día): Chicago manda las ~25
    filas en CADA loop como HEARTBEAT (sin cache-diff — payload mínimo; las
    acciones siguen con diff). El tablero devuelve `online` = último POST hace
    < 60s (`ONLINE_TTL_S`) y la vista muestra el punto verde/rojo (FEED EN
    LÍNEA / FEED APAGADO) + hora de la última actualización. Requiere
    re-regenerar la copia del Desktop.

- **2026-07-18 — la vista se MUDÓ a /research → tab RENTA VARIABLE INTERNACIONAL.**
  El tablero (`reuters-view.tsx`, con fundamentals, ficha y su copiloto in-view)
  ya no vive en /trading (que quedó con PIVOTS + INTRADAY) sino como segunda tab
  de la vista Research (`research-view.tsx` — ver docs/VISTA_RESEARCH.md).
  **Feed/ingest/`core/eikon_live.py` intactos**, pero los endpoints HTTP se
  MUDARON: `/api/trading/reuters*` → **`/api/research1816/reuters*`** (gate módulo
  `research` — directiva del user: TRADING queda admin-only y RESEARCH se habilita
  a toda la mesa). El copiloto de la vista también pasó a `research` (COPILOTO v1.55).

- **2026-07-16 — v1: nace la integración.**
  - Tabla `mercado.eikon_snapshot` + `/api/ingest/eikon/*` + `core/eikon_live.py`
    (patrón feed MAE). RIC editable en Manager.
  - Incidente: la auto-resolución de RICs por symbology ensució el catálogo (99
    tickers pelados) → se ELIMINÓ; RICs solo a mano; `fix_limpiar_rics` los limpió.
  - Debug maratónico del feed local: archivo llamado `eikon.py` (se importaba a sí
    mismo), pandas 3 incompatible con la lib eikon, `PRIMACT_1` solo futuros,
    `NA` de pandas en chequeos booleanos, consolas cp1252 que crashean con emojis.
    Todo asentado en §5/§7 para no repetirlo.
  - Feed ANDANDO con quote básico (last) → extendido: bid/ask/open/high/low/
    cierre/volumen/var%/var neta.
  - Vista TRADING → tab REUTERS: tabla 60% izquierda, poll 5s, panel derecho
    reservado. Columna CCL modelada pero VACÍA (decisión: se calcula más adelante).
  - `mercado.cedears.ratio` modelado (CEDEARs por acción) + editor en Manager.
  - After/pre market validados en vivo (`AFTMKT_PRC`/`AFTMKT_VOL`/`PREMKT_PRC`) +
    retornos por período `TR.PricePctChg*` → columnas PRE MKT / AFTER HS / 5D→5A.
  - Tabla a **100% del ancho** (se liberó el panel derecho reservado) + **orden
    por columna** con click (números desc, texto A→Z, nulls al final).
  - **Copiloto vista `reuters`** (v1.51 del asistente, `docs/COPILOTO.md`):
    el asistente ve el tablero completo — en especial los retornos por período —
    vía `<IaVistaPanel vista="reuters" />`. Gate `ia` + `trading`.
  - Noticias Reuters: ESTUDIADAS y validadas en vivo (§6b) — sin implementar.
  - Ajustes de mesa (28 RICs cargados, feed en producción): se ELIMINÓ
    APERTURA/`CF_OPEN` de todo el circuito (27/27 suscriptos sin el dato);
    selector de COLUMNAS ocultables (preferencia persistente); PRE/AFTER
    muestran solo la VARIACIÓN % (el precio sigue viajando en el payload);
    "CIERRE ANT." → "CIERRE"; el log de 1ra pasada filtra los avisos de
    PRIMACT_1 y dedupe — quedan solo problemas reales (ej. RIC mal cargado).

- **2026-07-17 — v2: FICHA DE EMPRESA (slice 1).**
  - Concepto (decisión del user): el tablero es el "screener"; la ficha es el
    módulo de empresa — **MENOS ES MÁS**, panel curado, nada de volcar el
    balance entero (lección del RESEARCH de renta variable que no gustó).
  - Campos TR.* del cheat sheet del user VALIDADOS en vivo (AAPL/RKLB):
    valuación (PE/FwdPE/EV/EBITDA/EV/EBIT/P.BV), márgenes, salud (deuda, caja,
    ratios), resultados FY0 en millones USD (Revenue/EBITDA/NI/FCF/Capex),
    serie 5 años (SDate=0 EDate=-4), consenso (target medio, rec media,
    PRÓXIMO BALANCE), 52 semanas, perfil. No vinieron: ROE/ROA/ROIC, EPS
    diluido, Beta, P/Sales, Payout (nombres a refinar si se quieren).
  - Feed: pull de fundamentals 1 vez/día (arranque + cada 24 h; si falla
    reintenta a los 30 min, nunca voltea los precios) → POST
    `/api/ingest/eikon/fundamentals` → tabla `mercado.eikon_fundamentals`.
  - Ficha: `GET /api/trading/reuters/ficha?ticker=X` = quote live + fundamentals
    + ratio + velas 1 año de `mercado.precios_acciones` (EOD ya en casa — el
    chart NO depende de Eikon). UI `reuters-ficha.tsx`: header (precio live,
    pre/after, rango 52s, próximo balance), chart 1 año (lightweight-charts),
    bloques VALUACIÓN / NEGOCIO (FY0 + mini-barras 5 años) / SALUD / CONSENSO
    (upside al target, recomendación en texto) / RETORNOS. Entrada: click en
    fila del screener o buscador (Enter abre la primera coincidencia).

- **2026-07-17 — v2.1: ficha rediseñada a 2×2 (feedback del user sobre v2).**
  - El chart de precio ocupaba demasiado (es secundario) → layout en 4
    cuadrantes de 50%: arriba-izq precio 1 año · abajo-izq RETORNOS en tabla +
    market cap + rango 52 semanas · arriba-der MÉTRICAS en tabs (NEGOCIO /
    SALUD / VALUACIÓN) · abajo-der la EVOLUCIÓN 5 AÑOS graficada (barras por
    año fiscal: ingresos/EBITDA/resultado/FCF, series apagables).
  - **CONSENSO DE ANALISTAS ELIMINADO** (decisión del user: no le interesa) —
    fuera de la UI y del feed (TR.PriceTargetMean / TR.RecMean ya no se piden).
    El "próximo balance" (ExpectedReportDate) sobrevive en el header (es
    agenda, no consenso).
  - **v2.2 (mismo día): series de 5 años AMPLIADAS** — el feed trae también
    deuda/caja (Scale=6 USD) y márgenes (sin Scale, son %) por año fiscal, en
    dos llamadas mergeadas por (ric, fecha). El gráfico de abajo-derecha tiene
    3 grupos: RESULTADOS / MÁRGENES / SALUD (sincronizado con el tab de
    métricas, elegible a mano).
  - ⚠️ **MÚLTIPLOS históricos: NO graficables** (verificado en vivo): pedir
    TR.PE / TR.EVToEBITDA / TR.PriceToBVPerShare con SDate/EDate devuelve
    precio de HOY ÷ resultados de cada año (AAPL "PE 38x" hace 5 años, falso —
    cotizaba ~25x). Quedan como foto actual; si algún día se quieren de verdad,
    buscar el data item de múltiplo histórico real en el Data Item Browser.

- **2026-07-17 — v2.3: gráfico con datos + serie TRIMESTRAL + screener FUNDAMENTALS.**
  - Gráfico de evolución: eje Y con valores (gridlines), valor sobre cada barra
    cuando hay ≤2 series activas, escala que banca pérdidas grandes (positivos
    y negativos con su propio máximo — antes una pérdida se salía del lienzo).
  - Toggle **ANUAL / TRIMESTRAL**: el feed baja también los últimos 8 trimestres
    (validado: `Period=FQ0` + `Frq=FQ` + SDate/EDate — SIN `Period=FQ0` la
    fuente repite el dato ANUAL por trimestre, trampa verificada). El trimestral
    trae 2026.
  - **Sub-vista FUNDAMENTALS** en el tab REUTERS (toggle a la derecha, junto al
    copiloto): el "scanner" de fundamentals — cada fila una empresa, columnas =
    métricas de la ficha (valuación/negocio/salud + fecha de reporte), orden por
    columna, columnas ocultables y **buscador multi-empresa** ("AAPL, MSFT
    NVDA") para comparar. Click en fila → ficha.
    `GET /api/trading/reuters/fundamentals` (sin las series, que pesan).

- **2026-07-17 — v2.4: filtro AFTER HOURS + gráfico legible + explicaciones ("?").**
  - Filtro **AFTER HOURS** en cotizaciones: toggle que muestra/oculta las
    columnas PRE y AFTER (fuera del selector de columnas normal; preferencia
    persistente).
  - Gráfico de evolución: escala con tope REDONDO (1/2/2.5/5×10^k → el eje ya
    no muestra "242%/48.5%" sin sentido) y **valor en TODAS las barras**
    (etiqueta vertical, con cabecera reservada para que no se recorte).
  - **Explicaciones en todo REUTERS**: cada columna de cotizaciones y de
    FUNDAMENTALS y cada dato de la ficha lleva un "?" con tooltip que dice qué
    es, cómo se calcula y cómo se lee (P/E, EV/EBITDA, FCF, DN/EBITDA, márgenes,
    pre/after, WTD vs 1M, etc.). El copiloto (v1.52) suma un GLOSARIO en sus
    reglas: rol "profesor de la vista" — explica cualquier métrica si le
    preguntan.

- **2026-07-17 — v2.5: ficha rediseñada + SPY/QQQ de PIVOTS al feed.**
  - Ficha: separadores nítidos entre cuadrantes, títulos en acento/negrita,
    métricas en 2 columnas, negritas en labels/valores (modo claro legible),
    botón ⛶ maximizar por cuadrante, gráfico de evolución dibujado al tamaño
    real del panel (ResizeObserver — fin del espacio muerto).
  - Los KPIs SPY/QQQ del toolbar de PIVOTS ahora salen de `/api/trading/reuters`
    (real-time del feed) en vez de la watchlist externa.
  - Ratios: el 2º Excel del user (17061.xlsx, 02-jul) resultó IDÉNTICO al 1º —
    **los CEDEARs de ETF (SPY/QQQ/XLF…) NO están en ese listado de BYMA**; falta
    el listado de ETFs para completarles el ratio (o cargarlos en Manager).

- **2026-07-17 — v3: MOTOR DE CCL IMPLÍCITO VIVO (server-side).**
  - `core/eikon_live._ccl_implicito`: CCL = last del CEDEAR (ARS,
    `mercado.cedears_snapshot`, motor propio) × ratio ÷ last del ADR (USD, feed
    Eikon). Calculado en el BACKEND (el front solo muestra), y **nunca rompe**:
    cualquier pata faltante (feed apagado, CEDEAR sin operar, ratio sin cargar)
    → None → celda vacía. Guard de frescura: el last del CEDEAR debe ser de HOY
    (ART) — mezclar un ARS viejo con un USD fresco fabrica un CCL falso.
  - Columna CCL del tablero activa; copiloto ve `ccl_implicito` con su regla.
  - KPIs de PIVOTS: SPY/QQQ = CEDEAR ARS (scanner) + **SPY ADR / QQQ ADR** en
    USD (tablero Reuters), lado a lado.
  - Briefing: 3ª columna **DÓLAR FUTURO (DLR)** (modal más ancho): ticker /
    días / último / TNA implícita desde `mercado.futuros_dlr_snapshot`.

- **2026-07-17 — v3.1: tablero más sobrio (feedback del user: "mucha info de una").**
  - Columnas VOLUMEN / MÍN / CIERRE / RATIO **ocultas por default** (se prenden
    desde COLUMNAS; la preferencia vieja del navegador se pisa con key nueva
    `reuters.cols.ocultas.v2`). CCL sigue visible.
  - El bloque RETORNOS (5D→5A) queda visualmente SEPARADO dentro de la misma
    tabla: fondo tintado propio (`--t-surface-2` al 40%) en cabeceras y celdas
    + separador más grueso al entrar al bloque — se lee "día vs. acumulado"
    sin partir la tabla. Descartado (user): presets de vista tipo
    PRECIO/RETORNOS/COMPLETA — prefiere una sola tabla con corte visual.

## 9. Pendientes

- **CCL implícito en vivo** (la razón de ser): `cedear_ars × ratio / adr_usd` por
  fila. Falta decidir de dónde sale el precio ARS live en el endpoint
  (`mercado.cedears_snapshot`) y cargar ratios en Manager.
- Noticias Reuters en la plataforma (estudio hecho, §6b) — diseñar CON el user
  dónde viven (¿panel en REUTERS? ¿HOME?) y recién ahí implementar.
- Cargar RICs del universo que la mesa quiera seguir (hoy: solo RKLB.O).
- Al cerrar la prueba: borrar `scripts/diag_eikon_snapshot.py` y
  `scripts/fix_limpiar_rics.py` (REGLA #5) y evaluar si `eikon_snapshot` pasa a
  tener frescura monitoreada (watchdog) como el resto de los feeds.
- Futuro: migrar la lib `eikon` → `lseg-data` (la deprecación es real).
