# Vista DATOS INTERNACIONALES (tab de Research) — integración 100% a la FRED API

> **DOC VIVO** de la tab "Datos Internacionales" (FRED) de la vista Research (doc
> madre de la vista: `docs/VISTA_RESEARCH.md`; molde de esta tab: `docs/RESEARCH_BCRA.md`).
> Misma regla: todo avance/decisión se asienta acá en el mismo commit — si el doc
> no refleja el estado real, el trabajo está incompleto.
> **Estado: FASE 1 + TAB TASAS USA CONSTRUIDAS (2026-07-19).** Scope acotado por el
> user: **de a poco, vista por vista** — arrancamos por **Tasas USA**; siguen
> Commodities/Agro · EEUU Macro · China. Backfill **desde 2020**. Argentina NO es
> una sub-tab de FRED (su data en FRED es pobre/vieja): entra como los CRUCES (§5).
> Pendiente del user: `git pull` + `apply_schema` + `--backfill` + restart (ver Registro).
>
> **Espíritu (igual que BCRA/1816):** leer de la API → persistir en Postgres → la
> vista sirve SIEMPRE de la DB (rápida, 24/7, sin depender de FRED al navegar). Un
> cron sincroniza varias veces al día para capturar la publicación apenas sale.

---

## 1. Objetivo

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

## 2. La FRED API — mecánica VERIFICADA

Base: `https://api.stlouisfed.org/fred/` — REST sobre HTTPS, todo por GET con query
params. Existe una `v2` en desarrollo; **usamos la estable `/fred/` (v1)**.

### 2.1 Autenticación — la diferencia CLAVE vs BCRA

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

### 2.2 Rate limit (verificado)

- **120 requests/min por API key** (documentado y replicado por todas las libs).
  Al exceder → **HTTP 429**.
- **Nuestro throttle:** ~1 req cada 0.6s (holgado bajo el techo) + backoff en
  429/5xx (patrón `fredr`: espera creciente, máx ~6 reintentos). Con el diseño
  incremental **nunca nos acercamos** al techo: ~45 series × 1 req = 45 requests
  por corrida (§6.5). El límite es **por key** → si varios procesos comparten la
  key, se suman: **el throttle vive centralizado en `core/fred_api.py`** (un solo
  carril de salida), no en cada job.

### 2.3 Endpoints que usamos (verificados)

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

### 2.4 Gotchas verificados (los que rompen código)

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

### 2.5 ALFRED / vintages — qué es y por qué lo DIFERIMOS

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

## 3. ⚠️ Lo que YA existe en el repo (no duplicar, no romper)

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

## 4. Universo CURADO por bloque (el seed de `research.fred_watch`)

**El principio (idéntico a BCRA/1816):** FRED tiene ~800k+ series. **JAMÁS se baja
el catálogo.** Se cura un universo de **~48 series** de valor de mesa, cada una con
su `bloque` (= sub-tab). Editable en `research.fred_watch`.

**Leyenda:** ✅ = **ID VERIFICADO** existe en FRED (fase de verificación completada);
⏳ = **a confirmar en Fase 0** (mencionada en cruces/prioridad pero no en la
verificación — se valida contra `/fred/series` antes de sembrar). Freq: D/W/M/Q.

### Bloque `tasas_usa` — Tasas USA (bloque #1 para una mesa de renta fija)
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

### Bloque `commodities` — Commodities (el corazón del modelo AR: soja + energía)
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

### Bloque `dolar_fx` — Dólar / FX global
| series_id | Etiqueta | Freq | Unidad | ✓ |
|---|---|---|---|---|
| DTWEXBGS | Dólar amplio (≈DXY) | D | índice | ✅ |
| DTWEXAFEGS | Dólar vs avanzadas (G10) | D | índice | ✅ |
| DEXUSEU | USD por EUR | D | USD/EUR | ✅ |
| DEXJPUS | JPY por USD (funding carry) | D | JPY/USD | ✅ |
| DEXCHUS | CNY por USD | D | CNY/USD | ✅ |
| DEXBZUS | BRL por USD (comparable del peso) | D | BRL/USD | ✅ |

### Bloque `riesgo_credito` — Riesgo / crédito / condiciones financieras
| series_id | Etiqueta | Freq | Unidad | ✓ |
|---|---|---|---|---|
| BAMLEMCBPIOAS | EM Corp OAS | D | pp | ✅ |
| BAMLEMHBHYCRPIOAS | EM HY Corp OAS (donde cae AR) | D | pp | ✅ |
| BAMLH0A0HYM2 | US HY OAS | D | pp | ✅ |
| BAMLC0A0CM | US IG OAS | D | pp | ✅ |
| VIXCLS | VIX | D | pts | ✅ |
| NFCI | Chicago Fed Financial Conditions | W | índice | ✅ |
| STLFSI4 | St. Louis Fed Financial Stress | W | índice | ✅ |

### Bloque `eeuu_inflacion` — EEUU inflación
| series_id | Etiqueta | Freq | Unidad | ✓ |
|---|---|---|---|---|
| CPIAUCSL | CPI headline (SA) | M | índice | ✅ |
| CPILFESL | Core CPI (SA) | M | índice | ✅ |
| PCEPI | PCE price index | M | índice | ✅ |
| PCEPILFE | Core PCE (target de la Fed) | M | índice | ✅ |
| T10YIE | Breakeven 10Y | D | % | ✅ |
| T5YIE | Breakeven 5Y | D | % | ✅ |
| T5YIFR | 5y5y forward (anclaje) | D | % | ✅ |

### Bloque `eeuu_actividad` — EEUU actividad
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

### Bloque `china` — China
| series_id | Etiqueta | Freq | Unidad | ✓ |
|---|---|---|---|---|
| CHNCPIALLMINMEI | CPI China (deflación = ojo commodities) | M | índice NSA | ✅ |
| XTEXVA01CNM667S | Exportaciones China | M | USD | ✅ |
| TRESEGCNM052N | Reservas ex-oro China | M | M DEG | ✅ |

> **Verificado:** el PBI de China en FRED (OECD) está desactualizado (termina
> Q3-2023) y M2 termina 2019 → **no van al seed**. Se priorizan exportaciones,
> reservas y CPI, que sí están al día.

### Bloque `brasil` — Brasil (principal socio comercial + comparable de riesgo)
| series_id | Etiqueta | Freq | Unidad | ✓ |
|---|---|---|---|---|
| IRSTCB01BRM156N | Selic (tasa BCB) | M | % | ✅ |
| IRSTCI01BRM156N | CDI / interbancaria | M | % | ✅ |
| BRACPIALLMINMEI | CPI Brasil (IPCA) | M | índice NSA | ✅ |

### Bloque `latam_em` — Latam / Emergentes (benchmarks de fair value AR)
| series_id | Etiqueta | Freq | Unidad | ✓ |
|---|---|---|---|---|
| DEXMXUS | MXN por USD | D | MXN/USD | ✅ |
| CCUSMA02CLM618N | CLP por USD (atado al cobre) | M | CLP/USD | ✅ |
| MEXCPIALLMINMEI | CPI México | M | índice NSA | ✅ |
| CHLCPIALLMINMEI | CPI Chile | M | índice NSA | ✅ |

### Bloque `liquidez_global` — Liquidez global (la marea que mueve TODO el riesgo)
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

## 5. Los CRUCES con datos propios — el diferencial (desarrollados)

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

## 6. Arquitectura de sync

**La verdad del dato primero (REGLA #2):** FRED publica en **días hábiles USA**, con
frecuencias mixtas (diarias intradía-EOD, mensuales/trimestrales con rezago y
**revisiones retroactivas**). "Actualizado 24/7" real = **la vista sirve SIEMPRE de
nuestra DB** + un cron que sincroniza varias veces al día.

### 6.1 `core/fred_api.py` — el cliente (patrón `bcra_api`, +API key)

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

### 6.2 Tablas (`sql/schema.sql`, schema `research`)

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

### 6.3 `jobs/fred_research.py` — el sincronizador (patrón `bcra_research`)

Copia exacta del molde, con el `_SEED` de §4 (lista de tuplas `(bloque, series_id,
etiqueta, unidad, freq, pais, orden)`). Modos:

- `--dry-run`: universo + watermarks + qué haría (0 requests de series).
- `--backfill [--desde YYYY-MM-DD]`: historia de cada serie del watch (una vez;
  throttled; `--desde` para acotar — las diarias con 30 años son grandes).
- **(default) incremental por watermark:** `desde = max(fecha) − N días` por serie.
- Refresca `fred_series` (metadata, 1 request por serie del watch) 1×/corrida.
- `JobRunLogger` (estándar de la casa).
- `--purgar-antes YYYY-MM-DD` (limpieza one-off scopeada, con `--dry-run`).

### 6.4 El watermark con colchón (revisiones retroactivas)

Como FRED **revisa hacia atrás** (gotcha #8), el `desde` incremental resta un
colchón. **Colchón por frecuencia** (más fino que el −7d fijo de BCRA):

- Series **diarias** (tasas, FX, commodities D): `−10 días` (capturan la última
  semana hábil + revisiones menores).
- Series **mensuales/trimestrales** (CPI, PCE, GDP, payrolls): `−95 días` (≈3 meses
  / 1 trimestre — GDP y payrolls se revisan 2–3 publicaciones después).

Esto se lee de `fred_watch.freq`. Un run sin datos nuevos re-lee esas ventanas y
hace `ON CONFLICT DO UPDATE` (idempotente: si el valor no cambió, no pasa nada; si
FRED lo revisó, se corrige). Costo: ~48 requests, escrituras solo de lo que cambió.

### 6.5 Cron y costos reales

```
# deploy/crontab.txt — FRED research (tab Datos Internacionales)
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

### 6.6 API interna + route handler Next

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

## 7. Diseño de la tab frontend (`research-fred.tsx`)

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

## 8. Fases (como el doc BCRA)

- **Fase 0 — Verificación (1 diag, sin infra):** `scripts/diag_fred.py` (read-only,
  con la `FRED_API_KEY`): (a) confirma los **⏳ DFEDTARU, PIORECRUSDM, CBBTCUSD,
  USREC/USRECD** vía `/fred/series`; (b) trae metadata de las 48 (frequency, units,
  SA/NSA, observation_start/end) para congelar el seed; (c) confirma el comportamiento
  de `observation_start/end`, `units` y la paginación desde el Droplet; (d) mide el
  peso del backfill (nº de obs por serie diaria larga). Con esa salida se congela el
  watch CON el user. **Se borra al cerrar (REGLA #5).**
- **Fase 1 — Ingesta:** `core/fred_api.py` + tablas `research.fred_*` +
  `jobs/fred_research.py` (backfill + cron) + `apply_schema` + SISTEMA.md. Entregable:
  las ~48 series en la DB actualizándose solas.
- **Fase 2 — La tab:** `research-fred.tsx` con sub-tabs por bloque (§7), route
  handler Next + proxy. Entregable: la vista viva (nivel + YoY + idx100 + recession
  shading).
- **Fase 3 — Los CRUCES (el diferencial):** los 8 cruces del §5 como endpoints
  `/cruces/*` + su UI. Empezar por #1 (beta soberano), #2 (CCL vs pares), #5
  (breakeven US vs AR). **Acá está el valor real** — sin esto es "otra tabla de
  números".
- ~~**Fase 4 — Copiloto**~~ — **CANCELADA (2026-08-19)**: el copiloto se dio de baja (`AV_AGENT.md` §0.k). Decía:
  registro + reglas de dominio (qué es cada serie, qué NO inferir — ej. no comparar
  SA con NSA, no leer una mensual como si fuera diaria). Encaja directo en el patrón
  por vista.
- **Futuro (no comprometido):** ALFRED/vintages (backtesting sin look-ahead,
  `output_type=4`); calendario de releases (`/releases/dates`); export
  PPT/Excel para comercial; KPI tiles en el HOME.

---

## 9. Decisiones tomadas / pendientes del user

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

## Registro (con fecha)

- **2026-07-19 (9) — REPORTES FINANCIEROS: feed unificado + fix del PDF embebido.**
  Feedback del user: no sub-tabs — UN solo lugar, lista a la izquierda (mails +
  documentos MEZCLADOS por fecha), contenido a la derecha (texto/PDF/comentario).
  Rehecho como master-detail; se borró el ReportesPanel/ReporteItem viejo (dead code).
  **Fix del PDF que no embebía** ("rechazó la conexión"): `next.config.ts` pone
  `X-Frame-Options: DENY` global (NO se toca — protección anti-clickjacking) → el
  iframe al endpoint queda bloqueado. Solución: el visor BAJA el PDF como blob y lo
  muestra desde una URL `blob:` (que no lleva ese header) → se ve embebido, interno,
  sin bajar la seguridad. Solo frontend.

- **2026-07-19 (8) — Vista REPORTES FINANCIEROS + carga manual de documentos.**
  Nueva tab en Research que junta el contexto de texto/documento. Dos piezas:
  - **Migración:** el panel de reportes 1816/ACA VALORES (mails) salió de RENTA FIJA
    ARGENTINA (que quedó con 3 paneles) a la tab nueva REPORTES FINANCIEROS.
  - **Carga manual (Manager → DOCUMENTOS):** PDFs (ej. el "Semanal") y comentarios que
    NO llegan por mail. **Storage decidido: bytea en Postgres** (`research.documentos`)
    — cero infra; la UX es idéntica a un bucket (si crece, se migra sin que cambie).
    Backend: `research_docs_sql.py` (service) · `api/routers/research_docs.py` (LECTURA,
    gate research: `/api/research-docs/list` + `/{id}/pdf` que sirve el PDF inline) ·
    `api/routers/manager/documentos.py` (ESCRITURA, gate manager: POST base64 / DELETE).
    Frontend: Manager tab DOCUMENTOS (form PDF/comentario + lista) · REPORTES
    FINANCIEROS con sub-tabs Automáticos (mail) / Documentos (master-detail: lista +
    PDF embebido en iframe / comentario como texto) · route handler Next
    `research-docs/[...path]` (pasa BINARIO con arrayBuffer) + proxy. Import-chain +
    ruff + typecheck OK.
  - **Del user (Droplet):** `git pull` → `python -m scripts.apply_schema` (crea
    `research.documentos`) → `systemctl restart api.service`. Vercel deploya el front.
    Después: Manager → DOCUMENTOS → subir el Semanal y verlo en Research.

- **2026-07-19 (7) — 4 bloques nuevos: Volatilidad · Dólar/FX · Riesgo-Crédito ·
    Macro Global.** El user los eligió todos. IDs verificados por WebSearch (datos
    recientes) — hallazgos: FRED **eliminó Russell 2000 y Wilshire 5000** (licencia,
    2019/2024) → no hay más índices de acciones amplios; los spreads **ICE BofA solo
    tienen ~3 años de historia** en FRED (recorte abril 2026); CPI China (OECD) con
    algo de rezago. Seed a 58 series.
  - **volatilidad** (single-chart, homogéneo ~pts): VIX, VXN, VXD, RVX, OVX, GVZ
    (familia CBOE, historia completa). Combinan en una sola vista.
  - **dolar_fx** (single-chart, **default Base 100**): dólar amplio, BRL, CNY, MXN,
    JPY, EUR. Escalas distintas → rebaseado por default (`BLOQUE_MODO_DEFAULT`).
  - **riesgo_credito** (CUADRANTES): CRÉDITO USA (HY+IG) · CRÉDITO EM (corp+HY) ·
    CONDICIONES (NFCI+STLFSI4). Spreads con ~3 años de historia.
  - **macro_global** (CUADRANTES): CHINA (CPI/exports/reservas) · BRASIL (Selic/IPCA)
    · LIQUIDEZ FED (balance/reservas/M2).
  - Índices de bolsa ahora también **default Base 100** (los 3 no se leían juntos en
    Nivel). Backend: seed (el cron siembra+backfillea desde 2020 solo). Frontend:
    labels/orden + CUADRANTES + SCALE_GROUP (spreads/condiciones/liquidez) +
    BLOQUE_MODO_DEFAULT. Typecheck OK.
  - **Del user (Droplet):** `git pull` + `python -m jobs.fred_research`.

- **2026-07-19 (6) — CUADRANTES 2x2 para bloques de escala heterogénea (frontend).**
  Feedback del user: EEUU MACRO no se puede leer en un eje único (índice ~300 vs
  empleo ~150.000 vs % ~4). Solución: los bloques listados en `CUADRANTES`
  (hoy solo `eeuu_macro`) se muestran en **2x2, un mini-chart con su propio eje Y
  por sub-grupo**: INFLACIÓN (CPI/coreCPI/corePCE) · EMPLEO (nóminas/desempleo/
  claims) · ACTIVIDAD (PBI/prod. industrial) · EXPECTATIVAS (confianza UMich/
  breakeven 10Y). Arrancan en **Base 100** por default (así se lee el macro; dentro
  de un cuadrante puede haber unidades mixtas). Sin cambio de backend — el grupeo
  vive en el componente (`CUADRANTES`, editable; sumar `commodities` u otro es una
  línea). El resto de los bloques siguen como single-chart con transform + índice de
  referencia. Solo frontend (`research-fred.tsx`), typecheck OK.
  - **(6b)** Commodities también a cuadrantes: COMPLEJO SOJA · GRANOS · ENERGÍA ·
    METALES.
  - **(6c) Selección EXCLUYENTE por grupo de escala (fix del user).** Feedback: dentro
    de un cuadrante las series igual NO combinan en el eje Y (ej. Empleo: nóminas
    ~159k vs desempleo ~4%). Regla nueva: en los cuadrantes las series se MARCAN
    (chips) y solo coexisten las del MISMO grupo de escala (`SCALE_GROUP`); marcar una
    de otro grupo apaga las incompatibles. Grupos: cpi (CPI+CPI núcleo), empleo
    (nóminas+claims), pct (desempleo/breakeven), grano (USD/t), oil (WTI+Brent); el
    resto (PCE, PBI, prod. ind., confianza, gas, cobre) va solo. Default de cada
    cuadrante = el grupo de su 1ª serie (Inflación abre con CPI+CPI núcleo juntos).
    Default de transformación en cuadrantes vuelve a **Nivel** (con la exclusividad,
    el nivel ya combina). Solo frontend.

- **2026-07-19 (5) — Vista 3 (EEUU Macro) + transformaciones + índice de referencia.**
  Tres pedidos del user, construidos juntos porque el macro los necesita.
  - **Vista 3 — bloque `eeuu_macro`** (10 series canónicas VERIFICADAS: CPIAUCSL,
    CPILFESL, PCEPILFE, PAYEMS, UNRATE, ICSA, GDPC1, INDPRO, UMCSENT, T10YIE).
    Unidades muy mixtas (índice/miles/%/claims) → se leen con las transformaciones.
  - **Bloque `indices`** (SP500, NASDAQCOM, DJIA) — sub-tab ÍNDICES BOLSA Y fuente
    del selector "+ índice…" para superponer. Verificado: SP500 y DJIA tienen tope
    de 10 años en FRED (licencia S&P DJ) pero como el backfill arranca en 2020 NO
    afecta; Nasdaq trae historia completa.
  - **Transformaciones (frontend, TODAS las vistas):** Nivel / Base 100 (rebase al
    inicio de la ventana) / Var % (retorno acumulado desde el inicio). Se calculan
    on-the-fly sobre el nivel guardado — sin pedir nada nuevo a FRED. Resuelven el
    comparar series de escalas distintas (clave en macro).
  - **Índice de referencia (2º eje Y):** selector por bloque para superponer un
    índice de bolsa. En Nivel va en un 2º eje a la derecha (violeta, dashed); en
    Base 100 / Var % se rebasa y comparte el eje único (comparación directa).
  - Backend: seed a 32 series (10+9+10+3). `_seed_watch` incremental ya existente →
    el cron siembra + backfillea los bloques nuevos desde 2020 solo. Frontend:
    `research-fred.tsx` reescrito (transform + ref overlay). Typecheck OK.
  - **Del user (Droplet):** `git pull` + `python -m jobs.fred_research` (siembra +
    backfillea eeuu_macro e indices). Vercel deploya el front al pushear.

- **2026-07-19 (4) — Oro retirado (no existe en FRED).** En la 1ª corrida del
  bloque commodities, `GOLDPMGBD228NLBM` tiró HTTP 400 "series does not exist".
  Verificado por WebSearch: FRED **eliminó las series LBMA de oro el 2022-01-31**
  (ICE Benchmark Administration retiró sus datos) — el verificador del workflow lo
  había dado ✅ por error. No hay spot diario de oro bueno en FRED post-2022. Se
  sacó del seed (quedan 9 commodities) y se agregó a `_RETIRADAS` (fuerza
  `activo=false` en cada corrida → la fila ya sembrada se desactiva sola, sin tocar
  la DB). Si se quiere oro, se sourcea de otro lado o se verifica una serie IMF con
  la key. Backfill real de commodities: 9/9 con oro afuera.

- **2026-07-19 (3) — Vista 2: bloque COMMODITIES / AGRO.** El user aprobó los 10
  IDs marcados (soja, harina, aceite, maíz, trigo, cobre — mensuales del FMI; WTI,
  Brent, oro, gas — diarios). Seed sumado a `jobs/fred_research.py` (ahora 20 series).
  `_seed_watch()` pasó a ser **incremental** (INSERT ON CONFLICT DO NOTHING para
  TODO el seed, no solo si el watch está vacío) → agregar un bloque = sumar filas al
  seed; el próximo run del cron inserta solo las nuevas y, como no tienen watermark,
  las backfillea desde 2020 solo (NO hace falta `--backfill` ni `apply_schema`).
  Frontend: la sub-tab COMMODITIES aparece sola (el componente ya la tenía en su
  orden/labels). **Del user (Droplet):** `git pull` + `python -m jobs.fred_research`
  (una corrida default siembra + backfillea el bloque nuevo). Caveat honesto asentado:
  los granos en FRED son MENSUALES (FMI, no el board de Chicago diario) y el bloque
  tiene unidades MIXTAS (el default muestra el complejo soja, misma escala USD/t;
  comparar escalas distintas es para el índice base 100, pendiente).

- **2026-07-19 (2) — FASE 1 + tab TASAS USA construidas.** Decisión del user: ir de
  menos a más, **vista por vista**; primera vista = **Tasas USA**; scope de países
  USA + China (+ Argentina vía cruces); backfill **desde 2020**.
  - **Backend:** `core/fred_api.py` (cliente con API key en query param + `file_type=json`,
    throttle 0.6s/120-min, backoff 429/5xx, descarta `"."` de FRED; `metadata()` +
    `observaciones()`) · tablas `research.fred_{series,watch,observations}` en
    `sql/schema.sql` (**series_id TEXTO**, `freq` en el watch por frecuencias mixtas)
    · `jobs/fred_research.py` (seed 10 series Tasas USA VERIFICADAS: DGS2/5/10/30,
    DFII10, T10Y2Y, T10Y3M, SOFR, FEDFUNDS, DFF; `--backfill` default desde 2020,
    incremental por watermark con **colchón por frecuencia** D:−10d / M/Q:−95d;
    `--dry-run`/`--purgar-antes`; JobRunLogger) · endpoints
    `GET /api/research-fred/{bloques,series}` (gate `research`, batch) · cron
    `0 12,16,20,23 * * 1-5` · router registrado en `api/main.py` · SISTEMA.md
    regenerado · cron en el ignore-set del test del Diagnóstico. Import-chain (REGLA #1)
    + ruff + test del registro: OK.
  - **Frontend (acaquant-web):** `research-fred.tsx` (tab con sub-tabs por bloque,
    header único + chart multi-serie, keep-alive) · nueva tab **DATOS INTERNACIONALES**
    en `research-view.tsx` (entre BCRA y RV Internacional) · route handler
    `research-fred/[...path]/route.ts` + gating en `proxy.ts`. Typecheck OK.
  - **PENDIENTE del user (Droplet):** poner `FRED_API_KEY` en el `.env` → `git pull` →
    `python -m scripts.apply_schema` (crea las 3 tablas) → `python -m jobs.fred_research
    --dry-run` (ver el plan) → `python -m jobs.fred_research --backfill` (historia
    desde 2020, una vez, fuera de rueda) → `systemctl restart api.service`. El cron
    mantiene solo. Vercel deploya el front al pushear a main.
  - **Próximas vistas (de a una, con el user):** Commodities/Agro (el cruce soja
    Chicago vs pizarra local), EEUU Macro (inflación+empleo+actividad), China.

- **2026-07-19 — Diseño MEGA PRO redactado.** Estructura calcada de
  `docs/RESEARCH_BCRA.md`, aterrizada en los patrones reales del repo
  (`core/bcra_api.py`, `jobs/bcra_research.py`, `api/routers/research_bcra.py`,
  `api/services/research_bcra_sql.py`, schema `research.bcra_*`). Mecánica FRED
  verificada (auth con key, 120/min, endpoints, ALFRED, gotchas units/freq/SA).
  Universo de ~48 series **con IDs verificados** (✅) + 3-4 a confirmar en Fase 0
  (⏳ DFEDTARU/PIORECRUSDM/CBBTCUSD/USREC). Cruces con datos propios desarrollados
  (el diferencial). **Bloqueado por la `FRED_API_KEY` del user.** Pendiente de su
  aprobación del seed y prioridad.

---
